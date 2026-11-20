"""Smart genealogy URL parser and source provenance link appender."""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from genealogy.schema import (
    LinkProvider,
    SourceFrontmatter,
    SourceLink,
    read_markdown_frontmatter,
    render_frontmatter,
)


def parse_provenance_url(
    url: str,
    *,
    accessed: str | None = None,
    note: str | None = None,
    requires_auth: bool | None = None,
) -> SourceLink:
    """Parse an online genealogy/archival URL into a structured SourceLink."""
    clean_url = url.strip()
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid HTTP/HTTPS URL: '{url}'")

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    qs = parse_qs(parsed.query)

    provider: LinkProvider = "other"
    default_auth = False
    auto_note = ""

    # Ancestry domains (ancestry.com, ancestry.co.uk, ancestry.ca, ancestry.com.au)
    if host == "ancestry.com" or host.endswith(".ancestry.com") or host.startswith("ancestry."):
        provider = "ancestry"
        default_auth = True
        m_img = re.search(r"/collections/(\d+)/images/([^/?#]+)", path)
        m_disc = re.search(r"/discoveryui-content/view/(\d+):(\d+)", path)
        if m_img:
            auto_note = f"Ancestry collection {m_img.group(1)}, image {m_img.group(2)}"
        elif m_disc:
            auto_note = f"Ancestry collection {m_disc.group(2)}, record {m_disc.group(1)}"
        elif "dbid" in qs or "h" in qs:
            dbid = qs.get("dbid", [""])[0]
            rec_h = qs.get("h", [""])[0]
            parts = [p for p in (f"dbid {dbid}" if dbid else "", f"record {rec_h}" if rec_h else "") if p]
            auto_note = "Ancestry " + ", ".join(parts)

    elif host == "familysearch.org" or host.endswith(".familysearch.org"):
        provider = "familysearch"
        default_auth = True
        m_ark = re.search(r"(ark:/61903/[^\s?#]+)", path)
        if m_ark:
            auto_note = f"FamilySearch {m_ark.group(1)}"

    elif host == "newspapers.com" or host.endswith(".newspapers.com"):
        provider = "newspapers"
        default_auth = True
        m_clip = re.search(r"/(?:article|clip|image)/([^/?#]+)", path)
        if m_clip:
            auto_note = f"Newspapers.com item {m_clip.group(1)}"

    elif host == "fold3.com" or host.endswith(".fold3.com"):
        provider = "fold3"
        default_auth = True
        m_f3 = re.search(r"/image/(\d+)", path)
        if m_f3:
            auto_note = f"Fold3 image {m_f3.group(1)}"

    elif host == "findagrave.com" or host.endswith(".findagrave.com"):
        provider = "findagrave"
        default_auth = False
        m_mem = re.search(r"/memorial/(\d+)", path)
        if m_mem:
            auto_note = f"Find a Grave memorial #{m_mem.group(1)}"

    elif host == "loc.gov" or host.endswith(".loc.gov"):
        provider = "loc"
        default_auth = False
        m_lccn = re.search(r"/(?:resource|item)/([^/?#]+)", path)
        if m_lccn:
            auto_note = f"Library of Congress {m_lccn.group(1)}"

    elif host == "catalog.archives.gov" or host.endswith(".archives.gov"):
        provider = "nara"
        default_auth = False
        m_naid = re.search(r"/id/(\d+)", path)
        if m_naid:
            auto_note = f"NARA Catalog NAID {m_naid.group(1)}"

    elif host == "wikitree.com" or host.endswith(".wikitree.com"):
        provider = "wikitree"
        default_auth = False
        m_wt = re.search(r"/wiki/([^/?#]+)", path)
        if m_wt:
            auto_note = f"WikiTree profile {m_wt.group(1)} (secondary/compiled lead)"

    final_accessed = accessed or datetime.date.today().isoformat()
    final_auth = default_auth if requires_auth is None else requires_auth
    final_note = note if note is not None else (auto_note or None)

    return SourceLink(
        provider=provider,
        url=clean_url,
        accessed=final_accessed,
        requires_auth=final_auth,
        note=final_note,
    )


def add_link_to_source(
    root: Path | str,
    source_id: str,
    url: str,
    *,
    note: str | None = None,
    accessed: str | None = None,
    requires_auth: bool | None = None,
) -> tuple[Path, SourceLink, bool]:
    """Append a parsed provenance link to `data/sources/<SOURCE_ID>.md`.

    Returns `(source_path, parsed_link, added)` where `added=False` if the exact URL already existed.
    """
    root_path = Path(root).resolve()
    src_path = root_path / "data" / "sources" / f"{source_id}.md"
    if not src_path.is_file():
        raise FileNotFoundError(f"Source file not found: {src_path}")

    link = parse_provenance_url(
        url,
        accessed=accessed,
        note=note,
        requires_auth=requires_auth,
    )
    raw_meta, body = read_markdown_frontmatter(src_path)
    model = SourceFrontmatter.model_validate(raw_meta)

    if any(existing.url == link.url for existing in model.links):
        return src_path, link, False

    links_list = list(raw_meta.get("links") or [])
    link_dict = {
        "provider": link.provider,
        "url": link.url,
        "accessed": link.accessed,
        "requires_auth": link.requires_auth,
    }
    if link.note:
        link_dict["note"] = link.note
    links_list.append(link_dict)
    raw_meta["links"] = links_list

    # Re-validate the updated frontmatter before writing
    SourceFrontmatter.model_validate(raw_meta)
    src_path.write_text(render_frontmatter(raw_meta, body), encoding="utf-8")
    return src_path, link, True
