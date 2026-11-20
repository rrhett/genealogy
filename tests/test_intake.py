"""Tests for artifact intake scanning, duplicate detection, and source stub scaffolding."""

from __future__ import annotations

from pathlib import Path

from genealogy.intake import scaffold_unlinked_sources, scan_artifacts
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
