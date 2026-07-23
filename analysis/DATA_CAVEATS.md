# Data caveats — IMD 0.25° city-cell series (1901–2025)

Read before citing `city_rain_metrics.csv` or the charts.

## 🔴 Pune: cell unusable for trends
Cells around Pune (18.5, 73.75–74.25) sit on the Western Ghats rain gradient.
IMD's station-weighted gridding assigns different stations to the cell in
different eras: annual totals flip between ~500–800 mm regimes (1970s,
2000–03 — Pune-like) and 2,400–6,766 mm regimes (1930s, 2004–09 — Ghats-like;
Pune's actual 2005 was ~1,200 mm, the cell says 6,766). Verified across three
longitudes (73.75/74.0/74.25) and three eras (1935/1975/2005): the artifact
moves with era, not location. **Excluded from cross-city trend claims.** For
Pune use IMD station data (dsp.imdpune.gov.in — the Pune observatory) instead.

## Chennai: nearest-land cell, not city-center
City-center grid point (13.0, 80.25) is sea-masked → series uses
(13.25, 80.25), north Chennai. Coastal-station extremes (e.g. 2015-12-01
Tambaram ~490 mm) are muted; the cell still records 2015's 329 mm as the
125-year max.

## Mumbai: inland cell
City-center point is sea-masked → series uses (19.0, 73.0), inland toward
Thane. 2005-07-26 shows 423 mm vs Santacruz's famous 944 mm — gridding + the
inland shift smooth station extremes. Trend direction is still usable;
absolute extremes understate the island city.

## Delhi: gridded extremes muted
The cell (28.75, 77.25) shows zero heavy days in the 2010s while city
stations (Safdarjung) logged >100 mm days. 25 km-cell averaging dilutes
convective bursts. Treat low decade bars as "no widespread heavy rain", not
"no heavy rain anywhere in Delhi".

## General
- 1900s decade = 9 years (series starts 1901); 2020s = 6 years (partial) —
  labeled on charts.
- 2025 is IMD's real-time product, subject to revision.
- Bengaluru waterlogging event feed: reporting gap Mar–Jul 2025 (6–31
  events/month of all types vs hundreds in monsoon 2024) — 2025 event counts
  understate reality; news-verified (2025-05-19 flood absent from feed).
