"""Tests for artifact intake scanning, duplicate detection, and source stub scaffolding."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from genealogy.cli import main
from genealogy.intake import (
    grab_screenshot,
    rename_artifact,
    scaffold_unlinked_sources,
    scan_artifacts,
)
from genealogy.schema import read_markdown_frontmatter
from genealogy.validator import validate_dataset


def test_intake_detects_duplicates_and_scaffolds_valid_stubs(tmp_path: Path) -> None:
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(parents=True)
    f1 = art_dir / "1920_census_west_cocke_tn.png"
    f2 = art_dir / "1920_census_west_copy.png"
    f3 = art_dir / "1918_wwi_draft_smith.pdf"

    f1.write_bytes(b"same-image-bytes")
    f2.write_bytes(b"same-image-bytes")
    f3.write_bytes(b"different-pdf-bytes")

    report = scan_artifacts(tmp_path)
    assert len(report.artifacts) == 3
    assert len(report.unlinked) == 3
    assert len(report.duplicates) == 1

    # Dry run writes nothing
    planned = scaffold_unlinked_sources(tmp_path, dry_run=True)
    assert len(planned) == 3
    assert not (tmp_path / "data" / "sources").exists()

    # Real scaffold creates valid source stubs with matching SHA-256 and leaves artifacts untouched
    created = scaffold_unlinked_sources(tmp_path, dry_run=False)
    assert len(created) == 3
    assert f1.read_bytes() == b"same-image-bytes"

    after = scan_artifacts(tmp_path)
    assert len(after.unlinked) == 0
    assert len(after.linked) == 3

    val = validate_dataset(tmp_path)
    assert val.errors == []
    assert val.warnings == []


def test_intake_flags_duplicate_of_excluded_lead(tmp_path: Path) -> None:
    from genealogy.schema import render_frontmatter
    from genealogy.validator import compute_sha256

    art_dir = tmp_path / "artifacts"
    src_dir = tmp_path / "data" / "sources"
    art_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    orig_wrong = art_dir / "1880_wrong_john.txt"
    orig_wrong.write_text("Wrong John West from Virginia", encoding="utf-8")
    sha = compute_sha256(orig_wrong)

    (src_dir / "S-1880-CENSUS-WRONG.md").write_text(
        render_frontmatter(
            {
                "id": "S-1880-CENSUS-WRONG",
                "title": "Wrong John West",
                "record_type": "census",
                "artifact_path": "artifacts/1880_wrong_john.txt",
                "artifact_sha256": sha,
                "relevance": "excluded",
                "exclusion_reason": "Different man born in VA",
                "individuals_cited": [],
            },
            "## Notes\nExcluded.\n",
        ),
        encoding="utf-8",
    )

    # User accidentally re-downloads the exact same wrong scan under a new filename
    redownload = art_dir / "1880_ancestry_hint_download.txt"
    redownload.write_text("Wrong John West from Virginia", encoding="utf-8")

    rep = scan_artifacts(tmp_path)
    assert len(rep.excluded_duplicates) == 1
    assert rep.excluded_duplicates[0]["artifact"] == "artifacts/1880_ancestry_hint_download.txt"
    assert rep.excluded_duplicates[0]["source_id"] == "S-1880-CENSUS-WRONG"


def test_grab_screenshot_finds_latest_and_handles_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "artifacts").mkdir(parents=True)
    screenshots = tmp_path / "Screenshots"
    screenshots.mkdir()

    older = screenshots / "Screenshot 2026-11-19.png"
    newer = screenshots / "Screenshot 2026-11-20.png"
    not_img = screenshots / "notes.txt"
    older.write_bytes(b"older-png-bytes")
    newer.write_bytes(b"newer-png-bytes")
    not_img.write_text("ignore me", encoding="utf-8")

    os.utime(older, (1000.0, 1000.0))
    os.utime(newer, (2000.0, 2000.0))
    os.utime(not_img, (3000.0, 3000.0))

    monkeypatch.setenv("GENEALOGY_SCREENSHOTS_DIR", str(screenshots))

    # Dry run does not copy
    dry = grab_screenshot(repo, dry_run=True)
    assert dry.source_path == newer.resolve()
    assert dry.rel_path == "artifacts/Screenshot 2026-11-20.png"
    assert not (repo / "artifacts" / "Screenshot 2026-11-20.png").exists()

    # Real grab copies newest image
    res = grab_screenshot(repo)
    assert res.already_exists is False
    assert (repo / "artifacts" / "Screenshot 2026-11-20.png").read_bytes() == b"newer-png-bytes"

    # Re-grabbing identical bytes reports already_exists without duplicating
    dup = grab_screenshot(repo)
    assert dup.already_exists is True
    assert dup.existing_rel_path == "artifacts/Screenshot 2026-11-20.png"

    # Explicit --from and --name without extension preserves .png
    res_named = grab_screenshot(repo, source=older, name="1920_census_west")
    assert res_named.rel_path == "artifacts/1920_census_west.png"
    assert (repo / "artifacts" / "1920_census_west.png").read_bytes() == b"older-png-bytes"

    # Path traversal is rejected
    with pytest.raises(ValueError):
        grab_screenshot(repo, source=older, name="../escape.png")


def test_rename_artifact_updates_unlinked_and_linked_sources(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    art_dir = repo / "artifacts"
    art_dir.mkdir(parents=True)
    raw = art_dir / "Screenshot 2026-11-20.png"
    raw.write_bytes(b"census-scan-bytes")

    # Dry run leaves file untouched
    dry = rename_artifact(repo, "artifacts/Screenshot 2026-11-20.png", "1910_census_west", dry_run=True)
    assert dry.new_rel_path == "artifacts/1910_census_west.png"
    assert raw.exists()

    # Rename unlinked artifact (auto-preserves .png extension)
    res = rename_artifact(repo, "Screenshot 2026-11-20.png", "1910_census_west")
    assert res.old_rel_path == "artifacts/Screenshot 2026-11-20.png"
    assert res.new_rel_path == "artifacts/1910_census_west.png"
    assert not raw.exists()
    assert (art_dir / "1910_census_west.png").read_bytes() == b"census-scan-bytes"

    # Scaffold a source, then rename again -> updates artifact_path & validates cleanly
    created = scaffold_unlinked_sources(repo)
    assert len(created) == 1
    _, src_path = created[0]

    res2 = rename_artifact(repo, "artifacts/1910_census_west.png", "1910_census_west_cocke_tn.png")
    assert res2.updated_sources == [created[0][0]]
    meta, _ = read_markdown_frontmatter(src_path)
    assert meta["artifact_path"] == "artifacts/1910_census_west_cocke_tn.png"

    val = validate_dataset(repo)
    assert val.errors == []
    assert val.warnings == []

    # Reject path traversal
    with pytest.raises(ValueError):
        rename_artifact(repo, "1910_census_west_cocke_tn.png", "../outside.png")


def test_cli_grab_screenshot_and_rename_artifact(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / "artifacts").mkdir(parents=True)
    shots = tmp_path / "shots"
    shots.mkdir()
    shot = shots / "Screenshot_2026-11-20.png"
    shot.write_bytes(b"cli-shot-bytes")

    rc1 = main(["grab-screenshot", "--root", str(repo), "--from", str(shots), "--json"])
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["rel_path"] == "artifacts/Screenshot_2026-11-20.png"
    assert out1["already_exists"] is False

    rc2 = main(
        [
            "rename-artifact",
            "artifacts/Screenshot_2026-11-20.png",
            "1930_census_west.png",
            "--root",
            str(repo),
            "--json",
        ]
    )
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["new_rel_path"] == "artifacts/1930_census_west.png"
    assert (repo / "artifacts" / "1930_census_west.png").is_file()
