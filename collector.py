#!/usr/bin/env python3
"""IUDX flood-sensor collector.

Harvests all iudx:EnvFlood resources from the IUDX catalogue, pulls latest
observations for every resource the account has an access policy for, and
appends them to a local DuckDB archive (append-only, deduped).

IUDX serves live snapshots only — the longitudinal archive this builds is
the durable asset (same thesis as the bhavcopy archive).

Usage:
    python3 collector.py                 # catalog refresh + latest pull
    python3 collector.py --temporal 7    # also backfill last N days (temporal API)
    python3 collector.py --coverage      # print access-coverage report only

Credentials: .env beside this file (IUDX_CLIENT_ID / IUDX_CLIENT_SECRET),
gitignored, chmod 600. Do not point this at ~/Downloads — macOS TCC denies
launchd access there.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "flood.duckdb"
TOKEN_CACHE = BASE / ".tokens.json"

CATALOGUE = "https://cos.iudx.org.in/iudx/cat/v1"
AAA = "https://authorization.iudx.org.in/auth/v1"
RS = "https://rs.cos.iudx.org.in/ngsi-ld/v1"

UA = "iudx-flood-collector/0.1"


# ---------------------------------------------------------------- helpers
def load_env():
    env = {}
    env_file = BASE / ".env"
    if not env_file.exists():
        sys.exit("missing .env with IUDX_CLIENT_ID / IUDX_CLIENT_SECRET")
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["IUDX_CLIENT_ID"], env["IUDX_CLIENT_SECRET"]


def http_json(url, headers=None, data=None, method=None, timeout=30):
    req = urllib.request.Request(
        url,
        data=data,
        method=method or ("POST" if data else "GET"),
        headers={"User-Agent": UA, **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


# ---------------------------------------------------------------- catalog
def fetch_flood_resources():
    """All iudx:EnvFlood resources from the catalogue (paginated)."""
    out, offset = [], 0
    while True:
        q = f"{CATALOGUE}/search?property=[type]&value=[[iudx:EnvFlood]]&limit=500&offset={offset}"
        _, d = http_json(q)
        rows = d.get("results", [])
        out.extend(rows)
        if len(rows) < 500 or len(out) >= d.get("totalHits", 0):
            break
        offset += 500
    return out


# ---------------------------------------------------------------- tokens
def load_token_cache():
    if TOKEN_CACHE.exists():
        return json.loads(TOKEN_CACHE.read_text())
    return {}


def save_token_cache(cache):
    TOKEN_CACHE.write_text(json.dumps(cache))
    TOKEN_CACHE.chmod(0o600)


def get_token(cid, csec, item_id, item_type="resource", cache=None):
    """Token per item, cached until 5 min before expiry.

    Returns (token, err) — err is the AAA error detail on failure.
    """
    cache = cache if cache is not None else {}
    hit = cache.get(item_id)
    if hit and hit["expiry"] - time.time() > 300:
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
        except Exception:
            detail = str(e.code)
        return None, f"{e.code}: {detail}"


# ---------------------------------------------------------------- storage
def init_db(con):
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


def upsert_resources(con, resources, now):
    for r in resources:
        geom = (r.get("location") or {}).get("geometry") or {}
        coords = geom.get("coordinates")
        lon = lat = None
        if geom.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2 \
                and all(isinstance(c, (int, float)) for c in coords[:2]):
            lon, lat = coords[0], coords[1]
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


def store_observations(con, resource_id, records, now):
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
def pull_latest(token, resource_id):
    s, d = http_json(f"{RS}/entities/{resource_id}", headers={"token": token})
    return d.get("results", [])


def pull_temporal(token, resource_id, days):
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
def coverage_report(con):
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


def main():
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
    now = datetime.now(timezone.utc)

    resources = fetch_flood_resources()
    upsert_resources(con, resources, now)
    active = [r for r in resources if r.get("itemStatus") == "ACTIVE"]
    print(f"catalogue: {len(resources)} EnvFlood resources ({len(active)} active)")

    cache = load_token_cache()
    granted = denied = rows_added = 0
    for r in active:
        rid = r["id"]
        tok, err = get_token(cid, csec, rid, cache=cache)
        if not tok:
            denied += 1
            con.execute(
                "INSERT INTO access_log VALUES (?,?,?,?,?)",
                [now, rid, "token", "denied", err],
            )
            continue
        granted += 1
        con.execute(
            "INSERT INTO access_log VALUES (?,?,?,?,?)", [now, rid, "token", "ok", ""]
        )
        try:
            recs = pull_latest(tok, rid)
            if args.temporal:
                recs += pull_temporal(tok, rid, args.temporal)
            added = store_observations(con, rid, recs, now)
            rows_added += added
            con.execute(
                "INSERT INTO access_log VALUES (?,?,?,?,?)",
                [now, rid, "fetch", "ok", f"{added} new"],
            )
        except urllib.error.HTTPError as e:
            con.execute(
                "INSERT INTO access_log VALUES (?,?,?,?,?)",
                [now, rid, "fetch", f"http_{e.code}", e.read().decode()[:200]],
            )
        time.sleep(0.3)  # be polite to the RS

    save_token_cache(cache)
    print(f"tokens: {granted} granted, {denied} denied | new observation rows: {rows_added}")
    coverage_report(con)
    con.close()


if __name__ == "__main__":
    main()
