"""Command-line interface (`genealogy`) for validation, intake, pipelines, and archival search."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from genealogy import __version__
from genealogy.apis.chronicling_america import search_newspapers
from genealogy.apis.nara import search_nara
from genealogy.apis.wikitree import (
    get_wikitree_ancestors,
    get_wikitree_profile,
    search_wikitree_person,
)
from genealogy.db import build_sqlite, run_sql_query, search_fts
from genealogy.gedcom import export_gedcom, import_gedcom
from genealogy.intake import scaffold_unlinked_sources, scan_artifacts
from genealogy.loader import load_dataset
from genealogy.timeline import build_timeline_markdown, render_tree
from genealogy.validator import validate_dataset


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_dataset(args.root)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(
            f"Validated {report.root}: "
            f"{report.individuals_count} individuals, "
            f"{report.sources_count} sources, "
            f"{report.relationships_count} relationships"
        )
        for issue in report.issues:
            loc = f" ({issue.path})" if issue.path else ""
            print(f"  [{issue.severity.upper()}] {issue.code}{loc}: {issue.message}")
        if not report.issues:
            print("  All checks passed with 0 errors and 0 warnings.")
        else:
            print(
                f"Summary: {len(report.errors)} error(s), {len(report.warnings)} warning(s)."
            )
    return 0 if report.is_valid(strict=args.strict) else 1


def _cmd_intake(args: argparse.Namespace) -> int:
    report = scan_artifacts(args.root)
    if args.json and not args.scaffold:
        data = {
            "root": str(report.root),
            "total_artifacts": len(report.artifacts),
            "linked_count": len(report.linked),
            "unlinked_count": len(report.unlinked),
            "duplicates": report.duplicates,
            "artifacts": [
                {
                    "path": a.rel_path,
                    "size_bytes": a.size_bytes,
                    "sha256": a.sha256,
                    "linked_sources": a.linked_sources,
                    "hash_mismatches": a.hash_mismatches,
                }
                for a in report.artifacts
            ],
        }
        print(json.dumps(data, indent=2))
        return 0

    print(
        f"Artifacts under {report.root / 'artifacts'}: "
        f"{len(report.artifacts)} total, {len(report.linked)} linked, {len(report.unlinked)} unlinked"
    )
    for art in report.artifacts:
        status = f"linked -> {', '.join(art.linked_sources)}" if art.linked_sources else "UNLINKED"
        mismatch = f" [HASH MISMATCH: {', '.join(art.hash_mismatches)}]" if art.hash_mismatches else ""
        print(f"  {art.rel_path}  sha256={art.sha256[:12]}...  ({status}){mismatch}")

    if report.duplicates:
        print("Duplicate artifacts detected (identical SHA-256):")
        for h, paths in report.duplicates.items():
            print(f"  {h[:12]}...: {', '.join(paths)}")

    if args.scaffold:
        created = scaffold_unlinked_sources(args.root, dry_run=args.dry_run)
        if not created:
            print("No unlinked artifacts to scaffold.")
        else:
            verb = "Would create" if args.dry_run else "Created"
            for sid, dest in created:
                print(f"  {verb} {sid} -> {dest}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    ds = load_dataset(args.root)
    intake = scan_artifacts(args.root)
    by_status = Counter(i.data.status for i in ds.individuals.values())
    living_count = sum(1 for i in ds.individuals.values() if i.data.living)
    by_rec_type = Counter(s.data.record_type for s in ds.sources.values())
    by_rel_type = Counter(r.type for r in ds.relationships)

    summary = {
        "root": str(ds.root),
        "individuals": {
            "total": len(ds.individuals),
            "by_status": dict(by_status),
            "living": living_count,
        },
        "sources": {
            "total": len(ds.sources),
            "by_record_type": dict(by_rec_type),
        },
        "relationships": {
            "total": len(ds.relationships),
            "by_type": dict(by_rel_type),
        },
        "artifacts": {
            "total": len(intake.artifacts),
            "linked": len(intake.linked),
            "unlinked": len(intake.unlinked),
        },
        "load_errors": len(ds.load_errors),
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Workspace: {ds.root}")
    print(
        f"  Individuals:   {len(ds.individuals)} "
        f"(confirmed={by_status.get('confirmed', 0)}, "
        f"tentative={by_status.get('tentative', 0)}, "
        f"hypothetical={by_status.get('hypothetical', 0)}, "
        f"living={living_count})"
    )
    print(f"  Sources:       {len(ds.sources)} ({dict(by_rec_type) or 'none'})")
    print(f"  Relationships: {len(ds.relationships)} ({dict(by_rel_type) or 'none'})")
    print(
        f"  Artifacts:     {len(intake.artifacts)} "
        f"(linked={len(intake.linked)}, unlinked={len(intake.unlinked)})"
    )
    if ds.load_errors:
        print(f"  Load errors:   {len(ds.load_errors)} (run `genealogy validate` for details)")
    return 0


def _cmd_build_db(args: argparse.Namespace) -> int:
    out = build_sqlite(args.root, args.out)
    print(f"Built SQLite database: {out}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    try:
        if args.fts:
            rows = search_fts(args.root, args.query, limit=args.limit)
        else:
            rows = run_sql_query(args.root, args.query)
    except Exception as exc:
        print(f"Query error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No matching rows.")
        return 0

    for row in rows:
        if args.fts:
            print(f"[{row['entity_type']}] {row['entity_id']} — {row['title']}")
            if row.get("snippet"):
                print(f"    {row['snippet']}")
        else:
            print(json.dumps(row))
    return 0


def _cmd_timeline(args: argparse.Namespace) -> int:
    try:
        md = build_timeline_markdown(args.root, args.individual_id, include_family=args.include_family)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"Wrote timeline to {args.out}")
    else:
        print(md, end="")
    return 0


def _cmd_tree(args: argparse.Namespace) -> int:
    try:
        output = render_tree(
            args.root,
            args.individual_id,
            fmt=args.format,
            depth=args.depth,
        )
    except (KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Wrote {args.format} graph to {args.out}")
    else:
        print(output, end="")
    return 0


def _cmd_import_gedcom(args: argparse.Namespace) -> int:
    summary = import_gedcom(args.file, args.root, dest=args.dest)
    print(f"Imported GEDCOM into {summary.destination}")
    print(f"  Compiled source ID: {summary.source_id}")
    print(f"  Individuals:        {summary.individuals_imported} (status=tentative)")
    print(f"  Relationships:      {summary.relationships_imported} (confidence=speculative)")
    for w in summary.warnings:
        print(f"  [WARNING] {w}")
    return 0


def _cmd_export_gedcom(args: argparse.Namespace) -> int:
    out = export_gedcom(
        args.root,
        args.out,
        version=args.version,
        redact_living=not args.include_living,
    )
    redaction_note = "living individuals redacted" if not args.include_living else "INCLUDING living individuals"
    print(f"Exported GEDCOM {args.version} to {out} ({redaction_note})")
    return 0


def _print_api_results(items: list[Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps([i.to_dict() for i in items], indent=2))
        return 0
    if not items:
        print("No results found.")
        return 0
    for idx, item in enumerate(items, start=1):
        d = item.to_dict()
        title = d.get("title") or d.get("full_name") or d.get("wikitree_id") or "(item)"
        print(f"{idx}. {title}")
        for k, v in d.items():
            if k in {"title", "full_name"} or not v:
                continue
            print(f"   {k}: {v}")
    return 0


def _cmd_search_newspapers(args: argparse.Namespace) -> int:
    try:
        hits = search_newspapers(
            args.query,
            state=args.state,
            start_year=args.start_year,
            end_year=args.end_year,
            limit=args.limit,
            page=args.page,
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"Chronicling America API error: {exc}", file=sys.stderr)
        return 1
    return _print_api_results(hits, args.json)


def _cmd_search_nara(args: argparse.Namespace) -> int:
    try:
        hits = search_nara(
            args.query,
            record_group=args.record_group,
            limit=args.limit,
            page=args.page,
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"NARA Catalog API error: {exc}", file=sys.stderr)
        return 1
    return _print_api_results(hits, args.json)


def _cmd_search_wikitree(args: argparse.Namespace) -> int:
    try:
        if args.profile:
            prof = get_wikitree_profile(args.profile)
            items = [prof] if prof else []
        elif args.ancestors:
            items = get_wikitree_ancestors(args.ancestors, depth=args.depth)
        else:
            if not (args.first or args.last or args.birth_date or args.birth_location):
                print(
                    "Provide --profile, --ancestors, or at least one of --first/--last/--birth-date/--birth-location.",
                    file=sys.stderr,
                )
                return 2
            items = search_wikitree_person(
                first_name=args.first,
                last_name=args.last,
                birth_date=args.birth_date,
                birth_location=args.birth_location,
                limit=args.limit,
            )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"WikiTree API error: {exc}", file=sys.stderr)
        return 1
    return _print_api_results(items, args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genealogy",
        description="Evidence-based genealogy workspace validator, pipelines, and archival search CLI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    p_val = sub.add_parser("validate", help="Validate data/ schema, links, biology, hashes, and GPS rules")
    p_val.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_val.add_argument("--strict", action="store_true", help="Fail with exit code 1 on warnings too")
    p_val.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_val.set_defaults(func=_cmd_validate)

    # intake
    p_in = sub.add_parser("intake", help="Audit artifacts/, compute SHA-256 hashes, and scaffold missing sources")
    p_in.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_in.add_argument("--scaffold", action="store_true", help="Create stub data/sources/ files for unlinked artifacts")
    p_in.add_argument("--dry-run", action="store_true", help="Preview scaffolded files without writing")
    p_in.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_in.set_defaults(func=_cmd_intake)

    # stats
    p_st = sub.add_parser("stats", help="Show workspace counts and status overview")
    p_st.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_st.add_argument("--json", action="store_true", help="Output JSON")
    p_st.set_defaults(func=_cmd_stats)

    # build-db
    p_db = sub.add_parser("build-db", help="Compile data/ into SQLite with FTS5 full-text search")
    p_db.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_db.add_argument("--out", default=None, help="Output SQLite file path (default: <root>/build/genealogy.sqlite)")
    p_db.set_defaults(func=_cmd_build_db)

    # query
    p_q = sub.add_parser("query", help="Search records via FTS5 (--fts) or read-only SQL SELECT")
    p_q.add_argument("query", help="FTS5 search terms (with --fts) or SQL SELECT statement")
    p_q.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_q.add_argument("--fts", action="store_true", help="Run full-text search across bios and transcriptions")
    p_q.add_argument("--limit", type=int, default=25, help="Max FTS results (default: 25)")
    p_q.add_argument("--json", action="store_true", help="Output JSON")
    p_q.set_defaults(func=_cmd_query)

    # timeline
    p_tl = sub.add_parser("timeline", help="Generate a chronological Markdown timeline for an individual")
    p_tl.add_argument("individual_id", help="Individual ID (e.g., I-WEST-CLAUDE-1886)")
    p_tl.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_tl.add_argument("--include-family", action="store_true", help="Interleave children's births and parents' deaths")
    p_tl.add_argument("--out", default=None, help="Optional output file path")
    p_tl.set_defaults(func=_cmd_timeline)

    # tree
    p_tr = sub.add_parser("tree", help="Render family relationships as Mermaid or Graphviz DOT")
    p_tr.add_argument("individual_id", nargs="?", default=None, help="Optional center individual ID")
    p_tr.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_tr.add_argument("--format", choices=["mermaid", "dot"], default="mermaid", help="Output format (default: mermaid)")
    p_tr.add_argument("--depth", type=int, default=3, help="Traversal depth around center individual (default: 3)")
    p_tr.add_argument("--out", default=None, help="Optional output file path")
    p_tr.set_defaults(func=_cmd_tree)

    # import-gedcom
    p_imp = sub.add_parser("import-gedcom", help="Import GEDCOM 5.5.1/7.0 into workspace/staging/ (default) or data/")
    p_imp.add_argument("file", help="Path to .ged file")
    p_imp.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_imp.add_argument("--dest", choices=["staging", "data"], default="staging", help="Target destination (default: staging)")
    p_imp.set_defaults(func=_cmd_import_gedcom)

    # export-gedcom
    p_exp = sub.add_parser("export-gedcom", help="Export data/ to GEDCOM 7.0 (default) or 5.5.1")
    p_exp.add_argument("out", help="Output .ged file path")
    p_exp.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_exp.add_argument("--version", choices=["7.0", "5.5.1"], default="7.0", help="GEDCOM version (default: 7.0)")
    p_exp.add_argument("--include-living", action="store_true", help="Include unredacted living individuals (default: redacted)")
    p_exp.set_defaults(func=_cmd_export_gedcom)

    # search (open archival APIs)
    p_srch = sub.add_parser("search", help="Query open archival & genealogy APIs")
    srch_sub = p_srch.add_subparsers(dest="provider", required=True)

    p_news = srch_sub.add_parser("newspapers", help="Search Chronicling America (loc.gov JSON API, 1770-1963)")
    p_news.add_argument("query", help="Search terms")
    p_news.add_argument("--state", default=None, help="State/location facet (e.g., tennessee)")
    p_news.add_argument("--start-year", type=int, default=None, help="Start year filter")
    p_news.add_argument("--end-year", type=int, default=None, help="End year filter")
    p_news.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    p_news.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    p_news.add_argument("--json", action="store_true", help="Output JSON")
    p_news.set_defaults(func=_cmd_search_newspapers)

    p_nara = srch_sub.add_parser("nara", help="Search NARA National Archives Catalog API v2")
    p_nara.add_argument("query", help="Search terms")
    p_nara.add_argument("--record-group", default=None, help="Optional NARA Record Group number (e.g., 29 for Census)")
    p_nara.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    p_nara.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    p_nara.add_argument("--json", action="store_true", help="Output JSON")
    p_nara.set_defaults(func=_cmd_search_nara)

    p_wt = srch_sub.add_parser("wikitree", help="Query WikiTree public read-only API (secondary leads only)")
    p_wt.add_argument("--first", default=None, help="First name")
    p_wt.add_argument("--last", default=None, help="Last name")
    p_wt.add_argument("--birth-date", default=None, help="Birth date or year (e.g., 1886)")
    p_wt.add_argument("--birth-location", default=None, help="Birth location")
    p_wt.add_argument("--profile", default=None, help="Fetch a specific WikiTree ID (e.g., Clemens-1)")
    p_wt.add_argument("--ancestors", default=None, help="Fetch ancestors for a WikiTree ID")
    p_wt.add_argument("--depth", type=int, default=3, help="Ancestor depth (default: 3)")
    p_wt.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    p_wt.add_argument("--json", action="store_true", help="Output JSON")
    p_wt.set_defaults(func=_cmd_search_wikitree)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
