"""Tests for Hispanic/Mexican naming, regional gap profiles, LFS pointer validation, and colonial viewer bounds."""

from __future__ import annotations

from pathlib import Path

import yaml

from genealogy.analysis import compute_research_gaps, detect_individual_regions
from genealogy.gedcom import export_gedcom, import_gedcom
from genealogy.loader import load_dataset
from genealogy.schema import render_frontmatter
from genealogy.validator import validate_dataset
from genealogy.viewer import build_viewer_payload
from genealogy.workspace import init_workspace


def test_hispanic_double_surnames_and_accents_gedcom_roundtrip(tmp_path: Path) -> None:
    ged_in = tmp_path / "jalisco.ged"
    ged_in.write_text(
        "0 HEAD\n"
        "1 GEDC\n"
        "2 VERS 7.0\n"
        "0 @I1@ INDI\n"
        "1 NAME José María /García López/\n"
        "1 SEX M\n"
        "1 BIRT\n"
        "2 DATE 14 MAR 1870\n"
        "2 PLAC Lagos de Moreno, Jalisco, México\n"
        "0 @I2@ INDI\n"
        "1 NAME María Refugio /Peña Gómez/\n"
        "1 SEX F\n"
        "1 BIRT\n"
        "2 DATE ABT 1874\n"
        "2 PLAC Encarnación de Díaz, Jalisco, México\n"
        "0 @F1@ FAM\n"
        "1 HUSB @I1@\n"
        "1 WIFE @I2@\n"
        "1 MARR\n"
        "2 DATE 10 MAY 1894\n"
        "2 PLAC Lagos de Moreno, Jalisco, México\n"
        "0 TRLR\n",
        encoding="utf-8",
    )

    summary = import_gedcom(ged_in, tmp_path, dest="staging")
    ds = load_dataset(summary.destination)
    assert "I-GARCIA-JOSE-1870" in ds.individuals
    assert "I-PENA-MARIA-1874" in ds.individuals

    jose = ds.individuals["I-GARCIA-JOSE-1870"].data
    assert jose.primary_name == "José María García López"
    assert jose.given_name == "José María"
    assert jose.surname == "García"
    assert jose.maternal_surname == "López"

    # Re-exporting preserves the full double surname inside slashes (not just maternal surname!)
    ged_out = tmp_path / "out.ged"
    export_gedcom(summary.destination, ged_out, version="7.0")
    out_txt = ged_out.read_text(encoding="utf-8")
    assert "1 NAME José María /García López/" in out_txt
    assert "1 NAME María Refugio /Peña Gómez/" in out_txt


def test_mexican_region_detection_gaps_dispensa_and_colonial_viewer(tmp_path: Path) -> None:
    ind_dir = tmp_path / "data" / "individuals"
    src_dir = tmp_path / "data" / "sources"
    ind_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    # Colonial founder in 1680 + Mexican cousin couple + U.S. New Mexico control person
    (src_dir / "S-1895-CHURCH-GARCIA-BAUTISMO.md").write_text(
        render_frontmatter(
            {
                "id": "S-1895-CHURCH-GARCIA-BAUTISMO",
                "title": "Bautismo — Parroquia de la Asunción, Lagos de Moreno, Jalisco",
                "record_type": "church",
                "date": "1895-04-02",
                "jurisdiction": "Lagos de Moreno, Jalisco, México",
                "source_type": "original",
                "information_type": "primary",
                "individuals_cited": ["I-GARCIA-PEDRO-1870", "I-GARCIA-LUISA-1873"],
            },
            "## Formal Citation\nLagos de Moreno Bautismos 1895.\n",
        ),
        encoding="utf-8",
    )

    people = [
        {
            "id": "I-GARCIA-DIEGO-1680",
            "primary_name": "Diego García",
            "sex": "M",
            "birth": {"date": "abt 1680", "place": "Nueva Galicia, México", "sources": []},
            "status": "tentative",
        },
        {
            "id": "I-GARCIA-JUAN-1840",
            "primary_name": "Juan García",
            "sex": "M",
            "birth": {"date": "1840", "place": "Jalisco, México", "sources": []},
            "status": "tentative",
        },
        {
            "id": "I-GARCIA-ANTONIO-1842",
            "primary_name": "Antonio García",
            "sex": "M",
            "birth": {"date": "1842", "place": "Jalisco, México", "sources": []},
            "status": "tentative",
        },
        {
            "id": "I-GARCIA-PEDRO-1870",
            "primary_name": "Pedro García López",
            "given_name": "Pedro",
            "surname": "García",
            "maternal_surname": "López",
            "sex": "M",
            "birth": {
                "date": "1870",
                "place": "Lagos de Moreno, Jalisco, México",
                "sources": ["S-1895-CHURCH-GARCIA-BAUTISMO"],
            },
            "death": {
                "date": "1945",
                "place": "Lagos de Moreno, Jalisco, México",
                "sources": ["S-1895-CHURCH-GARCIA-BAUTISMO"],
            },
            "status": "tentative",
        },
        {
            "id": "I-GARCIA-LUISA-1873",
            "primary_name": "Luisa García Gómez",
            "sex": "F",
            "birth": {
                "date": "1873",
                "place": "Jalisco, México",
                "sources": ["S-1895-CHURCH-GARCIA-BAUTISMO"],
            },
            "status": "tentative",
        },
        {
            "id": "I-BACA-CARLOS-1880",
            "primary_name": "Carlos Baca",
            "sex": "M",
            "birth": {"date": "1880", "place": "Santa Fe, New Mexico, USA", "sources": []},
            "status": "tentative",
        },
    ]
    for p in people:
        (ind_dir / f"{p['id']}.md").write_text(render_frontmatter(p, "## Bio\n"), encoding="utf-8")

    (tmp_path / "data" / "relationships.yaml").write_text(
        yaml.safe_dump(
            {
                "relationships": [
                    {"type": "parent_child", "person_1": "I-GARCIA-DIEGO-1680", "person_2": "I-GARCIA-JUAN-1840"},
                    {"type": "parent_child", "person_1": "I-GARCIA-DIEGO-1680", "person_2": "I-GARCIA-ANTONIO-1842"},
                    {"type": "parent_child", "person_1": "I-GARCIA-JUAN-1840", "person_2": "I-GARCIA-PEDRO-1870"},
                    {"type": "parent_child", "person_1": "I-GARCIA-ANTONIO-1842", "person_2": "I-GARCIA-LUISA-1873"},
                    {"type": "marriage", "person_1": "I-GARCIA-PEDRO-1870", "person_2": "I-GARCIA-LUISA-1873", "date": "1894"},
                ]
            }
        ),
        encoding="utf-8",
    )

    ds = load_dataset(tmp_path)
    assert detect_individual_regions(ds, "I-GARCIA-PEDRO-1870") == ["mexico"]
    # New Mexico must NOT be misclassified as Mexico
    assert detect_individual_regions(ds, "I-BACA-CARLOS-1880") == ["us"]

    gaps = compute_research_gaps(ds)
    pedro_census = next(r for r in gaps["census_gaps"] if r["individual_id"] == "I-GARCIA-PEDRO-1870")
    # Mexican profile checks ONLY the 1930 Mexican National Census (never U.S. 1880/1900/1910!)
    assert pedro_census["missing_censuses"] == [1930]
    assert pedro_census["regions"] == ["mexico"]

    # Pedro has a church record but lacks post-1859 Mexican Registro Civil
    pedro_rec = next(r for r in gaps["record_gaps"] if r["individual_id"] == "I-GARCIA-PEDRO-1870")
    assert any("Registro Civil" in msg for msg in pedro_rec["missing_records"])

    # Consanguineous Mexican marriage gets a Dispensa Matrimonial recommendation
    assert len(gaps["consanguineous_marriages"]) == 1
    assert "Dispensas Matrimoniales" in gaps["consanguineous_marriages"][0]["recommended_record"]

    # Viewer expands dynamic era slider down to colonial 1680!
    payload = build_viewer_payload(ds)
    assert payload["meta"]["era_min"] == 1680
    assert payload["individuals"]["I-GARCIA-PEDRO-1870"]["applicable_census_years"] == [1930]


def test_validator_lfs_pointer_and_skip_artifacts(tmp_path: Path) -> None:
    expected_sha = "a" * 64
    art_dir = tmp_path / "artifacts"
    src_dir = tmp_path / "data" / "sources"
    art_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    # Simulate an unfetched Git LFS pointer file on disk
    lfs_file = art_dir / "1895_bautismo.pdf"
    lfs_file.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{expected_sha}\n"
        "size 142857\n",
        encoding="utf-8",
    )

    (src_dir / "S-1895-CHURCH-BAUTISMO.md").write_text(
        render_frontmatter(
            {
                "id": "S-1895-CHURCH-BAUTISMO",
                "title": "Bautismo 1895",
                "record_type": "church",
                "artifact_path": "artifacts/1895_bautismo.pdf",
                "artifact_sha256": expected_sha,
            },
            "## Citation\nTest.\n",
        ),
        encoding="utf-8",
    )

    # Validator reads `oid sha256:` from the LFS pointer and validates cleanly!
    rep = validate_dataset(tmp_path)
    assert rep.errors == []

    # Remove the artifact completely -> fails normally, passes with --skip-artifacts
    lfs_file.unlink()
    assert any(e.code == "MISSING_ARTIFACT" for e in validate_dataset(tmp_path).errors)
    assert validate_dataset(tmp_path, skip_artifacts=True).errors == []


def test_init_with_regions_and_lfs_ci_skip_artifacts(tmp_path: Path) -> None:
    target = tmp_path / "mx-family"
    init_workspace(target, git_init=False, use_lfs=True, regions=["mexico", "us"])
    cfg = yaml.safe_load((target / ".genealogy.yaml").read_text(encoding="utf-8"))
    assert cfg["regions"] == ["mexico", "us"]
    ci_txt = (target / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    assert "--skip-artifacts" in ci_txt
