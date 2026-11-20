"""WikiTree public read-only JSON API client (secondary/compiled research leads only)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://api.wikitree.com/api.php"
USER_AGENT = "genealogy-workspace/0.1 (open-source genealogical research CLI)"
DEFAULT_APP_ID = "genealogy-workspace"
DEFAULT_FIELDS = (
    "Id,Name,FirstName,MiddleName,LastNameAtBirth,LastNameCurrent,"
    "BirthDate,BirthLocation,DeathDate,DeathLocation,Gender,Father,Mother"
)


@dataclass
class WikiTreeProfile:
    wikitree_id: str
    full_name: str
    sex: str
    birth_date: str
    birth_place: str
    death_date: str
    death_place: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "wikitree_id": self.wikitree_id,
            "full_name": self.full_name,
            "sex": self.sex,
            "birth_date": self.birth_date,
            "birth_place": self.birth_place,
            "death_date": self.death_date,
            "death_place": self.death_place,
            "url": self.url,
            "gps_note": "Secondary/compiled online tree lead — verify against primary records.",
        }


def _to_profile(raw: dict[str, Any]) -> WikiTreeProfile:
    wt_id = str(raw.get("Name") or raw.get("Id") or "")
    first = raw.get("FirstName") or ""
    middle = raw.get("MiddleName") or ""
    last = raw.get("LastNameAtBirth") or raw.get("LastNameCurrent") or ""
    full = " ".join(p for p in (first, middle, last) if p).strip() or wt_id
    gender = str(raw.get("Gender") or "").upper()
    sex = "M" if gender.startswith("M") else ("F" if gender.startswith("F") else "U")
    return WikiTreeProfile(
        wikitree_id=wt_id,
        full_name=full,
        sex=sex,
        birth_date=str(raw.get("BirthDate") or ""),
        birth_place=str(raw.get("BirthLocation") or ""),
        death_date=str(raw.get("DeathDate") or ""),
        death_place=str(raw.get("DeathLocation") or ""),
        url=f"https://www.wikitree.com/wiki/{wt_id}" if wt_id else "",
    )


def _call_wikitree(params: dict[str, Any], client: httpx.Client | None = None) -> list[dict[str, Any]]:
    req_params = {"appId": os.environ.get("WIKITREE_APP_ID", DEFAULT_APP_ID), **params}
    own_client = client is None
    http = client or httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        resp = http.get(BASE_URL, params=req_params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return [data]
    finally:
        if own_client:
            http.close()


def search_wikitree_person(
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    birth_date: str | None = None,
    birth_location: str | None = None,
    limit: int = 10,
    client: httpx.Client | None = None,
) -> list[WikiTreeProfile]:
    """Search public WikiTree profiles via action=searchPerson."""
    params: dict[str, Any] = {
        "action": "searchPerson",
        "fields": DEFAULT_FIELDS,
        "limit": max(1, min(limit, 50)),
    }
    if first_name:
        params["FirstName"] = first_name
    if last_name:
        params["LastName"] = last_name
    if birth_date:
        params["BirthDate"] = birth_date
    if birth_location:
        params["BirthLocation"] = birth_location

    payload = _call_wikitree(params, client=client)
    matches = payload[0].get("matches", []) if payload else []
    return [_to_profile(m) for m in matches[:limit] if isinstance(m, dict)]


def get_wikitree_profile(
    wikitree_id: str,
    *,
    client: httpx.Client | None = None,
) -> WikiTreeProfile | None:
    """Fetch a single public WikiTree profile via action=getProfile."""
    params = {
        "action": "getProfile",
        "key": wikitree_id,
        "fields": DEFAULT_FIELDS,
        "resolveRedirect": 1,
    }
    payload = _call_wikitree(params, client=client)
    if not payload or payload[0].get("status", 0) != 0:
        return None
    prof = payload[0].get("profile")
    return _to_profile(prof) if isinstance(prof, dict) else None


def get_wikitree_ancestors(
    wikitree_id: str,
    *,
    depth: int = 3,
    client: httpx.Client | None = None,
) -> list[WikiTreeProfile]:
    """Fetch ancestors of a public WikiTree profile via action=getAncestors."""
    params = {
        "action": "getAncestors",
        "key": wikitree_id,
        "depth": max(1, min(depth, 10)),
        "fields": DEFAULT_FIELDS,
        "resolveRedirect": 1,
    }
    payload = _call_wikitree(params, client=client)
    if not payload or payload[0].get("status", 0) != 0:
        return []
    ancestors = payload[0].get("ancestors", [])
    return [_to_profile(a) for a in ancestors if isinstance(a, dict)]
