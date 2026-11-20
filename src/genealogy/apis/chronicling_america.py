"""Library of Congress Chronicling America (loc.gov unified JSON API) client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://www.loc.gov/collections/chronicling-america/"
USER_AGENT = "genealogy-workspace/0.1 (open-source genealogical research CLI)"


@dataclass
class NewspaperHit:
    title: str
    date: str
    location: str
    url: str
    lccn: str
    snippet: str

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "date": self.date,
            "location": self.location,
            "url": self.url,
            "lccn": self.lccn,
            "snippet": self.snippet,
        }


def search_newspapers(
    query: str,
    *,
    state: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    limit: int = 10,
    page: int = 1,
    client: httpx.Client | None = None,
) -> list[NewspaperHit]:
    """Search digitized historical U.S. newspapers via the unified loc.gov JSON API."""
    params: dict[str, Any] = {
        "q": query,
        "fo": "json",
        "at": "results",
        "c": max(1, min(limit, 100)),
        "sp": max(1, page),
    }

    facets: list[str] = []
    if state:
        facets.append(f"location:{state.strip().lower()}")
    if facets:
        params["fa"] = "|".join(facets)
    if start_year and end_year:
        params["dates"] = f"{start_year}/{end_year}"
    elif start_year:
        params["dates"] = f"{start_year}/1963"
    elif end_year:
        params["dates"] = f"1770/{end_year}"

    own_client = client is None
    http = client or httpx.Client(timeout=45.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        resp = http.get(BASE_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if own_client:
            http.close()

    hits: list[NewspaperHit] = []
    for item in payload.get("results", [])[:limit]:
        title = item.get("title") or "(Untitled)"
        date = item.get("date") or ""
        loc = item.get("location") or []
        loc_str = ", ".join(loc) if isinstance(loc, list) else str(loc)
        url = item.get("url") or item.get("id") or ""
        lccn_raw = item.get("number_lccn") or []
        lccn = lccn_raw[0] if isinstance(lccn_raw, list) and lccn_raw else str(lccn_raw or "")
        desc = item.get("description") or []
        snippet = " ".join(desc) if isinstance(desc, list) else str(desc)
        hits.append(
            NewspaperHit(
                title=str(title),
                date=str(date),
                location=loc_str,
                url=str(url),
                lccn=str(lccn),
                snippet=snippet[:280].strip(),
            )
        )
    return hits
