#!/usr/bin/env python3
"""Antigravity PreToolUse hook: enforce AGENTS.md read-only boundaries on artifacts/ and notes/."""

from __future__ import annotations

import json
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
    return Path(__file__).resolve().parents[2]


def _is_under(target: Path, parent: Path) -> bool:
    try:
        target.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def evaluate(payload: dict) -> dict[str, str]:
    root = _repo_root(payload)
    artifacts_dir = root / "artifacts"
    notes_dir = root / "notes"

    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name") or "")
    args = tool_call.get("args") or {}

    if tool_name == "run_command":
        cmd = str(args.get("CommandLine") or args.get("command") or "")
        if _DESTRUCTIVE_CMD_RE.search(cmd):
            # Check if command references top-level artifacts/ or notes/ (not examples/.../artifacts/)
            if re.search(r"(?:^|[\s'\"`])(?:\./)?artifacts(?:/|[\s'\"`]|$)", cmd) or str(artifacts_dir) in cmd:
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
        if _is_under(candidate, artifacts_dir):
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
