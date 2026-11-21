"""Workspace bootstrapping (`genealogy init`) and conflict-free toolkit upgrades (`genealogy upgrade-workspace`)."""

from __future__ import annotations

import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from genealogy import __version__
from genealogy.loader import load_workspace_config

DEFAULT_TOOLKIT_REPO_URL = "git+https://github.com/rrhett/genealogy.git"

_USER_GITIGNORE = """\
# Generated build artifacts (rebuilt from data/)
build/
*.sqlite
*.sqlite-journal
*.sqlite-wal
*.sqlite-shm

# Temporary GEDCOM staging
workspace/staging/

# OS & editor files
.DS_Store
Thumbs.db
*.swp
.env
"""

_USER_GITATTRIBUTES_LFS = """\
# Git LFS tracking for large primary document scans in artifacts/
artifacts/**/*.pdf filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.png filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.jpg filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.jpeg filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.tif filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.tiff filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.webp filter=lfs diff=lfs merge=lfs -text
artifacts/**/*.heic filter=lfs diff=lfs merge=lfs -text
"""

_USER_PRE_COMMIT = """\
# Optional git pre-commit hook: run `pre-commit install` to enable.
repos:
  - repo: local
    hooks:
      - id: genealogy-validate
        name: Validate genealogy dataset (data/)
        entry: genealogy validate
        language: system
        pass_filenames: false
        files: ^(data/|artifacts/)
"""

_USER_AGENTS_LOCAL = """\
# Local Family-Specific Research Rules & Context
#
# This file is owned by YOU and is NEVER overwritten by `genealogy upgrade-workspace`.
# Add surname spelling conventions, geographic focus, privacy guidelines, or
# active brick walls you want your AI assistant to keep in mind.

## Primary Research Scope
- Focus surnames: (add your surnames here)
- Primary regions / eras: (add counties, states, or countries here)

## Privacy & Sharing Notes
- Keep all living relatives marked `living: true` in `data/individuals/`.
"""

_USER_README = """\
# Family History Research Workspace

An evidence-based genealogy repository managed with the `genealogy` CLI toolkit.

## Layout
- `artifacts/` — **READ-ONLY** raw scans (PDF, PNG, JPG, transcriptions)
- `notes/` — Your personal research notes, intuition, and family lore
- `workspace/` — AI agent scratchpad (`HYPOTHESES.md`, `NEGATIVE_SEARCHES.md`, `TODO.md`, GEDCOM `staging/`)
- `data/` — Canonical structured Markdown/YAML truth (`individuals/`, `sources/`, `relationships.yaml`)
- `AGENTS.local.md` — Your family-specific overrides (never overwritten on toolkit upgrade)

## Common Commands
```bash
genealogy intake                        # Check new scans in artifacts/ & compute SHA-256 hashes
genealogy add-link <SOURCE_ID> "<URL>"  # Attach Ancestry/FamilySearch/archival URL to a source
genealogy validate                      # Validate schema, links, biology, hashes & GPS rules
genealogy stats                         # Dataset summary
genealogy gaps                          # Region-aware missing censuses, parish/civil records & leads
genealogy view                          # Launch interactive DAG & pedigree-collapse tree viewer
genealogy kinship <ID_A> <ID_B>         # Trace all blood paths & Wright's r
genealogy collapse <ID>                 # Inspect pedigree collapse & multi-path ancestors
genealogy tracts                        # Chronological chain-of-title per land parcel
genealogy dna <ID_A> <ID_B> --observed-cm <cM>  # Endogamy-aware expected vs. observed shared DNA
genealogy report <ID>                   # Generate a printable Family Group Sheet
genealogy export-gedcom build/tree.ged  # Export GEDCOM 7.0 (redacts living people by default)
```

## Everyday Slash Commands (`/` in Chat)
- `/status` — Session-start briefing (`AGENTS.local.md`, `workspace/`, `genealogy stats`, `genealogy gaps`).
- `/ingest` — Drop scans into `artifacts/` (or notes into `notes/`), then run `/ingest` to run `genealogy intake`, attach URLs, transcribe verbatim, update `data/`, and validate.
- `/exclude` — Mark a mistaken lead `relevance: excluded` without deleting the scan from `artifacts/`.
- `/wrap-up` — End-of-session validation, `workspace/` scratchpad updates, and commit summary.

## Pulling Future Toolkit Updates
Your family data lives completely separate from the Python toolkit. Whenever the toolkit adds new skills, hooks, or CLI features:

```bash
uv tool upgrade genealogy-workspace
genealogy upgrade-workspace
```
This refreshes `AGENTS.md` and `.agents/` without ever touching `data/`, `artifacts/`, `notes/`, `workspace/`, or `AGENTS.local.md`.
"""


def _render_user_ci_workflow(ci_repo: str, *, skip_artifacts: bool = False) -> str:
    extra_flag = " --skip-artifacts" if skip_artifacts else ""
    note = (
        "        # --skip-artifacts avoids burning Git LFS bandwidth or failing when scans live in an external folder\n"
        if skip_artifacts
        else ""
    )
    return f"""\
name: Validate Family History Data

on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Validate data/
{note}        run: uvx --from "{ci_repo}" genealogy validate{extra_flag}
"""


def locate_plugin_bundle() -> Path:
    """Locate the bundled `plugin/` directory (works in installed wheels and dev checkouts)."""
    bundled = Path(__file__).resolve().parent / "_bundled_plugin"
    if (bundled / "plugin.json").exists():
        return bundled
    dev_plugin = Path(__file__).resolve().parents[2] / "plugin"
    if (dev_plugin / "plugin.json").exists():
        return dev_plugin
    raise FileNotFoundError(
        "Could not locate bundled genealogy plugin assets (_bundled_plugin or plugin/)."
    )


def _iter_managed_plugin_files(plugin_dir: Path) -> dict[str, Path]:
    """Map workspace-relative destination path -> source path inside plugin_dir."""
    mapping: dict[str, Path] = {}
    agents_md = plugin_dir / "rules" / "AGENTS.md"
    if agents_md.exists():
        mapping["AGENTS.md"] = agents_md

    hooks_json = plugin_dir / "hooks.json"
    if hooks_json.exists():
        mapping[".agents/hooks.json"] = hooks_json

    for sub in ("hooks", "skills"):
        sub_dir = plugin_dir / sub
        if not sub_dir.exists():
            continue
        for src in sorted(sub_dir.rglob("*")):
            if src.is_file() and not src.name.startswith("."):
                rel = src.relative_to(plugin_dir).as_posix()
                mapping[f".agents/{rel}"] = src
    return mapping


@dataclass
class SyncReport:
    root: Path
    toolkit_version: str
    agent_mode: str
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    git_initialized: bool = False
    config_updated: bool = False
    committed: bool = False
    commit_sha: str | None = None
    previous_version: str | None = None
    suggested_prompts: list[dict[str, str]] = field(default_factory=list)


# Versioned prompts surfaced by `genealogy upgrade-workspace` when crossing a version
# boundary that introduces new schema/data conventions.
# Add unreleased prompts to PENDING_UPGRADE_PROMPTS; scripts/release.sh automatically
# promotes them into UPGRADE_DATA_PROMPTS under the released version tuple.
# --- BEGIN UPGRADE PROMPTS (managed by scripts/release.sh) ---
PENDING_UPGRADE_PROMPTS: list[tuple[str, str]] = [
    (
        "Migrate marriage & marriage-investigation events",
        (
            "Audit data/individuals/ and data/relationships.yaml for two legacy patterns: "
            "(1) marriage details stored under type: residence or type: other events (check "
            "suggested_label or descriptions), and (2) pre-marital proceedings (Informaciones "
            "Matrimoniales, Presentaciones, Dispensas, banns, or marriage bonds) stored as "
            "type: marriage events. Move pre-marital proceedings to type: marriage_investigation, "
            "reserve type: marriage in events: strictly for actual ceremonies or civil actas, "
            "and ensure every couple remains linked in data/relationships.yaml (using "
            'date: "aft <investigation_date>" and confidence: probable where the ceremony record '
            "is still missing). Finally, run genealogy validate and genealogy gaps to verify."
        ),
    ),
]

UPGRADE_DATA_PROMPTS: list[tuple[tuple[int, ...], str, str]] = []
# --- END UPGRADE PROMPTS ---


def promote_pending_upgrade_prompts(workspace_py_path: Path, new_version: str) -> int:
    """Promote PENDING_UPGRADE_PROMPTS in `workspace_py_path` into UPGRADE_DATA_PROMPTS for `new_version`."""
    import ast

    text = workspace_py_path.read_text(encoding="utf-8")
    begin_marker = "# --- BEGIN UPGRADE PROMPTS (managed by scripts/release.sh) ---"
    end_marker = "# --- END UPGRADE PROMPTS ---"
    start_idx = text.index(begin_marker)
    end_idx = text.index(end_marker) + len(end_marker)
    block = text[start_idx:end_idx]

    ns: dict[str, Any] = {}
    exec(compile(ast.parse(block), str(workspace_py_path), "exec"), ns)
    pending: list[tuple[str, str]] = list(ns.get("PENDING_UPGRADE_PROMPTS", []))
    released: list[tuple[tuple[int, ...], str, str]] = list(ns.get("UPGRADE_DATA_PROMPTS", []))
    if not pending:
        return 0

    ver_tuple = _parse_version_tuple(new_version)
    for title, prompt in pending:
        released.append((ver_tuple, title, prompt))

    lines = [
        begin_marker,
        "PENDING_UPGRADE_PROMPTS: list[tuple[str, str]] = []",
        "",
        "UPGRADE_DATA_PROMPTS: list[tuple[tuple[int, ...], str, str]] = [",
    ]
    for vt, title, prompt in released:
        lines.append("    (")
        lines.append(f"        {vt!r},")
        lines.append(f"        {title!r},")
        lines.append(f"        {prompt!r},")
        lines.append("    ),")
    lines.append("]")
    lines.append(end_marker)

    new_text = text[:start_idx] + "\n".join(lines) + text[end_idx:]
    workspace_py_path.write_text(new_text, encoding="utf-8")
    return len(pending)


def _parse_version_tuple(version_str: str | None) -> tuple[int, ...]:
    """Parse a semantic version string (e.g. '0.8.1') into an integer tuple."""
    if not version_str:
        return (0, 0, 0)
    parts: list[int] = []
    for raw in version_str.strip().lstrip("v").split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def suggested_prompts_for_upgrade(
    previous_version: str | None,
    current_version: str,
) -> list[dict[str, str]]:
    """Return data-migration prompts introduced between `previous_version` and `current_version`."""
    prev_t = _parse_version_tuple(previous_version)
    curr_t = _parse_version_tuple(current_version)
    if prev_t >= curr_t:
        return []
    prompts: list[dict[str, str]] = []
    for ver_t, title, prompt in UPGRADE_DATA_PROMPTS:
        if prev_t < ver_t <= curr_t:
            prompts.append(
                {
                    "version": ".".join(str(x) for x in ver_t),
                    "title": title,
                    "prompt": prompt,
                }
            )
    return prompts


def is_workspace_dir(root: Path | str) -> bool:
    """Return True if `root` looks like an initialized genealogy workspace."""
    root_path = Path(root).expanduser().resolve()
    return (root_path / ".genealogy.yaml").is_file()


def _write_config(root: Path, cfg: dict[str, Any]) -> None:
    header = (
        "# Genealogy workspace configuration.\n"
        "# `toolkit_version` is updated by `genealogy upgrade-workspace`.\n"
        "# Optional: set `artifacts_dir` to store large scans outside Git (e.g., ~/Dropbox/FamilyScans).\n"
    )
    body = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    (root / ".genealogy.yaml").write_text(header + body, encoding="utf-8")


def _sync_agent_assets(
    root: Path,
    *,
    dry_run: bool = False,
    plugin_only: bool = False,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Sync managed AGENTS.md, .agents/skills/, .agents/hooks/ from bundled plugin."""
    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []

    if plugin_only:
        return added, updated, removed, unchanged

    plugin_dir = locate_plugin_bundle()
    desired = _iter_managed_plugin_files(plugin_dir)

    for rel_dest, src_path in sorted(desired.items()):
        dest_path = root / rel_dest
        src_bytes = src_path.read_bytes()
        if not dest_path.exists():
            added.append(rel_dest)
            if not dry_run:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(src_bytes)
                if src_path.stat().st_mode & stat.S_IXUSR:
                    dest_path.chmod(dest_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        elif dest_path.read_bytes() != src_bytes:
            updated.append(rel_dest)
            if not dry_run:
                dest_path.write_bytes(src_bytes)
                if src_path.stat().st_mode & stat.S_IXUSR:
                    dest_path.chmod(dest_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else:
            unchanged.append(rel_dest)

    # Remove stale files inside managed .agents/hooks and .agents/skills subtrees
    for managed_sub in (".agents/hooks", ".agents/skills"):
        sub_root = root / managed_sub
        if not sub_root.exists():
            continue
        for existing in sorted(sub_root.rglob("*")):
            if not existing.is_file():
                continue
            rel = existing.relative_to(root).as_posix()
            if rel not in desired:
                removed.append(rel)
                if not dry_run:
                    existing.unlink()

    return added, updated, removed, unchanged


def init_workspace(
    target_dir: Path | str,
    *,
    git_init: bool = True,
    use_lfs: bool = False,
    artifacts_dir: str | None = None,
    regions: list[str] | None = None,
    plugin_only: bool = False,
    ci_repo: str | None = None,
) -> SyncReport:
    """Bootstrap a clean, private genealogy research repository (zero Python source code)."""
    root = Path(target_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    # Data & working folders (never overwrite existing user files)
    for rel_dir in (
        "artifacts",
        "notes",
        "workspace",
        "data/individuals",
        "data/sources",
    ):
        d = root / rel_dir
        d.mkdir(parents=True, exist_ok=True)
        if not any(d.iterdir()):
            (d / ".gitkeep").write_text("", encoding="utf-8")

    rel_yaml = root / "data" / "relationships.yaml"
    if not rel_yaml.exists():
        rel_yaml.write_text(
            "# Canonical relationship graph edges.\n"
            "# For parent_child: person_1 MUST be the parent, person_2 MUST be the child.\n"
            "relationships: []\n",
            encoding="utf-8",
        )

    def _write_if_missing(rel_path: str, content: str) -> None:
        p = root / rel_path
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    _write_if_missing(".gitignore", _USER_GITIGNORE)
    _write_if_missing(".pre-commit-config.yaml", _USER_PRE_COMMIT)
    _write_if_missing("AGENTS.local.md", _USER_AGENTS_LOCAL)
    _write_if_missing("README.md", _USER_README)
    _write_if_missing(
        ".github/workflows/validate.yml",
        _render_user_ci_workflow(
            ci_repo or DEFAULT_TOOLKIT_REPO_URL,
            skip_artifacts=bool(use_lfs or artifacts_dir),
        ),
    )
    if use_lfs:
        _write_if_missing(".gitattributes", _USER_GITATTRIBUTES_LFS)

    cfg = load_workspace_config(root)
    agent_mode = "plugin" if plugin_only else "embedded"
    cfg["toolkit_version"] = __version__
    cfg["agent_mode"] = agent_mode
    cfg["regions"] = regions if regions else ["auto"]
    if artifacts_dir:
        cfg["artifacts_dir"] = artifacts_dir
    _write_config(root, cfg)

    added, updated, removed, unchanged = _sync_agent_assets(
        root, dry_run=False, plugin_only=plugin_only
    )

    git_initialized = False
    if git_init and not (root / ".git").exists() and shutil.which("git"):
        proc = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            proc = subprocess.run(
                ["git", "init"],
                cwd=str(root),
                capture_output=True,
                text=True,
            )
        git_initialized = proc.returncode == 0

    return SyncReport(
        root=root,
        toolkit_version=__version__,
        agent_mode=agent_mode,
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        git_initialized=git_initialized,
    )


def _is_git_repo(root: Path) -> bool:
    if not shutil.which("git"):
        return False
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def ensure_git_clean(root: Path) -> bool:
    """Verify that `root` (if it is a git repository) has no uncommitted changes.

    Returns True if `root` is a clean git repository, False if `root` is not a git
    repository. Raises RuntimeError if `root` has uncommitted changes or `git status` fails.
    """
    if not _is_git_repo(root):
        return False

    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"Failed to check git status in {root}: {detail}")

    dirty_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if dirty_lines:
        preview = "\n".join(f"  {line}" for line in dirty_lines[:10])
        more = f"\n  ... and {len(dirty_lines) - 10} more" if len(dirty_lines) > 10 else ""
        raise RuntimeError(
            f"Git workspace has uncommitted changes in {root}:\n"
            f"{preview}{more}\n"
            "Commit or stash your changes before running `genealogy upgrade-workspace` "
            "(or pass `--allow-dirty` to proceed anyway)."
        )
    return True


def _commit_workspace_upgrade(root: Path, toolkit_version: str) -> str | None:
    """Stage managed workspace files and commit the upgrade. Returns short SHA or None."""
    targets = [p for p in (".genealogy.yaml", "AGENTS.md", ".agents") if (root / p).exists()]
    if not targets:
        return None
    add_proc = subprocess.run(
        ["git", "add", "--", *targets],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if add_proc.returncode != 0:
        detail = add_proc.stderr.strip() or f"exit code {add_proc.returncode}"
        raise RuntimeError(f"Failed to stage workspace upgrade in {root}: {detail}")

    msg = f"Upgrade genealogy workspace to toolkit v{toolkit_version}"
    commit_proc = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if commit_proc.returncode != 0:
        detail = commit_proc.stderr.strip() or commit_proc.stdout.strip() or f"exit code {commit_proc.returncode}"
        raise RuntimeError(f"Failed to commit workspace upgrade in {root}: {detail}")

    sha_proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return sha_proc.stdout.strip() if sha_proc.returncode == 0 else None


def upgrade_workspace(
    root: Path | str,
    *,
    dry_run: bool = False,
    plugin_only: bool | None = None,
    allow_dirty: bool = False,
    auto_commit: bool = True,
) -> SyncReport:
    """Upgrade managed AGENTS.md, skills, and hooks in an existing user research repo.

    Never modifies data/, artifacts/, notes/, workspace/, README.md, or AGENTS.local.md.
    When `auto_commit=True` (default), automatically commits managed updates if the
    workspace is a clean git repository (skipped for `dry_run`, `allow_dirty`, or non-git dirs).
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"Workspace directory does not exist: {root_path}")

    if not dry_run and not allow_dirty:
        ensure_git_clean(root_path)

    cfg = load_workspace_config(root_path)
    previous_version = str(cfg["toolkit_version"]) if cfg.get("toolkit_version") else None
    prompts = suggested_prompts_for_upgrade(previous_version, __version__)

    if plugin_only is None:
        effective_plugin_only = cfg.get("agent_mode") == "plugin"
    else:
        effective_plugin_only = plugin_only

    agent_mode = "plugin" if effective_plugin_only else "embedded"
    added, updated, removed, unchanged = _sync_agent_assets(
        root_path, dry_run=dry_run, plugin_only=effective_plugin_only
    )

    config_updated = (
        cfg.get("toolkit_version") != __version__
        or cfg.get("agent_mode") != agent_mode
        or not (root_path / ".genealogy.yaml").exists()
    )
    if not dry_run and config_updated:
        cfg["toolkit_version"] = __version__
        cfg["agent_mode"] = agent_mode
        _write_config(root_path, cfg)

    committed = False
    commit_sha: str | None = None
    has_changes = bool(added or updated or removed or config_updated)
    if auto_commit and not dry_run and not allow_dirty and has_changes and _is_git_repo(root_path):
        commit_sha = _commit_workspace_upgrade(root_path, __version__)
        committed = commit_sha is not None

    return SyncReport(
        root=root_path,
        toolkit_version=__version__,
        agent_mode=agent_mode,
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        config_updated=config_updated,
        committed=committed,
        commit_sha=commit_sha,
        previous_version=previous_version,
        suggested_prompts=prompts,
    )
