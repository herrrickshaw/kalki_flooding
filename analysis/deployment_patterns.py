#!/usr/bin/env python3
"""Flood-sensor deployment-pattern analysis.

No observation history exists yet (access PENDING), but sensor PLACEMENT is
itself data: each city's engineering department encoded a decade of flood
experience in where it bolted level gauges. This classifies every sensor by
the infrastructure it watches and maps the spatial pattern per city.

Outputs: chennai_map.png, pune_map.png, pattern_summary.csv (this dir).
"""
import math
import re
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parent
DB = BASE.parent / "flood.duckdb"

# dataviz reference palette — first 3 categorical slots (all-pairs safe), light mode
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e5e4e0"
SURFACE = "#fcfcfb"


def classify_chennai(name):
    n = name.lower()
    if "subway" in n:
        return "Road underpass"
    if "canal" in n or "nullah" in n or "odai" in n:
        return "Canal / drain"
    return "Other low-lying"


def classify_pune(name, addr):
    a = (addr or "").lower()
    if re.search(r"nala|nale|odha|oda\b|canal", a):
        return "Nala / drain / lake"
    if re.search(r"talav|lake", a):
        return "Nala / drain / lake"  # lakes are the nala headwaters (Katraj → Ambil Odha)
    # river names, or a bridge/bandhara/riverside site whose address omits the
    # river word (Holkar Bridge, Sambhaji Bridge, Kharadi Samshanbhumi are all
    # on the Mula/Mutha) — "bridge" minus nala/canal implies a river crossing
    if re.search(r"\briver\b|mula|mutha|pawana|bridge|bandhara|samsham", a):
        return "River (bridge gauge)"
    return "Locality hotspot"


def load():
    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute(
        """SELECT instance, name, label, address, lon, lat FROM resources
           WHERE item_status='ACTIVE' AND lon IS NOT NULL ORDER BY name"""
    ).fetchall()
    con.close()
    return rows


def style_ax(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=13, loc="left", pad=12)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def scatter_by_cat(ax, pts, cat_colors):
    for cat, (color, marker) in cat_colors.items():
        xs = [p[0] for p in pts if p[2] == cat]
        ys = [p[1] for p in pts if p[2] == cat]
        ax.scatter(xs, ys, s=52, c=color, marker=marker, label=f"{cat} ({len(xs)})",
                   edgecolors=SURFACE, linewidths=1.2, zorder=3)


def km_scale(ax, lat0, lon0, km=5):
    dlon = km / (111.32 * math.cos(math.radians(lat0)))
    ax.plot([lon0, lon0 + dlon], [ax.get_ylim()[0] + 0.004] * 2, color=INK2, lw=2)
    ax.annotate(f"{km} km", (lon0 + dlon / 2, ax.get_ylim()[0] + 0.0075),
                ha="center", color=INK2, fontsize=8)


def main():
    rows = load()
    chennai = [(lon, lat, classify_chennai(name), name)
               for inst, name, lbl, addr, lon, lat in rows if inst == "chennai"]
    pune = [(lon, lat, classify_pune(name, addr), name, addr)
            for inst, name, lbl, addr, lon, lat in rows if inst == "pune"]

    # ---------------- Chennai
    fig, ax = plt.subplots(figsize=(7.5, 8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    lat0 = sum(p[1] for p in chennai) / len(chennai)
    ax.set_aspect(1 / math.cos(math.radians(lat0)))
    style_ax(ax, "Chennai: 27 flood sensors — where the city expects water\n"
                 "to pool (underpasses) and drains to choke (canals)")
    scatter_by_cat(ax, [(p[0], p[1], p[2]) for p in chennai],
                   {"Road underpass": (C1, "o"), "Canal / drain": (C2, "s"),
                    "Other low-lying": (C3, "^")})
    # selective direct labels — the pattern anchors only
    marks = {"FM_Box1_Kathivakkam_High_Road": ("Kathivakkam\n(Ennore, N. edge)", (8, 4)),
             "FM_Box2_Vyasarpadi_Subway": ("N. Chennai underpass\ncluster", (10, -2)),
             "FM_Box1_Stephenson_Bridge_Otteri_Nullah_Canal": ("Otteri Nullah", (8, 2)),
             "FM_Box1_Mambalam_Canal_CIT_Nagar": ("Mambalam drain (SW)", (6, -10)),
             "FM_Box1_Veerangal_Odai_Near_Puzhuthivakkam": ("Veerangal Odai (S.)", (10, 8)),
             "FM_Box1_Nandambakkam_Canal_MIOT": ("Adyar basin", (8, -3))}
    for lon, lat, cat, name in chennai:
        if name in marks:
            txt, off = marks[name]
            ax.annotate(txt, (lon, lat), xytext=off, textcoords="offset points",
                        fontsize=7.5, color=INK2)
    # north-bias band
    ax.axhline(13.09, color=INK2, lw=0.8, ls="--")
    ax.annotate("12 of 27 sensors north of lat 13.09°\n(North Chennai / Buckingham Canal corridor)",
                (80.176, 13.087), fontsize=7.5, color=INK2, va="top")
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=INK)
    km_scale(ax, lat0, 80.27, 5)
    ax.set_xlabel("longitude", color=INK2, fontsize=8)
    ax.set_ylabel("latitude", color=INK2, fontsize=8)
    fig.tight_layout()
    fig.savefig(BASE / "chennai_map.png", facecolor=SURFACE)
    plt.close(fig)

    # ---------------- Pune
    fig, ax = plt.subplots(figsize=(8.5, 7.5), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    lat0 = sum(p[1] for p in pune) / len(pune)
    ax.set_aspect(1 / math.cos(math.radians(lat0)))
    style_ax(ax, "Pune: 46 flood sensors — bridge gauges string along the rivers,\n"
                 "nala sensors track flash-flood drains, lakes guard the headwaters")
    scatter_by_cat(ax, [(p[0], p[1], p[2]) for p in pune],
                   {"River (bridge gauge)": (C1, "o"), "Nala / drain / lake": (C2, "s"),
                    "Locality hotspot": (C3, "^")})
    pmarks = {"FWR058": ("Mula enters city\n(Balewadi–Hinjawadi)", (-15, 8)),
              "FWR054": ("Pawana confluence", (8, 3)),
              "FWR064": ("Bund Garden —\nMula–Mutha confluence", (8, 0)),
              "FWR046": ("Kharadi (exit gauge)", (-40, -12)),
              "FWR066": ("Katraj Upper Lake\n(Ambil Odha headwater)", (6, -12)),
              "FWR026": ("Ambil Odha\n(2019 flash flood)", (8, -2)),
              "FWR059": ("Mutha upstream\n(Khadakwasla releases)", (-30, -18))}
    for lon, lat, cat, name, addr in pune:
        if name in pmarks:
            txt, off = pmarks[name]
            ax.annotate(txt, (lon, lat), xytext=off, textcoords="offset points",
                        fontsize=7.5, color=INK2)
    ax.legend(loc="lower right", fontsize=8, frameon=False, labelcolor=INK)
    km_scale(ax, lat0, 73.76, 5)
    ax.set_xlabel("longitude", color=INK2, fontsize=8)
    ax.set_ylabel("latitude", color=INK2, fontsize=8)
    fig.tight_layout()
    fig.savefig(BASE / "pune_map.png", facecolor=SURFACE)
    plt.close(fig)

    # ---------------- summary table (the "table view")
    import csv
    with open(BASE / "pattern_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city", "sensor", "category", "watches", "lon", "lat"])
        for lon, lat, cat, name in chennai:
            w.writerow(["chennai", name, cat, "", lon, lat])
        for lon, lat, cat, name, addr in pune:
            w.writerow(["pune", name, cat, addr, lon, lat])

    # console stats
    from collections import Counter
    print("Chennai:", Counter(p[2] for p in chennai))
    print("  north of 13.09:", sum(1 for p in chennai if p[1] > 13.09), "of", len(chennai))
    print("Pune:", Counter(p[2] for p in pune))
    riv = [p for p in pune if p[2] == "River (bridge gauge)"]
    print("  river gauges west-to-east span:",
          f"{min(p[0] for p in riv):.3f} → {max(p[0] for p in riv):.3f} lon "
          f"(~{(max(p[0] for p in riv)-min(p[0] for p in riv))*111.32*math.cos(math.radians(18.53)):.0f} km)")


if __name__ == "__main__":
    main()
