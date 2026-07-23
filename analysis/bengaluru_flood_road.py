#!/usr/bin/env python3
"""Bengaluru: waterlogging events × accident blackspots, + 100-year rainfall context.

Inputs (fetched from IUDX open resources with an RS-level token — see README):
  events_full.json      9,632 traffic-police road events (2024-01 → 2026-02),
                        of which ~1,257 are eventTitle == water_logging
  blackspots_full.json  50 road-accident blackspots with severityScore

Outputs (this dir): bengaluru_flood_road_map.png, waterlogging_seasonality.png,
  india_rainfall_100y.png, blackspot_waterlogging.csv
"""
import csv
import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
RAW = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "data"

# dataviz reference palette (light) — 2 series max on the map (all-pairs safe)
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e5e4e0", "#fcfcfb"


def km(lat):  # degrees lon -> km at latitude
    return 111.32 * math.cos(math.radians(lat))


def dist_km(a, b):
    dy = (a[1] - b[1]) * 111.32
    dx = (a[0] - b[0]) * km((a[1] + b[1]) / 2)
    return math.hypot(dx, dy)


def style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=12, loc="left", pad=10)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def main():
    events = json.load(open(RAW / "events_full.json"))
    spots = json.load(open(RAW / "blackspots_full.json"))

    def norm(c):
        """Return (lon, lat). The traffic-police feed emits [lat, lon] —
        backwards GeoJSON — while blackspots are correct [lon, lat]. Around
        Bengaluru lat≈13, lon≈77.5, so order is decidable from magnitude."""
        a, b = c[:2]
        return (b, a) if a < 60 < b else (a, b)

    wl = [e for e in events
          if e.get("eventTitle") == "water_logging"
          and isinstance(e.get("location"), dict)
          and e["location"].get("coordinates")]
    wl_pts = [norm(e["location"]["coordinates"]) for e in wl]
    # drop GPS garbage (11 events sit at e.g. lon 20.6 / lat −11.4 — outside
    # any Bengaluru bbox — and would inflate the density denominator ~100×)
    n_raw = len(wl_pts)
    keep = [(p, e) for p, e in zip(wl_pts, wl) if 77.3 < p[0] < 77.9 and 12.7 < p[1] < 13.3]
    wl_pts = [p for p, _ in keep]
    wl = [e for _, e in keep]
    if n_raw - len(wl_pts):
        print(f"dropped {n_raw - len(wl_pts)} events with out-of-city coords")
    bs = [(tuple(s["location"]["coordinates"][:2]), s.get("severityScore", 0))
          for s in spots if isinstance(s.get("location"), dict)]
    print(f"waterlogging events with coords: {len(wl_pts)} | blackspots: {len(bs)}")

    # ---- proximity: waterlogging events within 1 km of each blackspot,
    # vs the uniform-density expectation over the event bounding box
    lons = [p[0] for p in wl_pts]; lats = [p[1] for p in wl_pts]
    area = (max(lons) - min(lons)) * km(sum(lats) / len(lats)) * (max(lats) - min(lats)) * 111.32
    dens = len(wl_pts) / area  # events per km²
    expected_1km = dens * math.pi  # per 1-km disc
    rows, hits = [], 0
    for (pt, sev) in bs:
        n = sum(1 for p in wl_pts if dist_km(pt, p) <= 1.0)
        rows.append({"lon": pt[0], "lat": pt[1], "severity": sev, "wl_within_1km": n,
                     "ratio_vs_uniform": round(n / expected_1km, 2) if expected_1km else None})
        hits += n > 0
    rows.sort(key=lambda r: -r["wl_within_1km"])
    with open(BASE / "blackspot_waterlogging.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    med_ratio = sorted(r["ratio_vs_uniform"] for r in rows)[len(rows) // 2]
    print(f"blackspots with ≥1 waterlogging event within 1 km: {hits}/{len(bs)}")
    print(f"uniform expectation per 1-km disc: {expected_1km:.1f} | median blackspot ratio: {med_ratio}")

    # ---- map
    fig, ax = plt.subplots(figsize=(8.5, 8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    lat0 = sum(lats) / len(lats)
    ax.set_aspect(1 / math.cos(math.radians(lat0)))
    style(ax, "Bengaluru: 1,257 waterlogging events (2024–26) × 50 accident blackspots\n"
              "blackspot size = severity score")
    ax.scatter(lons, lats, s=7, c=BLUE, alpha=0.35, linewidths=0,
               label=f"Waterlogging event ({len(wl_pts)})", zorder=2)
    ax.scatter([p[0][0] for p in bs], [p[0][1] for p in bs],
               s=[max(20, sev * 1.2) for _, sev in bs], c=ORANGE, marker="o",
               edgecolors=SURFACE, linewidths=1.0,
               label="Accident blackspot (50)", zorder=3)
    top = rows[0]
    ax.annotate(f"worst overlap: {top['wl_within_1km']} waterlogging\nevents within 1 km",
                (top["lon"], top["lat"]), xytext=(10, 10), textcoords="offset points",
                fontsize=7.5, color=INK2,
                arrowprops=dict(arrowstyle="-", color=INK2, lw=0.7))
    ax.legend(loc="upper right", fontsize=8, frameon=False, labelcolor=INK)
    ax.set_xlabel("longitude", color=INK2, fontsize=8)
    ax.set_ylabel("latitude", color=INK2, fontsize=8)
    fig.tight_layout(); fig.savefig(BASE / "bengaluru_flood_road_map.png", facecolor=SURFACE)
    plt.close(fig)

    # ---- seasonality
    months = Counter(e["observationDateTime"][:7] for e in wl if e.get("observationDateTime"))
    keys = sorted(months)
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    style(ax, "Bengaluru waterlogging reports by month — the monsoon signature")
    ax.bar(range(len(keys)), [months[k] for k in keys], color=BLUE, width=0.7, zorder=3)
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels([k[2:] if k.endswith(("-01", "-07")) else k[5:] for k in keys],
                       rotation=60, fontsize=6.5)
    peak = max(keys, key=lambda k: months[k])
    ax.annotate(f"peak {peak}: {months[peak]}", (keys.index(peak), months[peak]),
                xytext=(0, 6), textcoords="offset points", ha="center",
                fontsize=8, color=INK2)
    fig.tight_layout(); fig.savefig(BASE / "waterlogging_seasonality.png", facecolor=SURFACE)
    plt.close(fig)

    # ---- 100-year rainfall (World Bank CCKP, CRU TS4.07, country-level)
    cached = RAW / "cckp.json"
    if cached.exists():
        series = json.load(open(cached))["data"]["IND"]
    else:
        url = ("https://cckpapi.worldbank.org/cckp/v1/cru-x0.5_timeseries_pr_timeseries_"
               "annual_1901-2022_mean_historical_cru_ts4.07_mean/IND?_format=json")
        # CCKP 403s python's default UA; a browser-ish UA passes (curl does too)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            series = json.loads(r.read())["data"]["IND"]
    yrs = sorted(int(k[:4]) for k in series)
    vals = {int(k[:4]): v for k, v in series.items()}
    ys = [vals[y] for y in yrs]
    roll = [sum(ys[max(0, i - 9):i + 1]) / len(ys[max(0, i - 9):i + 1]) for i in range(len(ys))]
    fig, ax = plt.subplots(figsize=(9, 3.8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    style(ax, "India annual precipitation 1901–2022 (CRU TS4.07 via World Bank CCKP)")
    ax.plot(yrs, ys, color=BLUE, lw=1, alpha=0.45, zorder=2)
    ax.plot(yrs, roll, color=ORANGE, lw=2, zorder=3)
    ax.annotate("10-yr rolling mean", (yrs[-25], roll[-25]), xytext=(0, 12),
                textcoords="offset points", fontsize=8, color=INK2)
    ax.annotate("annual", (yrs[8], ys[8]), xytext=(4, -12), textcoords="offset points",
                fontsize=8, color=INK2)
    ax.set_ylabel("mm/year", color=INK2, fontsize=8)
    fig.tight_layout(); fig.savefig(BASE / "india_rainfall_100y.png", facecolor=SURFACE)
    plt.close(fig)
    print("rainfall series:", yrs[0], "->", yrs[-1], f"| mean {sum(ys)/len(ys):.0f} mm")


if __name__ == "__main__":
    main()
