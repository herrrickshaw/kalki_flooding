import csv
import smtplib
import subprocess
import sys
import types
from datetime import date
from email.message import EmailMessage

import numpy as np
import pytest


@pytest.fixture
def ar(load, tmp_path, monkeypatch):
    m = load("annual_refresh")
    analysis = tmp_path / "analysis"
    (analysis / "data").mkdir(parents=True)
    monkeypatch.setattr(m, "BASE", tmp_path)
    monkeypatch.setattr(m, "ANALYSIS", analysis)
    monkeypatch.setattr(m, "DATA", analysis / "data")
    return m


def write_csv(path, rows, header=("date", "city", "rain_mm")):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path):
    return list(csv.reader(open(path)))


# ── refresh_years ────────────────────────────────────────────────────────────
class FakeSel:
    def __init__(self, values):
        self.values = np.array(values)


class FakeRain:
    def __init__(self, cells):
        self.cells = cells

    def sel(self, lat, lon):
        return FakeSel(self.cells[(lat, lon)])


class FakeDS:
    def __init__(self, days, cells):
        self.time = types.SimpleNamespace(values=np.array(days, dtype="datetime64[D]"))
        self._rain = FakeRain(cells)
        self.closed = False

    def __getitem__(self, k):
        assert k == "rain"
        return self._rain

    def close(self):
        self.closed = True


def install_imdlib(monkeypatch, per_year):
    calls = []
    mod = types.ModuleType("imdlib")
    mod.get_data = lambda var, s, e, **kw: calls.append(("get", var, s, e, kw["fn_format"]))

    def open_data(var, s, e, **kw):
        calls.append(("open", var, s, e))
        ds = per_year[s]
        return types.SimpleNamespace(get_xarray=lambda: ds)

    mod.open_data = open_data
    monkeypatch.setitem(sys.modules, "imdlib", mod)
    return calls


def record_subprocess(monkeypatch, ar, returncode=0):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(ar.subprocess, "run", run)
    return calls


def cells_for(ar, value_by_city):
    out = {}
    for city, (la, lo, fn) in ar.CELLS.items():
        out[(la, lo)] = value_by_city.get(city, [0.0, 0.0, 0.0])
    return out


def test_refresh_years_rewrites_only_the_refreshed_year(ar, monkeypatch, tmp_path):
    days = ["2024-12-30", "2024-12-31", "2025-01-01"]
    ds24 = FakeDS(days[:2], cells_for(ar, {"chennai": [1.5, -999.0], "mumbai": [250.123, 3.0], "bengaluru": [0.0, float("nan")]}))
    ds23 = FakeDS(["2023-06-01"], cells_for(ar, {"chennai": [7.0], "mumbai": [8.0]}))
    calls = install_imdlib(monkeypatch, {2023: ds23, 2024: ds24})
    sp = record_subprocess(monkeypatch, ar)
    write_csv(ar.DATA / "city_daily.csv", [["2022-01-01", "chennai", "9.0"], ["2023-06-01", "chennai", "0.0"],
                                           ["2024-01-01", "bengaluru", "4.0"]])
    write_csv(ar.DATA / "city_daily_more.csv", [["2023-06-01", "mumbai", "1.0"], ["2021-01-01", "delhi", "2.0"]])
    ar.refresh_years([2023, 2024])
    main = read_csv(ar.DATA / "city_daily.csv")
    more = read_csv(ar.DATA / "city_daily_more.csv")
    assert main[0] == ["date", "city", "rain_mm"] and more[0] == ["date", "city", "rain_mm"]
    # old rows of OTHER years are kept; rows of a refreshed year are replaced, not duplicated
    assert ["2022-01-01", "chennai", "9.0"] in main
    assert ["2023-06-01", "chennai", "0.0"] not in main and ["2023-06-01", "chennai", "7.0"] in main
    assert ["2024-01-01", "bengaluru", "4.0"] not in main
    assert ["2024-12-30", "chennai", "1.5"] in main
    assert ["2024-12-31", "chennai", "-999.0"] not in main          # negative = IMD missing marker, dropped
    assert not any(r[1] == "bengaluru" and r[0] == "2024-12-31" for r in main)   # NaN dropped
    assert ["2024-12-30", "bengaluru", "0.0"] in main               # zero rain is a real observation
    assert ["2024-12-30", "mumbai", "250.12"] in more                # rounded to 2 dp, routed to the _more file
    assert ["2021-01-01", "delhi", "2.0"] in more
    assert not any(r[1] == "mumbai" for r in main)
    assert ds24.closed and ds23.closed
    # one rclone upload per year, grid file moved to the remote under its year name
    moves = [c for c, kw in sp if c[:2] == ["rclone", "moveto"]]
    assert [m[3] for m in moves] == [f"{ar.REMOTE}/2023.grd", f"{ar.REMOTE}/2024.grd"]
    assert moves[0][2] == str(tmp_path / "rain" / "2023.grd") and moves[0][-2:] == ["--retries", "3"]
    assert all(kw.get("check") is True for _, kw in sp)
    assert [c[0] for c in calls].count("get") == 2
    assert ("get", "rain", 2023, 2023, "yearwise") in calls


def test_refresh_years_all_twelve_cells_are_extracted(ar, monkeypatch):
    day = ["2025-01-01"]
    values = {c: [float(i)] for i, c in enumerate(ar.CELLS)}
    install_imdlib(monkeypatch, {2025: FakeDS(day, cells_for(ar, values))})
    record_subprocess(monkeypatch, ar)
    write_csv(ar.DATA / "city_daily.csv", [])
    write_csv(ar.DATA / "city_daily_more.csv", [])
    ar.refresh_years([2025])
    got = {r[1]: float(r[2]) for p in ("city_daily.csv", "city_daily_more.csv") for r in read_csv(ar.DATA / p)[1:]}
    assert got == {c: float(i) for i, c in enumerate(ar.CELLS)}
    assert len(ar.CELLS) == 12
    assert {fn for _, _, fn in ar.CELLS.values()} == {"city_daily.csv", "city_daily_more.csv"}
    assert {c for c, v in ar.CELLS.items() if v[2] == "city_daily.csv"} == {"chennai", "bengaluru"}


# ── build_table ──────────────────────────────────────────────────────────────
class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 1, 15)


METRIC_HEADER = ["city", "year", "annual_mm", "jjas_mm", "ond_mm", "rainy_days", "heavy_days",
                 "very_heavy_days", "extremely_heavy_days", "max_1day_mm", "days_reported"]


def metrics_rows(city, early, late, mx=100.0, days=365, mx_year=1910):
    rows = []
    for y in range(1901, 1931):
        rows.append([city, y, 0, 0, 0, 0, early, 0, 0, mx if y == mx_year else 10.0, days])
    for y in range(1996, 2026):                                  # thisyear-30 .. thisyear-1
        rows.append([city, y, 0, 0, 0, 0, late, 0, 0, 10.0, days])
    return rows


def test_build_table_changes_ranking_and_exclusions(ar, monkeypatch):
    monkeypatch.setattr(ar, "date", FixedDate)
    rows = (metrics_rows("chennai", 2, 3, mx=410.0, mx_year=1920)       # +50%
            + metrics_rows("delhi", 4, 2)                                # -50%
            + metrics_rows("mumbai", 0, 5)                               # no early heavy days -> n/a
            + metrics_rows("pune", 1, 9)                                 # excluded (grid artifact)
            + metrics_rows("jaipur", 1, 2))                              # +100%
    write_csv(ar.ANALYSIS / "city_rain_metrics.csv", rows, METRIC_HEADER)
    t = ar.build_table()
    assert [r[0] for r in t] == ["Jaipur", "Chennai", "Mumbai", "Delhi", "Pune"]   # biggest rise first, n/a mid, excluded last
    chennai = t[1]
    assert chennai == ("Chennai", "2.00", "3.00", "+50%", "410.0 mm (1920)")
    assert t[0][3] == "+100%" and t[3][3] == "-50%" and t[2][3] == "n/a" and t[2][1] == "0.00"
    assert t[4] == ("Pune", "—", "—", "excluded", "grid artifact")


def test_build_table_windows_are_inclusive_and_sparse_years_ignored(ar, monkeypatch):
    monkeypatch.setattr(ar, "date", FixedDate)
    rows = metrics_rows("chennai", 2, 3)
    rows += [["chennai", 1900, 0, 0, 0, 0, 99, 0, 0, 10.0, 365],     # before 1901: ignored
             ["chennai", 1931, 0, 0, 0, 0, 99, 0, 0, 10.0, 365],     # after 1930: not early
             ["chennai", 1995, 0, 0, 0, 0, 99, 0, 0, 10.0, 365],     # before thisyear-30
             ["chennai", 2026, 0, 0, 0, 0, 99, 0, 0, 10.0, 365],     # the current (partial) year is not "late"
             ["chennai", 1910, 0, 0, 0, 0, 99, 0, 0, 500.0, 299]]    # < 300 reported days: dropped entirely
    write_csv(ar.ANALYSIS / "city_rain_metrics.csv", rows, METRIC_HEADER)
    t = ar.build_table()[0]
    assert t[1:4] == ("2.00", "3.00", "+50%") and t[4] == "100.0 mm (1910)"
    rows = metrics_rows("chennai", 2, 3) + [["chennai", 1920, 0, 0, 0, 0, 2, 0, 0, 10.0, 300]]
    write_csv(ar.ANALYSIS / "city_rain_metrics.csv", rows, METRIC_HEADER)
    assert ar.build_table()[0][1] == "2.00"                             # exactly 300 days is kept


# ── send_mail ────────────────────────────────────────────────────────────────
class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port, self.logged, self.sent = host, port, None, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        self.logged = (u, p)

    def send_message(self, msg):
        self.sent.append(msg)


def test_send_mail_builds_html_report_with_attachments(ar, monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "date", FixedDate)
    FakeSMTP.instances.clear()
    monkeypatch.setattr(ar.smtplib, "SMTP_SSL", FakeSMTP)
    creds = tmp_path / "creds.env"
    creds.write_text('# c\nGMAIL_USER="me@example.com"\nGMAIL_APP_PASSWORD=pw pw\nOTHER=1\n')
    monkeypatch.setattr(ar, "CREDS", creds)
    write_csv(ar.ANALYSIS / "city_rain_metrics.csv", metrics_rows("chennai", 2, 3), METRIC_HEADER)
    for f in ("city_extreme_days.png", "city_monsoon_trend.png"):
        (ar.ANALYSIS / f).write_bytes(b"\x89PNG-bytes")
    ar.send_mail(2025)
    s = FakeSMTP.instances[0]
    assert (s.host, s.port) == ("smtp.gmail.com", 465)
    assert s.logged == ("me@example.com", "pw pw")
    m = s.sent[0]
    assert m["Subject"] == "Annual rainfall refresh — 12 cities through 2025"
    assert m["From"] == "me@example.com" and m["To"] == ar.MAIL_TO
    html = m.get_body(preferencelist=("html",)).get_content()
    assert "through 2025" in html and "re-pulled (2024 revised + 2025)" in html
    assert "<td style='padding:4px 10px'>Chennai</td>" in html and "<b>+50%</b>" in html
    names = sorted(p.get_filename() for p in m.iter_attachments())
    assert names == ["city_extreme_days.png", "city_monsoon_trend.png", "city_rain_metrics.csv"]
    types_ = {p.get_filename(): p.get_content_type() for p in m.iter_attachments()}
    assert types_["city_extreme_days.png"] == "image/png" and types_["city_rain_metrics.csv"] == "text/csv"


# ── main ─────────────────────────────────────────────────────────────────────
def wire_main(ar, monkeypatch, tmp_path, today=date(2026, 1, 15), git_rc=0):
    class D(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(ar, "date", D)
    log = []
    monkeypatch.setattr(ar, "refresh_years", lambda years: log.append(("refresh", list(years))))
    monkeypatch.setattr(ar, "send_mail", lambda y: log.append(("mail", y)))
    pune = types.ModuleType("pune_station_series")
    pune.fetch_gsod = lambda y: log.append(("gsod", y)) or {f"{y}-02-01": 1.5, f"{y}-01-01": 0.5}
    monkeypatch.setitem(sys.modules, "pune_station_series", pune)

    def run(cmd, **kw):
        log.append(("run", cmd, kw.get("cwd"), kw.get("check")))
        rc = git_rc if cmd[:2] == ["git", "commit"] else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(ar.subprocess, "run", run)
    write_csv(ar.DATA / "city_daily_pune_station.csv",
              [["2024-05-05", "pune_station", "9.0"], ["2025-05-05", "pune_station", "9.0"], ["2023-05-05", "pune_station", "3.0"]])
    return log


def run_main(ar, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["annual_refresh.py", *argv])
    ar.main()


def test_main_skips_outside_january_february(ar, monkeypatch, tmp_path, capsys):
    log = wire_main(ar, monkeypatch, tmp_path, today=date(2026, 3, 1))
    run_main(ar, monkeypatch)
    assert "not Jan/Feb — skip (marker: .refresh_done_2025)" in capsys.readouterr().out
    assert log == []
    wire_main(ar, monkeypatch, tmp_path, today=date(2026, 2, 28))          # Feb is still in season
    (ar.DATA / ".refresh_done_2025").touch()
    log = wire_main(ar, monkeypatch, tmp_path, today=date(2026, 2, 28))
    run_main(ar, monkeypatch)
    assert "already refreshed" in capsys.readouterr().out and log == []


def test_main_force_runs_the_whole_pipeline(ar, monkeypatch, tmp_path, capsys):
    log = wire_main(ar, monkeypatch, tmp_path, today=date(2026, 6, 1))
    run_main(ar, monkeypatch, "--force")
    out = capsys.readouterr().out
    assert ("refresh", [2024, 2025]) in log
    assert [e for e in log if e[0] == "gsod"] == [("gsod", 2024), ("gsod", 2025)]
    pune = read_csv(ar.DATA / "city_daily_pune_station.csv")
    assert pune[0] == ["date", "city", "rain_mm"]
    assert ["2023-05-05", "pune_station", "3.0"] in pune                    # older years untouched
    assert ["2024-05-05", "pune_station", "9.0"] not in pune                # refreshed years replaced
    assert pune[-4:] == [["2024-01-01", "pune_station", "0.5"], ["2024-02-01", "pune_station", "1.5"],
                         ["2025-01-01", "pune_station", "0.5"], ["2025-02-01", "pune_station", "1.5"]]   # sorted per year
    assert "pune_station: refreshed 2024-2025 (4 rows)" in out
    cmds = [e[1] for e in log if e[0] == "run"]
    assert cmds[0] == [ar.VENV_PY, str(ar.ANALYSIS / "city_rain_metrics.py")]
    uploads = [c for c in cmds if c[:2] == ["rclone", "copyto"]]
    assert [c[3].rsplit("/", 1)[1] for c in uploads] == ["city_daily.csv", "city_daily_more.csv", "city_daily_pune_station.csv"]
    assert ("mail", 2025) in log
    assert ["git", "add", "analysis/"] in cmds
    commit = [c for c in cmds if c[:2] == ["git", "commit"]][0]
    assert "annual refresh: IMD final data through 2025" in commit[3]
    assert ["git", "push", "origin", "main"] in cmds
    assert (ar.DATA / ".refresh_done_2025").exists() and "marker .refresh_done_2025 written" in out


def test_main_no_mail_and_failed_commit_skip_those_steps(ar, monkeypatch, tmp_path, capsys):
    log = wire_main(ar, monkeypatch, tmp_path, today=date(2026, 1, 10), git_rc=1)
    run_main(ar, monkeypatch, "--no-mail")                                 # in season, no marker -> runs
    cmds = [e[1] for e in log if e[0] == "run"]
    assert not any(e[0] == "mail" for e in log)
    assert ["git", "push", "origin", "main"] not in cmds                  # nothing committed -> nothing pushed
    assert (ar.DATA / ".refresh_done_2025").exists()


def test_main_uses_last_calendar_year_as_the_target(ar, monkeypatch, tmp_path):
    log = wire_main(ar, monkeypatch, tmp_path, today=date(2027, 1, 5))
    run_main(ar, monkeypatch)
    assert ("refresh", [2025, 2026]) in log and (ar.DATA / ".refresh_done_2026").exists()
