import datetime as dt
import gzip
import io
import json
import math
import stat
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import duckdb
import pytest


@pytest.fixture
def col(load, tmp_path, monkeypatch):
    m = load("collector")
    monkeypatch.setattr(m, "BASE", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "flood.duckdb")
    monkeypatch.setattr(m, "TOKEN_CACHE", tmp_path / ".tokens.json")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    return m


@pytest.fixture
def con(col):
    c = duckdb.connect(":memory:")
    col.init_db(c)
    return c


# ── load_env / http_json ─────────────────────────────────────────────────────
def test_load_env(col, tmp_path):
    with pytest.raises(SystemExit, match="missing .env"):
        col.load_env()
    (tmp_path / ".env").write_text("# comment\nIUDX_CLIENT_ID = abc \nIUDX_CLIENT_SECRET=s=e=c\nOTHER\n")
    assert col.load_env() == ("abc", "s=e=c")                  # split on the FIRST '='
    (tmp_path / ".env").write_text("IUDX_CLIENT_ID=abc\n")
    with pytest.raises(KeyError):
        col.load_env()


class FakeResp:
    def __init__(self, body, status=200):
        self.status, self._b = status, body

    def read(self):
        return self._b if isinstance(self._b, bytes) else json.dumps(self._b).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_json_builds_the_request(col, monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen.update(url=req.full_url, method=req.get_method(), headers=dict(req.header_items()),
                    data=req.data, timeout=timeout)
        return FakeResp({"ok": 1}, 201)

    monkeypatch.setattr(col.urllib.request, "urlopen", fake)
    assert col.http_json("http://x/y") == (201, {"ok": 1})
    assert seen["method"] == "GET" and seen["timeout"] == 30 and seen["data"] is None
    assert seen["headers"]["User-agent"] == col.UA
    status, d = col.http_json("http://x/y", headers={"token": "t", "User-Agent": "mine"}, data=b"{}", timeout=5)
    assert (status, d) == (201, {"ok": 1})
    assert seen["method"] == "POST" and seen["timeout"] == 5
    assert seen["headers"]["Token"] == "t" and seen["headers"]["User-agent"] == "mine"   # caller can override UA
    col.http_json("http://x/y", data=b"{}", method="PUT")
    assert seen["method"] == "PUT"


# ── catalogue ────────────────────────────────────────────────────────────────
def test_fetch_flood_resources_paginates_until_a_short_page(col, monkeypatch):
    pages = {0: {"results": [{"id": i} for i in range(500)], "totalHits": 700},
             500: {"results": [{"id": i} for i in range(500, 700)], "totalHits": 700}}
    urls = []

    def fake(url, **kw):
        urls.append(url)
        off = int(url.rsplit("offset=", 1)[1])
        return 200, pages[off]

    monkeypatch.setattr(col, "http_json", fake)
    out = col.fetch_flood_resources()
    assert [r["id"] for r in out] == list(range(700))
    assert len(urls) == 2 and "offset=0" in urls[0] and "offset=500" in urls[1]
    assert "limit=500" in urls[0] and "iudx:EnvFlood" in urls[0]


def test_fetch_flood_resources_stops_at_total_hits_even_on_a_full_page(col, monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return 200, {"results": [{"id": i} for i in range(500)], "totalHits": 500}

    monkeypatch.setattr(col, "http_json", fake)
    assert len(col.fetch_flood_resources()) == 500 and len(calls) == 1


def test_fetch_flood_resources_empty(col, monkeypatch):
    monkeypatch.setattr(col, "http_json", lambda url, **kw: (200, {}))
    assert col.fetch_flood_resources() == []


# ── tokens ───────────────────────────────────────────────────────────────────
def test_token_cache_round_trip_with_private_mode(col):
    assert col.load_token_cache() == {}
    col.save_token_cache({"a": {"token": "t", "expiry": 1}})
    assert col.load_token_cache() == {"a": {"token": "t", "expiry": 1}}
    assert stat.S_IMODE(col.TOKEN_CACHE.stat().st_mode) == 0o600


def test_get_token_cache_hit_requires_more_than_five_minutes_left(col, monkeypatch):
    monkeypatch.setattr(col.time, "time", lambda: 1000.0)
    calls = []

    def fake(url, headers=None, data=None, **kw):
        calls.append((url, headers, json.loads(data)))
        return 200, {"results": {"accessToken": "NEW", "expiry": 99999}}

    monkeypatch.setattr(col, "http_json", fake)
    cache = {"r": {"token": "OLD", "expiry": 1000 + 301}}
    assert col.get_token("cid", "sec", "r", cache=cache) == ("OLD", None) and calls == []
    cache = {"r": {"token": "OLD", "expiry": 1000 + 300}}              # exactly 300 s left -> refresh
    assert col.get_token("cid", "sec", "r", cache=cache) == ("NEW", None)
    url, headers, body = calls[0]
    assert url == f"{col.AAA}/token"
    assert headers["clientId"] == "cid" and headers["clientSecret"] == "sec"
    assert body == {"itemId": "r", "itemType": "resource", "role": "consumer"}
    assert cache["r"] == {"token": "NEW", "expiry": 99999}


def test_get_token_without_a_cache_argument_does_not_persist(col, monkeypatch):
    monkeypatch.setattr(col, "http_json", lambda *a, **k: (200, {"results": {"accessToken": "T", "expiry": 1}}))
    assert col.get_token("c", "s", "r") == ("T", None)


def http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(body))


def test_get_token_failures_return_the_aaa_detail(col, monkeypatch):
    def boom(*a, **k):
        raise http_error(403, json.dumps({"detail": "no policy"}).encode())

    monkeypatch.setattr(col, "http_json", boom)
    assert col.get_token("c", "s", "r", cache={}) == (None, "403: no policy")

    def boom2(*a, **k):
        raise http_error(500, b"<html>not json</html>")

    monkeypatch.setattr(col, "http_json", boom2)
    assert col.get_token("c", "s", "r", cache={}) == (None, "500: 500")        # undecodable body -> code only

    def boom3(*a, **k):
        raise http_error(401, json.dumps({}).encode())

    monkeypatch.setattr(col, "http_json", boom3)
    assert col.get_token("c", "s", "r", cache={}) == (None, "401: ")


# ── storage ──────────────────────────────────────────────────────────────────
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_init_db_is_idempotent(con, col):
    col.init_db(con)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert tables == {"resources", "observations", "access_log"}


def res(i, **kw):
    r = {"id": f"id{i}", "name": f"n{i}", "label": f"L{i}", "instance": "pune", "provider": "p",
         "resourceGroup": "g", "accessPolicy": "SECURE", "itemStatus": "ACTIVE",
         "location": {"address": "somewhere", "geometry": {"type": "Point", "coordinates": [73.8, 18.5]}}}
    r.update(kw)
    return r


def test_upsert_resources_geometry_parsing(con, col):
    rows = [res(1), res(2, location={"geometry": {"type": "Polygon", "coordinates": [1, 2]}}),
            res(3, location={"geometry": {"type": "Point", "coordinates": ["x", 2]}}),
            res(4, location={"geometry": {"type": "Point", "coordinates": [5]}}),
            res(5, location=None), res(6, location={}), res(7, location={"geometry": {"type": "Point", "coordinates": [1, 2, 3]}})]
    col.upsert_resources(con, rows, NOW)
    got = {r[0]: r[1:] for r in con.execute("SELECT id, lon, lat, address FROM resources").fetchall()}
    assert got["id1"] == (73.8, 18.5, "somewhere")
    for k in ("id2", "id3", "id4", "id5", "id6"):
        assert got[k][:2] == (None, None), k
    assert got["id7"][:2] == (1, 2)                          # a third (altitude) coordinate is tolerated
    assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 7


def test_upsert_resources_updates_mutable_columns_only(con, col):
    col.upsert_resources(con, [res(1)], NOW)
    later = NOW + timedelta(days=1)
    col.upsert_resources(con, [res(1, label="NEW", accessPolicy="OPEN", itemStatus="INACTIVE", name="renamed",
                                   location={"geometry": {"type": "Point", "coordinates": [1.0, 2.0]}})], later)
    r = con.execute("SELECT name, label, access_policy, item_status, lon, first_seen, last_seen FROM resources").fetchone()
    assert r[:4] == ("n1", "NEW", "OPEN", "INACTIVE")        # name is not updated
    assert r[4] == 73.8                                      # nor is the position
    first_seen = con.execute("SELECT first_seen FROM resources").fetchone()[0]
    assert r[5] == first_seen and r[6] > r[5]                  # first_seen is kept, last_seen moves on


def test_store_observations_dedupes_on_resource_and_time(con, col):
    recs = [{"observationDateTime": "2026-06-01T10:00:00", "level": 1},
            {"observationDateTime": "2026-06-01T10:05:00", "level": 2},
            {"observationDateTime": "2026-06-01T10:00:00", "level": 99}]       # same key again
    assert col.store_observations(con, "r1", recs, NOW) == 2
    assert col.store_observations(con, "r1", recs, NOW) == 0                    # idempotent
    assert col.store_observations(con, "r2", recs[:1], NOW) == 1                # other resource, same time
    payload = con.execute("SELECT payload FROM observations WHERE resource_id='r1' ORDER BY obs_time").fetchall()
    assert json.loads(payload[0][0])["level"] == 1                              # first writer wins


def test_store_observations_timestamp_fallbacks(con, col):
    recs = [{"dateObserved": "2026-06-02T00:00:00", "v": 1}, {"v": 2}]
    assert col.store_observations(con, "r", recs, NOW) == 2
    times = [r[0] for r in con.execute("SELECT obs_time FROM observations ORDER BY obs_time").fetchall()]
    assert times[0].isoformat().startswith("2026-06-01T12:00:00")                # "now" for the undated one
    assert times[1].isoformat().startswith("2026-06-02")
    assert col.store_observations(con, "r", [], NOW) == 0


# ── pulls ────────────────────────────────────────────────────────────────────
def test_pull_latest_and_temporal_urls(col, monkeypatch):
    seen = []

    def fake(url, headers=None, **kw):
        seen.append((url, headers))
        return 200, {"results": [{"a": 1}]}

    monkeypatch.setattr(col, "http_json", fake)
    assert col.pull_latest("tok", "rid") == [{"a": 1}]
    assert seen[0] == (f"{col.RS}/entities/rid", {"token": "tok"})
    monkeypatch.setattr(col, "http_json", lambda url, headers=None, **kw: (200, {}))
    assert col.pull_latest("tok", "rid") == []

    monkeypatch.setattr(col, "http_json", fake)
    col.pull_temporal("tok", "rid", 7)
    url, headers = seen[-1]
    assert headers == {"token": "tok"}
    assert url.startswith(f"{col.RS}/temporal/entities?id=rid&timerel=during&time=")
    start = datetime.strptime(url.split("&time=")[1].split("&")[0], "%Y-%m-%dT%H:%M:%SZ")
    end = datetime.strptime(url.split("&endtime=")[1], "%Y-%m-%dT%H:%M:%SZ")
    assert abs((end - start) - timedelta(days=7)) <= timedelta(seconds=1)


def test_backfill_days_covers_the_approval_wait_within_bounds(con, col):
    assert col.backfill_days(con, "none", NOW) == col.MIN_BACKFILL_DAYS == 7
    local = NOW.astimezone()

    def put(days_ago):
        con.execute("DELETE FROM access_log")
        first = (local - timedelta(days=days_ago)).replace(tzinfo=None)
        con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", [first, "r", "token", "denied", ""])
        con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", [local.replace(tzinfo=None), "r", "token", "ok", ""])

    for ago, want in [(1, 7), (7, 7), (8, 8), (20, 20), (30, 30), (31, 30), (200, 30)]:
        put(ago)
        assert col.backfill_days(con, "r", NOW) == want, ago
    assert col.MAX_BACKFILL_DAYS == 30


def test_backfill_days_rounds_partial_days_up(con, col):
    local = NOW.astimezone()
    first = (local - timedelta(days=9, hours=1)).replace(tzinfo=None)
    con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", [first, "r", "token", "denied", ""])
    assert col.backfill_days(con, "r", NOW) == 10


# ── coverage report ──────────────────────────────────────────────────────────
def test_coverage_report_counts_the_latest_status_per_resource(con, col, capsys):
    ins = lambda ts, rid, st, stage="token": con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", [ts, rid, stage, st, ""])
    t0 = datetime(2026, 5, 1)
    ins(t0, "a", "denied"); ins(t0 + timedelta(days=1), "a", "ok")             # latest is ok
    ins(t0, "b", "denied")
    ins(t0, "c", "denied"); ins(t0 + timedelta(days=2), "c", "denied", stage="fetch")   # fetch rows ignored
    con.execute("INSERT INTO observations VALUES ('a', '2026-06-01', '{}', '2026-06-01')")
    con.execute("INSERT INTO observations VALUES ('a', '2026-06-02', '{}', '2026-06-01')")
    con.execute("INSERT INTO observations VALUES ('b', '2026-06-02', '{}', '2026-06-01')")
    col.coverage_report(con)
    out = capsys.readouterr().out
    assert "== access coverage (latest run per resource) ==" in out
    assert out.index("denied: 2") < out.index("ok: 1")                           # ordered by count desc
    assert "observations archived: 3 rows from 2 sensors" in out


# ── main ─────────────────────────────────────────────────────────────────────
class Net:
    """Programmable stand-in for the IUDX services used by main()."""

    def __init__(self, col, monkeypatch, resources, tokens, latest=None, temporal=None, fetch_err=None):
        self.latest_calls, self.temporal_calls, self.token_calls = [], [], []
        monkeypatch.setattr(col, "fetch_flood_resources", lambda: resources)

        def get_token(cid, csec, rid, item_type="resource", cache=None):
            self.token_calls.append((cid, csec, rid))
            t = tokens.get(rid)
            if t is None:
                return None, "403: not granted"
            cache[rid] = {"token": t, "expiry": 1}
            return t, None

        def pull_latest(tok, rid):
            self.latest_calls.append(rid)
            if fetch_err and rid in fetch_err:
                raise http_error(fetch_err[rid], b"upstream broke " * 30)
            return [dict(r) for r in (latest or {}).get(rid, [])]

        def pull_temporal(tok, rid, days):
            self.temporal_calls.append((rid, days))
            return [dict(r) for r in (temporal or {}).get(rid, [])]

        monkeypatch.setattr(col, "get_token", get_token)
        monkeypatch.setattr(col, "pull_latest", pull_latest)
        monkeypatch.setattr(col, "pull_temporal", pull_temporal)


def run_main(col, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["collector.py", *argv])
    col.main()


def setup_env(tmp_path):
    (tmp_path / ".env").write_text("IUDX_CLIENT_ID=cid\nIUDX_CLIENT_SECRET=sec\n")


def rows(db, sql):
    c = duckdb.connect(str(db))
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def test_main_coverage_only_needs_no_credentials(col, monkeypatch, capsys):
    run_main(col, monkeypatch, "--coverage")
    out = capsys.readouterr().out
    assert "== access coverage" in out and "observations archived: 0 rows from 0 sensors" in out
    assert not (col.BASE / ".tokens.json").exists()


def test_main_full_run(col, monkeypatch, tmp_path, capsys):
    setup_env(tmp_path)
    resources = [res(1), res(2), res(3, itemStatus="INACTIVE"), res(4)]
    obs = {"id1": [{"observationDateTime": "2026-06-01T10:00:00", "v": 1}],
           "id4": [{"observationDateTime": "2026-06-01T10:00:00", "v": 9}]}
    net = Net(col, monkeypatch, resources, {"id1": "T1", "id4": "T4"}, latest=obs,
              fetch_err={"id4": 500})
    run_main(col, monkeypatch)
    out = capsys.readouterr().out
    assert "catalogue: 4 EnvFlood resources (3 active)" in out
    assert "tokens: 2 granted, 1 denied | new observation rows: 1" in out
    assert net.token_calls == [("cid", "sec", "id1"), ("cid", "sec", "id2"), ("cid", "sec", "id4")]   # inactive skipped
    assert "first grant for id1… — backfilling 7d" in out
    assert net.temporal_calls == [("id1", 7)]          # id4's latest pull failed before its backfill
    log = rows(col.DB_PATH, "SELECT resource_id, stage, status, detail FROM access_log ORDER BY resource_id, stage")
    assert ("id2", "token", "denied", "403: not granted") in log
    assert ("id1", "token", "ok", "") in log and ("id1", "fetch", "ok", "1 new") in log
    http = [r for r in log if r[1] == "fetch" and r[0] == "id4"]
    assert http == [("id4", "fetch", "http_500", ("upstream broke " * 30)[:200])]   # truncated to 200 chars
    assert rows(col.DB_PATH, "SELECT count(*) FROM resources")[0][0] == 4
    assert json.loads(col.TOKEN_CACHE.read_text()).keys() == {"id1", "id4"}
    assert stat.S_IMODE(col.TOKEN_CACHE.stat().st_mode) == 0o600
    assert "observations archived: 1 rows from 1 sensors" in out


def test_main_backfills_only_on_first_grant_and_explicit_days_win(col, monkeypatch, tmp_path, capsys):
    setup_env(tmp_path)
    resources = [res(1), res(2)]
    latest = {"id1": [{"observationDateTime": "2026-06-01T10:00:00"}], "id2": [{"observationDateTime": "2026-06-01T10:00:00"}]}
    net = Net(col, monkeypatch, resources, {"id1": "a", "id2": "b"}, latest=latest,
              temporal={"id1": [{"observationDateTime": "2026-05-30T00:00:00"}]})
    run_main(col, monkeypatch)                                     # both sensors have no archive -> backfill
    assert sorted(net.temporal_calls) == [("id1", 7), ("id2", 7)]
    assert rows(col.DB_PATH, "SELECT count(*) FROM observations")[0][0] == 3      # 2 latest + 1 temporal
    net.temporal_calls.clear()
    run_main(col, monkeypatch)                                     # now they have rows -> no backfill
    assert net.temporal_calls == []
    run_main(col, monkeypatch, "--temporal", "3")                  # explicit depth for every granted sensor
    assert sorted(net.temporal_calls) == [("id1", 3), ("id2", 3)]
    assert "first grant" not in capsys.readouterr().out.split("tokens:")[-1]


def test_main_with_no_active_resources(col, monkeypatch, tmp_path, capsys):
    setup_env(tmp_path)
    Net(col, monkeypatch, [res(1, itemStatus="INACTIVE")], {})
    run_main(col, monkeypatch)
    out = capsys.readouterr().out
    assert "catalogue: 1 EnvFlood resources (0 active)" in out
    assert "tokens: 0 granted, 0 denied | new observation rows: 0" in out
