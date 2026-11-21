"""Tests for kinship path analysis, pedigree collapse, Malecot/Wright math, gaps, and reports."""

from __future__ import annotations

from pathlib import Path

import pytest

from genealogy.analysis import (
    build_family_report_markdown,
    compute_dna_corroboration,
    compute_kinship,
    compute_land_tracts,
    compute_pedigree_collapse,
    compute_research_gaps,
    find_consanguineous_marriages,
    label_kinship_steps,
)
from genealogy.loader import Dataset, LoadedIndividual
from genealogy.schema import IndividualFrontmatter, Relationship

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "west-family"


def test_label_kinship_steps() -> None:
    assert label_kinship_steps(0, 0) == "Self"
    assert label_kinship_steps(0, 1) == "Parent"
    assert label_kinship_steps(1, 0) == "Child"
    assert label_kinship_steps(1, 1) == "Sibling (via shared parent)"
    assert label_kinship_steps(2, 2) == "1st Cousin"
    assert label_kinship_steps(2, 3) == "1st Cousin 1x Removed"
    assert label_kinship_steps(3, 3) == "2nd Cousin"


def test_example_first_cousin_marriage_and_collapse() -> None:
    kin = compute_kinship(EXAMPLE_ROOT, "I-WEST-CLAUDE-1886", "I-RAINWATER-OCTA-1890")
    assert kin.are_married is True
    assert len(kin.paths) == 2
    assert {p.common_ancestor for p in kin.paths} == {"I-WEST-THOMAS-1830", "I-MILLER-SARAH-1834"}
    assert kin.wright_relationship == pytest.approx(0.125, abs=1e-6)
    assert kin.expected_offspring_inbreeding == pytest.approx(0.0625, abs=1e-6)

    # Edgar is offspring of first cousins -> F = 1/16 = 0.0625, 50% Gen-3 collapse
    col = compute_pedigree_collapse(EXAMPLE_ROOT, "I-WEST-EDGAR-1909")
    assert col.inbreeding_coefficient == pytest.approx(0.0625, abs=1e-6)
    gen3 = [g for g in col.generations if g.generation == 3][0]
    assert gen3.unique_ancestors == 2
    assert gen3.filled_slots == 4
    assert gen3.collapse_ratio == pytest.approx(0.5, abs=1e-4)
    assert len(col.multi_path_ancestors) == 2
    assert all(m.path_count == 2 for m in col.multi_path_ancestors)


def test_double_first_cousin_endogamy_math() -> None:
    """Verify exact Malecot coancestry for double first cousins (r = 0.25, offspring F = 0.125)."""
    ds = Dataset(root=Path("."))
    ids = [
        "I-A-F1-1800",
        "I-A-M1-1800",
        "I-B-F2-1800",
        "I-B-M2-1800",
        "I-P-S1-1830",
        "I-P-S2-1830",
        "I-P-T1-1830",
        "I-P-T2-1830",
        "I-C-X-1860",
        "I-C-Y-1860",
        "I-D-Z-1890",
    ]
    for iid in ids:
        ds.individuals[iid] = LoadedIndividual(
            path=Path(f"{iid}.md"),
            data=IndividualFrontmatter(id=iid, primary_name=iid),
            body="",
        )

    edges = [
        # Siblings S1 & S2 from (F1, M1)
        ("I-A-F1-1800", "I-P-S1-1830"),
        ("I-A-M1-1800", "I-P-S1-1830"),
        ("I-A-F1-1800", "I-P-S2-1830"),
        ("I-A-M1-1800", "I-P-S2-1830"),
        # Siblings T1 & T2 from (F2, M2)
        ("I-B-F2-1800", "I-P-T1-1830"),
        ("I-B-M2-1800", "I-P-T1-1830"),
        ("I-B-F2-1800", "I-P-T2-1830"),
        ("I-B-M2-1800", "I-P-T2-1830"),
        # X is child of (S1, T1); Y is child of (S2, T2) -> double first cousins!
        ("I-P-S1-1830", "I-C-X-1860"),
        ("I-P-T1-1830", "I-C-X-1860"),
        ("I-P-S2-1830", "I-C-Y-1860"),
        ("I-P-T2-1830", "I-C-Y-1860"),
        # Z is child of double first cousins X & Y
        ("I-C-X-1860", "I-D-Z-1890"),
        ("I-C-Y-1860", "I-D-Z-1890"),
    ]
    ds.relationships = [
        Relationship(type="parent_child", person_1=p, person_2=c, subtype="biological")
        for p, c in edges
    ] + [Relationship(type="marriage", person_1="I-C-X-1860", person_2="I-C-Y-1860")]

    kin = compute_kinship(ds, "I-C-X-1860", "I-C-Y-1860")
    assert len(kin.paths) == 4
    assert kin.wright_relationship == pytest.approx(0.25, abs=1e-6)
    assert kin.expected_offspring_inbreeding == pytest.approx(0.125, abs=1e-6)

    col_z = compute_pedigree_collapse(ds, "I-D-Z-1890")
    assert col_z.inbreeding_coefficient == pytest.approx(0.125, abs=1e-6)
    assert len(col_z.multi_path_ancestors) == 4
    assert len(find_consanguineous_marriages(ds)) == 1


def test_research_gaps_and_family_report() -> None:
    gaps = compute_research_gaps(EXAMPLE_ROOT)
    claude_gap = next(r for r in gaps["census_gaps"] if r["individual_id"] == "I-WEST-CLAUDE-1886")
    # 1890 must be skipped; 1900 & 1910 covered; 1920-1950 missing
    assert 1890 not in claude_gap["missing_censuses"]
    assert claude_gap["covered_censuses"] == [1900, 1910]
    assert claude_gap["missing_censuses"] == [1920, 1930, 1940, 1950]

    assert len(gaps["consanguineous_marriages"]) == 1
    assert any(s["source_id"] == "S-1880-CENSUS-WEST-JOHN-DIST9" for s in gaps["excluded_sources"])

    # Family Group Sheet includes consanguinity, links, and excluded leads
    john_rep = build_family_report_markdown(EXAMPLE_ROOT, "I-WEST-JOHN-1858", redact_living=True)
    assert "S-1880-CENSUS-WEST-JOHN-DIST9" in john_rep
    assert "Excluded / Ruled-Out Leads" in john_rep

    claude_rep = build_family_report_markdown(EXAMPLE_ROOT, "I-WEST-CLAUDE-1886", redact_living=True)
    assert "Consanguineous" in claude_rep
    assert "ancestry" in claude_rep

    # Living individual is redacted by default
    jane_redacted = build_family_report_markdown(EXAMPLE_ROOT, "I-WEST-JANE-1950", redact_living=True)
    assert "Living (Redacted)" in jane_redacted
    jane_full = build_family_report_markdown(EXAMPLE_ROOT, "I-WEST-JANE-1950", redact_living=False)
    assert "Jane West" in jane_full
    assert "Pedigree Collapse" in jane_full


def test_land_tracts_and_dna_corroboration() -> None:
    tracts = compute_land_tracts(EXAMPLE_ROOT)
    assert tracts["tract_count"] == 1
    fb = tracts["tracts"][0]
    assert fb["tract_group"] == "French Broad River"
    assert fb["event_count"] == 4
    assert "I-WEST-THOMAS-1830" in fb["individuals_involved"]
    assert "I-WEST-JOHN-1858" in fb["individuals_involved"]
    assert "I-WEST-MARY-1860" in fb["individuals_involved"]

    filtered = compute_land_tracts(EXAMPLE_ROOT, tract_filter="East 60")
    assert filtered["tract_count"] == 1
    assert filtered["tracts"][0]["event_count"] == 1

    # Claude & Octa are 1st cousins (r = 0.125 -> expected ~850 cM)
    dna_ok = compute_dna_corroboration(
        EXAMPLE_ROOT, "I-WEST-CLAUDE-1886", "I-RAINWATER-OCTA-1890", observed_cm=860.0
    )
    assert dna_ok["expected_cm"] == pytest.approx(850.0, abs=1.0)
    assert dna_ok["assessment"] == "consistent"
    assert len(dna_ok["paths"]) == 2

    dna_high = compute_dna_corroboration(
        EXAMPLE_ROOT, "I-WEST-CLAUDE-1886", "I-RAINWATER-OCTA-1890", observed_cm=1650.0
    )
    assert dna_high["assessment"] == "higher_than_expected"

    dna_low = compute_dna_corroboration(
        EXAMPLE_ROOT, "I-WEST-CLAUDE-1886", "I-RAINWATER-OCTA-1890", observed_cm=120.0
    )
    assert dna_low["assessment"] == "lower_than_expected"

    # Unrelated founders (Thomas West & Sarah Miller) with observed DNA -> unexplained_match
    dna_unexp = compute_dna_corroboration(
        EXAMPLE_ROOT, "I-WEST-THOMAS-1830", "I-MILLER-SARAH-1834", observed_cm=150.0
    )
    assert dna_unexp["expected_cm"] == 0.0
    assert dna_unexp["assessment"] == "unexplained_match"


def test_marriage_investigation_gaps(tmp_path: Path) -> None:
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
                "jurisdiction": "Jalisco, Mexico",
                "individuals_cited": ["I-SANCHEZ-VICENTE-1807", "I-GONZALEZ-MARIA-1812"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "sources" / "S-1840-CHURCH-MATR.md").write_text(
        _fm(
            {
                "id": "S-1840-CHURCH-MATR",
                "title": "Matrimonio 1840",
                "record_type": "church",
                "jurisdiction": "Jalisco, Mexico",
                "individuals_cited": ["I-SANCHEZ-VICENTE-1807", "I-LOPEZ-JUANA-1820"],
            }
        ),
        encoding="utf-8",
    )

    # Vicente had two marriages:
    # 1) 1826 investigation with Maria (no ceremony found yet -> SHOULD flag)
    # 2) 1840 investigation + 1840 marriage with Juana (ceremony found -> should NOT flag)
    (tmp_path / "data" / "individuals" / "I-SANCHEZ-VICENTE-1807.md").write_text(
        _fm(
            {
                "id": "I-SANCHEZ-VICENTE-1807",
                "primary_name": "Vicente Sanchez",
                "sex": "M",
                "birth": {"date": "1807", "place": "Jalisco, Mexico", "sources": ["S-1826-CHURCH-INFO"]},
                "events": [
                    {
                        "type": "marriage_investigation",
                        "date": "1826-11-19",
                        "place": "Jalisco, Mexico",
                        "description": "Informacion Matrimonial with Maria Gonzalez",
                        "sources": ["S-1826-CHURCH-INFO"],
                    },
                    {
                        "type": "marriage_investigation",
                        "date": "1840-01-10",
                        "place": "Jalisco, Mexico",
                        "description": "Informacion Matrimonial with Juana Lopez",
                        "sources": ["S-1840-CHURCH-MATR"],
                    },
                    {
                        "type": "marriage",
                        "date": "1840-02-05",
                        "place": "Jalisco, Mexico",
                        "description": "Matrimonio with Juana Lopez",
                        "sources": ["S-1840-CHURCH-MATR"],
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
                "birth": {"date": "1812", "place": "Jalisco, Mexico", "sources": ["S-1826-CHURCH-INFO"]},
                "status": "tentative",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "individuals" / "I-LOPEZ-JUANA-1820.md").write_text(
        _fm(
            {
                "id": "I-LOPEZ-JUANA-1820",
                "primary_name": "Juana Lopez",
                "sex": "F",
                "birth": {"date": "1820", "place": "Jalisco, Mexico", "sources": ["S-1840-CHURCH-MATR"]},
                "status": "tentative",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "individuals" / "I-SOLO-PEDRO-1810.md").write_text(
        _fm(
            {
                "id": "I-SOLO-PEDRO-1810",
                "primary_name": "Pedro Solo",
                "sex": "M",
                "events": [
                    {
                        "type": "marriage_investigation",
                        "date": "1832-04-01",
                        "place": "Jalisco, Mexico",
                        "sources": ["S-1826-CHURCH-INFO"],
                    }
                ],
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
                    },
                    {
                        "type": "marriage",
                        "person_1": "I-SANCHEZ-VICENTE-1807",
                        "person_2": "I-LOPEZ-JUANA-1820",
                        "date": "1840-02-05",
                        "confidence": "proven",
                        "sources": ["S-1840-CHURCH-MATR", "S-1826-CHURCH-INFO"],
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    gaps = compute_research_gaps(tmp_path)
    mgaps = gaps["marriage_ceremony_gaps"]
    assert len(mgaps) == 2
    assert mgaps[0]["person_1"] == "I-SANCHEZ-VICENTE-1807"
    assert mgaps[0]["person_2"] == "I-GONZALEZ-MARIA-1812"
    assert mgaps[0]["investigation_date"] == "1826-11-19"
    assert "Partida de Matrimonio" in mgaps[0]["recommended_record"]

    assert mgaps[1]["person_1"] == "I-SOLO-PEDRO-1810"
    assert mgaps[1]["person_2"] is None
    assert mgaps[1]["investigation_date"] == "1832-04-01"
