# kalki_flooding — IUDX flood-sensor archive and India city rainfall analysis

Longitudinal archive of India's urban flood-sensor network, collected from
[IUDX](https://catalogue.iudx.org.in) (India Urban Data Exchange), plus a set of
rainfall / waterlogging analyses for 12 Indian cities. The collector writes a
local DuckDB file (`flood.duckdb`); the analysis scripts write CSVs and PNG charts
under `analysis/`.

> Status: active (collector runs daily via launchd; rainfall refresh runs yearly).
> The repository name is `kalki_flooding`; the collector's internal name is `iudx-flood-collector`.
> Research use only; sensor data access is granted per provider and is not redistributed here.

New to the repo? See [How to use this repo with AI](HOW_TO_USE_WITH_AI.md).

## What it does
- Harvests all `iudx:EnvFlood` resources from the IUDX catalogue (74 sensors at the 2026-07-23 catalogue).
- Gets a per-sensor token with your registered client credentials and pulls the latest observation.
- Appends observations to `flood.duckdb`, deduped on `(resource_id, obs_time)`, and logs every token/fetch outcome to `access_log`.
- On a sensor's first grant, auto-backfills the whole approval wait (clamped to 7-30 days).
- Files ACL-APD access requests with the providers (`request_access.py`).
- Refreshes IMD gridded rainfall for 12 cities and mails a summary once a year (`annual_refresh.py`).

## Quick start
```bash
git clone https://github.com/herrrickshaw/kalki_flooding.git
cd kalki_flooding
python -m venv .venv && source .venv/bin/activate
pip install duckdb
printf 'IUDX_CLIENT_ID=...\nIUDX_CLIENT_SECRET=...\n' > .env && chmod 600 .env
python collector.py
```
On the owner's Mac use `/usr/bin/python3` (has duckdb); the Homebrew python does not.

## How it works
| Stage | Module | Output |
|---|---|---|
| Catalogue refresh | `collector.fetch_flood_resources` / `upsert_resources` | `resources` table |
| Token per resource | `collector.get_token` (cached in `.tokens.json`) | `access_log` (`token` rows) |
| Pull latest + temporal backfill | `collector.collect_resource` | `observations` table |
| Coverage report | `collector.coverage_report` | stdout |
| One-time access requests | `request_access.py` | requests at the providers' APD |
| Shared constants / `.env` parsing | `iudx_common.py` | - |

First-grant auto-backfill: when a sensor's token is granted and it has zero archived rows,
the run pulls a temporal backfill anchored to the sensor's earliest `access_log` entry,
clamped to `MIN_BACKFILL_DAYS`=7 .. `MAX_BACKFILL_DAYS`=30 (resource servers bound temporal ranges).
`--temporal N` overrides the depth.

```bash
python collector.py                # daily pull
python collector.py --temporal 7   # force 7-day backfill for all granted sensors
python collector.py --coverage     # access report only, no fetch
python request_access.py --list    # show the 3 provider groups + existing requests
python request_access.py --submit  # OUTWARD ACTION: files requests, providers are notified
python annual_refresh.py [--force] [--no-mail]   # Jan/Feb IMD refresh
```

## Repository layout
| Path | Purpose |
|---|---|
| `collector.py` | Catalogue -> tokens -> pulls -> DuckDB archive (CLI) |
| `request_access.py` | Submit access requests to the providers (CLI) |
| `iudx_common.py` | Endpoint constants and `.env` parsing shared by both CLIs |
| `annual_refresh.py` | Yearly IMD rainfall refresh, CSV rewrite, mail, Dropbox copy |
| `analysis/` | City rainfall metrics, Bengaluru waterlogging vs roads, Pune station series; data in `analysis/data/`, caveats in `analysis/DATA_CAVEATS.md` |
| `tests/` | pytest suite (no network) |
| `flood.duckdb` | The archive (gitignored). Tables: `resources`, `observations`, `access_log` |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| [IUDX catalogue / resource servers](https://catalogue.iudx.org.in) | EnvFlood sensor metadata and readings | REST, `SECURE` policy, token per resource | Data flows only after each provider grants a consumer policy |
| IMD gridded rainfall | Daily city-cell rainfall | `imdlib` download (see `annual_refresh.py`) | See `analysis/DATA_CAVEATS.md`; Pune grid cell is artifact-ridden, so Pune uses GSOD station data |

Sensor fleet (2026-07-23): FWR sensors in Pune (~45), FM_Box monitors in Chennai (~28),
Flood Level Monitoring in Kalyan-Dombivli (1).

## Configuration
- `.env` beside `collector.py`: `IUDX_CLIENT_ID`, `IUDX_CLIENT_SECRET` (chmod 600, gitignored, never committed).
  Kept in the repo directory, not `~/Downloads`, because macOS TCC blocks launchd from Downloads.
- `annual_refresh.py` additionally reads Gmail credentials from `~/.config/market-secrets/credentials.env`
  and uses `rclone` (remote `dropbox:imd-rainfall-grid`); `imdlib`/`xarray` are vendored in `./pkgs` (gitignored).

## Testing
```bash
pip install pytest ruff duckdb
ruff check . && pytest -q
```

## Limitations and known issues
- All sensors are `SECURE`: no data until the provider approves each request.
- The APD rejects resource-group ids, so `request_access.py` requests one representative leaf per group.
- `annual_refresh.py` hard-codes owner-specific paths, mail address and a `git push origin main`; it is not portable.
- Analysis scripts are not covered by tests and still contain `;` one-liners.

## Licence
No licence file yet - all rights reserved.
