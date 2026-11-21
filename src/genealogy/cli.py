"""Command-line interface (`genealogy`) for validation, intake, pipelines, and archival search."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from genealogy import __version__
from genealogy.analysis import (
    build_family_report_markdown,
    compute_dna_corroboration,
    compute_kinship,
    compute_land_tracts,
    compute_pedigree_collapse,
    compute_research_gaps,
)
from genealogy.apis.chronicling_america import search_newspapers
from genealogy.apis.nara import search_nara
from genealogy.apis.wikitree import (
    get_wikitree_ancestors,
    get_wikitree_profile,
    search_wikitree_person,
)
from genealogy.completion import generate_completion
from genealogy.constraints import evaluate_all_constraints
from genealogy.db import build_sqlite, run_sql_query, search_fts
from genealogy.gedcom import export_gedcom, import_gedcom
from genealogy.intake import (
    grab_screenshot,
    rename_artifact,
    scaffold_unlinked_sources,
    scan_artifacts,
)
from genealogy.loader import load_dataset
from genealogy.timeline import build_timeline_markdown, render_tree
from genealogy.urls import add_link_to_source
from genealogy.validator import validate_dataset
from genealogy.viewer import export_viewer_html, serve_viewer
from genealogy.workspace import init_workspace, is_workspace_dir, upgrade_workspace


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_dataset(args.root, skip_artifacts=args.skip_artifacts)
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
            "excluded_duplicates": report.excluded_duplicates,
            "artifacts": [
                {
                    "path": a.rel_path,
                    "size_bytes": a.size_bytes,
                    "sha256": a.sha256,
                    "linked_sources": a.linked_sources,
                    "hash_mismatches": a.hash_mismatches,
                    "excluded_matches": a.excluded_matches,
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
        ex_flag = (
            f" [DUPLICATE OF EXCLUDED LEAD: {', '.join(m['source_id'] for m in art.excluded_matches)}]"
            if art.excluded_matches
            else ""
        )
        print(f"  {art.rel_path}  sha256={art.sha256[:12]}...  ({status}){mismatch}{ex_flag}")

    if report.excluded_duplicates:
        print("WARNING — Artifact matches previously EXCLUDED mistaken lead(s):")
        for ed in report.excluded_duplicates:
            print(
                f"  {ed['artifact']} matches {ed['source_id']} "
                f"(excluded for: {ed['excluded_for'] or 'n/a'} — {ed['reason']})"
            )

    if report.duplicates:
        print("Duplicate artifacts detected (identical SHA-256):")
        for h, paths in report.duplicates.items():
            print(f"  {h[:12]}...: {', '.join(paths)}")

    if args.scaffold:
        try:
            created = scaffold_unlinked_sources(
                args.root,
                dry_run=args.dry_run,
                url=args.url,
                link_note=args.link_note,
            )
        except ValueError as exc:
            print(f"Intake error: {exc}", file=sys.stderr)
            return 1
        if not created:
            print("No unlinked artifacts to scaffold.")
        else:
            verb = "Would create" if args.dry_run else "Created"
            for sid, dest in created:
                print(f"  {verb} {sid} -> {dest}")
    return 0


def _cmd_grab_screenshot(args: argparse.Namespace) -> int:
    try:
        res = grab_screenshot(
            args.root,
            source=args.source,
            name=args.name,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"grab-screenshot error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "source_path": str(res.source_path),
                    "dest_path": str(res.dest_path),
                    "rel_path": res.rel_path,
                    "sha256": res.sha256,
                    "already_exists": res.already_exists,
                    "existing_rel_path": res.existing_rel_path,
                    "excluded_matches": res.excluded_matches,
                    "dry_run": args.dry_run,
                },
                indent=2,
            )
        )
        return 0

    if res.already_exists:
        print(f"Screenshot already present in artifacts/: {res.existing_rel_path}")
        print(f"  Source: {res.source_path}")
        print(f"  SHA256: {res.sha256}")
    else:
        verb = "Would copy" if args.dry_run else "Copied"
        print(f"{verb} latest screenshot -> {res.rel_path}")
        print(f"  From:   {res.source_path}")
        print(f"  To:     {res.dest_path}")
        print(f"  SHA256: {res.sha256}")

    if res.excluded_matches:
        print("WARNING — Screenshot matches previously EXCLUDED mistaken lead(s):")
        for m in res.excluded_matches:
            print(
                f"  {m['source_id']} (excluded for: {m['excluded_for'] or 'n/a'} — {m['reason']})"
            )
    return 0


def _cmd_rename_artifact(args: argparse.Namespace) -> int:
    try:
        res = rename_artifact(
            args.root,
            args.old_path,
            args.new_name,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"rename-artifact error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "old_rel_path": res.old_rel_path,
                    "new_rel_path": res.new_rel_path,
                    "old_abs_path": str(res.old_abs_path),
                    "new_abs_path": str(res.new_abs_path),
                    "sha256": res.sha256,
                    "updated_sources": res.updated_sources,
                    "dry_run": args.dry_run,
                },
                indent=2,
            )
        )
        return 0

    if res.old_rel_path == res.new_rel_path:
        print(f"Artifact already named {res.new_rel_path}")
        return 0

    verb = "Would rename" if args.dry_run else "Renamed"
    print(f"{verb} {res.old_rel_path} -> {res.new_rel_path}")
    print(f"  SHA256: {res.sha256}")
    if res.updated_sources:
        sverb = "Would update" if args.dry_run else "Updated"
        print(f"  {sverb} artifact_path in source(s): {', '.join(res.updated_sources)}")
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
    if summary.warnings:
        max_show = len(summary.warnings) if args.verbose else 10
        for w in summary.warnings[:max_show]:
            print(f"  [WARNING] {w}")
        if len(summary.warnings) > max_show:
            rem = len(summary.warnings) - max_show
            print(f"  ... and {rem} more warning(s) (re-run with --verbose to list all; unparsed dates were preserved in each profile's Notes)")
        print()

    print(f"Imported GEDCOM into: {summary.destination}")
    print(f"  Compiled source ID: {summary.source_id}")
    print(f"  Individuals:        {summary.individuals_imported} (status=tentative)")
    print(f"  Relationships:      {summary.relationships_imported} (confidence=speculative)")
    print(f"  Date warnings:      {len(summary.warnings)}")
    if args.dest == "staging":
        try:
            rel_dest = summary.destination.relative_to(Path(args.root).resolve())
        except ValueError:
            rel_dest = summary.destination
        print("\nNOTE: Files were staged safely outside `data/` (per GPS rules). Next steps:")
        print(f"  genealogy stats --root {rel_dest}")
        print(f"  genealogy validate --root {rel_dest}")
        print(f"  genealogy view --root {rel_dest}")
        print("  (Or re-run with `--dest data` to import directly into canonical `data/`.)")
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


def _cmd_kinship(args: argparse.Namespace) -> int:
    try:
        rep = compute_kinship(args.root, args.person_a, args.person_b, max_depth=args.max_depth)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
        return 0
    print(f"Kinship: {rep.person_a_name} ({rep.person_a}) <-> {rep.person_b_name} ({rep.person_b})")
    print(f"  Married:                       {'Yes' if rep.are_married else 'No'}")
    print(f"  Wright's Relationship (r):     {rep.wright_relationship:.5f}")
    print(f"  Expected Offspring F (phi):    {rep.expected_offspring_inbreeding:.5f}")
    print(f"  Distinct Blood Paths:          {len(rep.paths)}")
    for idx, p in enumerate(rep.paths, start=1):
        chain_a = " -> ".join(p.path_a)
        chain_b = " <- ".join(reversed(p.path_b[:-1]))
        full = f"{chain_a} <- {chain_b}" if chain_b else chain_a
        print(f"  {idx}. {p.label} via {p.common_ancestor_name} ({p.common_ancestor}) [{p.steps_a}↑ / {p.steps_b}↑]")
        print(f"     {full}")
    return 0


def _cmd_collapse(args: argparse.Namespace) -> int:
    try:
        rep = compute_pedigree_collapse(args.root, args.individual_id, max_gen=args.max_gen)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
        return 0
    print(f"Pedigree Collapse: {rep.primary_name} ({rep.individual_id})")
    print(f"  Wright's Inbreeding Coefficient (F): {rep.inbreeding_coefficient:.5f}")
    if rep.generations:
        print("  Per-Generation Ancestry Slots:")
        for g in rep.generations:
            pct = g.collapse_ratio * 100.0
            print(
                f"    Gen {g.generation}: {g.unique_ancestors} unique / "
                f"{g.filled_slots} filled slots (of {g.theoretical_slots} theoretical) "
                f"— collapse {pct:.1f}%"
            )
    if rep.multi_path_ancestors:
        print("  Multi-Path (Collapsed) Ancestors:")
        for mpa in rep.multi_path_ancestors:
            print(
                f"    - {mpa.primary_name} ({mpa.individual_id}): "
                f"×{mpa.path_count} across gen(s) {mpa.generations}"
            )
    else:
        print("  No multi-path (collapsed) ancestors detected within depth limit.")
    return 0


def _cmd_constraints(args: argparse.Namespace) -> int:
    evals = evaluate_all_constraints(
        args.root,
        person_id=args.person,
        status=args.status,
    )
    if args.json:
        print(json.dumps([ev.to_dict() for ev in evals], indent=2))
        return 0
    if not evals:
        print("No matching relationship constraints found.")
        return 0
    print(f"Relationship Constraints ({len(evals)}):")
    for ev in evals:
        sym = " (symmetric)" if ev.symmetric else ""
        print(
            f"\n[{ev.status.upper()}] {ev.constraint_id}: "
            f"{ev.person_1_name} ({ev.person_1}, steps={ev.steps_1}) <-> "
            f"{ev.person_2_name} ({ev.person_2}, steps={ev.steps_2}){sym}"
        )
        print(
            f"  Side 1 known ({len(ev.frontier_1.known_ancestors)}), unknown slots: {ev.frontier_1.unknown_slots}"
        )
        print(
            f"  Side 2 known ({len(ev.frontier_2.known_ancestors)}), unknown slots: {ev.frontier_2.unknown_slots}"
        )
        for ded in ev.deductions:
            print(f"  - [{ded.code}] {ded.message}")
            if ded.suggested_action:
                print(f"    Action: {ded.suggested_action}")
        if ev.ruled_out_candidates:
            print(f"  Ruled out ({len(ev.ruled_out_candidates)}):")
            for rc in ev.ruled_out_candidates:
                print(f"    x {rc.candidate_id} ({rc.candidate_name}): {'; '.join(rc.rejection_reasons)}")
    return 0


def _cmd_gaps(args: argparse.Namespace) -> int:
    reg_list = (
        [r.strip().lower() for r in args.regions.split(",") if r.strip()]
        if args.regions
        else None
    )
    gaps = compute_research_gaps(args.root, regions=reg_list)
    if args.json:
        print(json.dumps(gaps, indent=2))
        return 0
    print(f"Research Gap & Audit Report ({gaps['root']})")
    print(f"\nMissing Regional National Censuses: {len(gaps['census_gaps'])} person(s)")
    for row in gaps["census_gaps"]:
        miss = ", ".join(str(y) for y in row["missing_censuses"])
        cov = ", ".join(str(y) for y in row["covered_censuses"]) or "none"
        reg_lbl = "/".join(row.get("regions") or [])
        print(f"  - {row['primary_name']} ({row['individual_id']}, {row['lifespan']}) [{reg_lbl}]")
        print(f"    Covered: {cov} | Missing: {miss}")

    if gaps.get("record_gaps"):
        print(f"\nMissing Parish / Civil Vital Records: {len(gaps['record_gaps'])} person(s)")
        for rg in gaps["record_gaps"]:
            reg_lbl = "/".join(rg.get("regions") or [])
            print(f"  - {rg['primary_name']} ({rg['individual_id']}) [{reg_lbl}]: {'; '.join(rg['missing_records'])}")

    print(f"\nPromotion-Ready Tentative Profiles (>=2 original sources): {len(gaps['promotion_ready'])}")
    for pr in gaps["promotion_ready"]:
        print(f"  - {pr['primary_name']} ({pr['individual_id']}): {', '.join(pr['non_compiled_sources'])}")

    print(f"\nRecorded Discrepancies: {len(gaps['discrepancies'])}")
    for d in gaps["discrepancies"]:
        print(f"  - {d['primary_name']} ({d['individual_id']}): {d['note']}")

    print(f"\nConsanguineous Marriages Detected: {len(gaps['consanguineous_marriages'])}")
    for cm in gaps["consanguineous_marriages"]:
        print(
            f"  - {cm['person_a_name']} & {cm['person_b_name']}: "
            f"r={cm['wright_relationship']:.4f} ({cm['path_count']} shared blood path(s))"
        )
        if cm.get("recommended_record"):
            print(f"    Tip: {cm['recommended_record']}")

    if gaps.get("relationship_constraints"):
        print(f"\nUnresolved Relationship Constraints: {len(gaps['relationship_constraints'])}")
        for rc in gaps["relationship_constraints"]:
            print(
                f"  - [{rc['status'].upper()}] {rc['constraint_id']}: "
                f"{rc['person_1_name']} (steps={rc['steps_1']}) <-> "
                f"{rc['person_2_name']} (steps={rc['steps_2']})"
            )
            for ded in rc.get("deductions", []):
                print(f"      * [{ded['code']}] {ded['message']}")

    if gaps.get("marriage_ceremony_gaps"):
        print(f"\nMarriage Investigations Missing Ceremony Record: {len(gaps['marriage_ceremony_gaps'])}")
        for mg in gaps["marriage_ceremony_gaps"]:
            if mg.get("person_2"):
                couple = f"{mg['person_1_name']} ({mg['person_1']}) & {mg['person_2_name']} ({mg['person_2']})"
            else:
                couple = f"{mg['person_1_name']} ({mg['person_1']})"
            inv_dt = mg.get("investigation_date") or "?"
            rel_info = (
                f", relationship: {mg['relationship_date'] or '?'} [{mg['relationship_confidence']}]"
                if mg.get("relationship_confidence")
                else ""
            )
            print(f"  - {couple} (investigation: {inv_dt}{rel_info})")
            if mg.get("recommended_record"):
                print(f"    Tip: {mg['recommended_record']}")

    print(f"\nPending Sources: {len(gaps['pending_sources'])} | Excluded Leads: {len(gaps['excluded_sources'])}")
    for ex in gaps["excluded_sources"]:
        print(f"  - [EXCLUDED] {ex['source_id']}: {ex['title']} ({ex['exclusion_reason']})")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        md = build_family_report_markdown(
            args.root,
            args.individual_id,
            redact_living=not args.include_living,
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(md, encoding="utf-8")
        print(f"Wrote Family Group Sheet to {out_p}")
    else:
        print(md, end="")
    return 0


def _cmd_view(args: argparse.Namespace) -> int:
    if args.out:
        out = export_viewer_html(
            args.root,
            args.out,
            redact_living=not args.include_living,
        )
        print(f"Exported standalone interactive viewer to {out}")
        return 0
    serve_viewer(
        args.root,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        redact_living=not args.include_living,
    )
    return 0


def _cmd_tracts(args: argparse.Namespace) -> int:
    data = compute_land_tracts(args.root, tract_filter=args.tract)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data["tracts"]:
        print("No land tracts recorded in individual events.")
        return 0
    for tr in data["tracts"]:
        print(
            f"Tract: {tr['tract_group']} "
            f"({tr['event_count']} event(s), {len(tr['individuals_involved'])} individual(s))"
        )
        for e in tr["entries"]:
            srcs = ", ".join(e["sources"]) if e["sources"] else "unsourced"
            print(
                f"  - {e['date']} | {e['primary_name']} ({e['individual_id']}) | "
                f"{e['tract']} — {e['description']} [{srcs}]"
            )
    return 0


def _cmd_dna(args: argparse.Namespace) -> int:
    try:
        rep = compute_dna_corroboration(
            args.root,
            args.person_a,
            args.person_b,
            observed_cm=args.observed_cm,
            max_depth=args.max_depth,
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0
    print(f"Endogamy-Aware DNA Corroboration: {rep['person_a_name']} <-> {rep['person_b_name']}")
    print(f"  Documented Blood Paths:    {rep['path_count']}")
    print(f"  Malecot Coancestry (phi):  {rep['coancestry']:.6f}  |  Wright's r: {rep['wright_relationship']:.5f}")
    lo, hi = rep["expected_range_cm"]
    print(f"  Expected Shared DNA:       ~{rep['expected_cm']:.1f} cM (plausible band: {lo:.1f}–{hi:.1f} cM)")
    for idx, p in enumerate(rep["paths"], start=1):
        print(
            f"    {idx}. {p['label']} via {p['common_ancestor_name']} "
            f"[{p['steps_a']}↑ / {p['steps_b']}↑]: ~{p['expected_cm']:.1f} cM"
        )
    if rep["observed_cm"] is not None:
        print(f"  Observed Shared DNA:       {rep['observed_cm']:.1f} cM")
        print(f"  Assessment:                {rep['assessment']}")
        print(f"  Interpretation:            {rep['interpretation']}")
    for note in rep["notes"]:
        print(f"  Note: {note}")
    return 0


def _cmd_add_link(args: argparse.Namespace) -> int:
    req_auth: bool | None = None
    if args.auth:
        req_auth = True
    elif args.public:
        req_auth = False
    try:
        src_path, lnk, added = add_link_to_source(
            args.root,
            args.source_id,
            args.url,
            note=args.note,
            accessed=args.accessed,
            requires_auth=req_auth,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"add-link error: {exc}", file=sys.stderr)
        return 1
    if not added:
        print(f"Link already present on {args.source_id}: {lnk.url}")
        return 0
    auth_str = "auth required" if lnk.requires_auth else "public"
    print(f"Added [{lnk.provider}] ({auth_str}, accessed {lnk.accessed}) to {src_path}")
    if lnk.note:
        print(f"  Note: {lnk.note}")
    return 0


def _is_standard_system_path(path: Path) -> bool:
    """Return True if *path* lives under standard system binary/library roots."""
    parts = path.parts
    if len(parts) < 2 or parts[0] != "/":
        return False
    return parts[1] in {"usr", "bin", "lib", "lib64", "System", "opt"}


def _sandbox_allowlist_paths(repo_root: Path) -> list[Path]:
    """Return non-workspace paths needed to execute `genealogy` inside a sandbox."""
    resolved_root = repo_root.resolve()
    candidates: list[Path] = []

    bin_path = shutil.which("genealogy")
    if bin_path:
        candidates.append(Path(bin_path))

    prefix_path = Path(sys.prefix).resolve()
    candidates.append(prefix_path)

    exe_resolved = Path(sys.executable).resolve()
    # Cover uv-managed Python installs (e.g. ~/.local/share/uv/python/cpython-...)
    exe_root = exe_resolved.parent.parent if exe_resolved.parent.name == "bin" else exe_resolved.parent
    candidates.append(exe_root)

    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if _is_standard_system_path(path):
            continue
        if path.is_relative_to(resolved_root):
            continue
        if any(path != existing and path.is_relative_to(existing) for existing in result):
            continue
        result.append(path)
    return result


def _cmd_init(args: argparse.Namespace) -> int:
    reg_list = (
        [r.strip().lower() for r in args.regions.split(",") if r.strip()]
        if args.regions
        else None
    )
    rep = init_workspace(
        args.directory,
        git_init=not args.no_git,
        use_lfs=args.lfs,
        artifacts_dir=args.artifacts_dir,
        regions=reg_list,
        plugin_only=args.plugin_only,
        ci_repo=args.ci_repo,
    )
    print(f"Initialized genealogy research repository at: {rep.root}")
    print(f"  Toolkit version:  {rep.toolkit_version}")
    print(f"  Agent mode:       {rep.agent_mode}")
    if rep.added:
        print(f"  Synced assets:    {len(rep.added)} file(s) (AGENTS.md + .agents/skills + .agents/hooks)")
    if rep.git_initialized:
        print("  Git repository:   initialized on branch 'main'")
    if args.lfs:
        print("  Git LFS:          .gitattributes added for artifacts/** (run `git lfs install`)")
    if args.artifacts_dir:
        print(f"  External scans:   artifacts_dir = {args.artifacts_dir}")

    print("\nNext steps:")
    print(f"  1. cd {rep.root}")
    print("  2. git add -A && git commit -m \"Initial genealogy workspace\"")
    print("  3. Create a PRIVATE GitHub repository and push:")
    print("     git remote add origin <your-private-repo-url> && git push -u origin main")
    print("  4. Drop record scans in artifacts/ and notes in notes/, then run:")
    print("     genealogy intake")

    allow_paths = _sandbox_allowlist_paths(rep.root)
    if allow_paths:
        print("\nAntigravity Terminal Sandbox:")
        print("  If using Antigravity's Terminal Sandbox, grant read-only access in Project-Level Settings to:")
        for path in allow_paths:
            print(f"    - {path}")
        print("  (Or approve running `genealogy` outside the sandbox when prompted.)")
    return 0


def _cmd_upgrade_workspace(args: argparse.Namespace) -> int:
    plugin_override: bool | None = None
    if args.plugin_only:
        plugin_override = True
    elif args.embed_agent:
        plugin_override = False

    try:
        rep = upgrade_workspace(
            args.root,
            dry_run=args.dry_run,
            plugin_only=plugin_override,
            allow_dirty=args.allow_dirty,
            auto_commit=not args.no_commit,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"upgrade-workspace error: {exc}", file=sys.stderr)
        return 1

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Upgraded workspace {rep.root} to toolkit v{rep.toolkit_version} (mode={rep.agent_mode})")
    print(
        f"  Added: {len(rep.added)} | Updated: {len(rep.updated)} | "
        f"Removed: {len(rep.removed)} | Unchanged: {len(rep.unchanged)}"
    )
    for label, items in (("ADD", rep.added), ("UPD", rep.updated), ("DEL", rep.removed)):
        for item in items:
            print(f"    [{label}] {item}")
    if not args.dry_run and (rep.added or rep.updated or rep.removed or rep.config_updated):
        print("\nUser files untouched: data/, artifacts/, notes/, workspace/, README.md, AGENTS.local.md.")
        if rep.committed:
            print(f"\nCommitted workspace upgrade ({rep.commit_sha}).")
        else:
            staged_targets: list[str] = []
            if (rep.root / ".genealogy.yaml").exists():
                staged_targets.append(".genealogy.yaml")
            if (rep.root / "AGENTS.md").exists():
                staged_targets.append("AGENTS.md")
            if (rep.root / ".agents").exists():
                staged_targets.append(".agents/")
            targets_str = " ".join(staged_targets) if staged_targets else "-A"
            print("\nRecommended git commands to review and commit the update:")
            if rep.root != Path.cwd().resolve():
                print(f"  cd {rep.root}")
            print("  git status")
            print(f"  git add {targets_str}")
            print(f'  git commit -m "Upgrade genealogy workspace to toolkit v{rep.toolkit_version}"')
    if rep.suggested_prompts:
        print("\nSuggested Antigravity prompt(s) to update your existing data/ files:")
        for idx, item in enumerate(rep.suggested_prompts, start=1):
            print(f"  {idx}. [v{item['version']}] {item['title']}")
            print(f'     "{item["prompt"]}"')
    return 0


def _cmd_upgrade(args: argparse.Namespace) -> int:
    root_path = Path(args.root).expanduser().resolve()
    in_workspace = (not args.no_workspace) and is_workspace_dir(root_path)

    if args.dry_run:
        print("[DRY RUN] Would run: uv tool upgrade genealogy-workspace")
        if in_workspace:
            extra = []
            if args.no_commit:
                extra.append("--no-commit")
            if args.allow_dirty:
                extra.append("--allow-dirty")
            suffix = f" {' '.join(extra)}" if extra else ""
            print(
                f"[DRY RUN] Workspace detected at {root_path}; "
                f"would run: genealogy upgrade-workspace --root {root_path}{suffix}"
            )
        return 0

    uv_bin = shutil.which("uv")
    if not uv_bin:
        print(
            "upgrade error: `uv` not found on PATH. Install uv or run `uv tool upgrade genealogy-workspace` manually.",
            file=sys.stderr,
        )
        return 1

    uv_proc = subprocess.run([uv_bin, "tool", "upgrade", "genealogy-workspace"])
    if uv_proc.returncode != 0:
        return int(uv_proc.returncode)

    if in_workspace:
        # Re-exec via the newly upgraded CLI binary so the new bundled plugin & version are used.
        genealogy_bin = shutil.which("genealogy") or sys.argv[0]
        cmd = [genealogy_bin, "upgrade-workspace", "--root", str(root_path)]
        if args.no_commit:
            cmd.append("--no-commit")
        if args.allow_dirty:
            cmd.append("--allow-dirty")
        ws_proc = subprocess.run(cmd)
        return int(ws_proc.returncode)

    return 0


def _cmd_completion(args: argparse.Namespace) -> int:
    print(generate_completion(build_parser(), args.shell), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genealogy",
        description="Evidence-based genealogy workspace validator, pipelines, and archival search CLI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser(
        "init",
        help="Bootstrap a fresh, private family-history git repository (separate from toolkit code)",
    )
    p_init.add_argument("directory", nargs="?", default=".", help="Target directory (default: current directory)")
    p_init.add_argument("--no-git", action="store_true", help="Do not run `git init`")
    p_init.add_argument("--lfs", action="store_true", help="Add .gitattributes for Git LFS on artifacts/ scans")
    p_init.add_argument(
        "--artifacts-dir",
        default=None,
        help="Store raw scans outside Git (e.g., ~/Dropbox/FamilyScans) via .genealogy.yaml",
    )
    p_init.add_argument(
        "--regions",
        default="auto",
        help="Comma-separated record profiles: auto, mexico, us, uk, canada, general (default: auto)",
    )
    p_init.add_argument(
        "--plugin-only",
        action="store_true",
        help="Skip copying AGENTS.md/.agents/ into the repo (use globally installed Antigravity plugin instead)",
    )
    p_init.add_argument(
        "--ci-repo",
        default=None,
        help="Toolkit Git URL to embed in .github/workflows/validate.yml",
    )
    p_init.set_defaults(func=_cmd_init)

    # upgrade (self-upgrade via uv + auto-upgrade workspace if detected)
    p_self_upg = sub.add_parser(
        "upgrade",
        aliases=["update"],
        help="Upgrade the genealogy CLI via uv (and refresh the current workspace if inside one)",
    )
    p_self_upg.add_argument("--root", default=".", help="Workspace root directory to check/upgrade (default: .)")
    p_self_upg.add_argument(
        "--no-workspace",
        action="store_true",
        help="Upgrade only the CLI; skip refreshing the current workspace even if detected",
    )
    p_self_upg.add_argument("--dry-run", action="store_true", help="Preview upgrade commands without executing")
    p_self_upg.add_argument(
        "--no-commit",
        action="store_true",
        help="Do not automatically commit managed workspace updates if a workspace is upgraded",
    )
    p_self_upg.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow upgrading workspace even if git has uncommitted changes",
    )
    p_self_upg.set_defaults(func=_cmd_upgrade)

    # upgrade-workspace
    p_upg = sub.add_parser(
        "upgrade-workspace",
        aliases=["update-workspace"],
        help="Refresh managed AGENTS.md, skills, and hooks after upgrading the genealogy CLI",
    )
    p_upg.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_upg.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files")
    p_upg.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow upgrading even if the git workspace has uncommitted changes",
    )
    p_upg.add_argument(
        "--no-commit",
        action="store_true",
        help="Do not automatically commit managed workspace updates",
    )
    mode_grp = p_upg.add_mutually_exclusive_group()
    mode_grp.add_argument("--plugin-only", action="store_true", help="Switch workspace to global plugin mode")
    mode_grp.add_argument("--embed-agent", action="store_true", help="Sync AGENTS.md and .agents/ into workspace")
    p_upg.set_defaults(func=_cmd_upgrade_workspace)

    # completion
    p_comp = sub.add_parser(
        "completion",
        help="Print shell tab-completion script (eval \"$(genealogy completion bash)\")",
    )
    p_comp.add_argument("shell", choices=["bash", "zsh", "fish"], help="Target shell")
    p_comp.set_defaults(func=_cmd_completion)

    # validate
    p_val = sub.add_parser("validate", help="Validate data/ schema, links, biology, hashes, and GPS rules")
    p_val.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_val.add_argument("--strict", action="store_true", help="Fail with exit code 1 on warnings too")
    p_val.add_argument(
        "--skip-artifacts",
        action="store_true",
        help="Skip local artifact existence & hash checks (useful in CI when scans are in LFS or external storage)",
    )
    p_val.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_val.set_defaults(func=_cmd_validate)

    # intake
    p_in = sub.add_parser("intake", help="Audit artifacts/, compute SHA-256 hashes, and scaffold missing sources")
    p_in.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_in.add_argument("--scaffold", action="store_true", help="Create stub data/sources/ files for unlinked artifacts")
    p_in.add_argument("--url", default=None, help="Optional Ancestry/FamilySearch/archival URL to attach when scaffolding")
    p_in.add_argument("--link-note", default=None, help="Optional note for the --url provenance link")
    p_in.add_argument("--dry-run", action="store_true", help="Preview scaffolded files without writing")
    p_in.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_in.set_defaults(func=_cmd_intake)

    # grab-screenshot
    p_grab = sub.add_parser(
        "grab-screenshot",
        help="Find the latest OS screenshot and copy it into artifacts/",
    )
    p_grab.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_grab.add_argument(
        "--from",
        dest="source",
        default=None,
        help="Optional screenshot file or directory (auto-detects OS screenshot folders if omitted)",
    )
    p_grab.add_argument(
        "--name",
        default=None,
        help="Optional destination filename inside artifacts/ (preserves extension if omitted)",
    )
    p_grab.add_argument("--dry-run", action="store_true", help="Preview copy without writing")
    p_grab.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_grab.set_defaults(func=_cmd_grab_screenshot)

    # rename-artifact
    p_ren = sub.add_parser(
        "rename-artifact",
        help="Safely rename an artifact inside artifacts/ and update any linked sources",
    )
    p_ren.add_argument(
        "old_path",
        help="Current artifact path or filename (e.g., artifacts/Screenshot_2026.png)",
    )
    p_ren.add_argument(
        "new_name",
        help="New filename or relative path inside artifacts/ (e.g., 1920_census_west.png)",
    )
    p_ren.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_ren.add_argument("--dry-run", action="store_true", help="Preview rename without modifying files")
    p_ren.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_ren.set_defaults(func=_cmd_rename_artifact)

    # add-link
    p_lnk = sub.add_parser("add-link", help="Auto-parse and attach an online provenance URL (Ancestry, FamilySearch, etc.) to a source")
    p_lnk.add_argument("source_id", help="Source ID (e.g., S-1910-CENSUS-WEST-COCKE-TN)")
    p_lnk.add_argument("url", help="Online record/image URL")
    p_lnk.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_lnk.add_argument("--note", default=None, help="Override auto-extracted link note")
    p_lnk.add_argument("--accessed", default=None, help="Access date YYYY-MM-DD (default: today)")
    auth_grp = p_lnk.add_mutually_exclusive_group()
    auth_grp.add_argument("--auth", action="store_true", help="Force requires_auth: true")
    auth_grp.add_argument("--public", action="store_true", help="Force requires_auth: false")
    p_lnk.set_defaults(func=_cmd_add_link)

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
    p_imp.add_argument("--verbose", action="store_true", help="Print every date-parsing warning instead of summarizing")
    p_imp.set_defaults(func=_cmd_import_gedcom)

    # export-gedcom
    p_exp = sub.add_parser("export-gedcom", help="Export data/ to GEDCOM 7.0 (default) or 5.5.1")
    p_exp.add_argument("out", help="Output .ged file path")
    p_exp.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_exp.add_argument("--version", choices=["7.0", "5.5.1"], default="7.0", help="GEDCOM version (default: 7.0)")
    p_exp.add_argument("--include-living", action="store_true", help="Include unredacted living individuals (default: redacted)")
    p_exp.set_defaults(func=_cmd_export_gedcom)

    # kinship
    p_kin = sub.add_parser("kinship", help="Trace all blood paths, coancestry, and Wright's r between two individuals")
    p_kin.add_argument("person_a", help="First individual ID")
    p_kin.add_argument("person_b", help="Second individual ID")
    p_kin.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_kin.add_argument("--max-depth", type=int, default=10, help="Max upward generations to search (default: 10)")
    p_kin.add_argument("--json", action="store_true", help="Output JSON")
    p_kin.set_defaults(func=_cmd_kinship)

    # constraints
    p_con = sub.add_parser("constraints", help="Evaluate relationship constraints (e.g., marriage dispensations) and apply deductive reasoning")
    p_con.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_con.add_argument("--person", default=None, help="Filter constraints involving this individual ID")
    p_con.add_argument(
        "--status",
        choices=["satisfied", "narrowed", "open", "violated", "not_applicable"],
        default=None,
        help="Filter by evaluation status",
    )
    p_con.add_argument("--json", action="store_true", help="Output JSON")
    p_con.set_defaults(func=_cmd_constraints)

    # collapse
    p_col = sub.add_parser("collapse", help="Analyze pedigree collapse, multi-path ancestors, and Wright's F")
    p_col.add_argument("individual_id", help="Individual ID")
    p_col.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_col.add_argument("--max-gen", type=int, default=8, help="Max ancestor generations (default: 8)")
    p_col.add_argument("--json", action="store_true", help="Output JSON")
    p_col.set_defaults(func=_cmd_collapse)

    # gaps
    p_gaps = sub.add_parser("gaps", help="Report region-aware census/parish/civil gaps, promotion-ready profiles, discrepancies, and excluded leads")
    p_gaps.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_gaps.add_argument(
        "--regions",
        default=None,
        help="Override region profiles (comma-separated: auto, mexico, us, uk, canada, general)",
    )
    p_gaps.add_argument("--json", action="store_true", help="Output JSON")
    p_gaps.set_defaults(func=_cmd_gaps)

    # report
    p_rep = sub.add_parser("report", help="Generate a shareable Markdown Family Group Sheet & Research Dossier")
    p_rep.add_argument("individual_id", help="Individual ID")
    p_rep.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_rep.add_argument("--out", default=None, help="Optional output Markdown file path")
    p_rep.add_argument("--include-living", action="store_true", help="Include unredacted living details (default: redacted)")
    p_rep.set_defaults(func=_cmd_report)

    # tracts
    p_tracts = sub.add_parser("tracts", help="Display chronological chain-of-title tables grouped by land tract")
    p_tracts.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_tracts.add_argument("--tract", default=None, help="Optional substring filter for a specific tract")
    p_tracts.add_argument("--json", action="store_true", help="Output JSON")
    p_tracts.set_defaults(func=_cmd_tracts)

    # dna
    p_dna = sub.add_parser("dna", help="Endogamy-aware autosomal DNA expected shared cM calculator across all blood paths")
    p_dna.add_argument("person_a", help="First individual ID")
    p_dna.add_argument("person_b", help="Second individual ID")
    p_dna.add_argument("--observed-cm", type=float, default=None, help="Observed shared centiMorgans to evaluate against the pedigree")
    p_dna.add_argument("--max-depth", type=int, default=10, help="Max upward generations (default: 10)")
    p_dna.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_dna.add_argument("--json", action="store_true", help="Output JSON")
    p_dna.set_defaults(func=_cmd_dna)

    # view
    p_view = sub.add_parser("view", help="Launch local interactive DAG tree viewer or export standalone HTML")
    p_view.add_argument("--root", default=".", help="Workspace root directory (default: .)")
    p_view.add_argument("--host", default="127.0.0.1", help="Bind host for live server (default: 127.0.0.1)")
    p_view.add_argument("--port", type=int, default=8765, help="Port for live server (default: 8765)")
    p_view.add_argument("--no-open", action="store_true", help="Do not automatically open browser")
    p_view.add_argument("--out", default=None, help="Export a single self-contained HTML file instead of starting server")
    p_view.add_argument("--include-living", action="store_true", help="Show unredacted living individuals (default: redacted)")
    p_view.set_defaults(func=_cmd_view)

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
