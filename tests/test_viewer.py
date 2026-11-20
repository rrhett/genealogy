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
    assert 'command: "--copy"' in html_text
    assert "STANDALONE_MODE = true" in html_text
    assert "Living (Redacted)" in html_text
    assert "Jane West" not in html_text


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
