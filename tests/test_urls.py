"""Tests for smart genealogy URL parsing, `add-link`, and `intake --scaffold --url`."""

from __future__ import annotations

from pathlib import Path

import pytest

from genealogy.intake import scaffold_unlinked_sources
from genealogy.schema import read_markdown_frontmatter, render_frontmatter
from genealogy.urls import add_link_to_source, parse_provenance_url
from genealogy.validator import validate_dataset


@pytest.mark.parametrize(
    ("url", "provider", "auth", "note_fragment"),
    [
        (
            "https://www.ancestry.com/imageviewer/collections/7884/images/4449396_00512",
            "ancestry",
            True,
            "collection 7884",
        ),
        (
            "https://www.ancestry.com/discoveryui-content/view/41573109:6742",
            "ancestry",
            True,
            "record 41573109",
        ),
        (
            "https://www.familysearch.org/ark:/61903/1:1:MGXZ-001",
            "familysearch",
            True,
            "ark:/61903/1:1:MGXZ-001",
        ),
        (
            "https://www.newspapers.com/article/the-newport-plain-talk/123456/",
            "newspapers",
            True,
            "the-newport-plain-talk",
        ),
        (
            "https://www.fold3.com/image/5551234",
            "fold3",
            True,
            "5551234",
        ),
        (
            "https://www.findagrave.com/memorial/99887766/claude-west",
            "findagrave",
            False,
            "#99887766",
        ),
        (
            "https://www.loc.gov/resource/sn89058216/1914-02-12/ed-1/?sp=4",
            "loc",
            False,
            "sn89058216",
        ),
        (
            "https://catalog.archives.gov/id/123456",
            "nara",
            False,
            "123456",
        ),
        (
            "https://www.wikitree.com/wiki/Clemens-1",
            "wikitree",
            False,
            "Clemens-1",
        ),
    ],
)
def test_parse_provenance_url_providers(
    url: str, provider: str, auth: bool, note_fragment: str
) -> None:
    lnk = parse_provenance_url(url, accessed="2026-11-19")
    assert lnk.provider == provider
    assert lnk.requires_auth is auth
    assert lnk.accessed == "2026-11-19"
    assert lnk.note and note_fragment in lnk.note


def test_parse_provenance_url_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError, match="Invalid HTTP/HTTPS"):
        parse_provenance_url("javascript:alert(1)")


def test_add_link_to_source_and_intake_url(tmp_path: Path) -> None:
    src_dir = tmp_path / "data" / "sources"
    src_dir.mkdir(parents=True)
    src_file = src_dir / "S-1910-CENSUS-TEST.md"
    src_file.write_text(
        render_frontmatter(
            {
                "id": "S-1910-CENSUS-TEST",
                "title": "1910 Census Test",
                "record_type": "census",
            },
            "## Formal Citation\nTest.\n",
        ),
        encoding="utf-8",
    )

    url = "https://www.familysearch.org/ark:/61903/1:1:MGXZ-001"
    _, lnk, added = add_link_to_source(tmp_path, "S-1910-CENSUS-TEST", url)
    assert added is True
    assert lnk.provider == "familysearch"

    # Second call with same URL deduplicates cleanly
    _, _, added_again = add_link_to_source(tmp_path, "S-1910-CENSUS-TEST", url)
    assert added_again is False

    meta, _ = read_markdown_frontmatter(src_file)
    assert len(meta["links"]) == 1

    # Intake --scaffold --url attaches parsed link to new stub
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    (art_dir / "1920_census_west.txt").write_text("census scan", encoding="utf-8")
    created = scaffold_unlinked_sources(
        tmp_path,
        url="https://www.ancestry.com/imageviewer/collections/6061/images/4390812_00100",
    )
    assert len(created) == 1
    stub_meta, _ = read_markdown_frontmatter(created[0][1])
    assert stub_meta["links"][0]["provider"] == "ancestry"
    assert stub_meta["links"][0]["requires_auth"] is True
    assert validate_dataset(tmp_path).errors == []
