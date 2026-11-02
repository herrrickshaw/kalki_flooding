#!/usr/bin/env python3
"""IUDX flood-sensor collector.

Harvests all iudx:EnvFlood resources from the IUDX catalogue, pulls latest
observations for every resource the account has an access policy for, and
appends them to a local DuckDB archive (append-only, deduped).

IUDX serves live snapshots only — the longitudinal archive this builds is
the durable asset (same thesis as the bhavcopy archive).

Usage:
    python3 collector.py                 # catalog refresh + latest pull
    python3 collector.py --temporal 7    # force-backfill last N days for ALL granted sensors
    python3 collector.py --coverage      # print access-coverage report only

First-grant auto-backfill: a sensor whose token is granted but which has zero
archived observations (i.e., its approval just landed) automatically gets a
temporal pull covering the WHOLE approval wait — anchored to the sensor's
earliest access_log entry, clamped to [MIN_BACKFILL_DAYS, MAX_BACKFILL_DAYS].
A slow provider loses nothing (up to the cap); `--temporal N` overrides.

Credentials: .env beside this file (IUDX_CLIENT_ID / IUDX_CLIENT_SECRET),
gitignored, chmod 600. Do not point this at ~/Downloads — macOS TCC denies
launchd access there.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from iudx_common import AAA, CATALOGUE, CATALOGUE_PAGE_SIZE, HTTP_TIMEOUT_S, read_credentials

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "flood.duckdb"
TOKEN_CACHE = BASE / ".tokens.json"

RS = "https://rs.cos.iudx.org.in/ngsi-ld/v1"

UA = "iudx-flood-collector/0.1"

# first-grant backfill bounds: reach back to when we first started trying this
# sensor (its earliest access_log entry — i.e., the whole approval wait), but
# never less than a week and never more than the RS is likely to serve.
MIN_BACKFILL_DAYS = 7
MAX_BACKFILL_DAYS = 30
TOKEN_REFRESH_MARGIN_S = 300  # re-request a token 5 min before it expires
REQUEST_PAUSE_S = 0.3  # politeness delay between sensors
ERROR_BODY_CHARS = 200  # how much of an HTTP error body goes into access_log


# ---------------------------------------------------------------- helpers
def load_env() -> Tuple[str, str]:
    """Return (client_id, client_secret) from ``.env`` beside this file, or exit."""
    try:
        return read_credentials(BASE / ".env")
    except FileNotFoundError:
        sys.exit("missing .env with IUDX_CLIENT_ID / IUDX_CLIENT_SECRET")


def http_json(url: str, headers: Optional[Dict[str, str]] = None, data: Optional[bytes] = None,
              method: Optional[str] = None, timeout: int = HTTP_TIMEOUT_S) -> Tuple[int, Any]:
    """Send a request; return (status, decoded JSON body). Raises HTTPError on 4xx/5xx."""
    req = urllib.request.Request(
        url,
        data=data,
        method=method or ("POST" if data else "GET"),
        headers={"User-Agent": UA, **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


# ---------------------------------------------------------------- catalog
def fetch_flood_resources() -> List[dict]:
    """All iudx:EnvFlood resources from the catalogue (paginated)."""
    out, offset = [], 0
    while True:
        q = (f"{CATALOGUE}/search?property=[type]&value=[[iudx:EnvFlood]]"
             f"&limit={CATALOGUE_PAGE_SIZE}&offset={offset}")
        _, d = http_json(q)
        rows = d.get("results", [])
        out.extend(rows)
        if len(rows) < CATALOGUE_PAGE_SIZE or len(out) >= d.get("totalHits", 0):
            break
        offset += CATALOGUE_PAGE_SIZE
    return out


# ---------------------------------------------------------------- tokens
def load_token_cache(path: Path = None) -> dict:
    """Read the per-resource token cache ({} if absent)."""
    path = path or TOKEN_CACHE
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_token_cache(cache: dict, path: Path = None) -> None:
    """Persist the token cache with owner-only permissions."""
    path = path or TOKEN_CACHE
    path.write_text(json.dumps(cache))
    path.chmod(0o600)


def get_token(cid: str, csec: str, item_id: str, item_type: str = "resource",
              cache: Optional[dict] = None) -> Tuple[Optional[str], Optional[str]]:
    """Token per item, cached until 5 min before expiry.

    Returns (token, err) — err is the AAA error detail on failure.
    """
    cache = cache if cache is not None else {}
    hit = cache.get(item_id)
    if hit and hit["expiry"] - time.time() > TOKEN_REFRESH_MARGIN_S:
        return hit["token"], None
    body = json.dumps(
        {"itemId": item_id, "itemType": item_type, "role": "consumer"}
    ).encode()
    try:
        _, d = http_json(
            f"{AAA}/token",
            headers={
                "clientId": cid,
                "clientSecret": csec,
                "Content-Type": "application/json",
            },
            data=body,
        )
        res = d["results"]
        cache[item_id] = {"token": res["accessToken"], "expiry": res["expiry"]}
        return res["accessToken"], None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", "")
        except (ValueError, AttributeError, OSError):  # body not JSON-object / unreadable
            detail = str(e.code)
        return None, f"{e.code}: {detail}"


# ---------------------------------------------------------------- storage
def init_db(con: duckdb.DuckDBPyConnection) -> None:
    """Create the resources / observations / access_log tables if missing."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS resources (
            id TEXT PRIMARY KEY, name TEXT, label TEXT, instance TEXT,
            provider TEXT, resource_group TEXT, access_policy TEXT,
            item_status TEXT, address TEXT, lon DOUBLE, lat DOUBLE,
            first_seen TIMESTAMP, last_seen TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS observations (
            resource_id TEXT, obs_time TIMESTAMP, payload JSON,
            ingested_at TIMESTAMP,
            PRIMARY KEY (resource_id, obs_time)
        );
        CREATE TABLE IF NOT EXISTS access_log (
            run_ts TIMESTAMP, resource_id TEXT, stage TEXT,
            status TEXT, detail TEXT
        );
        """
    )


def resource_point(resource: dict) -> Tuple[Optional[float], Optional[float]]:
    """(lon, lat) of a catalogue item with a GeoJSON Point location, else (None, None)."""
    geom = (resource.get("location") or {}).get("geometry") or {}
    coords = geom.get("coordinates")
    if geom.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2 \
            and all(isinstance(c, (int, float)) for c in coords[:2]):
        return coords[0], coords[1]
    return None, None


def upsert_resources(con: duckdb.DuckDBPyConnection, resources: List[dict], now: datetime) -> None:
    """Insert new catalogue items; refresh label/policy/status/last_seen of known ones."""
    for r in resources:
        lon, lat = resource_point(r)
        con.execute(
            """
            INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (id) DO UPDATE SET
                label=excluded.label, access_policy=excluded.access_policy,
                item_status=excluded.item_status, last_seen=excluded.last_seen
            """,
            [
                r["id"], r.get("name"), r.get("label"), r.get("instance"),
                r.get("provider"), r.get("resourceGroup"), r.get("accessPolicy"),
                r.get("itemStatus"),
                (r.get("location") or {}).get("address"),
                lon, lat, now, now,
            ],
        )


def store_observations(con: duckdb.DuckDBPyConnection, resource_id: str,
                       records: List[dict], now: datetime) -> int:
    """Insert records, dedupe on (resource_id, obs_time). Returns rows added."""
    added = 0
    for rec in records:
        ts = rec.get("observationDateTime") or rec.get("dateObserved") or now.isoformat()
        try:
            con.execute(
                "INSERT INTO observations VALUES (?,?,?,?)",
                [resource_id, ts, json.dumps(rec), now],
            )
            added += 1
        except duckdb.ConstraintException:
            pass  # already archived
    return added


# ---------------------------------------------------------------- pulls
def pull_latest(token: str, resource_id: str) -> List[dict]:
    """Latest observation(s) for one resource."""
    s, d = http_json(f"{RS}/entities/{resource_id}", headers={"token": token})
    return d.get("results", [])


def backfill_days(con: duckdb.DuckDBPyConnection, resource_id: str, now: datetime) -> int:
    """Dynamic first-grant backfill depth: cover the whole approval wait.

    Anchored to the sensor's earliest access_log entry (the first time this
    collector ever tried it), clamped to [MIN_BACKFILL_DAYS, MAX_BACKFILL_DAYS].
    """
    first_try = con.execute(
        "SELECT min(run_ts) FROM access_log WHERE resource_id=?", [resource_id]
    ).fetchone()[0]
    if first_try is None:
        return MIN_BACKFILL_DAYS
    if first_try.tzinfo is None:
        # duckdb stores our aware-UTC inserts as LOCAL-naive (verified: the
        # 03:21 UTC run reads back as 08:51 IST) — reattach the local zone,
        # not UTC, or every wait is undercounted by the UTC offset
        first_try = first_try.replace(tzinfo=datetime.now().astimezone().tzinfo)
    waited = math.ceil((now - first_try).total_seconds() / 86400)
    return max(MIN_BACKFILL_DAYS, min(waited, MAX_BACKFILL_DAYS))


def pull_temporal(token: str, resource_id: str, days: int) -> List[dict]:
    """Observations of the last ``days`` days via the temporal API."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    q = (
        f"{RS}/temporal/entities?id={resource_id}&timerel=during"
        f"&time={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"&endtime={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    s, d = http_json(f"{q}", headers={"token": token})
    return d.get("results", [])


# ---------------------------------------------------------------- main
def coverage_report(con: duckdb.DuckDBPyConnection) -> None:
    """Print token-outcome counts (latest run per resource) and archive size."""
    print("== access coverage (latest run per resource) ==")
    rows = con.execute(
        """
        WITH latest AS (
            SELECT resource_id, status, detail,
                   ROW_NUMBER() OVER (PARTITION BY resource_id ORDER BY run_ts DESC) rn
            FROM access_log WHERE stage='token'
        )
        SELECT status, count(*) FROM latest WHERE rn=1 GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    for status, n in rows:
        print(f"  {status}: {n}")
    n_obs = con.execute("SELECT count(*), count(DISTINCT resource_id) FROM observations").fetchone()
    print(f"  observations archived: {n_obs[0]} rows from {n_obs[1]} sensors")


def log_access(con: duckdb.DuckDBPyConnection, now: datetime, rid: str, stage: str,
               status: str, detail: str) -> None:
    """Append one row to access_log (makes coverage auditable)."""
    con.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", [now, rid, stage, status, detail])


def collect_resource(con: duckdb.DuckDBPyConnection, token: str, rid: str, now: datetime,
                     temporal_days: Optional[int]) -> int:
    """Pull latest (+ backfill when requested or on first grant) and archive; return rows added.

    An explicit ``temporal_days`` wins; otherwise a first grant (no archived rows
    yet) triggers the automatic backfill of the approval-wait window.
    """
    recs = pull_latest(token, rid)
    days = temporal_days
    if not days:
        n_prior = con.execute(
            "SELECT count(*) FROM observations WHERE resource_id=?", [rid]
        ).fetchone()[0]
        if n_prior == 0:
            days = backfill_days(con, rid, now)
            print(f"  first grant for {rid[:8]}… — backfilling {days}d")
    if days:
        recs += pull_temporal(token, rid, days)
    return store_observations(con, rid, recs, now)


def run_collection(con: duckdb.DuckDBPyConnection, cid: str, csec: str, now: datetime,
                   temporal_days: Optional[int], token_cache_path: Path = None) -> None:
    """Catalogue refresh -> per-resource tokens -> pulls -> archive; prints a summary."""
    resources = fetch_flood_resources()
    upsert_resources(con, resources, now)
    active = [r for r in resources if r.get("itemStatus") == "ACTIVE"]
    print(f"catalogue: {len(resources)} EnvFlood resources ({len(active)} active)")

    cache = load_token_cache(token_cache_path)
    granted = denied = rows_added = 0
    for r in active:
        rid = r["id"]
        tok, err = get_token(cid, csec, rid, cache=cache)
        if not tok:
            denied += 1
            log_access(con, now, rid, "token", "denied", err)
            continue
        granted += 1
        log_access(con, now, rid, "token", "ok", "")
        try:
            added = collect_resource(con, tok, rid, now, temporal_days)
            rows_added += added
            log_access(con, now, rid, "fetch", "ok", f"{added} new")
        except urllib.error.HTTPError as e:
            log_access(con, now, rid, "fetch", f"http_{e.code}",
                       e.read().decode()[:ERROR_BODY_CHARS])
        time.sleep(REQUEST_PAUSE_S)  # be polite to the RS

    save_token_cache(cache, token_cache_path)
    print(f"tokens: {granted} granted, {denied} denied | new observation rows: {rows_added}")
    coverage_report(con)


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--temporal", type=int, metavar="DAYS", help="backfill N days via temporal API")
    ap.add_argument("--coverage", action="store_true", help="report only, no fetch")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH))
    init_db(con)
    if args.coverage:
        coverage_report(con)
        return

    cid, csec = load_env()
    run_collection(con, cid, csec, datetime.now(timezone.utc), args.temporal)
    con.close()


if __name__ == "__main__":
    main()
