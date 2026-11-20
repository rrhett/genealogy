"""Tests for Antigravity hooks (.agents/hooks/) and skill frontmatter integrity."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from genealogy.schema import read_markdown_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPT = REPO_ROOT / ".agents" / "hooks" / "guard_boundaries.py"
STOP_SCRIPT = REPO_ROOT / ".agents" / "hooks" / "validate_on_stop.py"


def _run_hook(script: Path, payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_hooks_json_is_valid() -> None:
    hooks_cfg = json.loads((REPO_ROOT / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    assert "protect-boundaries" in hooks_cfg
    assert "validate-data-on-stop" in hooks_cfg


def test_guard_boundaries_blocks_artifacts_and_asks_on_notes() -> None:
    # Direct write to artifacts/ -> deny
    res = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(REPO_ROOT / "artifacts" / "scan.png")},
            },
        },
    )
    assert res["decision"] == "deny"

    # Direct edit to notes/ -> ask
    res_notes = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": str(REPO_ROOT / "notes" / "lore.md")},
            },
        },
    )
    assert res_notes["decision"] == "ask"

    # Write to data/ or examples/west-family/artifacts/ -> allow
    res_data = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(REPO_ROOT / "data" / "sources" / "S-1900-CENSUS.md")},
            },
        },
    )
    assert res_data["decision"] == "allow"

    # Destructive shell rm on artifacts/ -> deny; read-only ls -> allow
    res_rm = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "rm -f artifacts/census.jpg"},
            },
        },
    )
    assert res_rm["decision"] == "deny"

    res_ls = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "ls -la artifacts/"},
            },
        },
    )
    assert res_ls["decision"] == "allow"

    res_touch = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "touch artifacts/new.png"},
            },
        },
    )
    assert res_touch["decision"] == "deny"

    # Nested examples/.../artifacts/ should NOT false-positive
    res_example_rm = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT)],
            "cwd": str(REPO_ROOT),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "rm -f examples/west-family/artifacts/temp.txt"},
            },
        },
    )
    assert res_example_rm["decision"] == "allow"


def test_validate_on_stop_hook(tmp_path: Path) -> None:
    # Valid non-empty dataset -> allow stop ({})
    res_valid = _run_hook(
        STOP_SCRIPT,
        {
            "workspacePaths": [str(REPO_ROOT / "examples" / "west-family")],
            "terminationReason": "NO_TOOL_CALL",
            "continuationActive": False,
        },
    )
    assert res_valid == {}

    # Empty data/ -> instant pass ({})
    (tmp_path / "data" / "individuals").mkdir(parents=True)
    (tmp_path / "data" / "sources").mkdir(parents=True)
    res_empty = _run_hook(
        STOP_SCRIPT,
        {
            "workspacePaths": [str(tmp_path)],
            "terminationReason": "NO_TOOL_CALL",
            "continuationActive": False,
        },
    )
    assert res_empty == {}

    # Broken individual file in data/ -> continue with reason
    (tmp_path / "data" / "individuals" / "I-BROKEN-TEST-1900.md").write_text(
        "---\nid: I-BROKEN-TEST-1900\nprimary_name: Broken\nbirth:\n  date: '1900'\n  sources: ['S-1900-CENSUS-MISSING']\n---\n",
        encoding="utf-8",
    )
    res_broken = _run_hook(
        STOP_SCRIPT,
        {
            "workspacePaths": [str(tmp_path)],
            "terminationReason": "NO_TOOL_CALL",
            "continuationActive": False,
        },
    )
    assert res_broken["decision"] == "continue"
    assert "MISSING_SOURCE_REF" in res_broken["reason"]

    # When continuationActive is True -> do not loop forever
    res_loop_guard = _run_hook(
        STOP_SCRIPT,
        {
            "workspacePaths": [str(tmp_path)],
            "terminationReason": "NO_TOOL_CALL",
            "continuationActive": True,
        },
    )
    assert res_loop_guard == {}


def test_all_skills_have_valid_frontmatter() -> None:
    skills_dir = REPO_ROOT / ".agents" / "skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    assert len(skill_files) == 6
    for sf in skill_files:
        meta, body = read_markdown_frontmatter(sf)
        assert meta.get("name") == sf.parent.name
        assert isinstance(meta.get("description"), str) and len(meta["description"]) > 20
        assert body.strip()
