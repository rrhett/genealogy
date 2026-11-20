#!/usr/bin/env python3
"""Antigravity PreToolUse hook: enforce AGENTS.md read-only boundaries on artifacts/ and notes/."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_PATH_ARG_KEYS = ("Path", "TargetFile", "path", "file_path", "target_file")
_DESTRUCTIVE_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:rm|mv|rmdir|unlink|truncate|chmod|chown|touch|git\s+rm)\b|"
    r"(?:>>?|\btee\b|\bsed\s+-i|\bperl\s+-i)\s+",
    re.IGNORECASE,
)


def _repo_root(payload: dict) -> Path:
    ws = payload.get("workspacePaths")
    if isinstance(ws, list) and ws:
        return Path(ws[0]).resolve()
    cwd = payload.get("cwd")
    if cwd:
        return Path(str(cwd)).resolve()
    return Path(__file__).resolve().parents[2]


def _configured_artifacts_dir(root: Path) -> Path | None:
    env_dir = os.environ.get("GENEALOGY_ARTIFACTS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        return p.resolve() if p.is_absolute() else (root / p).resolve()
    cfg = root / ".genealogy.yaml"
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^\s*artifacts_dir\s*:\s*['\"]?([^'\"#]+?)['\"]?\s*$", line)
            if m:
                val = m.group(1).strip()
                if val:
                    p = Path(val).expanduser()
                    return p.resolve() if p.is_absolute() else (root / p).resolve()
    return None


def _is_under(target: Path, parent: Path) -> bool:
    try:
        target.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_genealogy_workspace(root: Path) -> bool:
    """Return True only when the workspace looks like a genealogy repository."""
    return (
        (root / ".genealogy.yaml").is_file()
        or (root / "data" / "relationships.yaml").is_file()
        or (root / "data" / "individuals").is_dir()
    )


def evaluate(payload: dict) -> dict[str, str]:
    root = _repo_root(payload)
    if not _is_genealogy_workspace(root):
        return {"decision": "allow"}

    artifacts_dirs = [root / "artifacts"]
    custom_art = _configured_artifacts_dir(root)
    if custom_art and custom_art not in artifacts_dirs:
        artifacts_dirs.append(custom_art)
    notes_dir = root / "notes"

    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name") or "")
    args = tool_call.get("args") or {}

    if tool_name == "run_command":
        cmd = str(args.get("CommandLine") or args.get("command") or "")
        if _DESTRUCTIVE_CMD_RE.search(cmd):
            if re.search(r"(?:^|[\s'\"`])(?:\./)?artifacts(?:/|[\s'\"`]|$)", cmd) or any(
                str(d) in cmd for d in artifacts_dirs
            ):
                return {
                    "decision": "deny",
                    "reason": "AGENTS.md guardrail: artifacts/ is READ-ONLY and cannot be modified or deleted.",
                }
            if re.search(r"(?:^|[\s'\"`])(?:\./)?notes(?:/|[\s'\"`]|$)", cmd) or str(notes_dir) in cmd:
                return {
                    "decision": "ask",
                    "reason": "AGENTS.md guardrail: notes/ is human-owned. Confirm before modifying files in notes/.",
                }
        return {"decision": "allow"}

    # File write/edit tools
    cwd = Path(payload.get("cwd") or root)
    for key in _PATH_ARG_KEYS:
        raw_path = args.get(key)
        if not raw_path:
            continue
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if any(_is_under(candidate, ad) for ad in artifacts_dirs):
            return {
                "decision": "deny",
                "reason": "AGENTS.md guardrail: artifacts/ is READ-ONLY. Never modify, rename, or delete primary records.",
            }
        if _is_under(candidate, notes_dir):
            return {
                "decision": "ask",
                "reason": "AGENTS.md guardrail: notes/ is human-owned. Confirm the user explicitly asked to edit this file.",
            }

    return {"decision": "allow"}


def main() -> int:
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    result = evaluate(payload)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
