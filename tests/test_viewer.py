"""Tests for the interactive DAG/marriage-hub viewer payload, static export, and HTTP server."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from genealogy.viewer import (
    build_viewer_payload,
    export_viewer_html,
    make_viewer_server,
)

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "west-family"


def test_viewer_payload_and_static_export(tmp_path: Path) -> None:
    payload = build_viewer_payload(EXAMPLE_ROOT, redact_living=True)
    assert payload["meta"]["counts"]["individuals"] == 9
    assert payload["meta"]["counts"]["consanguineous_marriages"] == 1
    assert payload["individuals"]["I-WEST-JANE-1950"]["redacted"] is True
    assert payload["individuals"]["I-WEST-JANE-1950"]["primary_name"] == "Living (Redacted)"
    assert payload["individuals"]["I-WEST-JOHN-1858"]["excluded_source_ids"] == [
        "S-1880-CENSUS-WEST-JOHN-DIST9"
    ]
    assert payload["individuals"]["I-WEST-CLAUDE-1886"]["covered_census_years"] == [1900, 1910]
    assert payload["meta"]["counts"]["tracts"] == 1
    assert payload["tracts"][0]["tract_group"] == "French Broad River"
    assert "1910 U.S. census" in payload["sources"]["S-1910-CENSUS-WEST-COCKE-TN"]["formal_citation"]
    assert any(h["consanguineous"] for h in payload["layout"]["hubs"])

    # Standalone HTML export
    out_html = export_viewer_html(EXAMPLE_ROOT, tmp_path / "tree.html", redact_living=True)
    html_text = out_html.read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light dark">' in html_text
    assert '<dialog id="artifact-dialog" closedby="any"' in html_text
    assert 'id="tract-filter"' in html_text
    assert 'id="era-slider"' in html_text
    assert 'id="btn-export-svg"' in html_text
    assert 'id="btn-expand-all"' in html_text
    assert 'id="layer-bands"' in html_text
    assert "hover-focus-active" in html_text
    assert "toggleBranchCollapse" in html_text
    assert "roundedOrthogonalPath" in html_text
    assert "alignParentsOverNode" in html_text
    assert "HOVER_ALIGN_DELAY_MS" in html_text
    assert 'id="toggle-compact-collateral"' in html_text
    assert "compact-collateral" in html_text
    assert 'id="btn-collapse-collaterals"' in html_text
    assert "toggleHubCollaterals" in html_text
    assert 'command: "--copy"' in html_text
    assert "STANDALONE_MODE = true" in html_text
    assert "Living (Redacted)" in html_text
    assert "Jane West" not in html_text


def test_layout_generation_alignment_and_couple_pairing() -> None:
    """Verify west-family inosculated layout aligns generations, pairs couples inline, and has zero overlap."""
    payload = build_viewer_payload(EXAMPLE_ROOT, redact_living=True)
    layout = payload["layout"]
    nodes = layout["nodes"]
    cw = layout["card_w"]

    # Exact biological generations: 0..4
    assert nodes["I-WEST-THOMAS-1830"]["generation"] == nodes["I-MILLER-SARAH-1834"]["generation"] == 0
    assert nodes["I-WEST-JOHN-1858"]["generation"] == nodes["I-WEST-MARY-1860"]["generation"] == 1
    assert nodes["I-RAINWATER-THOMAS-UNKNOWN"]["generation"] == 1
    assert nodes["I-WEST-CLAUDE-1886"]["generation"] == nodes["I-RAINWATER-OCTA-1890"]["generation"] == 2
    assert nodes["I-WEST-EDGAR-1909"]["generation"] == 3
    assert nodes["I-WEST-JANE-1950"]["generation"] == 4

    # Consanguineous couple Claude + Octa should be paired inline side-by-side
    consang_hubs = [h for h in layout["hubs"] if h["consanguineous"]]
    assert len(consang_hubs) == 1
    assert consang_hubs[0]["inline"] is True
    assert consang_hubs[0]["bus_y"] is not None

    # No two cards in the same row may overlap
    by_gen: dict[int, list[float]] = {}
    for pos in nodes.values():
        by_gen.setdefault(pos["generation"], []).append(pos["x"])
    for xs in by_gen.values():
        xs.sort()
        for a, b in zip(xs, xs[1:], strict=False):
            assert b - a >= cw + 20


def test_layout_uneven_branch_depths_and_cross_gen_inosculation(tmp_path: Path) -> None:
    """Spouses with different known ancestor depths must still sit on the same generation row."""
    from genealogy.loader import Dataset, LoadedIndividual
    from genealogy.schema import IndividualFrontmatter, Relationship

    def _ind(iid: str, name: str, sex: str = "U") -> LoadedIndividual:
        return LoadedIndividual(
            path=tmp_path / f"{iid}.md",
            data=IndividualFrontmatter(id=iid, primary_name=name, sex=sex),
            body="",
        )

    # Father has 3 generations of known ancestors; mother has only 1 known ancestor
    inds = {
        "I-DOE-CHILD-1980": _ind("I-DOE-CHILD-1980", "Child Doe", "M"),
        "I-DOE-DAD-1950": _ind("I-DOE-DAD-1950", "Dad Doe", "M"),
        "I-ROE-MOM-1952": _ind("I-ROE-MOM-1952", "Mom Roe", "F"),
        "I-DOE-PGF-1920": _ind("I-DOE-PGF-1920", "Paternal GF", "M"),
        "I-DOE-PGGF-1890": _ind("I-DOE-PGGF-1890", "Paternal GGF", "M"),
        "I-DOE-PGGGF-1860": _ind("I-DOE-PGGGF-1860", "Paternal GGGF", "M"),
        "I-ROE-MGF-1922": _ind("I-ROE-MGF-1922", "Maternal GF", "M"),
    }
    rels = [
        Relationship(type="marriage", person_1="I-DOE-DAD-1950", person_2="I-ROE-MOM-1952"),
        Relationship(type="parent_child", person_1="I-DOE-DAD-1950", person_2="I-DOE-CHILD-1980"),
        Relationship(type="parent_child", person_1="I-ROE-MOM-1952", person_2="I-DOE-CHILD-1980"),
        Relationship(type="parent_child", person_1="I-DOE-PGF-1920", person_2="I-DOE-DAD-1950"),
        Relationship(type="parent_child", person_1="I-DOE-PGGF-1890", person_2="I-DOE-PGF-1920"),
        Relationship(type="parent_child", person_1="I-DOE-PGGGF-1860", person_2="I-DOE-PGGF-1890"),
        Relationship(type="parent_child", person_1="I-ROE-MGF-1922", person_2="I-ROE-MOM-1952"),
    ]
    ds = Dataset(
        root=tmp_path,
        individuals=inds,
        sources={},
        relationships=rels,
        config={"focal_person": "I-DOE-CHILD-1980"},
    )
    payload = build_viewer_payload(ds, redact_living=False)
    nodes = payload["layout"]["nodes"]

    # Dad and Mom MUST be on the exact same generation row (1 row above Child)
    assert nodes["I-DOE-DAD-1950"]["generation"] == nodes["I-ROE-MOM-1952"]["generation"]
    assert nodes["I-DOE-CHILD-1980"]["generation"] == nodes["I-DOE-DAD-1950"]["generation"] + 1
    assert (
        nodes["I-DOE-PGF-1920"]["generation"]
        == nodes["I-ROE-MGF-1922"]["generation"]
        == nodes["I-DOE-DAD-1950"]["generation"] - 1
    )

    # Dad and Mom should be paired inline
    dad_mom_hub = [
        h for h in payload["layout"]["hubs"] if set(h["parents"]) == {"I-DOE-DAD-1950", "I-ROE-MOM-1952"}
    ]
    assert len(dad_mom_hub) == 1
    assert dad_mom_hub[0]["inline"] is True

    # Now add a cross-generation marriage (1st cousin once removed / uncle-niece style)
    # and verify parent < child hard DAG invariant still holds everywhere
    inds["I-DOE-UNCLE-1925"] = _ind("I-DOE-UNCLE-1925", "Uncle Doe", "M")
    inds["I-DOE-OFFSPRING-1975"] = _ind("I-DOE-OFFSPRING-1975", "Offspring Doe", "F")
    rels.extend(
        [
            Relationship(type="parent_child", person_1="I-DOE-PGGF-1890", person_2="I-DOE-UNCLE-1925"),
            Relationship(type="marriage", person_1="I-DOE-UNCLE-1925", person_2="I-ROE-MOM-1952"),
            Relationship(type="parent_child", person_1="I-DOE-UNCLE-1925", person_2="I-DOE-OFFSPRING-1975"),
            Relationship(type="parent_child", person_1="I-ROE-MOM-1952", person_2="I-DOE-OFFSPRING-1975"),
        ]
    )
    payload2 = build_viewer_payload(ds, redact_living=False)
    nodes2 = payload2["layout"]["nodes"]
    for r in rels:
        if r.type == "parent_child":
            assert nodes2[r.person_1]["generation"] < nodes2[r.person_2]["generation"]


def test_viewer_http_server_endpoints_and_traversal_guard() -> None:
    server = make_viewer_server(EXAMPLE_ROOT, host="127.0.0.1", port=0, redact_living=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        # 1. Index HTML
        with urllib.request.urlopen(f"{base}/") as resp:
            assert resp.status == 200
            assert "Genealogy DAG Viewer" in resp.read().decode("utf-8")

        # 2. Live graph JSON with redact toggle
        with urllib.request.urlopen(f"{base}/api/graph.json?redact_living=0") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["individuals"]["I-WEST-JANE-1950"]["primary_name"] == "Jane West"

        # 3. Live kinship endpoint
        with urllib.request.urlopen(
            f"{base}/api/kinship?a=I-WEST-CLAUDE-1886&b=I-RAINWATER-OCTA-1890"
        ) as resp:
            kin = json.loads(resp.read().decode("utf-8"))
            assert kin["wright_relationship"] == pytest.approx(0.125, abs=1e-5)
            assert kin["path_count"] == 2

        # 4. Live collapse endpoint
        with urllib.request.urlopen(f"{base}/api/collapse?id=I-WEST-EDGAR-1909") as resp:
            col = json.loads(resp.read().decode("utf-8"))
            assert col["inbreeding_coefficient"] == pytest.approx(0.0625, abs=1e-5)

        # 5. Legitimate local artifact served cleanly
        with urllib.request.urlopen(
            f"{base}/artifacts/1882_deed_west_french_broad_cocke_tn.txt"
        ) as resp:
            assert resp.status == 200
            assert "French Broad River" in resp.read().decode("utf-8")

        # 6. Directory traversal attempt blocked (403 or 404)
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base}/artifacts/%2e%2e/%2e%2e/README.md")
        assert exc_info.value.code in {403, 404}
    finally:
        server.shutdown()
        server.server_close()
