"""Tests for relationship constraints schema, deductive reasoning, validation, DB, and CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from genealogy.analysis import build_family_report_markdown, compute_research_gaps
from genealogy.cli import main
from genealogy.constraints import evaluate_all_constraints
from genealogy.db import build_sqlite
from genealogy.schema import RelationshipConstraint, RelationshipsFile, render_frontmatter
from genealogy.validator import validate_dataset


def _write_individual(
    root: Path,
    iid: str,
    name: str,
    sex: str,
    birth_date: str | None = None,
    *,
    given_name: str | None = None,
    surname: str | None = None,
    sources: list[str] | None = None,
) -> None:
    meta: dict[str, object] = {
        "id": iid,
        "primary_name": name,
        "sex": sex,
        "status": "tentative",
    }
    if given_name:
        meta["given_name"] = given_name
    if surname:
        meta["surname"] = surname
    if birth_date:
        meta["birth"] = {
            "date": birth_date,
            "place": "Jalisco, Mexico",
            "sources": sources or ["S-1865-CHURCH-DISPENSA-JALISCO"],
        }
    p = root / "data" / "individuals" / f"{iid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_frontmatter(meta, f"## Summary\nProfile for {name}.\n"), encoding="utf-8")


def _write_source(root: Path, sid: str, title: str = "1865 Dispensa Matrimonial") -> None:
    meta = {
        "id": sid,
        "title": title,
        "record_type": "church",
        "date": "1865-05-10",
        "source_type": "original",
        "information_type": "primary",
        "relevance": "relevant",
    }
    p = root / "data" / "sources" / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_frontmatter(meta, "## Citation\nArchivo del Arzobispado.\n"), encoding="utf-8")


def _write_relationships(
    root: Path,
    edges: list[tuple[str, str]],
    constraints: list[dict[str, object]],
) -> None:
    rels = [
        {
            "type": "parent_child",
            "person_1": p1,
            "person_2": p2,
            "subtype": "biological",
            "confidence": "probable",
            "sources": ["S-1865-CHURCH-DISPENSA-JALISCO"],
        }
        for p1, p2 in edges
    ]
    payload = {"relationships": rels, "constraints": constraints}
    p = root / "data" / "relationships.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_constraint_schema_validation() -> None:
    rf = RelationshipsFile.model_validate({"relationships": []})
    assert rf.constraints == []

    c = RelationshipConstraint(
        person_1="I-LOPEZ-MARIA-1845",
        person_2="I-GARCIA-JOSE-1842",
        steps_1=2,
        steps_2=3,
        common_ancestor_sex="M",
        sources=["S-1865-CHURCH-DISPENSA-JALISCO"],
    )
    assert c.steps_1 == 2
    assert c.steps_2 == 3

    with pytest.raises(ValidationError):
        RelationshipConstraint(
            person_1="I-LOPEZ-MARIA-1845",
            person_2="I-LOPEZ-MARIA-1845",
            steps_1=2,
            steps_2=3,
        )

    with pytest.raises(ValidationError):
        RelationshipConstraint(
            person_1="I-LOPEZ-MARIA-1845",
            person_2="I-GARCIA-JOSE-1842",
            steps_1=0,
            steps_2=0,
        )


def test_narrowed_elimination_and_direct_parent_inference(tmp_path: Path) -> None:
    """Bride's 4 grandparents are all known; Groom is missing 1 great-grandfather slot.
    Sex + chronology eliminate 3 of Bride's grandparents, deducing the exact missing parent.
    """
    _write_source(tmp_path, "S-1865-CHURCH-DISPENSA-JALISCO")

    # Bride (1845), parents (1820), and all 4 grandparents
    _write_individual(tmp_path, "I-LOPEZ-MARIA-1845", "María López", "F", "1845")
    _write_individual(tmp_path, "I-LOPEZ-PEDRO-1820", "Pedro López", "M", "1820")
    _write_individual(tmp_path, "I-RUIZ-JUANA-1822", "Juana Ruiz", "F", "1822")

    # Bride's 4 grandparents:
    # - Juan López (M, 1775) -> chronologically compatible with being parent of Groom's GM (1800)
    # - Ana Pérez (F, 1795) -> ruled out by sex (missing slot is father)
    # - Carlos Ruiz (M, 1796) -> ruled out by chronology (born 1796, Groom's GM born 1800 -> <12 yrs)
    # - Elena Díaz (F, 1797) -> ruled out by sex
    _write_individual(tmp_path, "I-LOPEZ-JUAN-1775", "Juan López", "M", "1775", surname="López")
    _write_individual(tmp_path, "I-PEREZ-ANA-1795", "Ana Pérez", "F", "1795", surname="Pérez")
    _write_individual(tmp_path, "I-RUIZ-CARLOS-1796", "Carlos Ruiz", "M", "1796", surname="Ruiz")
    _write_individual(tmp_path, "I-DIAZ-ELENA-1797", "Elena Díaz", "F", "1797", surname="Díaz")

    # Groom (1842), parents (1820), 4 grandparents (1800), and 7 of 8 great-grandparents
    _write_individual(tmp_path, "I-GARCIA-JOSE-1842", "José García", "M", "1842")
    _write_individual(tmp_path, "I-GARCIA-LUIS-1820", "Luis García", "M", "1820")
    _write_individual(tmp_path, "I-SANCHEZ-ROSA-1822", "Rosa Sánchez", "F", "1822")

    _write_individual(tmp_path, "I-GARCIA-MANUEL-1798", "Manuel García", "M", "1798")
    _write_individual(tmp_path, "I-LOPEZ-CARMEN-1800", "Carmen López", "F", "1800", surname="López")
    _write_individual(tmp_path, "I-SANCHEZ-TOMAS-1799", "Tomás Sánchez", "M", "1799")
    _write_individual(tmp_path, "I-MORA-LUCIA-1801", "Lucía Mora", "F", "1801")

    # 7 known great-grandparents (Carmen López's father is missing!)
    _write_individual(tmp_path, "I-GARCIA-GGF-1770", "Mateo García", "M", "1770")
    _write_individual(tmp_path, "I-GARCIA-GGM-1772", "Sofía Vega", "F", "1772")
    _write_individual(tmp_path, "I-LOPEZ-GGM-1776", "Teresa Gómez", "F", "1776")
    _write_individual(tmp_path, "I-SANCHEZ-GGF-1771", "Diego Sánchez", "M", "1771")
    _write_individual(tmp_path, "I-SANCHEZ-GGM-1773", "Clara Gil", "F", "1773")
    _write_individual(tmp_path, "I-MORA-GGF-1774", "Pablo Mora", "M", "1774")
    _write_individual(tmp_path, "I-MORA-GGM-1776", "Inés Cruz", "F", "1776")

    edges = [
        # Bride pedigree
        ("I-LOPEZ-PEDRO-1820", "I-LOPEZ-MARIA-1845"),
        ("I-RUIZ-JUANA-1822", "I-LOPEZ-MARIA-1845"),
        ("I-LOPEZ-JUAN-1775", "I-LOPEZ-PEDRO-1820"),
        ("I-PEREZ-ANA-1795", "I-LOPEZ-PEDRO-1820"),
        ("I-RUIZ-CARLOS-1796", "I-RUIZ-JUANA-1822"),
        ("I-DIAZ-ELENA-1797", "I-RUIZ-JUANA-1822"),
        # Groom pedigree
        ("I-GARCIA-LUIS-1820", "I-GARCIA-JOSE-1842"),
        ("I-SANCHEZ-ROSA-1822", "I-GARCIA-JOSE-1842"),
        ("I-GARCIA-MANUEL-1798", "I-GARCIA-LUIS-1820"),
        ("I-LOPEZ-CARMEN-1800", "I-GARCIA-LUIS-1820"),
        ("I-SANCHEZ-TOMAS-1799", "I-SANCHEZ-ROSA-1822"),
        ("I-MORA-LUCIA-1801", "I-SANCHEZ-ROSA-1822"),
        ("I-GARCIA-GGF-1770", "I-GARCIA-MANUEL-1798"),
        ("I-GARCIA-GGM-1772", "I-GARCIA-MANUEL-1798"),
        # Only mother known for Carmen López; father is missing!
        ("I-LOPEZ-GGM-1776", "I-LOPEZ-CARMEN-1800"),
        ("I-SANCHEZ-GGF-1771", "I-SANCHEZ-TOMAS-1799"),
        ("I-SANCHEZ-GGM-1773", "I-SANCHEZ-TOMAS-1799"),
        ("I-MORA-GGF-1774", "I-MORA-LUCIA-1801"),
        ("I-MORA-GGM-1776", "I-MORA-LUCIA-1801"),
    ]
    constraints = [
        {
            "id": "RC-1865-DISPENSA",
            "type": "common_ancestor",
            "person_1": "I-LOPEZ-MARIA-1845",
            "person_2": "I-GARCIA-JOSE-1842",
            "steps_1": 2,
            "steps_2": 3,
            "confidence": "probable",
            "sources": ["S-1865-CHURCH-DISPENSA-JALISCO"],
            "notes": "Grandparent of bride is great-grandparent of groom.",
        }
    ]
    _write_relationships(tmp_path, edges, constraints)

    evals = evaluate_all_constraints(tmp_path)
    assert len(evals) == 1
    ev = evals[0]
    assert ev.status == "narrowed"
    assert ev.frontier_1.unknown_slots == 0
    assert ev.frontier_2.unknown_slots == 1
    assert len(ev.compatible_candidates) == 1
    assert ev.compatible_candidates[0].candidate_id == "I-LOPEZ-JUAN-1775"
    assert ev.compatible_candidates[0].surname_match is True
    assert len(ev.ruled_out_candidates) == 3

    codes = {d.code for d in ev.deductions}
    assert "UNIQUE_CANDIDATE" in codes
    assert "DIRECT_PARENT_INFERENCE" in codes
    direct = next(d for d in ev.deductions if d.code == "DIRECT_PARENT_INFERENCE")
    assert direct.inferred_parent == "I-LOPEZ-JUAN-1775"
    assert direct.inferred_child == "I-LOPEZ-CARMEN-1800"

    # Now add the inferred parent-child link and verify it becomes SATISFIED
    edges.append(("I-LOPEZ-JUAN-1775", "I-LOPEZ-CARMEN-1800"))
    _write_relationships(tmp_path, edges, constraints)
    evals_after = evaluate_all_constraints(tmp_path)
    assert evals_after[0].status == "satisfied"
    assert evals_after[0].witnesses == ["I-LOPEZ-JUAN-1775"]


def test_violated_constraint_and_validator_warning(tmp_path: Path) -> None:
    _write_source(tmp_path, "S-1865-CHURCH-DISPENSA-JALISCO")
    _write_individual(tmp_path, "I-A-CHILD-1850", "Child A", "F", "1850")
    _write_individual(tmp_path, "I-A-DAD-1820", "Dad A", "M", "1820")
    _write_individual(tmp_path, "I-A-MOM-1822", "Mom A", "F", "1822")
    _write_individual(tmp_path, "I-B-CHILD-1852", "Child B", "M", "1852")
    _write_individual(tmp_path, "I-B-DAD-1821", "Dad B", "M", "1821")
    _write_individual(tmp_path, "I-B-MOM-1823", "Mom B", "F", "1823")

    _write_relationships(
        tmp_path,
        [
            ("I-A-DAD-1820", "I-A-CHILD-1850"),
            ("I-A-MOM-1822", "I-A-CHILD-1850"),
            ("I-B-DAD-1821", "I-B-CHILD-1852"),
            ("I-B-MOM-1823", "I-B-CHILD-1852"),
        ],
        [
            {
                "id": "RC-SIBLINGS",
                "type": "common_ancestor",
                "person_1": "I-A-CHILD-1850",
                "person_2": "I-B-CHILD-1852",
                "steps_1": 1,
                "steps_2": 1,
                "sources": ["S-1865-CHURCH-DISPENSA-JALISCO"],
            }
        ],
    )

    evals = evaluate_all_constraints(tmp_path)
    assert evals[0].status == "violated"
    assert any(d.code == "CONTRADICTION" for d in evals[0].deductions)

    report = validate_dataset(tmp_path, skip_artifacts=True)
    assert any(w.code == "CONSTRAINT_VIOLATED" for w in report.warnings)


def test_symmetric_and_potential_identity_overlap(tmp_path: Path) -> None:
    _write_source(tmp_path, "S-1865-CHURCH-DISPENSA-JALISCO")
    _write_individual(tmp_path, "I-A-CHILD-1850", "Child A", "F", "1850")
    _write_individual(tmp_path, "I-A-DAD-1820", "Dad A", "M", "1820")
    _write_individual(tmp_path, "I-GARCIA-JUAN-1790", "Juan García", "M", "1790", given_name="Juan", surname="García")

    _write_individual(tmp_path, "I-B-CHILD-1852", "Child B", "M", "1852")
    _write_individual(tmp_path, "I-GARCIA-JUAN-1792", "Juan José García", "M", "1792", given_name="Juan José", surname="García")

    _write_relationships(
        tmp_path,
        [
            ("I-A-DAD-1820", "I-A-CHILD-1850"),
            ("I-GARCIA-JUAN-1790", "I-A-DAD-1820"),
            ("I-GARCIA-JUAN-1792", "I-B-CHILD-1852"),
        ],
        [
            {
                "id": "RC-SYM",
                "type": "consanguinity",
                "person_1": "I-A-CHILD-1850",
                "person_2": "I-B-CHILD-1852",
                "steps_1": 1,
                "steps_2": 2,
                "symmetric": True,
                "sources": ["S-1865-CHURCH-DISPENSA-JALISCO"],
            }
        ],
    )

    evals = evaluate_all_constraints(tmp_path)
    ev = evals[0]
    assert ev.status == "open"
    # Swapped (steps_1=2, steps_2=1) should be chosen because it exposes the matching depth pair
    assert len(ev.potential_identity_overlaps) == 1
    ov = ev.potential_identity_overlaps[0]
    assert ov.person_1_ancestor == "I-GARCIA-JUAN-1790"
    assert ov.person_2_ancestor == "I-GARCIA-JUAN-1792"


def test_db_gaps_report_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_source(tmp_path, "S-1865-CHURCH-DISPENSA-JALISCO")
    _write_individual(tmp_path, "I-LOPEZ-MARIA-1845", "María López", "F", "1845")
    _write_individual(tmp_path, "I-GARCIA-JOSE-1842", "José García", "M", "1842")
    _write_relationships(
        tmp_path,
        [],
        [
            {
                "id": "RC-001",
                "type": "common_ancestor",
                "person_1": "I-LOPEZ-MARIA-1845",
                "person_2": "I-GARCIA-JOSE-1842",
                "steps_1": 2,
                "steps_2": 3,
                "sources": ["S-1865-CHURCH-DISPENSA-JALISCO"],
            }
        ],
    )

    db_path = build_sqlite(tmp_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT constraint_id, steps_1, steps_2 FROM relationship_constraints").fetchall()
    conn.close()
    assert rows == [("RC-001", 2, 3)]

    gaps = compute_research_gaps(tmp_path)
    assert len(gaps["relationship_constraints"]) == 1
    assert gaps["relationship_constraints"][0]["constraint_id"] == "RC-001"

    md = build_family_report_markdown(tmp_path, "I-LOPEZ-MARIA-1845")
    assert "## Relationship Constraints & Deductions" in md
    assert "RC-001" in md

    rc = main(["constraints", "--root", str(tmp_path), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["constraint_id"] == "RC-001"
