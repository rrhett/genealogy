"""Artifact intake scanner, SHA-256 manifest, duplicate detector, and source stub scaffolder."""

from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from genealogy.loader import Dataset, load_dataset
from genealogy.schema import read_markdown_frontmatter, render_frontmatter
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


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_SCREENSHOT_NAME_RE = re.compile(
    r"(?:screenshot|screen[\s_-]?shot|snip|capture|captura|bildschirmfoto|spectacle|flameshot)",
    re.IGNORECASE,
)


@dataclass
class GrabScreenshotResult:
    source_path: Path
    dest_path: Path
    rel_path: str
    sha256: str
    already_exists: bool = False
    existing_rel_path: str | None = None
    excluded_matches: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RenameArtifactResult:
    old_rel_path: str
    new_rel_path: str
    old_abs_path: Path
    new_abs_path: Path
    sha256: str
    updated_sources: list[str] = field(default_factory=list)


def _screenshot_search_dirs(
    root: Path,
    config: dict[str, Any],
    explicit_from: Path | str | None = None,
) -> list[tuple[Path, bool]]:
    """Return `(directory, require_screenshot_name)` pairs to search for screenshots."""
    if explicit_from is not None:
        p = Path(explicit_from).expanduser()
        resolved = p.resolve() if p.is_absolute() else (root / p).resolve()
        return [(resolved, False)]

    env_dir = os.environ.get("GENEALOGY_SCREENSHOTS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        return [(p.resolve() if p.is_absolute() else (root / p).resolve(), False)]

    cfg_dir = config.get("screenshots_dir")
    if isinstance(cfg_dir, str) and cfg_dir.strip():
        p = Path(cfg_dir.strip()).expanduser()
        return [(p.resolve() if p.is_absolute() else (root / p).resolve(), False)]

    home = Path.home()
    candidates: list[tuple[Path, bool]] = []
    xdg_pics = os.environ.get("XDG_PICTURES_DIR")
    if xdg_pics:
        xdg_path = Path(xdg_pics).expanduser()
        candidates.append((xdg_path / "Screenshots", False))
    candidates.extend(
        [
            (home / "Pictures" / "Screenshots", False),
            (home / "OneDrive" / "Pictures" / "Screenshots", False),
            (home / "Desktop", True),
            (Path(xdg_pics).expanduser() if xdg_pics else home / "Pictures", True),
        ]
    )
    return candidates


def find_latest_screenshot(
    root: Path | str,
    config: dict[str, Any] | None = None,
    *,
    explicit_from: Path | str | None = None,
) -> Path:
    """Locate the most recently modified screenshot file from OS or configured folders."""
    root_path = Path(root).resolve()
    cfg = config if config is not None else {}

    if explicit_from is not None:
        p = Path(explicit_from).expanduser()
        resolved = p.resolve() if p.is_absolute() else (root_path / p).resolve()
        if resolved.is_file():
            return resolved
        if not resolved.exists():
            raise FileNotFoundError(f"Screenshot source path does not exist: {resolved}")

    search_dirs = _screenshot_search_dirs(root_path, cfg, explicit_from=explicit_from)
    best_file: Path | None = None
    best_mtime: float = -1.0
    searched: list[str] = []

    for directory, require_name in search_dirs:
        if not directory.is_dir():
            continue
        searched.append(str(directory))
        for entry in directory.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            if require_name and not _SCREENSHOT_NAME_RE.search(entry.stem):
                continue
            mtime = entry.stat().st_mtime
            if mtime > best_mtime or (
                mtime == best_mtime and best_file is not None and str(entry) < str(best_file)
            ):
                best_mtime = mtime
                best_file = entry.resolve()

    if best_file is None:
        loc_desc = ", ".join(searched) if searched else "default OS screenshot directories"
        raise FileNotFoundError(f"No screenshot image found in: {loc_desc}")
    return best_file


def _normalize_relative_artifact_name(raw_name: str) -> str:
    norm = raw_name.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    if norm.startswith("artifacts/"):
        norm = norm[len("artifacts/") :]
    if not norm or norm.startswith("/") or ".." in Path(norm).parts:
        raise ValueError(f"Invalid artifact filename: '{raw_name}'")
    return norm


def _ensure_within_artifacts_dir(ds: Dataset, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(ds.artifacts_dir)
    except ValueError as exc:
        raise ValueError(
            f"Target path '{candidate}' escapes artifacts directory '{ds.artifacts_dir}'."
        ) from exc
    return resolved


def grab_screenshot(
    root: Path | str,
    *,
    source: Path | str | None = None,
    name: str | None = None,
    dry_run: bool = False,
) -> GrabScreenshotResult:
    """Find the latest screenshot and copy it into `artifacts/`."""
    ds = load_dataset(root)
    src_file = find_latest_screenshot(ds.root, ds.config, explicit_from=source)

    rel_target: str | None = None
    if name is not None:
        rel_target = _normalize_relative_artifact_name(name)
        if not Path(rel_target).suffix:
            rel_target = f"{rel_target}{src_file.suffix}"
        _ensure_within_artifacts_dir(ds, ds.artifacts_dir / rel_target)

    sha = compute_sha256(src_file)

    excluded_matches = [
        {
            "source_id": sid,
            "excluded_for": ", ".join(src.data.excluded_for),
            "reason": src.data.exclusion_reason or "",
        }
        for sid, src in ds.sources.items()
        if src.data.relevance == "excluded" and src.data.artifact_sha256 == sha
    ]

    if ds.artifacts_dir.exists():
        for existing in sorted(ds.artifacts_dir.rglob("*")):
            if existing.is_file() and not existing.name.startswith("."):
                if compute_sha256(existing) == sha:
                    rel = ds.logical_artifact_path(existing)
                    return GrabScreenshotResult(
                        source_path=src_file,
                        dest_path=existing.resolve(),
                        rel_path=rel,
                        sha256=sha,
                        already_exists=True,
                        existing_rel_path=rel,
                        excluded_matches=excluded_matches,
                    )

    if rel_target is not None:
        dest = _ensure_within_artifacts_dir(ds, ds.artifacts_dir / rel_target)
        if dest.exists():
            raise FileExistsError(
                f"Destination artifact already exists: {ds.logical_artifact_path(dest)}"
            )
    else:
        dest = _ensure_within_artifacts_dir(ds, ds.artifacts_dir / src_file.name)
        if dest.exists():
            stem = src_file.stem
            suffix = src_file.suffix
            counter = 2
            while dest.exists():
                dest = _ensure_within_artifacts_dir(
                    ds, ds.artifacts_dir / f"{stem}-{counter}{suffix}"
                )
                counter += 1

    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)

    return GrabScreenshotResult(
        source_path=src_file,
        dest_path=dest,
        rel_path=ds.logical_artifact_path(dest),
        sha256=sha,
        already_exists=False,
        excluded_matches=excluded_matches,
    )


def rename_artifact(
    root: Path | str,
    old_path: str,
    new_name: str,
    *,
    dry_run: bool = False,
) -> RenameArtifactResult:
    """Safely rename an artifact inside `artifacts/` and update any linked sources."""
    ds = load_dataset(root)

    norm_old = old_path.strip().replace("\\", "/")
    while norm_old.startswith("./"):
        norm_old = norm_old[2:]
    if norm_old.startswith("artifacts/"):
        old_abs = ds.resolve_artifact_path(norm_old)
    else:
        p = Path(old_path)
        old_abs = p.resolve() if p.is_absolute() else (ds.artifacts_dir / norm_old).resolve()

    old_abs = _ensure_within_artifacts_dir(ds, old_abs)
    if not old_abs.is_file():
        raise FileNotFoundError(f"Artifact not found: {old_path}")

    norm_new = _normalize_relative_artifact_name(new_name)
    if not Path(norm_new).suffix:
        norm_new = f"{norm_new}{old_abs.suffix}"

    if "/" in norm_new:
        new_abs = _ensure_within_artifacts_dir(ds, ds.artifacts_dir / norm_new)
    else:
        new_abs = _ensure_within_artifacts_dir(ds, old_abs.parent / norm_new)

    old_rel = ds.logical_artifact_path(old_abs)
    new_rel = ds.logical_artifact_path(new_abs)

    if new_abs == old_abs:
        return RenameArtifactResult(
            old_rel_path=old_rel,
            new_rel_path=new_rel,
            old_abs_path=old_abs,
            new_abs_path=new_abs,
            sha256=compute_sha256(old_abs),
        )

    if new_abs.exists():
        raise FileExistsError(f"Target artifact already exists: {new_rel}")

    sha = compute_sha256(old_abs)
    linked_sources = [
        (sid, src)
        for sid, src in sorted(ds.sources.items())
        if src.data.artifact_path and ds.resolve_artifact_path(src.data.artifact_path) == old_abs
    ]

    if not dry_run:
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        old_abs.rename(new_abs)
        for _sid, src in linked_sources:
            meta, body = read_markdown_frontmatter(src.path)
            meta["artifact_path"] = new_rel
            src.path.write_text(render_frontmatter(meta, body), encoding="utf-8")

    return RenameArtifactResult(
        old_rel_path=old_rel,
        new_rel_path=new_rel,
        old_abs_path=old_abs,
        new_abs_path=new_abs,
        sha256=sha,
        updated_sources=[sid for sid, _src in linked_sources],
    )
