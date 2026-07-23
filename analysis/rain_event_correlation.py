#!/usr/bin/env python3
"""Bengaluru: daily IMD cell rainfall × traffic-police waterlogging reports.

Window: 2024-01-01 → 2025-12-31 (IMD daily grid × event feed overlap).
Caveat printed loud: the event feed has a reporting gap Mar–Jul 2025
(6–31 events/month of ALL types, vs hundreds in monsoon 2024), so 2025
event counts understate reality — confirmed by news: the 2025-05-19 flood
(3 dead, 500+ homes, 130 mm/12 h) shows 83.8 mm in the IMD cell but zero
events in the feed.

Output: rain_vs_events.png + printed stats.
"""
import csv
import json
import math
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e5e4e0", "#fcfcfb"

rain = {}
with open(BASE / "data" / "city_daily.csv") as f:
    for r in csv.DictReader(f):
        if r["city"] == "bengaluru" and r["date"] >= "2024-01-01":
            rain[r["date"]] = float(r["rain_mm"])

wl_all = json.load(open(BASE / "data" / "bengaluru_waterlogging.json"))
wl = Counter(e["observationDateTime"][:10] for e in wl_all if e.get("observationDateTime"))

days = sorted(rain)
mm = [rain[d] for d in days]
ev = [wl.get(d, 0) for d in days]


def pearson(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0


print(f"{len(days)} days | same-day r={pearson(mm, ev):.3f} | "
      f"lag-1 r={pearson(mm[:-1], ev[1:]):.3f}")
print("NOTE: event feed gap Mar-Jul 2025 — 2025 counts understate reality")

fig, ax = plt.subplots(figsize=(8, 5.5), dpi=150)
fig.patch.set_facecolor(SURFACE)
ax.set_facecolor(SURFACE)
ax.set_title("Bengaluru 2024–25: daily rainfall (IMD cell) vs waterlogging reports\n"
             "each dot = one day; 2025 feed gap understates its events",
             color=INK, fontsize=11, loc="left", pad=10)
in24 = [d < "2025" for d in days]
ax.scatter([m for m, f in zip(mm, in24) if f], [e for e, f in zip(ev, in24) if f],
           s=22, c=BLUE, alpha=0.6, linewidths=0, label="2024 days")
ax.scatter([m for m, f in zip(mm, in24) if not f], [e for e, f in zip(ev, in24) if not f],
           s=22, c=ORANGE, alpha=0.6, linewidths=0, label="2025 days (feed gap Mar–Jul)")
for d, x, y in zip(days, mm, ev):
    if y >= 46 or x >= 64.5:
        ax.annotate(d, (x, y), xytext=(5, 4), textcoords="offset points",
                    fontsize=7, color=INK2)
ax.set_xlabel("rain (mm/day, IMD 0.25° cell)", color=INK2, fontsize=9)
ax.set_ylabel("waterlogging reports", color=INK2, fontsize=9)
ax.tick_params(colors=INK2, labelsize=8)
for s in ax.spines.values():
    s.set_color(GRID)
ax.grid(True, color=GRID, linewidth=0.6)
ax.set_axisbelow(True)
ax.legend(loc="upper right", fontsize=8, frameon=False, labelcolor=INK)
fig.tight_layout()
fig.savefig(BASE / "rain_vs_events.png", facecolor=SURFACE)
print("wrote rain_vs_events.png")
