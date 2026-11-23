import csv
import gzip
import io
import json
import urllib.error

import pytest


@pytest.fixture
def pss(load, tmp_path, monkeypatch):
    m = load("pune_station_series")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(m, "BASE", tmp_path)
    monkeypatch.setattr(m, "OUT", tmp_path / "data" / "city_daily_pune_station.csv")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    return m


class Resp:
    def __init__(self, raw):
        self.raw = raw

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ghcn_csv(rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return gzip.compress(buf.getvalue().encode())


def test_fetch_ghcn_keeps_only_qa_passed_precipitation_in_mm(pss, monkeypatch):
    rows = [["IN", "19010101", "PRCP", "125", "", "", "", ""],         # tenths of mm -> 12.5
            ["IN", "19010102", "PRCP", "0", "", "", "", ""],
            ["IN", "19010103", "TMAX", "300", "", "", "", ""],         # other element
            ["IN", "19010104", "PRCP", "50", "", "G", "", ""],         # QA flag set
            ["IN", "19010105", "PRCP", "7", "", " ", "", ""]]          # blank (whitespace) flag = passed
    seen = {}

    def fake(req, timeout=None):
        seen.update(url=req.full_url, ua=req.get_header("User-agent"), timeout=timeout)
        return Resp(ghcn_csv(rows))

    monkeypatch.setattr(pss.urllib.request, "urlopen", fake)
    assert pss.fetch_ghcn() == {"1901-01-01": 12.5, "1901-01-02": 0.0, "1901-01-05": 0.7}
    assert seen["url"] == pss.GHCN and seen["ua"] == "Mozilla/5.0" and seen["timeout"] == 90


def gsod(records):
    return Resp(json.dumps(records).encode())


def test_fetch_gsod_converts_inches_and_drops_missing_and_garbage(pss, monkeypatch):
    recs = [{"DATE": "2000-07-01", "PRCP": "1.00"},                    # 25.4 mm
            {"DATE": "2000-07-02", "PRCP": "99.99"},                   # missing marker
            {"DATE": "2000-07-03", "PRCP": " "},                       # blank
            {"DATE": "2000-07-04"},                                    # absent
            {"DATE": "2000-07-05", "PRCP": "7.815"},                   # 198.501 -> 198.5: the QC limit, kept
            {"DATE": "2000-07-06", "PRCP": "7.82"},                    # 198.63 mm: rejected as garbage
            {"DATE": "2000-07-07", "PRCP": "18.58"},                   # the recurring 471.9 mm garbage value
            {"DATE": "2000-07-08", "PRCP": "0.00"}]
    urls = []
    monkeypatch.setattr(pss.urllib.request, "urlopen", lambda req, timeout=None: urls.append((req.full_url, timeout)) or gsod(recs))
    out = pss.fetch_gsod(2000)
    assert out == {"2000-07-01": 25.4, "2000-07-05": 198.5, "2000-07-08": 0.0}
    assert "startDate=2000-01-01&endDate=2000-12-31" in urls[0][0] and urls[0][1] == 45


def test_fetch_gsod_retries_with_growing_backoff_then_raises(pss, monkeypatch):
    sleeps = []
    monkeypatch.setattr(pss.time, "sleep", sleeps.append)
    attempts = []

    def flaky(req, timeout=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("down")
        return gsod([{"DATE": "2001-01-01", "PRCP": "1.00"}])

    monkeypatch.setattr(pss.urllib.request, "urlopen", flaky)
    assert pss.fetch_gsod(2001) == {"2001-01-01": 25.4}
    assert sleeps == [5, 10] and len(attempts) == 3

    sleeps.clear()
    attempts.clear()
    monkeypatch.setattr(pss.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("x")))
    with pytest.raises(urllib.error.URLError):
        pss.fetch_gsod(2001)
    assert sleeps == [5, 10]                                    # no sleep after the final failure
    with pytest.raises(urllib.error.URLError):
        pss.fetch_gsod(2001, retries=1)
    assert sleeps == [5, 10]


def test_main_splices_ghcn_before_1973_and_gsod_from_1973(pss, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pss, "fetch_ghcn", lambda: {"1972-12-31": 5.0, "1973-01-01": 99.0, "1901-01-01": 1.0})
    asked = []

    def gsod_year(y):
        asked.append(y)
        return ({f"{y}-01-02": float(y - 1972)} if y in (1973, 2025) else {})

    monkeypatch.setattr(pss, "fetch_gsod", gsod_year)
    # grid-cell data for the era comparison
    with open(tmp_path / "data" / "city_daily_more.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "city", "rain_mm"])
        w.writerows([["1975-06-01", "pune", "10.0"], ["1975-06-02", "pune", "5.0"], ["1975-06-01", "delhi", "99"]])
    pss.main()
    out = capsys.readouterr().out
    assert asked == list(range(1973, 2026))                      # 1973..2025 inclusive
    rows = list(csv.reader(open(pss.OUT)))
    assert rows[0] == ["date", "city", "rain_mm"]
    got = {r[0]: float(r[2]) for r in rows[1:]}
    assert got == {"1901-01-01": 1.0, "1972-12-31": 5.0, "1973-01-02": 1.0, "2025-01-02": 53.0}   # GHCN stops before 1973-01-01; GSOD from then
    assert [r[0] for r in rows[1:]] == sorted(got)
    assert all(r[1] == "pune_station" for r in rows[1:])
    assert "GHCN days: 3" in out and "merged days: 4" in out and "1973-01-01" not in "".join(got)
    assert "1975: station       0 mm (0d ⚠️) | grid cell      15 mm" in out     # grid cell: pune rows only
    assert "1935: station       0 mm (0d ⚠️) | grid cell       0 mm" in out
    assert "2024:" in out
