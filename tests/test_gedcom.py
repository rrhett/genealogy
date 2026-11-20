"""Tests for GEDCOM 5.5.1/7.0 staging import, privacy-redacted export, and round-trip."""

from __future__ import annotations

from pathlib import Path

from genealogy.gedcom import export_gedcom, gedcom_date_to_iso, import_gedcom
from genealogy.validator import validate_dataset

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "west-family"


def test_gedcom_date_conversion() -> None:
    assert gedcom_date_to_iso("14 MAY 1886") == "1886-05-14"
    assert gedcom_date_to_iso("MAY 1886") == "1886-05"
    assert gedcom_date_to_iso("ABT 1890") == "abt 1890"
    assert gedcom_date_to_iso("BET 1885 AND 1888") == "bet 1885 and 1888"
    assert gedcom_date_to_iso("BEF 12 NOV 1954") == "bef 1954-11-12"


def test_import_gedcom_stages_valid_tentative_dataset(tmp_path: Path) -> None:
    summary = import_gedcom(EXAMPLE_ROOT / "sample.ged", tmp_path, dest="staging")
    assert summary.individuals_imported == 4
    assert summary.relationships_imported == 4  # 1 marriage + 3 parent_child edges
    assert summary.warnings == []

    # The staged dataset itself must validate cleanly with 0 errors and 0 warnings
    val = validate_dataset(summary.destination)
    assert val.errors == []
    assert val.warnings == []
    assert val.individuals_count == 4


def test_export_gedcom_redacts_living_by_default_and_roundtrips(tmp_path: Path) -> None:
    redacted_ged = tmp_path / "redacted.ged"
    export_gedcom(EXAMPLE_ROOT, redacted_ged, version="7.0", redact_living=True)
    text = redacted_ged.read_text(encoding="utf-8")

    assert "2 VERS 7.0" in text
    assert "1 NAME Claude /West/" in text
    assert "1 NAME Living /Private/" in text
    assert "Jane /West/" not in text
    assert "ancestry [auth required]" in text
    assert "District 9 Blacksmith" not in text  # excluded sources omitted from GEDCOM export

    # Opt-in include-living exports unredacted name
    full_ged = tmp_path / "full.ged"
    export_gedcom(EXAMPLE_ROOT, full_ged, version="5.5.1", redact_living=False)
    full_text = full_ged.read_text(encoding="utf-8")
    assert "2 VERS 5.5.1" in full_text
    assert "1 CHAR UTF-8" in full_text
    assert "1 NAME Jane /West/" in full_text

    # Re-importing the exported GEDCOM 7.0 produces a valid staged dataset
    reimported = import_gedcom(redacted_ged, tmp_path, dest="staging")
    assert reimported.individuals_imported == 9
    val = validate_dataset(reimported.destination)
    assert val.errors == []
