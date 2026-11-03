from __future__ import annotations

import csv

import annual_refresh as ar


def test_replace_year_rows(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("date,city,mm\n2023-01-01,x,1\n2024-01-01,x,2\n2025-01-01,x,3\n")
    ar.replace_year_rows(p, ["2024", "2025"], [["2025-02-02", "x", 9]])
    assert p.read_text().splitlines() == ["date,city,mm", "2023-01-01,x,1", "2025-02-02,x,9"]


def test_build_table(tmp_path):
    p = tmp_path / "m.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city", "year", "days_reported", "heavy_days", "max_1day_mm"])
        for city, mult in (("chennai", 1), ("pune", 2)):
            for y in (1901, 1902, 2024, 2025):
                w.writerow([city, y, 365, (1 if y < 1930 else 2) * mult, 100 + y % 7])
            w.writerow([city, 2000, 100, 50, 999])  # sparse year is dropped
    t = ar.build_table(p, thisyear=2026)
    assert t[0][:4] == ("Chennai", "1.00", "2.00", "+100%")
    assert t[1] == ("Pune", "—", "—", "excluded", "grid artifact")
