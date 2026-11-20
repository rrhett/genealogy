"""Tests for SQLite + FTS5 compilation, read-only SQL queries, timelines, and tree renderers."""

from __future__ import annotations

from pathlib import Path

import pytest

from genealogy.db import build_sqlite, run_sql_query, search_fts
from genealogy.timeline import build_timeline_markdown, render_tree

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "west-family"


def test_sqlite_build_and_fts_search(tmp_path: Path) -> None:
    db_file = build_sqlite(EXAMPLE_ROOT, tmp_path / "test.sqlite")
    assert db_file.exists()
    assert db_file.stat().st_size > 0

    hits = search_fts(EXAMPLE_ROOT, "Rainwater")
    entity_ids = {h["entity_id"] for h in hits}
    assert "I-RAINWATER-OCTA-1890" in entity_ids
    assert "S-1908-MARRIAGE-WEST-RAINWATER" in entity_ids


def test_sql_query_read_only_enforcement() -> None:
    rows = run_sql_query(
        EXAMPLE_ROOT,
        "SELECT id, primary_name FROM individuals WHERE status = 'confirmed' ORDER BY id",
    )
    ids = [r["id"] for r in rows]
    assert ids == ["I-RAINWATER-OCTA-1890", "I-WEST-CLAUDE-1886", "I-WEST-JOHN-1858"]

    with pytest.raises(ValueError, match="read-only"):
        run_sql_query(EXAMPLE_ROOT, "DELETE FROM individuals")


def test_timeline_and_tree_renderers() -> None:
    tl = build_timeline_markdown(EXAMPLE_ROOT, "I-WEST-CLAUDE-1886", include_family=True)
    assert "# Timeline: Claude West" in tl
    assert "1886-05-14" in tl
    assert "1908-03-15" in tl
    assert "Birth of child Edgar West" in tl
    assert "1954-11-12" in tl

    mermaid = render_tree(EXAMPLE_ROOT, fmt="mermaid")
    assert mermaid.startswith("flowchart TD")
    assert "I_WEST_JOHN_1858 --> I_WEST_CLAUDE_1886" in mermaid
    assert "m. 1908-03-15" in mermaid

    dot = render_tree(EXAMPLE_ROOT, center_id="I-WEST-CLAUDE-1886", fmt="dot", depth=1)
    assert dot.startswith("digraph Genealogy")
    assert '"I-WEST-CLAUDE-1886"' in dot
    assert '"I-WEST-JANE-1950"' not in dot  # 2 hops away, excluded at depth=1
