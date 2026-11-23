import csv
import math
import sys

import pytest


@pytest.fixture
def crm(load, tmp_path, monkeypatch):
    m = load("city_rain_metrics")
    monkeypatch.setattr(m, "BASE", tmp_path)
    (tmp_path / "data").mkdir()
    return m


def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "city", "rain_mm"])
        w.writerows(rows)


def metrics(tmp_path):
    return {(r["city"], int(r["year"])): r for r in csv.DictReader(open(tmp_path / "city_rain_metrics.csv"))}


def test_category_thresholds_are_inclusive(crm, tmp_path, monkeypatch):
    rows = [["2020-01-10", "x", v] for v in (2.4, 2.5, 64.4, 64.5, 115.5, 115.6, 204.4, 204.5)]
    src = tmp_path / "data" / "city_daily.csv"
    write(src, rows)
    monkeypatch.setattr(crm, "SRCS", [src])
    crm.main()
    r = metrics(tmp_path)[("x", 2020)]
    assert r["rainy_days"] == "7"                       # >= 2.5
    assert r["heavy_days"] == "5"                       # >= 64.5
    assert r["very_heavy_days"] == "3"                  # >= 115.6
    assert r["extremely_heavy_days"] == "1"             # >= 204.5
    assert r["max_1day_mm"] == "204.5" and r["days_reported"] == "8"
    assert float(r["annual_mm"]) == round(sum(float(x[2]) for x in rows), 1)


def test_season_totals_use_the_right_months(crm, tmp_path, monkeypatch):
    rows = [[f"2021-{m:02d}-15", "x", float(m)] for m in range(1, 13)]
    src = tmp_path / "data" / "city_daily.csv"
    write(src, rows)
    monkeypatch.setattr(crm, "SRCS", [src])
    crm.main()
    r = metrics(tmp_path)[("x", 2021)]
    assert float(r["jjas_mm"]) == 6 + 7 + 8 + 9          # Jun-Sep inclusive: May and Oct excluded
    assert float(r["ond_mm"]) == 10 + 11 + 12            # Oct-Dec
    assert float(r["annual_mm"]) == 78.0


def test_multiple_sources_are_merged_and_sorted(crm, tmp_path, monkeypatch, capsys):
    a, b = tmp_path / "data" / "city_daily.csv", tmp_path / "data" / "city_daily_more.csv"
    write(a, [["2020-06-01", "pune", 1.0], ["2019-06-01", "chennai", 1.0], ["2020-06-02", "chennai", 2.0]])
    write(b, [["2020-06-01", "mumbai", 5.0], ["2020-06-03", "chennai", 4.0]])
    monkeypatch.setattr(crm, "SRCS", [a, b])
    crm.main()
    lines = open(tmp_path / "city_rain_metrics.csv").read().splitlines()
    assert lines[0].startswith("city,year,annual_mm,jjas_mm,ond_mm,rainy_days,heavy_days")
    assert [l.split(",")[:2] for l in lines[1:]] == [["chennai", "2019"], ["chennai", "2020"], ["mumbai", "2020"], ["pune", "2020"]]
    assert metrics(tmp_path)[("chennai", 2020)]["annual_mm"] == "6.0" and metrics(tmp_path)[("chennai", 2020)]["days_reported"] == "2"
    assert "(4 city-years)" in capsys.readouterr().out


def test_default_sources_are_the_data_glob(load, tmp_path, monkeypatch):
    import city_rain_metrics as base  # noqa: F401  (imported by load below with argv)
    m = load("city_rain_metrics")
    assert [p.name for p in m.SRCS] == sorted(p.name for p in (m.BASE / "data").glob("city_daily*.csv"))
    m2 = load("city_rain_metrics", str(tmp_path / "a.csv"), str(tmp_path / "b.csv"))
    assert [p.name for p in m2.SRCS] == ["a.csv", "b.csv"]                 # explicit CSVs win


def full_years(city, years, mm=1.0, ndays=300):
    out = []
    for y in years:
        for d in range(ndays):
            month = 1 + d * 12 // 366
            day = 1 + d % 28
            out.append([f"{y}-{month:02d}-{day:02d}", city, mm])
    return out


def test_charts_are_written_for_few_and_many_cities(crm, tmp_path, monkeypatch, capsys):
    src = tmp_path / "data" / "city_daily.csv"
    few = full_years("chennai", range(2000, 2003)) + full_years("bengaluru", range(2000, 2003))
    write(src, few)
    monkeypatch.setattr(crm, "SRCS", [src])
    crm.main()
    assert (tmp_path / "city_monsoon_trend.png").stat().st_size > 1000
    assert (tmp_path / "city_extreme_days.png").stat().st_size > 1000
    assert "wrote city_monsoon_trend.png, city_extreme_days.png" in capsys.readouterr().out
    for f in ("city_monsoon_trend.png", "city_extreme_days.png"):
        (tmp_path / f).unlink()
    many = []
    for c in ("a", "b", "c", "d", "e"):                          # > 3 cities -> two-column layout
        many += full_years(c, range(2000, 2002))
    write(src, many)
    crm.main()
    assert (tmp_path / "city_monsoon_trend.png").exists() and (tmp_path / "city_extreme_days.png").exists()


def test_single_city_and_short_years(crm, tmp_path, monkeypatch):
    import matplotlib.figure as mf
    seen = []
    real = mf.Figure.savefig
    monkeypatch.setattr(mf.Figure, "savefig", lambda self, *a, **k: (seen.append(
        [list(l.get_xdata()) for l in self.axes[0].lines]), real(self, *a, **k))[1])
    src = tmp_path / "data" / "city_daily.csv"
    write(src, full_years("solo", [2001]) + full_years("solo", [2002], ndays=299))     # 299 days: excluded from plots
    monkeypatch.setattr(crm, "SRCS", [src])
    crm.main()
    assert metrics(tmp_path)[("solo", 2002)]["days_reported"] == "299"       # still in the CSV
    assert (tmp_path / "city_monsoon_trend.png").exists()
    assert seen[0][0] == [2001]                          # the 299-day year is left out of the plotted series


def test_no_rows_writes_header_only_and_no_charts(crm, tmp_path, monkeypatch, capsys):
    src = tmp_path / "data" / "city_daily.csv"
    write(src, [])
    monkeypatch.setattr(crm, "SRCS", [src])
    crm.main()
    assert len(open(tmp_path / "city_rain_metrics.csv").read().splitlines()) == 1
    assert not (tmp_path / "city_monsoon_trend.png").exists()
    assert "(0 city-years)" in capsys.readouterr().out


def test_style_helper_sets_title_and_grid(crm):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    crm.style(ax, "hello")
    assert ax.get_title(loc="left") == "hello"
    assert ax.get_axisbelow() is True
    plt.close(fig)
