"""Tests for schema models, genealogical date parsing, and frontmatter I/O."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from genealogy.schema import (
    IndividualFrontmatter,
    LifeEvent,
    Relationship,
    SourceFrontmatter,
    parse_genealogy_date,
    render_frontmatter,
    split_frontmatter,
)


@pytest.mark.parametrize(
    ("raw", "expected_gedcom", "year_min", "year_max"),
    [
        ("1886", "1886", 1886, 1886),
        ("1886-05", "MAY 1886", 1886, 1886),
        ("1954-11-12", "12 NOV 1954", 1954, 1954),
        ("abt 1890", "ABT 1890", 1890, 1890),
        ("bef 1900-06", "BEF JUN 1900", None, 1900),
        ("aft 1850-01-05", "AFT 5 JAN 1850", 1850, None),
        ("est 1840", "EST 1840", 1840, 1840),
        ("cal 1882-03", "CAL MAR 1882", 1882, 1882),
        ("bet 1885 and 1888", "BET 1885 AND 1888", 1885, 1888),
        ("bet 1885-01 and 1886-12", "BET JAN 1885 AND DEC 1886", 1885, 1886),
        ("2000-02-29", "29 FEB 2000", 2000, 2000),
    ],
)
def test_valid_dates(raw: str, expected_gedcom: str, year_min: int | None, year_max: int | None) -> None:
    parsed = parse_genealogy_date(raw)
    assert parsed is not None
    assert parsed.to_gedcom() == expected_gedcom
    assert parsed.year_min == year_min
    assert parsed.year_max == year_max


def test_undated_and_empty_dates() -> None:
    assert parse_genealogy_date(None) is None
    assert parse_genealogy_date("") is None
    assert parse_genealogy_date("undated") is None


@pytest.mark.parametrize(
    "bad",
    [
        "1886-13-01",
        "1900-02-29",  # 1900 was not a leap year
        "May 1886",
        "bet 1900 and 1890",
        "circa 1880",
        "9999",
    ],
)
def test_invalid_dates(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_genealogy_date(bad)


def test_individual_and_source_models() -> None:
    ind = IndividualFrontmatter(
        id="I-WEST-CLAUDE-1886",
        primary_name="Claude West",
        sex="M",
        birth={"date": "1886-05", "place": "Cocke County, TN", "sources": ["S-1900-CENSUS-WEST"]},
        status="confirmed",
    )
    assert ind.all_source_ids() == {"S-1900-CENSUS-WEST"}
    assert ind.living is False

    # Invalid individual ID
    with pytest.raises(ValidationError):
        IndividualFrontmatter(id="BAD-ID", primary_name="Nobody")

    # Self-referential relationship rejected
    with pytest.raises(ValidationError):
        Relationship(
            type="sibling",
            person_1="I-WEST-CLAUDE-1886",
            person_2="I-WEST-CLAUDE-1886",
        )

    # Subtype only allowed on parent_child
    with pytest.raises(ValidationError):
        Relationship(
            type="marriage",
            person_1="I-WEST-CLAUDE-1886",
            person_2="I-RAINWATER-OCTA-1890",
            subtype="biological",
        )

    # Invalid sha256 rejected
    with pytest.raises(ValidationError):
        SourceFrontmatter(
            id="S-1910-CENSUS-WEST",
            title="1910 Census",
            record_type="census",
            artifact_sha256="not-a-sha",
        )


def test_frontmatter_roundtrip() -> None:
    meta = {"id": "I-WEST-JOHN-1858", "primary_name": "John West", "status": "tentative"}
    body = "## Biographical Summary\nBorn in North Carolina.\n"
    rendered = render_frontmatter(meta, body)
    parsed_meta, parsed_body = split_frontmatter(rendered)
    assert parsed_meta == meta
    assert parsed_body == body


def test_custom_fields_allowed_while_unknown_top_level_keys_forbidden() -> None:
    ind = IndividualFrontmatter(
        id="I-WEST-CLAUDE-1886",
        primary_name="Claude West",
        custom={"godparents": ["Jane Doe"], "dna_haplogroup": "R-M269"},
    )
    assert ind.custom["godparents"] == ["Jane Doe"]

    src = SourceFrontmatter(
        id="S-1910-CENSUS-WEST",
        title="1910 Census",
        record_type="census",
        custom={"microfilm_frame": 42},
    )
    assert src.custom["microfilm_frame"] == 42

    rel = Relationship(
        type="fan_associate",
        person_1="I-WEST-CLAUDE-1886",
        person_2="I-RAINWATER-OCTA-1890",
        custom={"role": "baptism_sponsor"},
    )
    assert rel.custom["role"] == "baptism_sponsor"

    with pytest.raises(ValidationError):
        IndividualFrontmatter(
            id="I-WEST-CLAUDE-1886",
            primary_name="Claude West",
            unexpected_key="should fail",  # type: ignore[call-arg]
        )


def test_individual_marriage_event() -> None:
    ind = IndividualFrontmatter(
        id="I-WEST-CLAUDE-1886",
        primary_name="Claude West",
        events=[
            LifeEvent(
                type="marriage",
                date="1908-06-14",
                place="Cocke County, Tennessee, USA",
                description="Married Octa Rainwater",
                sources=["S-1908-MARRIAGE-WEST-RAINWATER"],
            )
        ],
    )
    assert ind.events[0].type == "marriage"
