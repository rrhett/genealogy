"""Tests for the interactive DAG/marriage-hub viewer payload, static export, and HTTP server."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from genealogy.viewer import (
    _build_living_redactor,
    _compute_dag_layout,
    _fast_pair_cost_int,
    _pair_cross_diff,
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
    assert "roundedMultiGenPath" in html_text
    assert "INITIAL_VIEWER_STATE = {}" in html_text
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

    # Multi-generation jump (if any edge skips >= 1 row) must get a clear route_x avoiding intermediate cards
    cw = payload2["layout"]["card_w"]
    for edge in payload2["layout"]["edges"]:
        if edge["kind"] == "hub_to_child" and "route_x" in edge:
            rx = edge["route_x"]
            child_gen = nodes2[edge["to"]]["generation"]
            parent_gen = max(nodes2[p]["generation"] for p in edge["parents"])
            for pos in nodes2.values():
                if parent_gen < pos["generation"] < child_gen:
                    assert abs(rx - pos["x"]) >= cw / 2.0 + 4.0

    # Even without focal_person configured, relative generation labels are computed from bottom-most row
    ds_no_focal = Dataset(root=tmp_path, individuals=inds, sources={}, relationships=rels, config={})
    payload3 = build_viewer_payload(ds_no_focal, redact_living=False)
    gen_labels = [g["label"] for g in payload3["layout"]["generations"]]
    assert "Focal / Self" in gen_labels
    assert "Parents (+1)" in gen_labels


def test_layout_coupled_inversion_zero_crossings(tmp_path: Path) -> None:
    """Coupled 2-layer inversion (4 couples -> 2 couples -> 1 couple) must untangle to 0 crossings."""
    from genealogy.loader import Dataset, LoadedIndividual
    from genealogy.schema import IndividualFrontmatter, Relationship

    def _ind(iid: str, name: str, sex: str = "U") -> LoadedIndividual:
        return LoadedIndividual(
            path=tmp_path / f"{iid}.md",
            data=IndividualFrontmatter(id=iid, primary_name=name, sex=sex),
            body="",
        )

    # Gen 0: Left-branch parents (1750) vs Right-branch parents (1760)
    # Gen 1: 4 couples — Left-branch son (L1, L2) -> Child B; Right-branch son (R1, R2) -> Child A;
    #        plus two unconstrained leaf in-law couples (IL1/IL2 -> SpouseA, IR1/IR2 -> SpouseB)
    # Gen 2: Couple A [SpouseA (F), ChildA (M)] and Couple B [ChildB (M), SpouseB (F)]
    # Gen 3: Consanguineous couple [SonOfA (M), DaughterOfB (F)] -> Grandchild
    inds = {
        "I-ANC-LEFT-1750": _ind("I-ANC-LEFT-1750", "Anc Left", "M"),
        "I-ANC-RIGHT-1760": _ind("I-ANC-RIGHT-1760", "Anc Right", "M"),
        "I-PAR-LB-1780": _ind("I-PAR-LB-1780", "Par LB", "M"),
        "I-PAR-LBW-1782": _ind("I-PAR-LBW-1782", "Par LBW", "F"),
        "I-PAR-IL-1785": _ind("I-PAR-IL-1785", "Par IL", "M"),
        "I-PAR-ILW-1787": _ind("I-PAR-ILW-1787", "Par ILW", "F"),
        "I-PAR-RA-1790": _ind("I-PAR-RA-1790", "Par RA", "M"),
        "I-PAR-RAW-1792": _ind("I-PAR-RAW-1792", "Par RAW", "F"),
        "I-PAR-IR-1795": _ind("I-PAR-IR-1795", "Par IR", "M"),
        "I-PAR-IRW-1797": _ind("I-PAR-IRW-1797", "Par IRW", "F"),
        "I-GEN2-A-1820": _ind("I-GEN2-A-1820", "Andres", "M"),
        "I-GEN2-AW-1822": _ind("I-GEN2-AW-1822", "Juliana", "F"),
        "I-GEN2-B-1825": _ind("I-GEN2-B-1825", "Rafael", "M"),
        "I-GEN2-BW-1827": _ind("I-GEN2-BW-1827", "Francisca", "F"),
        "I-GEN3-M-1850": _ind("I-GEN3-M-1850", "Mariano", "M"),
        "I-GEN3-F-1852": _ind("I-GEN3-F-1852", "Juana", "F"),
        "I-GEN4-KID-1880": _ind("I-GEN4-KID-1880", "Child", "M"),
    }
    rels = [
        Relationship(type="parent_child", person_1="I-ANC-LEFT-1750", person_2="I-PAR-LB-1780"),
        Relationship(type="parent_child", person_1="I-ANC-RIGHT-1760", person_2="I-PAR-RA-1790"),
        Relationship(type="marriage", person_1="I-PAR-LB-1780", person_2="I-PAR-LBW-1782"),
        Relationship(type="marriage", person_1="I-PAR-IL-1785", person_2="I-PAR-ILW-1787"),
        Relationship(type="marriage", person_1="I-PAR-RA-1790", person_2="I-PAR-RAW-1792"),
        Relationship(type="marriage", person_1="I-PAR-IR-1795", person_2="I-PAR-IRW-1797"),
        Relationship(type="parent_child", person_1="I-PAR-LB-1780", person_2="I-GEN2-B-1825"),
        Relationship(type="parent_child", person_1="I-PAR-LBW-1782", person_2="I-GEN2-B-1825"),
        Relationship(type="parent_child", person_1="I-PAR-IL-1785", person_2="I-GEN2-AW-1822"),
        Relationship(type="parent_child", person_1="I-PAR-ILW-1787", person_2="I-GEN2-AW-1822"),
        Relationship(type="parent_child", person_1="I-PAR-RA-1790", person_2="I-GEN2-A-1820"),
        Relationship(type="parent_child", person_1="I-PAR-RAW-1792", person_2="I-GEN2-A-1820"),
        Relationship(type="parent_child", person_1="I-PAR-IR-1795", person_2="I-GEN2-BW-1827"),
        Relationship(type="parent_child", person_1="I-PAR-IRW-1797", person_2="I-GEN2-BW-1827"),
        Relationship(type="marriage", person_1="I-GEN2-A-1820", person_2="I-GEN2-AW-1822"),
        Relationship(type="marriage", person_1="I-GEN2-B-1825", person_2="I-GEN2-BW-1827"),
        Relationship(type="parent_child", person_1="I-GEN2-A-1820", person_2="I-GEN3-M-1850"),
        Relationship(type="parent_child", person_1="I-GEN2-AW-1822", person_2="I-GEN3-M-1850"),
        Relationship(type="parent_child", person_1="I-GEN2-B-1825", person_2="I-GEN3-F-1852"),
        Relationship(type="parent_child", person_1="I-GEN2-BW-1827", person_2="I-GEN3-F-1852"),
        Relationship(type="marriage", person_1="I-GEN3-M-1850", person_2="I-GEN3-F-1852"),
        Relationship(type="parent_child", person_1="I-GEN3-M-1850", person_2="I-GEN4-KID-1880"),
        Relationship(type="parent_child", person_1="I-GEN3-F-1852", person_2="I-GEN4-KID-1880"),
    ]
    ds = Dataset(root=tmp_path, individuals=inds, sources={}, relationships=rels, config={"focal_person": "I-GEN4-KID-1880"})
    payload = build_viewer_payload(ds, redact_living=False)
    nodes = payload["layout"]["nodes"]

    # Check exact edge crossings between every consecutive pair of generations using hub X -> child X
    hubs_by_id = {h["id"]: h for h in payload["layout"]["hubs"]}
    edges_by_gen: dict[int, list[tuple[float, float]]] = {}
    for e in payload["layout"]["edges"]:
        if e["kind"] == "hub_to_child":
            h = hubs_by_id[e["from"]]
            c = nodes[e["to"]]
            edges_by_gen.setdefault(c["generation"], []).append((h["x"], c["x"]))
    for gen, segs in edges_by_gen.items():
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                x1, y1 = segs[i]
                x2, y2 = segs[j]
                assert (x1 - x2) * (y1 - y2) >= 0, f"Crossing detected entering gen {gen}: {segs[i]} vs {segs[j]}"


def test_viewer_state_persistence_round_trip(tmp_path: Path) -> None:
    """Viewer options & layout state persist to <root>/build/viewer-state.json across restarts."""
    import shutil

    ws = tmp_path / "west-copy"
    shutil.copytree(EXAMPLE_ROOT, ws)

    server = make_viewer_server(ws, host="127.0.0.1", port=0, redact_living=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        # Initially empty
        with urllib.request.urlopen(f"{base}/api/viewer-state") as resp:
            assert json.loads(resp.read().decode("utf-8")) == {}

        # POST viewer state (including an unknown key that should be stripped)
        payload = {
            "version": 1,
            "compactCollateral": False,
            "showFan": False,
            "collapsedAncestors": ["I-WEST-EDGAR-1909"],
            "collapsedCollaterals": ["I-MILLER-SARAH-1834|I-WEST-THOMAS-1830"],
            "rowAlignments": {"3": "I-WEST-EDGAR-1909"},
            "viewport": {"tx": 120.5, "ty": -40.0, "scale": 0.85},
            "unexpectedKey": "should-be-dropped",
        }
        req = urllib.request.Request(
            f"{base}/api/viewer-state",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            saved = json.loads(resp.read().decode("utf-8"))
            assert saved["compactCollateral"] is False
            assert saved["collapsedAncestors"] == ["I-WEST-EDGAR-1909"]
            assert "unexpectedKey" not in saved

        # Verify file on disk in build/viewer-state.json
        state_file = ws / "build" / "viewer-state.json"
        assert state_file.is_file()
        on_disk = json.loads(state_file.read_text(encoding="utf-8"))
        assert on_disk["viewport"] == {"tx": 120.5, "ty": -40.0, "scale": 0.85}

        # GET /api/viewer-state and GET / both reflect saved state
        with urllib.request.urlopen(f"{base}/api/viewer-state") as resp:
            assert json.loads(resp.read().decode("utf-8")) == on_disk
        with urllib.request.urlopen(f"{base}/") as resp:
            html = resp.read().decode("utf-8")
            assert '"collapsedAncestors": ["I-WEST-EDGAR-1909"]' in html
    finally:
        server.shutdown()
        server.server_close()

    # Standalone export must NOT leak local build/viewer-state.json
    out_html = export_viewer_html(ws, tmp_path / "standalone.html", redact_living=True)
    assert "INITIAL_VIEWER_STATE = {}" in out_html.read_text(encoding="utf-8")


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


def test_fast_crossing_helpers_match_bruteforce() -> None:
    """Verify Fenwick cost and two-pointer swap delta match brute-force crossing+span math."""
    import random

    def _brute_pair_cost(edges_uv: list[tuple[float, float]]) -> float:
        m = len(edges_uv)
        if m <= 1:
            return sum((u - v) ** 2 for u, v in edges_uv) * 0.05
        crossings = 0
        span = 0.0
        for i in range(m):
            u1, v1 = edges_uv[i]
            span += (u1 - v1) ** 2
            for j in range(i + 1, m):
                u2, v2 = edges_uv[j]
                if (u1 - u2) * (v1 - v2) < 0:
                    crossings += 1
        return crossings * 100.0 + span * 0.05

    rng = random.Random(1234)
    for _ in range(40):
        groups2 = [
            sorted(rng.choice([0, 2, 4, 5, 7, 10, 13]) for _ in range(rng.randint(0, 4)))
            for _ in range(12)
        ]
        lxs2 = [i * 3 for i in range(12)]
        edges_up = [(u2 * 0.5, lx2 * 0.5) for us2, lx2 in zip(groups2, lxs2, strict=True) for u2 in us2]
        assert _fast_pair_cost_int(groups2, lxs2) == pytest.approx(_brute_pair_cost(edges_up), abs=1e-9)

        # Verify _pair_cross_diff matches exact crossing change when swapping two adjacent nodes
        i = rng.randrange(len(groups2) - 1)
        before = _fast_pair_cost_int(groups2, lxs2)
        g_swapped = list(groups2)
        g_swapped[i], g_swapped[i + 1] = g_swapped[i + 1], g_swapped[i]
        after = _fast_pair_cost_int(g_swapped, lxs2)
        # Subtract exact span difference so only crossing delta remains
        span_before = sum((u2 - lxs2[k]) ** 2 for k in (i, i + 1) for u2 in groups2[k]) * 0.0125
        span_after = sum((u2 - lxs2[k]) ** 2 for k in (i, i + 1) for u2 in g_swapped[k]) * 0.0125
        exact_dcross = round(((after - before) - (span_after - span_before)) / 100.0)
        assert _pair_cross_diff(groups2[i], groups2[i + 1]) == exact_dcross


def test_living_redactor_trie_edge_cases(tmp_path: Path) -> None:
    """Trie-compiled living redactor handles overlapping prefixes, punctuation, and accents."""
    from genealogy.loader import Dataset, LoadedIndividual
    from genealogy.schema import IndividualFrontmatter

    ds = Dataset(
        root=tmp_path,
        individuals={
            "I-WEST-JANE-1980": LoadedIndividual(
                path=tmp_path / "jane.md",
                data=IndividualFrontmatter(
                    id="I-WEST-JANE-1980",
                    primary_name="Jane West",
                    name_variants=["Jane", "Jane West-Miller", "J. [Nick]+1"],
                    living=True,
                ),
                body="",
            ),
            "I-GARCIA-ANA-1990": LoadedIndividual(
                path=tmp_path / "ana.md",
                data=IndividualFrontmatter(
                    id="I-GARCIA-ANA-1990",
                    primary_name="Ana María",
                    name_variants=["Ana"],
                    living=True,
                ),
                body="",
            ),
        },
    )
    scrub = _build_living_redactor(ds)
    sample = "Met JANE WEST-MILLER, Jane West, jane, J. [Nick]+1, and ana maría / ANA yesterday."
    redacted = scrub(sample)
    assert redacted == "Met [Living], [Living], [Living], [Living], and [Living] / [Living] yesterday."


def test_large_endogamous_dag_layout_invariants_and_speed(tmp_path: Path) -> None:
    """240+ person endogamous DAG lays out cleanly with zero card overlaps and valid gen order."""
    import random
    import time

    from genealogy.loader import Dataset, LoadedIndividual
    from genealogy.schema import IndividualFrontmatter, Relationship, VitalEvent

    rng = random.Random(42)
    inds: dict[str, LoadedIndividual] = {}
    rels: list[Relationship] = []
    gen_couples: list[list[tuple[str, str]]] = []
    idx = 0
    for g in range(8):
        year = 1720 + g * 25
        row: list[tuple[str, str]] = []
        for c in range(12):
            idx += 1
            m_id = f"I-FAM{c % 8}-M{idx}-{year}"
            idx += 1
            f_id = f"I-FAM{(c + 2) % 8}-F{idx}-{year + 2}"
            for pid, sex, yr in ((m_id, "M", year), (f_id, "F", year + 2)):
                inds[pid] = LoadedIndividual(
                    path=tmp_path / f"{pid}.md",
                    data=IndividualFrontmatter(
                        id=pid,
                        primary_name=f"Person {pid}",
                        sex=sex,
                        birth=VitalEvent(date=str(yr)),
                    ),
                    body="",
                )
            rels.append(Relationship(type="marriage", person_1=m_id, person_2=f_id, date=str(year + 22)))
            row.append((m_id, f_id))
        gen_couples.append(row)
        if g > 0:
            prev = gen_couples[g - 1]
            for ci, (m_id, f_id) in enumerate(row):
                pm, pf = prev[ci % len(prev)]
                rels.append(Relationship(type="parent_child", person_1=pm, person_2=m_id))
                rels.append(Relationship(type="parent_child", person_1=pf, person_2=m_id))
                w_offset = rng.randint(1, 3) if rng.random() < 0.45 else rng.randint(4, len(prev) - 1)
                wpm, wpf = prev[(ci + w_offset) % len(prev)]
                rels.append(Relationship(type="parent_child", person_1=wpm, person_2=f_id))
                rels.append(Relationship(type="parent_child", person_1=wpf, person_2=f_id))
            for sc in range(6):
                idx += 1
                s_id = f"I-SIB-X{idx}-{year + 4}"
                inds[s_id] = LoadedIndividual(
                    path=tmp_path / f"{s_id}.md",
                    data=IndividualFrontmatter(
                        id=s_id, primary_name=f"Sibling {idx}", sex="M", birth=VitalEvent(date=str(year + 4))
                    ),
                    body="",
                )
                pm, pf = prev[sc % len(prev)]
                rels.append(Relationship(type="parent_child", person_1=pm, person_2=s_id))
                rels.append(Relationship(type="parent_child", person_1=pf, person_2=s_id))

    ds = Dataset(
        root=tmp_path,
        individuals=inds,
        sources={},
        relationships=rels,
        config={"focal_person": gen_couples[-1][0][0]},
    )
    t0 = time.perf_counter()
    layout = _compute_dag_layout(ds, set())
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"_compute_dag_layout too slow: {elapsed:.3f}s"

    nodes = layout["nodes"]
    assert len(nodes) == len(inds)
    for rel in rels:
        if rel.type == "parent_child":
            assert nodes[rel.person_1]["generation"] < nodes[rel.person_2]["generation"]

    cw = layout["card_w"]
    by_gen: dict[int, list[float]] = {}
    for pos in nodes.values():
        by_gen.setdefault(pos["generation"], []).append(pos["x"])
    for xs in by_gen.values():
        xs.sort()
        for a, b in zip(xs, xs[1:], strict=False):
            assert b - a >= cw + 20


def test_viewer_server_cache_invalidation_and_fresh_viewer_state(tmp_path: Path) -> None:
    """Server invalidates cached graph when data/ changes and always serves fresh viewer-state."""
    import shutil

    ws = tmp_path / "west-cache"
    shutil.copytree(EXAMPLE_ROOT, ws)

    server = make_viewer_server(ws, host="127.0.0.1", port=0, redact_living=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        # Warm cache
        with urllib.request.urlopen(f"{base}/api/graph.json") as resp:
            d1 = json.loads(resp.read().decode("utf-8"))
        assert d1["meta"]["counts"]["individuals"] == 9

        # Update build/viewer-state.json while data/ is unchanged -> GET / reflects immediately
        req = urllib.request.Request(
            f"{base}/api/viewer-state",
            data=json.dumps({"selectedId": "I-WEST-EDGAR-1909"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(f"{base}/") as resp:
            assert '"selectedId": "I-WEST-EDGAR-1909"' in resp.read().decode("utf-8")

        # Add a new individual file under data/individuals/ -> cache invalidates automatically
        (ws / "data" / "individuals" / "I-WEST-EXTRA-1915.md").write_text(
            "---\nid: I-WEST-EXTRA-1915\nprimary_name: Extra West\nsex: M\nstatus: tentative\n---\n",
            encoding="utf-8",
        )
        with urllib.request.urlopen(f"{base}/api/graph.json") as resp:
            d2 = json.loads(resp.read().decode("utf-8"))
        assert d2["meta"]["counts"]["individuals"] == 10
        assert "I-WEST-EXTRA-1915" in d2["individuals"]
    finally:
        server.shutdown()
        server.server_close()
