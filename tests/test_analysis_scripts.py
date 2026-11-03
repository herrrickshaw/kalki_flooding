"""Characterisation tests for analysis/bengaluru_flood_road.py and analysis/city_rain_metrics.py.

Each script is copied into a temp dir (outputs land next to the script) and run on synthetic
input; stdout and the CSV outputs are compared with golden text, and PNGs must be produced.
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

ANALYSIS = Path(__file__).resolve().parents[1] / "analysis"
GOLDEN = Path(__file__).resolve().parent / "golden"


def _run(tmp_path: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(ANALYSIS / script, work / script)
    return subprocess.run([sys.executable, str(work / script), *args], capture_output=True,
                          text=True, check=True, cwd=work)


def _bengaluru_inputs(raw: Path) -> None:
    rng = random.Random(7)
    events = []
    for i in range(60):
        lat, lon = 12.9 + rng.random() * 0.2, 77.5 + rng.random() * 0.2
        month = 1 + i % 12
        events.append({"eventTitle": "water_logging", "location": {"coordinates": [lat, lon]},
                       "observationDateTime": f"2024-{month:02d}-05T10:00:00"})
    events.append({"eventTitle": "water_logging", "location": {"coordinates": [20.6, -11.4]},
                   "observationDateTime": "2024-06-01T00:00:00"})
    events.append({"eventTitle": "other", "location": {"coordinates": [13.0, 77.6]}})
    spots = [{"location": {"coordinates": [77.5 + rng.random() * 0.2, 12.9 + rng.random() * 0.2]},
              "severityScore": rng.randint(5, 40)} for _ in range(8)]
    spots.append({"location": None})
    raw.mkdir()
    (raw / "events_full.json").write_text(json.dumps(events))
    (raw / "blackspots_full.json").write_text(json.dumps(spots))
    series = {f"{y}-01-01": 900 + (y * 37) % 200 for y in range(1901, 1931)}
    (raw / "cckp.json").write_text(json.dumps({"data": {"IND": series}}))


def test_bengaluru_flood_road(tmp_path):
    raw = tmp_path / "raw"
    _bengaluru_inputs(raw)
    res = _run(tmp_path, "bengaluru_flood_road.py", str(raw))
    out = tmp_path / "work"
    assert res.stdout == (GOLDEN / "bengaluru_stdout.txt").read_text()
    assert (out / "blackspot_waterlogging.csv").read_text() == (GOLDEN / "bengaluru.csv").read_text()
    for png in ("bengaluru_flood_road_map.png", "waterlogging_seasonality.png", "india_rainfall_100y.png"):
        assert (out / png).stat().st_size > 1000


def _city_csv(path: Path) -> None:
    rng = random.Random(3)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "city", "rain_mm"])
        for city in ("bengaluru", "chennai", "pune"):
            for y in (2000, 2001):
                for d in range(365):
                    month, day = 1 + d // 31 % 12, 1 + d % 28
                    mm = rng.choice([0, 0, 1.2, 3.0, 70.0, 120.0, 210.0]) if d % 3 == 0 else 0.0
                    w.writerow([f"{y}-{month:02d}-{day:02d}", city, mm])


def test_city_rain_metrics(tmp_path):
    src = tmp_path / "city_daily_test.csv"
    _city_csv(src)
    res = _run(tmp_path, "city_rain_metrics.py", str(src))
    out = tmp_path / "work"
    assert res.stdout.replace(str(out.resolve()), "<W>") == (GOLDEN / "city_stdout.txt").read_text()
    assert (out / "city_rain_metrics.csv").read_text() == (GOLDEN / "city_rain_metrics.csv").read_text()
    for png in ("city_monsoon_trend.png", "city_extreme_days.png"):
        assert (out / png).stat().st_size > 1000


def test_city_rain_metrics_empty_input(tmp_path):
    src = tmp_path / "empty.csv"
    src.write_text("date,city,rain_mm\n")
    res = _run(tmp_path, "city_rain_metrics.py", str(src))
    assert "(0 city-years)" in res.stdout
    assert not (tmp_path / "work" / "city_monsoon_trend.png").exists()
