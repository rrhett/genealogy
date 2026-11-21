"""Comprehensive tests for schema, referential, graph, biological, artifact, and GPS validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from genealogy.schema import render_frontmatter
from genealogy.validator import compute_sha256, validate_dataset

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "west-family"


def _write_ind(root: Path, meta: dict, body: str = "## Bio\n") -> Path:
    p = root / "data" / "individuals" / f"{meta['id']}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_frontmatter(meta, body), encoding="utf-8")
    return p


def _write_src(root: Path, meta: dict, body: str = "## Citation\n") -> Path:
    p = root / "data" / "sources" / f"{meta['id']}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_frontmatter(meta, body), encoding="utf-8")
    return p


def _write_rels(root: Path, rels: list[dict]) -> Path:
    p = root / "data" / "relationships.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"relationships": rels}), encoding="utf-8")
    return p


def test_example_dataset_validates_strictly() -> None:
    report = validate_dataset(EXAMPLE_ROOT)
    assert report.individuals_count == 9
    assert report.sources_count == 8
    assert report.relationships_count == 14
    assert report.errors == []
    assert report.warnings == []
    assert report.is_valid(strict=True)


def test_empty_starter_root_validates_cleanly(tmp_path: Path) -> None:
    from genealogy.workspace import init_workspace

    init_workspace(tmp_path, git_init=False)
    report = validate_dataset(tmp_path)
    assert report.errors == []
    assert report.warnings == []
    assert report.is_valid(strict=True)


def test_detects_missing_refs_and_hash_mismatch(tmp_path: Path) -> None:
    art = tmp_path / "artifacts" / "scan.txt"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text("original content", encoding="utf-8")
    unlinked = tmp_path / "artifacts" / "unlinked.txt"
    unlinked.write_text("orphan scan", encoding="utf-8")

    _write_src(
        tmp_path,
        {
            "id": "S-1900-CENSUS-TEST",
            "title": "1900 Census",
            "record_type": "census",
            "artifact_path": "artifacts/scan.txt",
            "artifact_sha256": "0" * 64,  # intentionally wrong
            "individuals_cited": ["I-GHOST-PERSON-1880"],
        },
    )
    _write_ind(
        tmp_path,
        {
            "id": "I-DOE-JANE-1880",
            "primary_name": "Jane Doe",
            "sex": "F",
            "living": True,
            "birth": {"date": "1880", "sources": ["S-1900-CENSUS-MISSING"]},
            "death": {"date": "1870", "sources": ["S-1900-CENSUS-TEST"]},
            "status": "confirmed",
        },
    )

    report = validate_dataset(tmp_path)
    error_codes = {e.code for e in report.errors}
    warn_codes = {w.code for w in report.warnings}

    assert "ARTIFACT_HASH_MISMATCH" in error_codes
    assert "MISSING_INDIVIDUAL_REF" in error_codes
    assert "MISSING_SOURCE_REF" in error_codes
    assert "LIVING_WITH_DEATH" in error_codes
    assert "DEATH_BEFORE_BIRTH" in error_codes
    assert "UNLINKED_ARTIFACT" in warn_codes


def test_detects_ancestry_cycle_and_biological_impossibilities(tmp_path: Path) -> None:
    art = tmp_path / "artifacts" / "rec.txt"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text("record", encoding="utf-8")
    sha = compute_sha256(art)

    _write_src(
        tmp_path,
        {
            "id": "S-1900-COMPILED-TREE",
            "title": "Compiled Tree",
            "record_type": "compiled",
            "source_type": "authored",
            "artifact_path": "artifacts/rec.txt",
            "artifact_sha256": sha,
            "individuals_cited": ["I-TEST-A-1850", "I-TEST-B-1840"],
        },
    )
    _write_ind(
        tmp_path,
        {
            "id": "I-TEST-A-1850",
            "primary_name": "Person A",
            "sex": "F",
            "birth": {"date": "1850", "sources": ["S-1900-COMPILED-TREE"]},
            "death": {"date": "1880", "sources": ["S-1900-COMPILED-TREE"]},
            "status": "confirmed",
        },
    )
    _write_ind(
        tmp_path,
        {
            "id": "I-TEST-B-1840",
            "primary_name": "Person B",
            "sex": "M",
            "birth": {"date": "1840", "sources": ["S-1900-COMPILED-TREE"]},
            "death": {"date": "1900", "sources": ["S-1900-COMPILED-TREE"]},
            "status": "tentative",
        },
    )
    # Create an impossible cycle A -> B -> A and marriage after death
    _write_rels(
        tmp_path,
        [
            {
                "type": "parent_child",
                "person_1": "I-TEST-A-1850",
                "person_2": "I-TEST-B-1840",
                "confidence": "proven",
                "sources": ["S-1900-COMPILED-TREE"],
            },
            {
                "type": "parent_child",
                "person_1": "I-TEST-B-1840",
                "person_2": "I-TEST-A-1850",
                "confidence": "probable",
                "sources": ["S-1900-COMPILED-TREE"],
            },
            {
                "type": "marriage",
                "person_1": "I-TEST-A-1850",
                "person_2": "I-TEST-B-1840",
                "date": "1890",
                "confidence": "speculative",
                "sources": ["S-1900-COMPILED-TREE"],
            },
        ],
    )

    report = validate_dataset(tmp_path)
    error_codes = {e.code for e in report.errors}
    warn_codes = {w.code for w in report.warnings}

    assert "ANCESTRY_CYCLE" in error_codes
    assert "CHILD_BEFORE_PARENT" in error_codes
    assert "MARRIAGE_AFTER_DEATH" in error_codes
    assert "GPS_CONFIRMED_UNDER_SOURCED" in warn_codes
    assert "GPS_PROVEN_REL_UNDER_SOURCED" in warn_codes


def test_excluded_source_guardrails(tmp_path: Path) -> None:
    _write_src(
        tmp_path,
        {
            "id": "S-1880-CENSUS-WRONG",
            "title": "Wrong John",
            "record_type": "census",
            "relevance": "excluded",
            "excluded_for": ["I-WEST-JOHN-1858"],
            # Missing exclusion_reason -> warning
            "individuals_cited": [],
        },
    )
    _write_ind(
        tmp_path,
        {
            "id": "I-WEST-JOHN-1858",
            "primary_name": "John West",
            "birth": {"date": "1858", "sources": ["S-1880-CENSUS-WRONG"]},
            "status": "tentative",
        },
    )
    report = validate_dataset(tmp_path)
    assert "EXCLUDED_SOURCE_CITED" in {e.code for e in report.errors}
    assert "MISSING_EXCLUSION_REASON" in {w.code for w in report.warnings}


def test_suggested_label_warning_on_non_other(tmp_path: Path) -> None:
    _write_src(
        tmp_path,
        {
            "id": "S-1826-CHURCH-INFO",
            "title": "Informacion Matrimonial",
            "record_type": "church",
            "individuals_cited": ["I-TEST-JUAN-1800"],
        },
    )
    _write_ind(
        tmp_path,
        {
            "id": "I-TEST-JUAN-1800",
            "primary_name": "Juan Test",
            "events": [
                {
                    "type": "other",
                    "date": "1812",
                    "description": "Confirmation",
                    "suggested_label": "confirmation",
                    "sources": ["S-1826-CHURCH-INFO"],
                },
                {
                    "type": "residence",
                    "date": "1820",
                    "suggested_label": "should_warn",
                    "sources": ["S-1826-CHURCH-INFO"],
                },
            ],
            "status": "tentative",
        },
    )
    report = validate_dataset(tmp_path)
    warn_codes = [w.code for w in report.warnings]
    assert warn_codes.count("SUGGESTED_LABEL_ON_NON_OTHER") == 1
