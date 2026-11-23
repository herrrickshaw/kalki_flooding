import csv
import json
import math
import sys
from pathlib import Path

import pytest
from conftest import ROOT, run_script

SCRIPT = ROOT / "analysis" / "rain_event_correlation.py"


def pearson(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den


@pytest.fixture
def area(tmp_path):
    (tmp_path / "analysis" / "data").mkdir(parents=True)      # the script resolves data/ next to itself
    return tmp_path


def setup_data(area, rain, events, other_city=True):
    with open(area / "analysis" / "data" / "city_daily.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "city", "rain_mm"])
        for d, v in rain.items():
            w.writerow([d, "bengaluru", v])
        if other_city:
            w.writerow(["2024-01-01", "chennai", 500.0])           # another city must not leak in
    ev = []
    for d, n in events.items():
        ev += [{"observationDateTime": f"{d}T10:00:00+05:30"}] * n
    ev.append({"observationDateTime": None})
    ev.append({})
    json.dump(ev, open(area / "analysis" / "data" / "bengaluru_waterlogging.json", "w"))


def run(area, monkeypatch):
    return run_script(SCRIPT, area / "analysis" / "exec_rain_event_correlation.py", monkeypatch=monkeypatch)


def test_correlations_match_the_formula(area, monkeypatch, capsys):
    days = [f"2024-06-{d:02d}" for d in range(1, 11)]
    rain = dict(zip(days, [0, 5, 70, 10, 0, 3, 90, 1, 0, 20]))
    events = dict(zip(days, [0, 1, 50, 4, 0, 0, 60, 2, 0, 5]))
    setup_data(area, rain, events)
    g = run(area, monkeypatch)
    out = capsys.readouterr().out
    mm = [rain[d] for d in days]
    ev = [events[d] for d in days]
    assert f"10 days | same-day r={pearson(mm, ev):.3f} | lag-1 r={pearson(mm[:-1], ev[1:]):.3f}" in out
    assert "NOTE: event feed gap Mar-Jul 2025" in out and "wrote rain_vs_events.png" in out
    assert g["days"] == days
    assert (area / "analysis" / "rain_vs_events.png").stat().st_size > 1000


def test_only_bengaluru_rows_from_2024_are_used_and_dates_without_events_count_zero(area, monkeypatch, capsys):
    rain = {"2023-12-31": 400.0, "2024-01-01": 1.0, "2024-01-02": 2.0, "2025-12-31": 3.0}
    setup_data(area, rain, {"2024-01-02": 4})
    g = run(area, monkeypatch)
    assert g["days"] == ["2024-01-01", "2024-01-02", "2025-12-31"]      # 2023 row dropped, chennai ignored
    assert g["ev"] == [0, 4, 0] and g["mm"] == [1.0, 2.0, 3.0]
    assert g["in24"] == [True, True, False]


def test_pearson_of_a_constant_series_is_zero(area, monkeypatch, capsys):
    days = [f"2024-03-{d:02d}" for d in range(1, 6)]
    setup_data(area, {d: 7.0 for d in days}, {d: i for i, d in enumerate(days)})
    run(area, monkeypatch)
    assert "same-day r=0.000 | lag-1 r=0.000" in capsys.readouterr().out


def test_pearson_perfect_relationships(area, monkeypatch, capsys):
    days = [f"2024-03-{d:02d}" for d in range(1, 7)]
    rain = dict(zip(days, [1, 2, 3, 4, 5, 6]))
    setup_data(area, rain, dict(zip(days, [2, 4, 6, 8, 10, 12])))
    g = run(area, monkeypatch)
    assert "same-day r=1.000" in capsys.readouterr().out
    assert g["pearson"]([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
