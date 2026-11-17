"""Early-vs-recent heavy-day trend table in annual_refresh.build_table
(file paths redirected to tmp_path; no imdlib, rclone or mail)."""
import csv
from datetime import date

import annual_refresh as ar

HDR = ["city", "year", "annual_mm", "jjas_mm", "ond_mm", "rainy_days", "heavy_days",
       "very_heavy_days", "extremely_heavy_days", "max_1day_mm", "days_reported"]


def test_build_table(tmp_path, monkeypatch):
    this = date.today().year
    rows = []
    for y in range(1901, 1931):
        rows.append(["chennai", y, 0, 0, 0, 0, 2, 0, 0, 100.0, 365])
        rows.append(["pune", y, 0, 0, 0, 0, 1, 0, 0, 50.0, 365])
    for y in range(this - 30, this):
        rows.append(["chennai", y, 0, 0, 0, 0, 3, 0, 0, 300.0 if y == this - 5 else 90.0, 365])
        rows.append(["pune", y, 0, 0, 0, 0, 9, 0, 0, 50.0, 365])
    rows.append(["chennai", this, 0, 0, 0, 0, 99, 0, 0, 999.0, 120])  # partial year dropped
    with open(tmp_path / "city_rain_metrics.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(rows)
    monkeypatch.setattr(ar, "ANALYSIS", tmp_path)
    out = ar.build_table()
    assert out[0] == ("Chennai", "2.00", "3.00", "+50%", f"300.0 mm ({this - 5})")
    assert out[1] == ("Pune", "—", "—", "excluded", "grid artifact")
