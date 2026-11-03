"""Shared fixtures: an in-memory/tmp DuckDB and a fake IUDX HTTP layer (no network)."""
from __future__ import annotations

import io
import json
import urllib.error

import duckdb
import pytest

import collector

RESOURCES = [
    {"id": "A", "name": "a", "label": "LA", "instance": "pune", "provider": "p",
     "resourceGroup": "G", "accessPolicy": "SECURE", "itemStatus": "ACTIVE",
     "location": {"address": "x", "geometry": {"type": "Point", "coordinates": [73.8, 18.5]}}},
    {"id": "B", "label": "LB", "itemStatus": "ACTIVE"},
    {"id": "C", "label": "LC", "itemStatus": "INACTIVE"},
    {"id": "D", "label": "LD", "itemStatus": "ACTIVE"},
]


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(body))


def fake_http_json(url, headers=None, data=None, method=None, timeout=30):
    """A: token ok; B: 403 with JSON detail; D: 500 with non-JSON body."""
    if "/search" in url:
        return 200, {"results": RESOURCES, "totalHits": len(RESOURCES)}
    if url.endswith("/token"):
        item = json.loads(data)["itemId"]
        if item == "A":
            return 200, {"results": {"accessToken": "tokA", "expiry": 9e12}}
        if item == "B":
            raise http_error(403, b'{"detail": "no policy"}')
        raise http_error(500, b"not json")
    if "/temporal/" in url:
        return 200, {"results": [{"observationDateTime": "2026-01-01T00:00:00Z", "v": 1},
                                 {"observationDateTime": "2026-01-02T00:00:00Z", "v": 2}]}
    if "/entities/" in url:
        return 200, {"results": [{"observationDateTime": "2026-01-01T00:00:00Z", "v": 1}]}
    raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    collector.init_db(c)
    yield c
    c.close()


@pytest.fixture
def fake_net(monkeypatch):
    monkeypatch.setattr(collector, "http_json", fake_http_json)
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
