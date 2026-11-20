"""Unit tests for Chronicling America, NARA Catalog v2, and WikiTree API clients."""

from __future__ import annotations

import httpx
import pytest

from genealogy.apis.chronicling_america import search_newspapers
from genealogy.apis.nara import search_nara
from genealogy.apis.wikitree import (
    get_wikitree_ancestors,
    get_wikitree_profile,
    search_wikitree_person,
)


def test_chronicling_america_client() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "The Newport Plain Talk",
                        "date": "1908-03-19",
                        "location": ["tennessee", "cocke county"],
                        "url": "https://www.loc.gov/item/sn89058001/",
                        "number_lccn": ["sn89058001"],
                        "description": ["Marriage of Claud West and Octa Rainwater."],
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        hits = search_newspapers(
            "Claude West",
            state="Tennessee",
            start_year=1900,
            end_year=1920,
            limit=5,
            client=client,
        )

    assert "fo=json" in captured["url"]
    assert "at=results" in captured["url"]
    assert "fa=location%3Atennessee" in captured["url"]
    assert "dates=1900%2F1920" in captured["url"]
    assert len(hits) == 1
    assert hits[0].title == "The Newport Plain Talk"
    assert hits[0].lccn == "sn89058001"


def test_nara_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NARA_API_KEY", raising=False)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "body": {
                    "hits": {
                        "hits": [
                            {
                                "_id": "123456",
                                "_source": {
                                    "record": {
                                        "naId": 123456,
                                        "title": "1910 Census Schedule - Cocke County, TN",
                                        "levelOfDescription": "fileUnit",
                                        "ancestors": [{"recordGroupNumber": "29"}],
                                        "inclusiveDates": {
                                            "inclusiveStartDate": {"year": 1910},
                                            "inclusiveEndDate": {"year": 1910},
                                        },
                                    }
                                },
                            }
                        ]
                    }
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        hits = search_nara(
            "Cocke County",
            record_group="29",
            limit=5,
            api_key="test-key",
            client=client,
        )

    assert "recordGroupNumber=29" in str(captured["url"])
    assert captured["api_key"] == "test-key"
    assert len(hits) == 1
    assert hits[0].na_id == "123456"
    assert hits[0].record_group == "RG 29"
    assert hits[0].url == "https://catalog.archives.gov/id/123456"

    # Missing NARA_API_KEY fails fast with an actionable message
    with pytest.raises(RuntimeError, match="NARA_API_KEY"):
        search_nara("Cocke County", api_key=None)


def test_wikitree_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("appId") == "genealogy-workspace"
        action = request.url.params.get("action")
        if action == "searchPerson":
            return httpx.Response(
                200,
                json=[
                    {
                        "status": 0,
                        "matches": [
                            {
                                "Name": "West-12345",
                                "FirstName": "Claude",
                                "LastNameAtBirth": "West",
                                "Gender": "Male",
                                "BirthDate": "1886-05-14",
                                "BirthLocation": "Cocke County, Tennessee",
                            }
                        ],
                    }
                ],
            )
        if action == "getProfile":
            return httpx.Response(
                200,
                json=[
                    {
                        "status": 0,
                        "profile": {
                            "Name": "Clemens-1",
                            "FirstName": "Samuel",
                            "MiddleName": "Langhorne",
                            "LastNameAtBirth": "Clemens",
                            "Gender": "Male",
                            "BirthDate": "1835-11-30",
                        },
                    }
                ],
            )
        if action == "getAncestors":
            return httpx.Response(
                200,
                json=[
                    {
                        "status": 0,
                        "ancestors": [
                            {"Name": "West-12345", "FirstName": "Claude", "LastNameAtBirth": "West"},
                            {"Name": "West-12340", "FirstName": "John", "LastNameAtBirth": "West"},
                        ],
                    }
                ],
            )
        return httpx.Response(400)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        matches = search_wikitree_person(first_name="Claude", last_name="West", client=client)
        assert len(matches) == 1
        assert matches[0].wikitree_id == "West-12345"
        assert "Secondary/compiled" in matches[0].to_dict()["gps_note"]

        prof = get_wikitree_profile("Clemens-1", client=client)
        assert prof is not None
        assert prof.full_name == "Samuel Langhorne Clemens"

        ancs = get_wikitree_ancestors("West-12345", depth=2, client=client)
        assert len(ancs) == 2
