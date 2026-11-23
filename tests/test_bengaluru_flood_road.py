import csv
import io
import json
import math

import pytest


def ev(lon, lat, title="water_logging", t="2024-07-05T10:00:00+05:30", swapped=True):
    coords = [lat, lon] if swapped else [lon, lat]               # the police feed is [lat, lon]
    return {"eventTitle": title, "observationDateTime": t, "location": {"type": "Point", "coordinates": coords}}


def spot(lon, lat, sev=50):
    return {"location": {"type": "Point", "coordinates": [lon, lat]}, "severityScore": sev}


CCKP = {"data": {"IND": {f"{y}-01-01": 1000.0 + y for y in range(1901, 2023)}}}


@pytest.fixture
def bfr(load, tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    m = load("bengaluru_flood_road", str(raw))
    monkeypatch.setattr(m, "BASE", tmp_path)
    return m, raw


def put(raw, events, spots, cckp=CCKP):
    json.dump(events, open(raw / "events_full.json", "w"))
    json.dump(spots, open(raw / "blackspots_full.json", "w"))
    if cckp is not None:
        json.dump(cckp, open(raw / "cckp.json", "w"))


def world():
    # centre at (77.60, 12.97); 10 events right on spot A, 3 near spot B, none near C
    events = [ev(77.60, 12.97)] * 10 + [ev(77.70 + i * 1e-4, 13.05) for i in range(3)]
    events += [ev(77.50, 12.90, t="2024-08-01T00:00:00+05:30"), ev(77.80, 13.20, t="2025-01-15T00:00:00+05:30")]
    events += [ev(77.6, 12.97, title="accident"), {"eventTitle": "water_logging"},       # wrong type / no location
               {"eventTitle": "water_logging", "location": {"coordinates": []}}]
    spots = [spot(77.60, 12.97, 80), spot(77.70, 13.05, 40), spot(77.40, 13.25, 10)]
    return events, spots


def test_norm_via_coordinates_and_filtering(bfr, capsys):
    m, raw = bfr
    events, spots = world()
    events.append(ev(20.6, -11.4))                                     # GPS garbage far outside the city
    events.append(ev(77.95, 13.0))                                     # just east of the bbox (lon > 77.9)
    put(raw, events, spots)
    m.main()
    out = capsys.readouterr().out
    assert "dropped 2 events with out-of-city coords" in out
    assert "waterlogging events with coords: 15 | blackspots: 3" in out


def test_bbox_edges_are_exclusive(bfr, capsys):
    m, raw = bfr
    inside = [ev(77.5, 12.8), ev(77.6, 12.9)]
    edge = [ev(77.3, 12.9), ev(77.9, 12.9), ev(77.5, 12.7), ev(77.5, 13.3)]      # exactly on a bound: dropped
    put(raw, inside + edge, [spot(77.5, 12.8)])
    m.main()
    out = capsys.readouterr().out
    assert "dropped 4 events" in out and "waterlogging events with coords: 2 | blackspots: 1" in out


def test_properly_ordered_coordinates_are_not_swapped(bfr, capsys):
    m, raw = bfr
    events = ([ev(77.6, 12.97, swapped=False)] * 4 + [ev(77.6, 12.97, swapped=True)] * 6
              + [ev(77.5, 12.9), ev(77.7, 13.1)])               # spread so the bounding box has an area
    put(raw, events, [spot(77.6, 12.97)])
    m.main()
    rows = list(csv.DictReader(open(m.BASE / "blackspot_waterlogging.csv")))
    assert rows[0]["wl_within_1km"] == "10"                           # both orderings land on the same point


def test_proximity_counts_ratio_and_ordering(bfr, capsys):
    m, raw = bfr
    events, spots = world()
    put(raw, events, spots)
    m.main()
    out = capsys.readouterr().out
    rows = list(csv.DictReader(open(m.BASE / "blackspot_waterlogging.csv")))
    assert [int(r["wl_within_1km"]) for r in rows] == [10, 3, 0]          # sorted by count, descending
    assert [r["severity"] for r in rows] == ["80", "40", "10"]
    # uniform expectation: events / bbox area (km^2) x pi
    pts = [(77.60, 12.97)] * 10 + [(77.70 + i * 1e-4, 13.05) for i in range(3)] + [(77.50, 12.90), (77.80, 13.20)]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    area = (max(lons) - min(lons)) * 111.32 * math.cos(math.radians(sum(lats) / len(lats))) * (max(lats) - min(lats)) * 111.32
    exp = len(pts) / area * math.pi
    assert float(rows[0]["ratio_vs_uniform"]) == pytest.approx(round(10 / exp, 2), abs=0.01)
    assert rows[2]["ratio_vs_uniform"] == "0.0"
    assert "blackspots with ≥1 waterlogging event within 1 km: 2/3" in out
    assert f"uniform expectation per 1-km disc: {exp:.1f}" in out
    med = sorted(float(r["ratio_vs_uniform"]) for r in rows)[1]
    assert f"median blackspot ratio: {med}" in out


def test_one_kilometre_radius_is_inclusive_of_the_boundary_side(bfr):
    m, raw = bfr
    a, b = (77.60, 12.97), (77.60, 12.97 + 1.0 / 111.32)
    assert m.dist_km(a, b) == pytest.approx(1.0)
    assert m.dist_km(a, a) == 0
    assert m.km(0.0) == pytest.approx(111.32) and m.km(60.0) == pytest.approx(111.32 / 2)
    # east-west distance shrinks with latitude
    assert m.dist_km((77.0, 60.0), (78.0, 60.0)) == pytest.approx(111.32 / 2, rel=1e-6)


def test_outputs_and_seasonality_peak(bfr, capsys):
    m, raw = bfr
    events, spots = world()
    put(raw, events, spots)
    m.main()
    out = capsys.readouterr().out
    for f in ("bengaluru_flood_road_map.png", "waterlogging_seasonality.png", "india_rainfall_100y.png"):
        assert (m.BASE / f).stat().st_size > 1000, f
    assert "rainfall series: 1901 -> 2022 | mean 2962 mm" in out          # mean of 1000 + year over 1901..2022
    assert "dropped" not in out


def test_rainfall_is_fetched_with_a_browser_agent_when_not_cached(bfr, monkeypatch, capsys):
    m, raw = bfr
    events, spots = world()
    put(raw, events, spots, cckp=None)
    seen = {}

    class R:
        def read(self):
            return json.dumps({"data": {"IND": {f"{1901 + i}-01-01": float(10 * (i + 1)) for i in range(30)}}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        seen.update(url=req.full_url, ua=req.get_header("User-agent"), timeout=timeout)
        return R()

    monkeypatch.setattr(m.urllib.request, "urlopen", fake)
    m.main()
    assert "cckpapi.worldbank.org" in seen["url"] and seen["ua"] == "Mozilla/5.0" and seen["timeout"] == 30
    assert "rainfall series: 1901 -> 1930 | mean 155 mm" in capsys.readouterr().out


def test_raw_dir_defaults_to_the_data_folder(load):
    m = load("bengaluru_flood_road")
    assert m.RAW == m.BASE / "data"
    assert load("bengaluru_flood_road", "/some/where").RAW.as_posix() == "/some/where"


@pytest.mark.xfail(strict=True, raises=ZeroDivisionError,
                   reason="bengaluru_flood_road.py:98 — when every waterlogging event sits at one point (or there "
                          "is only one), the bounding-box area is 0 and the density division raises "
                          "ZeroDivisionError instead of the intended `if expected_1km` guard")
def test_a_single_cluster_of_events_does_not_crash(bfr, capsys):
    m, raw = bfr
    put(raw, [ev(77.6, 12.97)] * 5, [spot(77.6, 12.97)])
    m.main()
    assert "within 1 km: 1/1" in capsys.readouterr().out


def test_a_blackspot_exactly_one_kilometre_away_counts(bfr, monkeypatch):
    m, raw = bfr
    events = [ev(77.5, 12.9), ev(77.7, 13.1), ev(77.6, 13.0)]
    put(raw, events, [spot(77.6, 13.0)])
    monkeypatch.setattr(m, "dist_km", lambda a, b: 1.0)
    m.main()
    rows = list(csv.DictReader(open(m.BASE / "blackspot_waterlogging.csv")))
    assert rows[0]["wl_within_1km"] == "3"
