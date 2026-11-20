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
genealogy intake                 # Check new scans in artifacts/ & compute SHA-256 hashes
genealogy validate               # Validate schema, links, biology, hashes & GPS rules
genealogy stats                  # Dataset summary
genealogy gaps                   # Missing U.S. Censuses (1790-1950), discrepancies & leads
genealogy view                   # Launch interactive DAG & pedigree-collapse tree viewer
genealogy report <ID>            # Generate a printable Family Group Sheet
genealogy export-gedcom build/tree.ged  # Export GEDCOM 7.0 (redacts living people by default)
```

## Pulling Future Toolkit Updates
Your family data lives completely separate from the Python toolkit. Whenever the toolkit adds new skills, hooks, or CLI features:

```bash
uv tool upgrade genealogy-workspace
genealogy upgrade-workspace
```
This refreshes `AGENTS.md` and `.agents/` without ever touching `data/`, `artifacts/`, `notes/`, `workspace/`, or `AGENTS.local.md`.
"""


def _render_user_ci_workflow(ci_repo: str) -> str:
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
        # Replace the URL below with your published genealogy toolkit repository URL
        run: uvx --from "{ci_repo}" genealogy validate
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
        _render_user_ci_workflow(ci_repo or DEFAULT_TOOLKIT_REPO_URL),
    )
    if use_lfs:
        _write_if_missing(".gitattributes", _USER_GITATTRIBUTES_LFS)

    cfg = load_workspace_config(root)
    agent_mode = "plugin" if plugin_only else "embedded"
    cfg["toolkit_version"] = __version__
    cfg["agent_mode"] = agent_mode
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


def upgrade_workspace(
    root: Path | str,
    *,
    dry_run: bool = False,
    plugin_only: bool | None = None,
) -> SyncReport:
    """Upgrade managed AGENTS.md, skills, and hooks in an existing user research repo.

    Never modifies data/, artifacts/, notes/, workspace/, README.md, or AGENTS.local.md.
    """
    root_path = Path(root).expanduser().resolve()
    cfg = load_workspace_config(root_path)

    if plugin_only is None:
        effective_plugin_only = cfg.get("agent_mode") == "plugin"
    else:
        effective_plugin_only = plugin_only

    agent_mode = "plugin" if effective_plugin_only else "embedded"
    added, updated, removed, unchanged = _sync_agent_assets(
        root_path, dry_run=dry_run, plugin_only=effective_plugin_only
    )

    if not dry_run:
        cfg["toolkit_version"] = __version__
        cfg["agent_mode"] = agent_mode
        _write_config(root_path, cfg)

    return SyncReport(
        root=root_path,
        toolkit_version=__version__,
        agent_mode=agent_mode,
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
    )
