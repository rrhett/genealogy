"""Tests for GEDCOM 5.5.1/7.0 staging import, privacy-redacted export, and round-trip."""

from __future__ import annotations

from pathlib import Path

from genealogy.gedcom import export_gedcom, gedcom_date_to_iso, import_gedcom
from genealogy.validator import validate_dataset

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "west-family"


def test_gedcom_date_conversion() -> None:
    # Standard GEDCOM dates
    assert gedcom_date_to_iso("14 MAY 1886") == "1886-05-14"
    assert gedcom_date_to_iso("MAY 1886") == "1886-05"
    assert gedcom_date_to_iso("ABT 1890") == "abt 1890"
    assert gedcom_date_to_iso("BET 1885 AND 1888") == "bet 1885 and 1888"
    assert gedcom_date_to_iso("BEF 12 NOV 1954") == "bef 1954-11-12"

    # Full English months, punctuated qualifiers, US slash dates & ranges
    assert gedcom_date_to_iso("1 March 1986") == "1986-03-01"
    assert gedcom_date_to_iso("February 16, 1750") == "1750-02-16"
    assert gedcom_date_to_iso("Abt. 1710") == "abt 1710"
    assert gedcom_date_to_iso("Before 1951") == "bef 1951"
    assert gedcom_date_to_iso("Aft. 13 Apr 1812") == "aft 1812-04-13"
    assert gedcom_date_to_iso("1993-2020") == "bet 1993 and 2020"
    assert gedcom_date_to_iso("1 Sep 1775-1778") == "bet 1775-09-01 and 1778"
    assert gedcom_date_to_iso("Between April 1824 and Jan 1837") == "bet 1824-04 and 1837-01"
    assert gedcom_date_to_iso("10/26/2016") == "2016-10-26"

    # Spanish & French months and qualifiers (Ancestry multilingual exports)
    assert gedcom_date_to_iso("15 Mayo 1930") == "1930-05-15"
    assert gedcom_date_to_iso("8 Dic 1905") == "1905-12-08"
    assert gedcom_date_to_iso("19 Abr 1814") == "1814-04-19"
    assert gedcom_date_to_iso("29 Set 1602") == "1602-09-29"
    assert gedcom_date_to_iso("aprox1760") == "abt 1760"
    assert gedcom_date_to_iso("aproximadamente 1738") == "abt 1738"
    assert gedcom_date_to_iso("12 Août 1668") == "1668-08-12"
    assert gedcom_date_to_iso("24 Févr 1729") == "1729-02-24"
    assert gedcom_date_to_iso("12 Janv 1743") == "1743-01-12"
    assert gedcom_date_to_iso("Environ 1652") == "abt 1652"

    # Truly non-year strings return None (preserved in profile Notes on import)
    assert gedcom_date_to_iso("17 Oct") is None
    assert gedcom_date_to_iso("en France") is None


def test_unparsed_gedcom_dates_preserved_in_notes(tmp_path: Path) -> None:
    ged = tmp_path / "partial.ged"
    ged.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.1\n"
        "0 @I1@ INDI\n1 NAME Carmen /Example/\n1 SEX F\n"
        "1 BIRT\n2 DATE aprox1760\n"
        "1 DEAT\n2 DATE 17 Oct\n"
        "0 TRLR\n",
        encoding="utf-8",
    )
    summary = import_gedcom(ged, tmp_path, dest="staging")
    assert len(summary.warnings) == 1
    profile = (summary.destination / "data" / "individuals" / "I-EXAMPLE-CARMEN-1760.md").read_text(
        encoding="utf-8"
    )
    assert "date: abt 1760" in profile
    assert "Original unparsed GEDCOM death date: `17 Oct`" in profile


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


def test_export_gedcom_marriage_investigation_and_suggested_label(tmp_path: Path) -> None:
    import yaml

    def _fm(meta: dict) -> str:
        return f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n"

    (tmp_path / "data" / "individuals").mkdir(parents=True)
    (tmp_path / "data" / "sources").mkdir(parents=True)

    (tmp_path / "data" / "sources" / "S-1826-CHURCH-INFO.md").write_text(
        _fm(
            {
                "id": "S-1826-CHURCH-INFO",
                "title": "Informacion Matrimonial 1826",
                "record_type": "church",
                "individuals_cited": ["I-SANCHEZ-VICENTE-1807", "I-GONZALEZ-MARIA-1812"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "individuals" / "I-SANCHEZ-VICENTE-1807.md").write_text(
        _fm(
            {
                "id": "I-SANCHEZ-VICENTE-1807",
                "primary_name": "Vicente Sanchez",
                "sex": "M",
                "events": [
                    {
                        "type": "marriage_investigation",
                        "date": "1826-11-19",
                        "place": "Jalisco, Mexico",
                        "description": "Informacion Matrimonial",
                        "sources": ["S-1826-CHURCH-INFO"],
                    },
                    {
                        "type": "other",
                        "date": "1815-05-01",
                        "description": "Parish confirmation",
                        "suggested_label": "confirmation",
                        "sources": ["S-1826-CHURCH-INFO"],
                    },
                ],
                "status": "tentative",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "individuals" / "I-GONZALEZ-MARIA-1812.md").write_text(
        _fm(
            {
                "id": "I-GONZALEZ-MARIA-1812",
                "primary_name": "Maria Gonzalez",
                "sex": "F",
                "status": "tentative",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "relationships.yaml").write_text(
        yaml.safe_dump(
            {
                "relationships": [
                    {
                        "type": "marriage",
                        "person_1": "I-SANCHEZ-VICENTE-1807",
                        "person_2": "I-GONZALEZ-MARIA-1812",
                        "date": "aft 1826-11-19",
                        "confidence": "probable",
                        "sources": ["S-1826-CHURCH-INFO"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_ged = tmp_path / "out.ged"
    export_gedcom(tmp_path, out_ged)
    text = out_ged.read_text(encoding="utf-8")
    assert "1 EVEN\n2 TYPE Marriage Investigation\n2 DATE 19 NOV 1826" in text
    assert "2 NOTE Informacion Matrimonial" in text
    assert "1 EVEN\n2 TYPE confirmation\n2 DATE 1 MAY 1815" in text
    assert text.count("1 MARR") == 1
