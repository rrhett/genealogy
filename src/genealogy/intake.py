"""Artifact intake scanner, SHA-256 manifest, duplicate detector, and source stub scaffolder."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from genealogy.loader import load_dataset
from genealogy.schema import render_frontmatter
from genealogy.urls import parse_provenance_url
from genealogy.validator import compute_sha256


@dataclass
class ArtifactRecord:
    rel_path: str
    size_bytes: int
    sha256: str
    linked_sources: list[str] = field(default_factory=list)
    hash_mismatches: list[str] = field(default_factory=list)
    excluded_matches: list[dict[str, str]] = field(default_factory=list)


@dataclass
class IntakeReport:
    root: Path
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    excluded_duplicates: list[dict[str, str]] = field(default_factory=list)

    @property
    def unlinked(self) -> list[ArtifactRecord]:
        return [a for a in self.artifacts if not a.linked_sources]

    @property
    def linked(self) -> list[ArtifactRecord]:
        return [a for a in self.artifacts if a.linked_sources]


def _slugify_artifact_to_source_id(rel_path: str) -> str:
    """Propose a deterministic S-<YEAR|UNDATED>-<TYPE>-<SLUG> ID from an artifact filename."""
    stem = Path(rel_path).stem
    year_match = re.search(r"(1[6-9]\d{2}|20\d{2})", stem)
    year = year_match.group(1) if year_match else "UNDATED"

    lower = stem.lower()
    type_keywords = [
        ("census", "CENSUS"),
        ("draft", "MILITARY"),
        ("wwi", "MILITARY"),
        ("wwii", "MILITARY"),
        ("military", "MILITARY"),
        ("marriage", "MARRIAGE"),
        ("birth", "BIRTH"),
        ("death", "DEATH"),
        ("obit", "NEWSPAPER"),
        ("news", "NEWSPAPER"),
        ("cemetery", "CEMETERY"),
        ("grave", "CEMETERY"),
        ("will", "PROBATE"),
        ("probate", "PROBATE"),
        ("deed", "LAND"),
        ("land", "LAND"),
        ("baptism", "CHURCH"),
        ("church", "CHURCH"),
    ]
    rec_type = "RECORD"
    for kw, label in type_keywords:
        if kw in lower:
            rec_type = label
            break

    # Clean slug tokens
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").upper()
    tokens = [t for t in cleaned.split("-") if t and t != year and t != rec_type]
    slug = "-".join(tokens[:4]) if tokens else "UNTITLED"
    return f"S-{year}-{rec_type}-{slug}"


def _guess_record_type(source_id: str) -> str:
    parts = source_id.split("-")
    tag = parts[2] if len(parts) > 2 else ""
    mapping = {
        "CENSUS": "census",
        "MILITARY": "military",
        "MARRIAGE": "vital",
        "BIRTH": "vital",
        "DEATH": "vital",
        "NEWSPAPER": "newspaper",
        "CEMETERY": "cemetery",
        "PROBATE": "probate",
        "LAND": "land",
        "CHURCH": "church",
    }
    return mapping.get(tag, "other")


def scan_artifacts(root: Path | str) -> IntakeReport:
    """Scan `<root>/artifacts/`, compute SHA-256 digests, and cross-reference `data/sources/`."""
    ds = load_dataset(root)
    report = IntakeReport(root=ds.root)

    # Index sources by normalized artifact path and excluded sources by SHA-256
    sources_by_artifact: dict[Path, list[tuple[str, str | None]]] = defaultdict(list)
    excluded_by_sha: dict[str, list[dict[str, str]]] = defaultdict(list)
    for sid, src in ds.sources.items():
        if src.data.artifact_path:
            art_p = ds.resolve_artifact_path(src.data.artifact_path)
            sources_by_artifact[art_p].append((sid, src.data.artifact_sha256))
        if src.data.relevance == "excluded" and src.data.artifact_sha256:
            excluded_by_sha[src.data.artifact_sha256].append(
                {
                    "source_id": sid,
                    "excluded_for": ", ".join(src.data.excluded_for),
                    "reason": src.data.exclusion_reason or "",
                }
            )

    by_hash: dict[str, list[str]] = defaultdict(list)

    if ds.artifacts_dir.exists():
        for file_path in sorted(ds.artifacts_dir.rglob("*")):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            rel = ds.logical_artifact_path(file_path)
            sha = compute_sha256(file_path)
            by_hash[sha].append(rel)

            linked: list[str] = []
            mismatches: list[str] = []
            for sid, expected_sha in sources_by_artifact.get(file_path.resolve(), []):
                linked.append(sid)
                if expected_sha and expected_sha != sha:
                    mismatches.append(sid)

            ex_matches = [
                m for m in excluded_by_sha.get(sha, []) if m["source_id"] not in linked
            ]
            for m in ex_matches:
                report.excluded_duplicates.append(
                    {
                        "artifact": rel,
                        "source_id": m["source_id"],
                        "excluded_for": m["excluded_for"],
                        "reason": m["reason"],
                    }
                )

            report.artifacts.append(
                ArtifactRecord(
                    rel_path=rel,
                    size_bytes=file_path.stat().st_size,
                    sha256=sha,
                    linked_sources=linked,
                    hash_mismatches=mismatches,
                    excluded_matches=ex_matches,
                )
            )

    report.duplicates = {h: paths for h, paths in by_hash.items() if len(paths) > 1}
    return report


def scaffold_unlinked_sources(
    root: Path | str,
    *,
    dry_run: bool = False,
    url: str | None = None,
    link_note: str | None = None,
) -> list[tuple[str, Path]]:
    """Create stub `data/sources/<ID>.md` files for all unlinked artifacts.

    Never modifies `artifacts/`. Returns list of (source_id, path) created (or planned).
    If `url` is supplied, attaches the parsed provenance link to each newly scaffolded stub.
    """
    report = scan_artifacts(root)
    src_dir = report.root / "data" / "sources"
    if not dry_run:
        src_dir.mkdir(parents=True, exist_ok=True)

    parsed_links: list[dict[str, object]] = []
    if url:
        lnk = parse_provenance_url(url, note=link_note)
        entry: dict[str, object] = {
            "provider": lnk.provider,
            "url": lnk.url,
            "accessed": lnk.accessed,
            "requires_auth": lnk.requires_auth,
        }
        if lnk.note:
            entry["note"] = lnk.note
        parsed_links.append(entry)

    created: list[tuple[str, Path]] = []
    existing_ids = {p.stem for p in src_dir.glob("*.md")} if src_dir.exists() else set()

    for art in report.unlinked:
        base_id = _slugify_artifact_to_source_id(art.rel_path)
        sid = base_id
        counter = 1
        while sid in existing_ids:
            counter += 1
            sid = f"{base_id}-{counter:02d}"
        existing_ids.add(sid)

        dest = src_dir / f"{sid}.md"
        year_part = sid.split("-")[1]
        meta = {
            "id": sid,
            "title": f"TODO: Title for {Path(art.rel_path).name}",
            "record_type": _guess_record_type(sid),
            "date": year_part if year_part.isdigit() else "undated",
            "jurisdiction": "TODO: County, State, Country",
            "repository": "",
            "links": list(parsed_links),
            "artifact_path": art.rel_path,
            "artifact_sha256": art.sha256,
            "source_type": "original",
            "information_type": "undetermined",
            "relevance": "pending",
            "individuals_cited": [],
        }
        body = (
            "## Formal Citation\n"
            "TODO: Add Evidence Explained citation.\n\n"
            "## Verbatim Transcription & Extracted Data\n"
            "TODO: Transcribe verbatim using the `artifact-transcription` skill.\n"
        )
        if not dry_run:
            dest.write_text(render_frontmatter(meta, body), encoding="utf-8")
        created.append((sid, dest))

    return created
