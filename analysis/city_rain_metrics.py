#!/usr/bin/env python3
"""Derived rainfall metrics per city-year from the IMD 0.25° cell series.

Input: city_daily.csv (date, city, rain_mm) — built by the IMD grid collector.
Output (this dir): city_rain_metrics.csv + two charts.

Metrics per city-year, using IMD's official daily-intensity categories:
  annual_mm            total
  jjas_mm              SW monsoon (Jun–Sep) — Bengaluru's wet season
  ond_mm               NE monsoon (Oct–Dec) — Chennai's wet season
  rainy_days           ≥ 2.5 mm
  heavy_days           ≥ 64.5 mm   (IMD "heavy")
  very_heavy_days      ≥ 115.6 mm  (IMD "very heavy")
  extremely_heavy_days ≥ 204.5 mm  (IMD "extremely heavy")
  max_1day_mm          wettest single day

Usage: python3 city_rain_metrics.py /path/to/city_daily.csv
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "data" / "city_daily.csv"

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e5e4e0", "#fcfcfb"


def style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=8)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def main():
    acc = defaultdict(lambda: {"annual": 0.0, "jjas": 0.0, "ond": 0.0, "rainy": 0,
                               "heavy": 0, "very_heavy": 0, "extremely_heavy": 0,
                               "max1d": 0.0, "days": 0})
    with open(SRC) as f:
        for r in csv.DictReader(f):
            y, m = int(r["date"][:4]), int(r["date"][5:7])
            mm = float(r["rain_mm"])
            a = acc[(r["city"], y)]
            a["annual"] += mm
            a["days"] += 1
            if 6 <= m <= 9:
                a["jjas"] += mm
            if 10 <= m <= 12:
                a["ond"] += mm
            if mm >= 2.5:
                a["rainy"] += 1
            if mm >= 64.5:
                a["heavy"] += 1
            if mm >= 115.6:
                a["very_heavy"] += 1
            if mm >= 204.5:
                a["extremely_heavy"] += 1
            a["max1d"] = max(a["max1d"], mm)

    out = BASE / "city_rain_metrics.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city", "year", "annual_mm", "jjas_mm", "ond_mm", "rainy_days",
                    "heavy_days", "very_heavy_days", "extremely_heavy_days",
                    "max_1day_mm", "days_reported"])
        for (city, y) in sorted(acc):
            a = acc[(city, y)]
            # a year with big gaps would understate everything — flag, keep
            w.writerow([city, y, round(a["annual"], 1), round(a["jjas"], 1),
                        round(a["ond"], 1), a["rainy"], a["heavy"], a["very_heavy"],
                        a["extremely_heavy"], round(a["max1d"], 1), a["days"]])
    print(f"wrote {out} ({len(acc)} city-years)")

    cities = sorted({c for c, _ in acc})
    if not cities:
        return

    def series(city, key):
        ys = sorted(y for c, y in acc if c == city and acc[(c, y)]["days"] >= 300)
        return ys, [acc[(city, y)][key] for y in ys]

    def roll10(vals):
        return [sum(vals[max(0, i - 9):i + 1]) / len(vals[max(0, i - 9):i + 1])
                for i in range(len(vals))]

    # ---- monsoon totals: each city gets ITS monsoon
    fig, axes = plt.subplots(len(cities), 1, figsize=(9, 3.2 * len(cities)), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    axes = axes if len(cities) > 1 else [axes]
    season = {"bengaluru": ("jjas", "SW monsoon Jun–Sep"),
              "chennai": ("ond", "NE monsoon Oct–Dec")}
    for ax, city in zip(axes, cities):
        key, label = season.get(city, ("jjas", "Jun–Sep"))
        ys, vals = series(city, key)
        style(ax, f"{city.title()} {label} rainfall — annual + 10-yr mean")
        ax.plot(ys, vals, color=BLUE, lw=1, alpha=0.45)
        ax.plot(ys, roll10(vals), color=ORANGE, lw=2)
        ax.set_ylabel("mm", color=INK2, fontsize=8)
    fig.tight_layout()
    fig.savefig(BASE / "city_monsoon_trend.png", facecolor=SURFACE)
    plt.close(fig)

    # ---- extreme days (heavy ≥64.5) per decade, per city
    fig, axes = plt.subplots(len(cities), 1, figsize=(9, 3.0 * len(cities)), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    axes = axes if len(cities) > 1 else [axes]
    for ax, city in zip(axes, cities):
        dec = defaultdict(int)
        for (c, y) in acc:
            if c == city and acc[(c, y)]["days"] >= 300:
                dec[y // 10 * 10] += acc[(c, y)]["heavy"]
        ks = sorted(dec)
        n_years = defaultdict(int)
        for (c, y) in acc:
            if c == city and acc[(c, y)]["days"] >= 300:
                n_years[y // 10 * 10] += 1
        labels = [f"{k}s" if n_years[k] >= 10 else f"{k}s\n({n_years[k]}y)" for k in ks]
        style(ax, f"{city.title()} heavy-rain days (≥64.5 mm) per decade")
        ax.bar(labels, [dec[k] for k in ks], color=BLUE, width=0.7,
               zorder=3)
        for i, k in enumerate(ks):
            ax.annotate(dec[k], (i, dec[k]), xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=7.5, color=INK2)
    fig.tight_layout()
    fig.savefig(BASE / "city_extreme_days.png", facecolor=SURFACE)
    plt.close(fig)
    print("wrote city_monsoon_trend.png, city_extreme_days.png")


if __name__ == "__main__":
    main()
