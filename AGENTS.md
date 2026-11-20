# AGENTS.md — Genealogy Toolkit & Plugin Contributor Guide

This repository is the **genealogy-workspace CLI toolkit and Antigravity plugin**—not a personal family-history data repository. End users install this package via `uv tool install` and bootstrap their own private family-history repositories with `genealogy init <dir>`.

> **Note:** The canonical genealogy researcher `AGENTS.md` (which is deployed into user data repositories and loaded by the Antigravity plugin) lives at `plugin/rules/AGENTS.md`.

## Repository Layout
- `src/genealogy/` — Python package and `genealogy` CLI (`validate`, `intake`, `stats`, `build-db`, `query`, `timeline`, `tree`, `view`, `kinship`, `collapse`, `gaps`, `report`, `import-gedcom`, `export-gedcom`, `search`, `init`, `upgrade-workspace`).
- `plugin/` — Single source of truth for the Antigravity plugin (`plugin.json`, `hooks.json`, `hooks/`, `rules/AGENTS.md`, `skills/`). Bundled into the Python wheel via Hatchling `force-include` so `genealogy init` and `genealogy upgrade-workspace` stay in sync automatically.
- `examples/west-family/` — Worked inosculated (endogamous) example dataset used for demos and integration tests.
- `tests/` — Unit, hook, workspace bootstrap/upgrade, HTTP viewer, and CLI end-to-end tests.

## Development & Verification Commands
```bash
uv sync
uv run ruff check src tests plugin/hooks
uv run pytest -v
uv run genealogy validate --root examples/west-family --strict
agy plugin validate plugin --strict
```
