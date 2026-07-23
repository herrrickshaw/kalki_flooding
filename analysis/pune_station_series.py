#!/usr/bin/env python3
"""Pune STATION daily rainfall 1901-2025 — replaces the unusable grid cell.

Sources (both open, no registration):
  1901-1972: GHCN-Daily station IN012190100 "POONA" (18.533, 73.85, WMO 43063)
             — IMD observatory record, PRCP in tenths of mm.
  1973-2025: NOAA GSOD station 43063099999 (same observatory/airport)
             — PRCP in inches (99.99 = missing), converted to mm.

Rationale: IMD's 0.25-deg grid cells around Pune flip between Pune-like and
Ghats-like rainfall regimes depending on era (station-assignment artifact,
see DATA_CAVEATS.md). GHCN's POONA record is near-complete 1901-1969 but dies
after 2010; GSOD covers 1973-present. The splice year (1973) is where GSOD
becomes available. Caveat: GSOD days are 00-00 UTC vs IMD's 08:30 IST
observation day — single-day extremes can land on a neighbouring date.

Writes data/pune_station_daily.csv (date, city='pune_station', rain_mm) and
prints a station-vs-gridcell era comparison to quantify the artifact.
"""
import csv
import gzip
import io
import json
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
# name matches the city_daily*.csv glob so city_rain_metrics.py picks it up
OUT = BASE / "data" / "city_daily_pune_station.csv"

GHCN = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/IN012190100.csv.gz"
GSOD = ("https://www.ncei.noaa.gov/access/services/data/v1?"
        "dataset=global-summary-of-the-day&stations=43063099999"
        "&dataTypes=PRCP&startDate={y}-01-01&endDate={y}-12-31&format=json")


def fetch_ghcn():
    req = urllib.request.Request(GHCN, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = gzip.decompress(r.read())
    out = {}
    for row in csv.reader(io.StringIO(raw.decode())):
        if row[2] == "PRCP" and row[5].strip() == "":  # qflag blank = passed QA
            d = f"{row[1][:4]}-{row[1][4:6]}-{row[1][6:8]}"
            out[d] = round(int(row[3]) / 10.0, 2)  # tenths of mm -> mm
    return out


def fetch_gsod(y, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(GSOD.format(y=y),
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                rows = json.loads(r.read())
            out = {}
            for rec in rows:
                v = (rec.get("PRCP") or "").strip()
                if v and v != "99.99":
                    mm = round(float(v) * 25.4, 2)  # in -> mm
                    # QC: GSOD carries recurring garbage values (18.58in=471.9mm
                    # appears in 1983/1998/2023; 405mm on a dry-season day).
                    # GHCN's verified 1901-72 max is 132mm -> reject >200mm.
                    if mm <= 198.5:  # 1.5x GHCN-era max (132.3)
                        out[rec["DATE"]] = mm
            return out
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(5 * (i + 1))


def main():
    ghcn = fetch_ghcn()
    print(f"GHCN days: {len(ghcn)}")
    daily = {d: v for d, v in ghcn.items() if d < "1973-01-01"}
    for y in range(1973, 2026):
        g = fetch_gsod(y)
        daily.update(g)
        print(f"  gsod {y}: {len(g)} days", flush=True)
        time.sleep(0.5)
    print(f"merged days: {len(daily)}")

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "city", "rain_mm"])
        for d in sorted(daily):
            w.writerow([d, "pune_station", daily[d]])
    print(f"wrote {OUT}")

    # ---- era comparison vs the artifact grid cell
    grid = {}
    for fn in ("city_daily_more.csv",):
        for r in csv.DictReader(open(BASE / "data" / fn)):
            if r["city"] == "pune":
                grid[r["date"]] = float(r["rain_mm"])
    print("\nannual totals, station vs grid cell (artifact eras obvious):")
    for y in (1935, 1955, 1975, 1995, 2005, 2015, 2024):
        sd = [v for d, v in daily.items() if d.startswith(str(y))]
        gd = [v for d, v in grid.items() if d.startswith(str(y))]
        ok = len(sd) >= 300
        print(f"  {y}: station {sum(sd):7.0f} mm ({len(sd)}d{'' if ok else ' ⚠️'})"
              f" | grid cell {sum(gd):7.0f} mm")


if __name__ == "__main__":
    main()
