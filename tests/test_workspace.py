"""Tests for workspace bootstrapping (`init`), conflict-free upgrades, and external artifacts_dir."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from genealogy import __version__
from genealogy.cli import main
from genealogy.intake import scaffold_unlinked_sources, scan_artifacts
from genealogy.validator import validate_dataset
from genealogy.viewer import make_viewer_server
from genealogy.workspace import init_workspace, upgrade_workspace


def test_init_workspace_creates_clean_git_repo_and_validates(tmp_path: Path) -> None:
    target = tmp_path / "my-family"
    rep = init_workspace(target, git_init=True, use_lfs=True)

    assert rep.root == target.resolve()
    assert rep.toolkit_version == __version__
    assert rep.agent_mode == "embedded"
    assert rep.git_initialized is True
    assert (target / ".git").is_dir()
    assert (target / "AGENTS.md").is_file()
    assert (target / "AGENTS.local.md").is_file()
    assert (target / ".agents" / "hooks.json").is_file()
    assert (target / ".agents" / "hooks" / "guard_boundaries.py").is_file()
    assert len(list((target / ".agents" / "skills").glob("*/SKILL.md"))) == 11
    assert (target / ".gitattributes").is_file()
    assert "filter=lfs" in (target / ".gitattributes").read_text(encoding="utf-8")
    assert "github.com/rrhett/genealogy.git" in (
        target / ".github" / "workflows" / "validate.yml"
    ).read_text(encoding="utf-8")
    assert (target / ".genealogy.yaml").is_file()
    assert (target / "data" / "relationships.yaml").is_file()

    # Fresh bootstrapped repo validates strictly with 0 errors and 0 warnings
    val = validate_dataset(target)
    assert val.errors == []
    assert val.warnings == []


def test_upgrade_workspace_refreshes_agent_files_without_touching_user_data(tmp_path: Path) -> None:
    target = tmp_path / "family-repo"
    init_workspace(target, git_init=False, artifacts_dir="/tmp/custom-scans")

    # Add user content that must NEVER be overwritten
    user_note = target / "notes" / "grandma.md"
    user_note.write_text("Grandma's oral history\n", encoding="utf-8")
    local_rules = target / "AGENTS.local.md"
    local_rules.write_text("# My custom family rules\n", encoding="utf-8")

    # Simulate stale AGENTS.md + obsolete skill file from an older toolkit version
    (target / "AGENTS.md").write_text("# Outdated AGENTS\n", encoding="utf-8")
    stale_skill = target / ".agents" / "skills" / "deprecated-skill" / "SKILL.md"
    stale_skill.parent.mkdir(parents=True)
    stale_skill.write_text("old", encoding="utf-8")

    # Dry-run reports pending changes without writing
    dry = upgrade_workspace(target, dry_run=True)
    assert "AGENTS.md" in dry.updated
    assert ".agents/skills/deprecated-skill/SKILL.md" in dry.removed
    assert stale_skill.exists()

    # Real upgrade syncs managed files & cleans stale ones while preserving all user data
    real = upgrade_workspace(target, dry_run=False)
    assert "AGENTS.md" in real.updated
    assert ".agents/skills/deprecated-skill/SKILL.md" in real.removed
    assert not stale_skill.exists()
    assert user_note.read_text(encoding="utf-8") == "Grandma's oral history\n"
    assert local_rules.read_text(encoding="utf-8") == "# My custom family rules\n"
    assert "/tmp/custom-scans" in (target / ".genealogy.yaml").read_text(encoding="utf-8")


def test_init_plugin_only_skips_embedded_agents(tmp_path: Path) -> None:
    target = tmp_path / "plugin-only-repo"
    rep = init_workspace(target, git_init=False, plugin_only=True)
    assert rep.agent_mode == "plugin"
    assert not (target / ".agents").exists()
    assert not (target / "AGENTS.md").exists()
    assert (target / "AGENTS.local.md").is_file()


def test_external_artifacts_dir_works_across_intake_validate_viewer_and_hook(tmp_path: Path) -> None:
    ext_scans = tmp_path / "dropbox_scans"
    ext_scans.mkdir()
    scan = ext_scans / "1920_census_west.txt"
    scan.write_text("1920 Census — West Household", encoding="utf-8")

    repo = tmp_path / "private_repo"
    init_workspace(repo, git_init=False, artifacts_dir=str(ext_scans))

    # Intake discovers scan in external folder & uses canonical logical path `artifacts/...`
    intake = scan_artifacts(repo)
    assert len(intake.unlinked) == 1
    assert intake.unlinked[0].rel_path == "artifacts/1920_census_west.txt"

    created = scaffold_unlinked_sources(repo)
    assert len(created) == 1

    # Validation resolves SHA-256 cleanly from external artifacts_dir
    val = validate_dataset(repo)
    assert val.errors == []
    assert val.warnings == []

    # Viewer serves external artifact safely
    server = make_viewer_server(repo, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/artifacts/1920_census_west.txt") as resp:
            assert resp.status == 200
            assert "West Household" in resp.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    # Guard hook blocks writes inside external artifacts_dir too
    hook_script = repo / ".agents" / "hooks" / "guard_boundaries.py"
    proc = subprocess.run(
        [sys.executable, str(hook_script)],
        input=json.dumps(
            {
                "workspacePaths": [str(repo)],
                "cwd": str(repo),
                "toolCall": {"name": "write_file", "args": {"Path": str(scan)}},
            }
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["decision"] == "deny"


def test_cli_init_and_upgrade_workspace(tmp_path: Path, capsys) -> None:
    target = tmp_path / "cli-family"
    assert main(["init", str(target), "--no-git"]) == 0
    assert "Initialized genealogy research repository" in capsys.readouterr().out

    assert main(["upgrade-workspace", "--root", str(target), "--dry-run"]) == 0
    assert "[DRY RUN]" in capsys.readouterr().out
