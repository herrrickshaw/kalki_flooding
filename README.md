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

<!-- 
DATA LIBRARY LINK - Add this section to every repo README.md
This snippet provides discovery and documentation links.
-->

## 📊 Data Discovery

This repository is part of the **Global Data Library** — a unified catalog of 10,528 datasets across 40+ repositories.

### Quick Links

- **[Global Data Library README](.ruflo/DATA_LIBRARY_README.md)** — Full catalog, search API, and usage examples
- **[Data Library Python Interface](.ruflo/data-library/data_library.py)** — Query datasets programmatically
- **[Repository Scanner](.ruflo/data-library/repo_scanner.py)** — Reindex all repos to update the catalog

### Datasets in This Repository

The data catalog automatically inventories all datasets in this repo. To find your data:

```python
from data_library import DataLibrary

lib = DataLibrary()

# Search this repo's datasets
results = lib.search("", source="<repo-name>")

# Get dataset details
dataset = lib.get("<dataset_id>")
print(f"Rows: {dataset['row_count']}")
print(f"Freshness: {dataset['freshness_hours']} hours old")
print(f"Storage: {dataset['storage_tier']}")
```

### Browse the Full Catalog

**Market Coverage** (5 markets, 21,279 symbols):
- India (NSE/BSE): 2,364 instruments
- US (NASDAQ/NYSE): 7,442 instruments
- Europe (17 exchanges): 1,214 instruments
- Japan (TSE): 3,709 instruments
- Korea (KRX): 2,768 instruments

**Government Sources** (30+ ministries):
- MOSPI: 25 datasets (GDP, CPI, trade, agri, power)
- SEBI: 151,928 XBRL results + IPO pipeline
- PIB: 25+ ministry announcements
- DGFT: India trade data (monthly)
- Agmarknet: 300+ mandi prices (daily)
- NSE/MCX: Real-time derivatives chains

See [Global Data Library README](.ruflo/DATA_LIBRARY_README.md) for complete documentation.

### Finding Data Across All Repos

```python
# Find India OHLCV data (might be in multiple repos)
lib.search("india ohlcv", market="india")

# Get the fastest/freshest version
optimal = lib.get_optimal("india ohlcv", latency="<100ms", freshness="<1day")
# Returns: {"storage_tier": "cassandra", "path": "..."}

# Check data gaps
gaps = lib.gaps("india", date_from="2026-01-01")

# See which collectors are stale
status = lib.collectors_status()
```

---
