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

Usage: python3 city_rain_metrics.py [csv ...]   (default: data/city_daily*.csv)
"""
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
SRCS = ([Path(p) for p in sys.argv[1:]]
        or sorted((BASE / "data").glob("city_daily*.csv")))

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


MIN_DAYS = 300  # a city-year needs this many reported days to enter the charts

# IMD daily-intensity thresholds (mm) -> accumulator key
INTENSITY = (("rainy", 2.5), ("heavy", 64.5), ("very_heavy", 115.6), ("extremely_heavy", 204.5))
SEASON = {"bengaluru": ("jjas", "SW monsoon Jun–Sep"),
          "chennai": ("ond", "NE monsoon Oct–Dec")}


def new_acc():
    return {"annual": 0.0, "jjas": 0.0, "ond": 0.0, "rainy": 0,
            "heavy": 0, "very_heavy": 0, "extremely_heavy": 0,
            "max1d": 0.0, "days": 0}


def accumulate(srcs):
    """{(city, year): metrics} summed over the daily CSVs (date, city, rain_mm)."""
    acc = defaultdict(new_acc)
    for src in srcs:
        with open(src) as f:
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
                for key, threshold in INTENSITY:
                    if mm >= threshold:
                        a[key] += 1
                a["max1d"] = max(a["max1d"], mm)
    return acc


def write_metrics(acc, out):
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


def series(acc, city, key):
    ys = sorted(y for c, y in acc if c == city and acc[(c, y)]["days"] >= MIN_DAYS)
    return ys, [acc[(city, y)][key] for y in ys]


def roll10(vals):
    return [sum(vals[max(0, i - 9):i + 1]) / len(vals[max(0, i - 9):i + 1])
            for i in range(len(vals))]


def city_axes(n_cities, row_height):
    """Figure + flat list of axes (2 columns for >3 cities); spare axes are hidden."""
    ncols = 2 if n_cities > 3 else 1
    nrows = math.ceil(n_cities / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.5 * ncols, row_height * nrows), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    axes = [a for row in (axes if nrows > 1 else [axes]) for a in (row if ncols > 1 else [row])]
    for ax in axes[n_cities:]:
        ax.axis("off")
    return fig, axes


def plot_monsoon_trend(acc, cities, path):
    """Monsoon totals: each city gets ITS monsoon."""
    fig, axes = city_axes(len(cities), 2.6)
    for ax, city in zip(axes, cities):
        key, label = SEASON.get(city, ("jjas", "Jun–Sep"))
        ys, vals = series(acc, city, key)
        style(ax, f"{city.title()} {label} rainfall — annual + 10-yr mean")
        ax.plot(ys, vals, color=BLUE, lw=1, alpha=0.45)
        ax.plot(ys, roll10(vals), color=ORANGE, lw=2)
        ax.set_ylabel("mm", color=INK2, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def decade_counts(acc, city):
    """(heavy-day totals, years counted) per decade for one city."""
    dec, n_years = defaultdict(int), defaultdict(int)
    for (c, y) in acc:
        if c == city and acc[(c, y)]["days"] >= MIN_DAYS:
            dec[y // 10 * 10] += acc[(c, y)]["heavy"]
            n_years[y // 10 * 10] += 1
    return dec, n_years


def plot_extreme_days(acc, cities, path):
    """Heavy-rain days (≥64.5 mm) per decade, per city."""
    fig, axes = city_axes(len(cities), 2.5)
    for ax, city in zip(axes, cities):
        dec, n_years = decade_counts(acc, city)
        ks = sorted(dec)
        labels = [f"{k}s" if n_years[k] >= 10 else f"{k}s\n({n_years[k]}y)" for k in ks]
        style(ax, f"{city.title()} heavy-rain days (≥64.5 mm) per decade")
        ax.bar(labels, [dec[k] for k in ks], color=BLUE, width=0.7, zorder=3)
        for i, k in enumerate(ks):
            ax.annotate(dec[k], (i, dec[k]), xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=7.5, color=INK2)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main():
    acc = accumulate(SRCS)
    out = BASE / "city_rain_metrics.csv"
    write_metrics(acc, out)
    print(f"wrote {out} ({len(acc)} city-years)")

    cities = sorted({c for c, _ in acc})
    if not cities:
        return
    plot_monsoon_trend(acc, cities, BASE / "city_monsoon_trend.png")
    plot_extreme_days(acc, cities, BASE / "city_extreme_days.png")
    print("wrote city_monsoon_trend.png, city_extreme_days.png")


if __name__ == "__main__":
    main()
