"""IMD intensity-category aggregation in analysis/city_rain_metrics.py,
run on a hand-made daily series in tmp_path (no repo data touched)."""
import csv

import city_rain_metrics as crm


def _run(tmp_path, monkeypatch, rows):
    src = tmp_path / "city_daily_test.csv"
    with open(src, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "city", "rain_mm"])
        w.writerows(rows)
    monkeypatch.setattr(crm, "SRCS", [src])
    monkeypatch.setattr(crm, "BASE", tmp_path)
    crm.main()
    with open(tmp_path / "city_rain_metrics.csv") as f:
        return {(r["city"], r["year"]): r for r in csv.DictReader(f)}


def test_imd_thresholds_are_inclusive_and_cumulative(tmp_path, monkeypatch, capsys):
    rows = [
        ["2020-01-01", "chennai", 2.4],    # below rainy-day threshold
        ["2020-01-02", "chennai", 2.5],    # rainy
        ["2020-07-01", "chennai", 64.5],   # heavy (JJAS)
        ["2020-08-01", "chennai", 115.6],  # very heavy (JJAS)
        ["2020-11-01", "chennai", 204.5],  # extremely heavy (OND)
        ["2020-12-31", "chennai", 0.0],
        ["2021-06-15", "chennai", 10.0],
    ]
    out = _run(tmp_path, monkeypatch, rows)
    r = out[("chennai", "2020")]
    assert float(r["annual_mm"]) == round(2.4 + 2.5 + 64.5 + 115.6 + 204.5, 1)
    assert float(r["jjas_mm"]) == 180.1
    assert float(r["ond_mm"]) == 204.5
    assert int(r["rainy_days"]) == 4
    assert int(r["heavy_days"]) == 3        # >= 64.5, includes very/extremely heavy
    assert int(r["very_heavy_days"]) == 2
    assert int(r["extremely_heavy_days"]) == 1
    assert float(r["max_1day_mm"]) == 204.5
    assert int(r["days_reported"]) == 6
    assert int(out[("chennai", "2021")]["days_reported"]) == 1


def test_cities_kept_separate(tmp_path, monkeypatch, capsys):
    out = _run(tmp_path, monkeypatch, [["2020-07-01", "pune", 70], ["2020-07-01", "mumbai", 5]])
    assert out[("pune", "2020")]["heavy_days"] == "1"
    assert out[("mumbai", "2020")]["heavy_days"] == "0"
