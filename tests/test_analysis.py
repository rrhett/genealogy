"""Tests for kinship path analysis, pedigree collapse, Malecot/Wright math, gaps, and reports."""

from __future__ import annotations

from pathlib import Path

import pytest

from genealogy.analysis import (
    build_family_report_markdown,
    compute_kinship,
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
