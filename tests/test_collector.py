from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import collector
from iudx_common import parse_env, read_credentials

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def test_resource_point_valid_and_invalid():
    ok = {"location": {"geometry": {"type": "Point", "coordinates": [73.8, 18.5]}}}
    assert collector.resource_point(ok) == (73.8, 18.5)
    assert collector.resource_point({}) == (None, None)
    bad = {"location": {"geometry": {"type": "Point", "coordinates": ["a", 1]}}}
    assert collector.resource_point(bad) == (None, None)
    poly = {"location": {"geometry": {"type": "Polygon", "coordinates": [1, 2]}}}
    assert collector.resource_point(poly) == (None, None)


def test_parse_env_and_read_credentials(tmp_path):
    assert parse_env("# c\nA=1\nB = two\nnoeq") == {"A": "1", "B": "two"}
    f = tmp_path / ".env"
    f.write_text("IUDX_CLIENT_ID=i\nIUDX_CLIENT_SECRET = s\n")
    assert read_credentials(f) == ("i", "s")


def test_load_env_missing_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "BASE", tmp_path)
    with pytest.raises(SystemExit, match="missing .env"):
        collector.load_env()


def test_store_observations_dedupes(con):
    recs = [{"observationDateTime": "2026-01-01T00:00:00Z", "v": 1}]
    assert collector.store_observations(con, "A", recs, NOW) == 1
    assert collector.store_observations(con, "A", recs, NOW) == 0


def test_backfill_days_clamped(con):
    assert collector.backfill_days(con, "A", NOW) == collector.MIN_BACKFILL_DAYS
    for waited, expected in ((10, 10), (400, collector.MAX_BACKFILL_DAYS)):
        con.execute("DELETE FROM access_log")
        first = (NOW - timedelta(days=waited)).astimezone().replace(tzinfo=None)
        con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", [first, "A", "token", "denied", ""])
        assert collector.backfill_days(con, "A", NOW) == expected


def test_token_cache_roundtrip_and_reuse(tmp_path, monkeypatch):
    path = tmp_path / "t.json"
    assert collector.load_token_cache(path) == {}
    collector.save_token_cache({"A": {"token": "t", "expiry": 9e12}}, path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    cache = collector.load_token_cache(path)
    monkeypatch.setattr(collector, "http_json", lambda *a, **k: pytest.fail("network"))
    assert collector.get_token("c", "s", "A", cache=cache) == ("t", None)


def test_get_token_denied_detail(fake_net):
    assert collector.get_token("c", "s", "B", cache={}) == (None, "403: no policy")
    assert collector.get_token("c", "s", "D", cache={}) == (None, "500: 500")


def test_run_collection_end_to_end(con, fake_net, tmp_path, capsys):
    cache_path = tmp_path / "tok.json"
    collector.run_collection(con, "c", "s", NOW, None, cache_path)
    out = capsys.readouterr().out
    assert "catalogue: 4 EnvFlood resources (3 active)" in out
    assert "first grant for A… — backfilling 7d" in out
    assert "tokens: 1 granted, 2 denied | new observation rows: 2" in out
    assert con.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    stati = dict(con.execute(
        "SELECT resource_id, status FROM access_log WHERE stage='token'").fetchall())
    assert stati == {"A": "ok", "B": "denied", "D": "denied"}
    assert list(collector.load_token_cache(cache_path)) == ["A"]
    # second run: no first-grant backfill, only the (deduped) latest record
    collector.run_collection(con, "c", "s", NOW, None, cache_path)
    assert "first grant" not in capsys.readouterr().out.split("catalogue:", 2)[-1]
