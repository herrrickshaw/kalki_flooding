"""DuckDB archive logic in collector.py against an in-memory database
(no IUDX calls, no credentials, no flood.duckdb)."""
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

import collector as c


@pytest.fixture
def con():
    con = duckdb.connect(":memory:")
    c.init_db(con)
    yield con
    con.close()


def test_upsert_resources_parses_point_and_updates_status(con):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = {"id": "res/1", "name": "s1", "itemStatus": "ACTIVE",
         "location": {"address": "x", "geometry": {"type": "Point", "coordinates": [73.8, 18.5]}}}
    c.upsert_resources(con, [r, {"id": "res/2", "location": {"geometry": {"type": "Polygon"}}}], now)
    assert con.execute("SELECT lon, lat FROM resources WHERE id='res/1'").fetchone() == (73.8, 18.5)
    assert con.execute("SELECT lon FROM resources WHERE id='res/2'").fetchone() == (None,)
    c.upsert_resources(con, [dict(r, itemStatus="INACTIVE")], now)
    assert con.execute("SELECT count(*), max(item_status) FROM resources").fetchone() == (2, "INACTIVE")


def test_store_observations_dedupes_on_time(con):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    recs = [{"observationDateTime": "2026-01-01T00:00:00Z", "level": 1},
            {"observationDateTime": "2026-01-01T00:15:00Z", "level": 2}]
    assert c.store_observations(con, "res/1", recs, now) == 2
    assert c.store_observations(con, "res/1", recs, now) == 0
    assert c.store_observations(con, "res/2", recs[:1], now) == 1


def test_backfill_days_clamped_and_anchored_to_first_try(con):
    now = datetime.now(timezone.utc)
    assert c.backfill_days(con, "res/9", now) == c.MIN_BACKFILL_DAYS
    # ceil of the wait in days; the +/-2h offsets would flip if the stored
    # timestamp were re-read in the wrong zone (e.g. IST vs UTC, 5.5h).
    for wait, expected in [(timedelta(days=2), c.MIN_BACKFILL_DAYS),
                           (timedelta(days=12, hours=-2), 12),
                           (timedelta(days=12, hours=2), 13),
                           (timedelta(days=90), c.MAX_BACKFILL_DAYS)]:
        con.execute("DELETE FROM access_log")
        con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)",
                    [now - wait, "res/9", "latest", "403", ""])
        con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)",
                    [now, "res/9", "latest", "403", ""])
        assert c.backfill_days(con, "res/9", now) == expected, wait
