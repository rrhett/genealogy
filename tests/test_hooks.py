"""Tests for Antigravity hooks (.agents/hooks/) and skill frontmatter integrity."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from genealogy.schema import read_markdown_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugin"
GUARD_SCRIPT = PLUGIN_ROOT / "hooks" / "guard_boundaries.py"
STOP_SCRIPT = PLUGIN_ROOT / "hooks" / "validate_on_stop.py"


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
    plugin_manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert plugin_manifest["name"] == "genealogy-research"
    assert plugin_manifest["version"]
    assert (PLUGIN_ROOT / plugin_manifest["logo"]).is_file()
    assert (PLUGIN_ROOT / "rules" / "AGENTS.md").is_file()

    hooks_cfg = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))
    assert "protect-boundaries" in hooks_cfg
    assert "validate-data-on-stop" in hooks_cfg


def test_guard_boundaries_blocks_artifacts_and_asks_on_notes(tmp_path: Path) -> None:
    # Unrelated non-genealogy software repo -> hook is a no-op (allows writes to build artifacts/)
    software_repo = tmp_path / "software_app"
    software_repo.mkdir()
    res_non_gen = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(software_repo)],
            "cwd": str(software_repo),
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(software_repo / "artifacts" / "bundle.js")},
            },
        },
    )
    assert res_non_gen["decision"] == "allow"

    # Genealogy workspace -> enforces artifacts/ (deny) and notes/ (ask)
    gen_repo = tmp_path / "family_repo"
    (gen_repo / "data").mkdir(parents=True)
    (gen_repo / "data" / "relationships.yaml").write_text("relationships: []\n", encoding="utf-8")

    # Direct write to artifacts/ -> deny
    res = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(gen_repo / "artifacts" / "scan.png")},
            },
        },
    )
    assert res["decision"] == "deny"

    # Direct edit to notes/ -> ask
    res_notes = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": str(gen_repo / "notes" / "lore.md")},
            },
        },
    )
    assert res_notes["decision"] == "ask"

    # Write to data/ -> allow
    res_data = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(gen_repo / "data" / "sources" / "S-1900-CENSUS.md")},
            },
        },
    )
    assert res_data["decision"] == "allow"

    # Destructive shell rm on artifacts/ -> deny; read-only ls -> allow
    res_rm = _run_hook(
        GUARD_SCRIPT,
        {
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
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
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
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
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
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
            "workspacePaths": [str(gen_repo)],
            "cwd": str(gen_repo),
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
    skills_dir = PLUGIN_ROOT / "skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    assert len(skill_files) == 6
    for sf in skill_files:
        meta, body = read_markdown_frontmatter(sf)
        assert meta.get("name") == sf.parent.name
        assert isinstance(meta.get("description"), str) and len(meta["description"]) > 20
        assert body.strip()
