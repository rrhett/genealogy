#!/usr/bin/env python3
"""Antigravity Stop hook: run `genealogy validate` when data/ contains records."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root(payload: dict) -> Path:
    ws = payload.get("workspacePaths")
    if isinstance(ws, list) and ws:
        return Path(ws[0]).resolve()
    return Path(__file__).resolve().parents[2]


def _has_data_records(root: Path) -> bool:
    data_dir = root / "data"
    if not data_dir.exists():
        return False
    if any((data_dir / "individuals").glob("*.md")):
        return True
    if any((data_dir / "sources").glob("*.md")):
        return True
    rel = data_dir / "relationships.yaml"
    if rel.exists():
        text = rel.read_text(encoding="utf-8")
        # Non-empty if there is at least one list item
        if "\n  -" in text or "\n-" in text:
            return True
    return False


def _build_validate_cmd(root: Path) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    hook_repo_root = Path(__file__).resolve().parents[2]
    for candidate_root in (root, hook_repo_root):
        venv_bin = candidate_root / ".venv" / "bin" / "genealogy"
        if venv_bin.exists():
            return [str(venv_bin), "validate", "--root", str(root)], env
    if shutil.which("uv"):
        return ["uv", "run", "--project", str(hook_repo_root), "genealogy", "validate", "--root", str(root)], env
    env["PYTHONPATH"] = str(hook_repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return [sys.executable, "-m", "genealogy.cli", "validate", "--root", str(root)], env


def evaluate(payload: dict) -> dict[str, str]:
    if payload.get("continuationActive"):
        return {}
    reason = payload.get("terminationReason")
    if reason and reason != "NO_TOOL_CALL":
        return {}

    root = _repo_root(payload)
    if not _has_data_records(root):
        return {}

    cmd, env = _build_validate_cmd(root)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
    except Exception:
        return {}

    if proc.returncode != 0:
        details = (proc.stdout + "\n" + proc.stderr).strip()[:1200]
        return {
            "decision": "continue",
            "reason": (
                "Data validation failed (`uv run genealogy validate`). "
                f"Please resolve these errors before finishing:\n\n{details}"
            ),
        }
    return {}


def main() -> int:
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    print(json.dumps(evaluate(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
