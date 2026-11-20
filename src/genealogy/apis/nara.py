"""National Archives and Records Administration (NARA) Catalog API v2 client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://catalog.archives.gov/api/v2/records/search"
USER_AGENT = "genealogy-workspace/0.1 (open-source genealogical research CLI)"


@dataclass
class NaraHit:
    na_id: str
    title: str
    level: str
    record_group: str
    dates: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "na_id": self.na_id,
            "title": self.title,
            "level": self.level,
            "record_group": self.record_group,
            "dates": self.dates,
            "url": self.url,
        }


def _extract_record(hit: dict[str, Any]) -> NaraHit:
    src = hit.get("_source", {})
    rec = src.get("record", src) if isinstance(src, dict) else {}
    na_id = str(rec.get("naId") or hit.get("_id") or "")
    title = str(rec.get("title") or "(Untitled)")
    level = str(rec.get("levelOfDescription") or "")

    rg = ""
    ancestors = rec.get("ancestors") or []
    if isinstance(ancestors, list):
        for anc in ancestors:
            if isinstance(anc, dict) and anc.get("recordGroupNumber"):
                rg = f"RG {anc['recordGroupNumber']}"
                break
    if not rg and rec.get("recordGroupNumber"):
        rg = f"RG {rec['recordGroupNumber']}"

    dates = ""
    inc_dates = rec.get("inclusiveDates") or {}
    if isinstance(inc_dates, dict):
        s_year = inc_dates.get("inclusiveStartDate", {}).get("year") if isinstance(inc_dates.get("inclusiveStartDate"), dict) else None
        e_year = inc_dates.get("inclusiveEndDate", {}).get("year") if isinstance(inc_dates.get("inclusiveEndDate"), dict) else None
        if s_year and e_year:
            dates = f"{s_year}-{e_year}"
        elif s_year or e_year:
            dates = str(s_year or e_year)

    url = f"https://catalog.archives.gov/id/{na_id}" if na_id else ""
    return NaraHit(
        na_id=na_id,
        title=title,
        level=level,
        record_group=rg,
        dates=dates,
        url=url,
    )


def search_nara(
    query: str,
    *,
    record_group: str | None = None,
    limit: int = 10,
    page: int = 1,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> list[NaraHit]:
    """Search the NARA Catalog API v2 for archival records."""
    params: dict[str, Any] = {
        "q": query,
        "limit": max(1, min(limit, 100)),
        "page": max(1, page),
    }
    if record_group:
        params["recordGroupNumber"] = record_group.strip()

    key = api_key or os.environ.get("NARA_API_KEY")
    if not key:
        raise RuntimeError(
            "NARA Catalog API v2 requires an API key in the NARA_API_KEY environment variable "
            "(free keys are available by contacting Catalog_API@nara.gov)."
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "x-api-key": key,
    }

    own_client = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=True, headers=headers)
    try:
        resp = http.get(BASE_URL, params=params, headers=headers)
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"NARA Catalog API returned non-JSON response (HTTP {resp.status_code}). "
                "Verify your NARA_API_KEY is valid."
            ) from exc
    finally:
        if own_client:
            http.close()

    # NARA v2 returns results under body.hits.hits (or top-level hits.hits)
    body = payload.get("body", payload)
    hits_raw = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
    return [_extract_record(h) for h in hits_raw[:limit]]
