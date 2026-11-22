"""Tests for data/places/<ID>.md schema, validation, SQLite indexing, CLI, and viewer."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.request
from pathlib import Path

from genealogy.analysis import compute_place_profile, list_places_summary
from genealogy.cli import main as cli_main
from genealogy.db import build_sqlite, search_fts
from genealogy.loader import load_dataset
from genealogy.validator import validate_dataset
from genealogy.viewer import build_viewer_payload, make_viewer_server, render_viewer_html


def _write_md(path: Path, frontmatter: str, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter.strip()}\n---\n{body}\n", encoding="utf-8")


def _build_sample_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "individuals").mkdir(parents=True)
    (root / "data" / "sources").mkdir(parents=True)
    (root / "data" / "places").mkdir(parents=True)

    _write_md(
        root / "data" / "sources" / "S-1860-CHURCH-EL-PALO.md",
        """
id: S-1860-CHURCH-EL-PALO
title: "1860 Parish Baptismal Register, Rancho El Palo"
record_type: church
date: "1860"
jurisdiction: "Parroquia de San Pedro, Jalisco"
individuals_cited:
  - I-GARCIA-JOSE-1830
  - I-GARCIA-JUAN-1860
  - I-GARCIA-MARIA-1862
  - I-PEREZ-CARLOS-1840
""",
    )
    _write_md(
        root / "data" / "sources" / "S-1890-CIVIL-MARRIAGE.md",
        """
id: S-1890-CIVIL-MARRIAGE
title: "1890 Civil Marriage, El Palo"
record_type: vital
date: "1890"
individuals_cited:
  - I-GARCIA-JUAN-1860
  - I-GARCIA-MARIA-1862
  - I-GARCIA-PEDRO-1890
  - I-PEREZ-CARLOS-1840
""",
    )

    # Direct ancestor (grandfather)
    _write_md(
        root / "data" / "individuals" / "I-GARCIA-JOSE-1830.md",
        """
id: I-GARCIA-JOSE-1830
primary_name: "José García"
sex: M
birth:
  date: "1830"
  place: "Rancho El Palo, Jalisco"
  sources: ["S-1860-CHURCH-EL-PALO"]
status: tentative
""",
    )
    # Parent
    _write_md(
        root / "data" / "individuals" / "I-GARCIA-JUAN-1860.md",
        """
id: I-GARCIA-JUAN-1860
primary_name: "Juan García"
sex: M
birth:
  date: "1860"
  place: "El Palo"
  sources: ["S-1860-CHURCH-EL-PALO"]
status: tentative
""",
    )
    # Aunt (collateral to focal)
    _write_md(
        root / "data" / "individuals" / "I-GARCIA-MARIA-1862.md",
        """
id: I-GARCIA-MARIA-1862
primary_name: "María García"
sex: F
events:
  - type: residence
    date: "1890"
    place: "El Palo, Jalisco"
    sources: ["S-1890-CIVIL-MARRIAGE"]
status: tentative
""",
    )
    # Focal person
    _write_md(
        root / "data" / "individuals" / "I-GARCIA-PEDRO-1890.md",
        """
id: I-GARCIA-PEDRO-1890
primary_name: "Pedro García"
sex: M
birth:
  date: "1890"
  place: "Rancho El Palo"
  sources: ["S-1890-CIVIL-MARRIAGE"]
status: tentative
""",
    )
    # Unrelated FAN neighbor / owner (linked via place_id even with non-matching text)
    _write_md(
        root / "data" / "individuals" / "I-PEREZ-CARLOS-1840.md",
        """
id: I-PEREZ-CARLOS-1840
primary_name: "Carlos Pérez"
sex: M
events:
  - type: land
    date: "1865"
    place: "Hacienda Property"
    place_id: P-MX-JAL-EL-PALO
    description: "Owner of estate"
    sources: ["S-1860-CHURCH-EL-PALO"]
status: tentative
""",
    )

    (root / "data" / "relationships.yaml").write_text(
        """
relationships:
  - type: parent_child
    person_1: I-GARCIA-JOSE-1830
    person_2: I-GARCIA-JUAN-1860
    subtype: biological
    confidence: probable
    sources: ["S-1860-CHURCH-EL-PALO"]
  - type: parent_child
    person_1: I-GARCIA-JOSE-1830
    person_2: I-GARCIA-MARIA-1862
    subtype: biological
    confidence: probable
    sources: ["S-1860-CHURCH-EL-PALO"]
  - type: parent_child
    person_1: I-GARCIA-JUAN-1860
    person_2: I-GARCIA-PEDRO-1890
    subtype: biological
    confidence: probable
    sources: ["S-1890-CIVIL-MARRIAGE"]
  - type: fan_associate
    person_1: I-GARCIA-PEDRO-1890
    person_2: I-PEREZ-CARLOS-1840
    place: "Rancho El Palo"
    confidence: probable
    sources: ["S-1890-CIVIL-MARRIAGE"]
    notes: "Compadre / neighbor"
""",
        encoding="utf-8",
    )

    _write_md(
        root / "data" / "places" / "P-MX-JAL-EL-PALO.md",
        """
id: P-MX-JAL-EL-PALO
name: "Rancho El Palo"
aliases: ["El Palo"]
place_type: rancho
jurisdiction_history:
  - period: "1821-1867"
    civil: "Cantón de Guadalajara, Jalisco"
    ecclesiastical: "Parroquia de San Pedro"
    sources: ["S-1860-CHURCH-EL-PALO"]
people:
  - name: "Carlos Pérez"
    individual_id: I-PEREZ-CARLOS-1840
    period: "1860-1880"
    role: "dueño"
    sources: ["S-1860-CHURCH-EL-PALO"]
  - name: "María García"
    individual_id: I-GARCIA-MARIA-1862
    period: "1880-1895"
    role: "resident"
    sources: ["S-1890-CIVIL-MARRIAGE"]
sources: ["S-1860-CHURCH-EL-PALO"]
status: tentative
""",
        body="## Community Notes\nMulti-family rural rancho.\n",
    )
    return root


def test_place_schema_loader_and_validator(tmp_path: Path) -> None:
    root = _build_sample_workspace(tmp_path)
    ds = load_dataset(root)
    assert "P-MX-JAL-EL-PALO" in ds.places
    assert ds.load_errors == []

    rep = validate_dataset(root, skip_artifacts=True)
    assert rep.is_valid(strict=True)
    assert rep.places_count == 1

    # Add invalid place referencing missing source and missing person individual, plus invalid place_id on individual
    _write_md(
        root / "data" / "places" / "P-BAD-PLACE.md",
        """
id: P-BAD-PLACE
name: "Bad Place"
people:
  - name: "Ghost"
    individual_id: I-GHOST-NONE-1800
    role: "laborer"
sources: ["S-1900-MISSING-SOURCE"]
""",
    )
    _write_md(
        root / "data" / "individuals" / "I-BAD-PLACE-REF-1850.md",
        """
id: I-BAD-PLACE-REF-1850
primary_name: "Bad Place Ref"
sex: M
birth:
  date: "1850"
  place: "Nowhere"
  place_id: P-DOES-NOT-EXIST
  sources: ["S-1860-CHURCH-EL-PALO"]
status: tentative
""",
    )
    rep2 = validate_dataset(root, skip_artifacts=True)
    codes = {i.code for i in rep2.errors}
    assert "MISSING_SOURCE_REF" in codes
    assert "MISSING_INDIVIDUAL_REF" in codes
    assert "MISSING_PLACE_REF" in codes


def test_place_db_and_fts(tmp_path: Path) -> None:
    root = _build_sample_workspace(tmp_path)
    db_path = build_sqlite(root)
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT id, name, place_type FROM places").fetchall()
    conn.close()
    assert rows == [("P-MX-JAL-EL-PALO", "Rancho El Palo", "rancho")]

    hits = search_fts(root, "Guadalajara")
    assert any(h["entity_type"] == "place" and h["entity_id"] == "P-MX-JAL-EL-PALO" for h in hits)


def test_place_profile_aggregation_and_kinship_overlay(tmp_path: Path) -> None:
    root = _build_sample_workspace(tmp_path)
    summary = list_places_summary(root)
    assert summary["place_count"] == 1
    assert summary["places"][0]["individual_count"] == 5

    prof = compute_place_profile(root, "P-MX-JAL-EL-PALO", root_id="I-GARCIA-PEDRO-1890")
    by_id = {ind["id"]: ind for ind in prof["individuals"]}
    assert by_id["I-GARCIA-PEDRO-1890"]["kinship"]["category"] == "self"
    assert by_id["I-GARCIA-JUAN-1860"]["kinship"]["category"] == "direct_ancestor"
    assert by_id["I-GARCIA-JOSE-1830"]["kinship"]["category"] == "direct_ancestor"
    assert by_id["I-GARCIA-MARIA-1862"]["kinship"]["category"] == "collateral"
    assert by_id["I-PEREZ-CARLOS-1840"]["kinship"]["category"] == "fan_or_unrelated"
    assert len(prof["fan_links"]) == 1
    assert len(prof["sources"]) == 2


def test_cli_places_json(tmp_path: Path, capsys) -> None:
    root = _build_sample_workspace(tmp_path)
    rc = cli_main(["places", "P-MX-JAL-EL-PALO", "--root", str(root), "--focal", "I-GARCIA-PEDRO-1890", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "P-MX-JAL-EL-PALO"
    assert out["root_id"] == "I-GARCIA-PEDRO-1890"
    assert len(out["individuals"]) == 5


def test_viewer_places_endpoints_and_html(tmp_path: Path) -> None:
    root = _build_sample_workspace(tmp_path)
    payload = build_viewer_payload(root)
    assert payload["meta"]["counts"]["places"] == 1
    assert len(payload["places"]) == 1

    html = render_viewer_html(root, standalone=True)
    assert 'id="place-filter"' in html

    server = make_viewer_server(root, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/places") as resp:
            summary = json.loads(resp.read().decode("utf-8"))
        assert summary["place_count"] == 1

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/place?id=P-MX-JAL-EL-PALO&root=I-GARCIA-PEDRO-1890"
        ) as resp:
            prof = json.loads(resp.read().decode("utf-8"))
        assert prof["root_id"] == "I-GARCIA-PEDRO-1890"
        by_id = {ind["id"]: ind for ind in prof["individuals"]}
        assert by_id["I-GARCIA-MARIA-1862"]["kinship"]["category"] == "collateral"
    finally:
        server.shutdown()
        server.server_close()
