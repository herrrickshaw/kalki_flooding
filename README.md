# iudx-flood-collector

Longitudinal archive of India's urban flood-sensor network, collected from
[IUDX](https://catalogue.iudx.org.in) (India Urban Data Exchange).

IUDX serves **live snapshots** — nobody keeps history. This collector turns the
74-sensor `iudx:EnvFlood` fleet into a durable time-series archive
(`flood.duckdb`), the same thesis as the NSE bhavcopy archive.

## Sensor fleet (catalogue, 2026-07-23)

| Group | Instance | Sensors | Notes |
|---|---|---|---|
| FWR sensors | pune | ~45 | water level/flow, e.g. Kharadi |
| FM_Box monitors | chennai | ~28 | subways & canals (Mylapore, Nungambakkam, …) |
| Flood Level Monitoring | kalyan-dombivli | 1 | city-wide |

All are `SECURE`: tokens come from your registered client credentials, but data
flows only after each provider grants a consumer policy (see below).

## Layout

- `collector.py` — catalogue refresh → per-resource tokens → latest (+ optional
  temporal backfill) → DuckDB. Append-only, deduped on `(resource_id, obs_time)`.
  Logs every token/fetch outcome to `access_log` so coverage is auditable.
- `request_access.py` — submits ACL-APD access requests to the providers.
  **Outward action** — run once, deliberately (`--list` first, then `--submit`).
- `.env` — `IUDX_CLIENT_ID` / `IUDX_CLIENT_SECRET`, chmod 600, gitignored.
  Kept here (not `~/Downloads`) because macOS TCC blocks launchd from Downloads.
- `flood.duckdb` — the archive. Tables: `resources`, `observations`, `access_log`.

## Run

```bash
python3 collector.py                # daily pull
python3 collector.py --temporal 7   # force 7-day backfill for all granted sensors
python3 collector.py --coverage     # access report only
```

**First-grant auto-backfill**: when a provider approves and a sensor's token is
granted for the first time (zero archived rows), that run automatically pulls a
temporal backfill covering the **whole approval wait** — anchored to the
sensor's earliest `access_log` entry, clamped to 7–30 days
(`MIN_BACKFILL_DAYS`/`MAX_BACKFILL_DAYS`; the cap because resource servers
bound temporal query ranges). A slow provider loses nothing up to the cap.
`--temporal N` overrides the depth.

Use `/usr/bin/python3` (has duckdb) — the Homebrew python does not.

## Access flow (one-time)

1. `python3 request_access.py --list` — see the 3 provider groups.
2. `python3 request_access.py --submit` — file requests (providers get notified).
   Alternatively: log in at catalogue.iudx.org.in and click *Request Access* on
   each resource group.
3. Once a provider approves, the next `collector.py` run picks the sensors up
   automatically — no config change needed.
