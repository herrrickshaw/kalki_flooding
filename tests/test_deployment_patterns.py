import csv
from collections import Counter

import duckdb
import pytest


@pytest.fixture
def dp(load, tmp_path, monkeypatch):
    m = load("deployment_patterns")
    (tmp_path / "analysis").mkdir()
    monkeypatch.setattr(m, "BASE", tmp_path / "analysis")
    monkeypatch.setattr(m, "DB", tmp_path / "flood.duckdb")
    return m


@pytest.mark.parametrize("name,cat", [
    ("FM_Box2_Vyasarpadi_Subway", "Road underpass"),
    ("FM_Box1_X_SUBWAY_Y", "Road underpass"),
    ("FM_Box1_Stephenson_Bridge_Otteri_Nullah_Canal", "Canal / drain"),
    ("FM_Box1_Veerangal_Odai", "Canal / drain"),
    ("FM_Box1_Foo_Nullah", "Canal / drain"),
    ("FM_Box1_Kathivakkam_High_Road", "Other low-lying"),
    ("Subway_Canal", "Road underpass"),             # underpass wins over canal
])
def test_classify_chennai(dp, name, cat):
    assert dp.classify_chennai(name) == cat


@pytest.mark.parametrize("addr,cat", [
    ("Ambil Odha, Sinhgad Road", "Nala / drain / lake"),
    ("Katraj Talav", "Nala / drain / lake"),
    ("Pashan Lake", "Nala / drain / lake"),
    ("Nale Stop", "Nala / drain / lake"),
    ("Canal Road", "Nala / drain / lake"),
    ("Mula river, Baner", "River (bridge gauge)"),
    ("Holkar Bridge", "River (bridge gauge)"),
    ("Mutha bandhara", "River (bridge gauge)"),
    ("Pawana crossing", "River (bridge gauge)"),
    ("Bridge over nala", "Nala / drain / lake"),     # nala/canal beats the bridge keyword
    ("Viman Nagar Chowk", "Locality hotspot"),
    ("", "Locality hotspot"),
    (None, "Locality hotspot"),
])
def test_classify_pune(dp, addr, cat):
    assert dp.classify_pune("n", addr) == cat


def make_db(path, rows):
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE resources (id TEXT, instance TEXT, name TEXT, label TEXT, address TEXT,
                   lon DOUBLE, lat DOUBLE, item_status TEXT)""")
    for i, r in enumerate(rows):
        con.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?)", [f"id{i}", *r])
    con.close()


def chennai_rows(n=6, north=3):
    return [("chennai", f"S{i}_Subway" if i % 2 else f"S{i}_Canal", "l", "a", 80.2 + i * 0.01,
             13.10 if i < north else 13.0, "ACTIVE") for i in range(n)]


def pune_rows(n=6):
    addrs = ["Mula river", "Ambil Odha", "Viman Nagar", "Holkar Bridge", "Pashan Lake", "Kothrud"]
    return [("pune", f"FWR{i:03d}", "l", addrs[i % len(addrs)], 73.8 + i * 0.01, 18.5, "ACTIVE") for i in range(n)]


def test_load_returns_only_active_located_sensors_sorted_by_name(dp, tmp_path):
    rows = [("chennai", "B", "l", "a", 80.0, 13.0, "ACTIVE"), ("chennai", "A", "l", "a", 80.1, 13.1, "ACTIVE"),
            ("chennai", "C", "l", "a", 80.2, 13.2, "INACTIVE"), ("pune", "D", "l", "a", None, 18.0, "ACTIVE")]
    make_db(dp.DB, rows)
    got = dp.load()
    assert [r[1] for r in got] == ["A", "B"]
    assert got[0] == ("chennai", "A", "l", "a", 80.1, 13.1)


def test_main_writes_summary_charts_and_prints_stats(dp, tmp_path, capsys):
    named = [("chennai", "FM_Box1_Kathivakkam_High_Road", "l", "a", 80.30, 13.20, "ACTIVE"),
             ("chennai", "FM_Box2_Vyasarpadi_Subway", "l", "a", 80.25, 13.12, "ACTIVE"),
             ("pune", "FWR058", "l", "Mula river", 73.77, 18.57, "ACTIVE"),
             ("pune", "FWR026", "l", "Ambil Odha", 73.85, 18.45, "ACTIVE")]
    make_db(dp.DB, chennai_rows() + pune_rows() + named)
    dp.main()
    out = capsys.readouterr().out
    base = dp.BASE
    assert (base / "chennai_map.png").stat().st_size > 1000 and (base / "pune_map.png").stat().st_size > 1000
    rows = list(csv.reader(open(base / "pattern_summary.csv")))
    assert rows[0] == ["city", "sensor", "category", "watches", "lon", "lat"]
    cities = Counter(r[0] for r in rows[1:])
    assert cities == {"chennai": 8, "pune": 8}
    ch = [r for r in rows if r[1] == "FM_Box2_Vyasarpadi_Subway"][0]
    assert ch[2] == "Road underpass" and ch[3] == "" and ch[4:] == ["80.25", "13.12"]
    pu = [r for r in rows if r[1] == "FWR058"][0]
    assert pu[2] == "River (bridge gauge)" and pu[3] == "Mula river"       # Pune rows carry the address
    assert "north of 13.09: " in out and "of 8" in out
    north = sum(1 for r in rows[1:] if r[0] == "chennai" and float(r[5]) > 13.09)
    assert f"north of 13.09: {north} of 8" in out
    assert "Chennai: Counter(" in out and "Pune: Counter(" in out
    rivers = [float(r[4]) for r in rows[1:] if r[0] == "pune" and r[2] == "River (bridge gauge)"]
    span = (max(rivers) - min(rivers))
    assert f"{min(rivers):.3f} → {max(rivers):.3f} lon" in out
    import math
    assert f"(~{span * 111.32 * math.cos(math.radians(18.53)):.0f} km)" in out


def test_km_scale_converts_degrees_by_latitude(dp):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.set_ylim(13.0, 13.2)
    dp.km_scale(ax, 13.0, 80.0, 5)
    line = ax.lines[0]
    d_lon = line.get_xdata()[1] - line.get_xdata()[0]
    import math
    assert d_lon == pytest.approx(5 / (111.32 * math.cos(math.radians(13.0))))
    assert ax.texts[0].get_text() == "5 km"
    plt.close(fig)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="deployment_patterns.py:44 — the river regex ends in `samsham`, which cannot match the "
                          "'Kharadi Samshanbhumi' site the comment above it names (the pattern looks like a typo "
                          "for `samshan`), so that gauge is classed a Locality hotspot")
def test_kharadi_samshanbhumi_is_a_river_gauge(dp):
    assert dp.classify_pune("FWR046", "Kharadi Samshanbhumi") == "River (bridge gauge)"
