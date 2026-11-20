"""End-to-end CLI tests verifying every subcommand works cleanly out of the box."""

from __future__ import annotations

import json
from pathlib import Path

from genealogy.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "west-family"


def test_cli_validate_and_stats(capsys) -> None:
    assert main(["validate", "--root", str(EXAMPLE_ROOT), "--strict", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 0

    assert main(["stats", "--root", str(EXAMPLE_ROOT), "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["individuals"]["total"] == 9
    assert stats["individuals"]["living"] == 1
    assert stats["artifacts"]["linked"] == 7
    assert stats["artifacts"]["unlinked"] == 0


def test_cli_db_query_timeline_tree_gedcom(tmp_path: Path, capsys) -> None:
    db_out = tmp_path / "west.sqlite"
    assert main(["build-db", "--root", str(EXAMPLE_ROOT), "--out", str(db_out)]) == 0
    assert db_out.exists()
    capsys.readouterr()

    assert main(["query", "Union Cemetery", "--fts", "--root", str(EXAMPLE_ROOT), "--json"]) == 0
    fts_rows = json.loads(capsys.readouterr().out)
    assert any(r["entity_id"] == "I-WEST-CLAUDE-1886" for r in fts_rows)

    assert main(["timeline", "I-WEST-CLAUDE-1886", "--include-family", "--root", str(EXAMPLE_ROOT)]) == 0
    tl = capsys.readouterr().out
    assert "Claude West" in tl

    assert main(["tree", "--format", "mermaid", "--root", str(EXAMPLE_ROOT)]) == 0
    tree = capsys.readouterr().out
    assert "flowchart TD" in tree

    ged_out = tmp_path / "export.ged"
    assert main(["export-gedcom", str(ged_out), "--root", str(EXAMPLE_ROOT)]) == 0
    assert ged_out.exists()
    assert "Living /Private/" in ged_out.read_text(encoding="utf-8")
    capsys.readouterr()

    assert main(["import-gedcom", str(EXAMPLE_ROOT / "sample.ged"), "--root", str(tmp_path)]) == 0
    out_txt = capsys.readouterr().out
    assert "Individuals:        4" in out_txt

    assert main(["kinship", "I-WEST-CLAUDE-1886", "I-RAINWATER-OCTA-1890", "--root", str(EXAMPLE_ROOT), "--json"]) == 0
    kin = json.loads(capsys.readouterr().out)
    assert kin["path_count"] == 2

    assert main(["collapse", "I-WEST-EDGAR-1909", "--root", str(EXAMPLE_ROOT), "--json"]) == 0
    col = json.loads(capsys.readouterr().out)
    assert col["inbreeding_coefficient"] == 0.0625

    assert main(["gaps", "--root", str(EXAMPLE_ROOT), "--json"]) == 0
    gaps = json.loads(capsys.readouterr().out)
    assert len(gaps["consanguineous_marriages"]) == 1

    rep_out = tmp_path / "claude_report.md"
    assert main(["report", "I-WEST-CLAUDE-1886", "--root", str(EXAMPLE_ROOT), "--out", str(rep_out)]) == 0
    assert "Family Group Sheet: Claude West" in rep_out.read_text(encoding="utf-8")
    capsys.readouterr()

    html_out = tmp_path / "viewer.html"
    assert main(["view", "--root", str(EXAMPLE_ROOT), "--out", str(html_out)]) == 0
    assert html_out.exists()
    assert "Genealogy DAG Viewer" in html_out.read_text(encoding="utf-8")
