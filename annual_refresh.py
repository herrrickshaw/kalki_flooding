#!/usr/bin/env python3
"""Annual IMD rainfall refresh: runs from daily_pipeline in Jan/Feb, once.

What it does (target year Y = last calendar year):
  1. Re-downloads grids for Y-1 and Y from IMD (Y-1 again because IMD revises
     its real-time product after the fact), pushes both to
     dropbox:imd-rainfall-grid (overwriting the real-time versions).
  2. Re-extracts all 12 city cells for those two years and rewrites their rows
     in analysis/data/city_daily.csv (+ _more.csv).
  3. Re-runs city_rain_metrics.py, refreshes charts + CSV.
  4. Mails the recomputed 12-city summary (table built from data, not
     hardcoded) with charts attached.
  5. Copies updated CSVs to Dropbox, commits + pushes the repo.
  6. Writes marker analysis/data/.refresh_done_<Y> — the pipeline guard.

Flags: --force (ignore month/marker), --no-mail (skip step 4, for testing).
Run with the market-pipeline venv python (needs numpy/pandas/matplotlib);
imdlib+xarray are vendored in ./pkgs (machine-local, gitignored).
"""
import argparse
import csv
import mimetypes
import smtplib
import subprocess
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "pkgs"))

ANALYSIS = BASE / "analysis"
DATA = ANALYSIS / "data"
REMOTE = "dropbox:imd-rainfall-grid"
VENV_PY = "/Users/umashankar/market-pipeline/code/python_files/.venv/bin/python"
CREDS = Path.home() / ".config/market-secrets/credentials.env"
MAIL_TO = "umashankartd1991@gmail.com"

# city -> (lat, lon, csv-file) ; Chennai/Mumbai use nearest LAND cells
CELLS = {
    "chennai": (13.25, 80.25, "city_daily.csv"),
    "bengaluru": (13.0, 77.5, "city_daily.csv"),
    "mumbai": (19.0, 73.0, "city_daily_more.csv"),
    "delhi": (28.75, 77.25, "city_daily_more.csv"),
    "kolkata": (22.5, 88.25, "city_daily_more.csv"),
    "hyderabad": (17.5, 78.5, "city_daily_more.csv"),
    "ahmedabad": (23.0, 72.5, "city_daily_more.csv"),
    "pune": (18.5, 73.75, "city_daily_more.csv"),
    "surat": (21.25, 72.75, "city_daily_more.csv"),
    "jaipur": (27.0, 75.75, "city_daily_more.csv"),
    "lucknow": (26.75, 81.0, "city_daily_more.csv"),
    "kanpur": (26.5, 80.25, "city_daily_more.csv"),
}
EXCLUDE_TREND = {"pune"}  # grid artifact — see analysis/DATA_CAVEATS.md


def refresh_years(years):
    import imdlib
    for y in years:
        imdlib.get_data("rain", y, y, fn_format="yearwise", file_dir=str(BASE))
        ds = imdlib.open_data("rain", y, y, fn_format="yearwise",
                              file_dir=str(BASE)).get_xarray()
        new_rows = {"city_daily.csv": [], "city_daily_more.csv": []}
        for city, (la, lo, fn) in CELLS.items():
            v = ds["rain"].sel(lat=la, lon=lo)
            for t, val in zip(ds.time.values, v.values):
                if val >= 0:
                    new_rows[fn].append([str(t)[:10], city, round(float(val), 2)])
        ds.close()
        for fn, rows in new_rows.items():
            p = DATA / fn
            kept = [r for r in csv.reader(open(p))
                    if r and (r[0] == "date" or not r[0].startswith(str(y)))]
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerows(kept)
                w.writerows(rows)
        grd = BASE / "rain" / f"{y}.grd"
        subprocess.run(["rclone", "moveto", str(grd), f"{REMOTE}/{y}.grd",
                        "--retries", "3"], check=True)
        print(f"refreshed {y}")


def build_table():
    rows = [r for r in csv.DictReader(open(ANALYSIS / "city_rain_metrics.csv"))
            if int(r["days_reported"]) >= 300]
    thisyear = date.today().year
    out = []
    for city in sorted({r["city"] for r in rows}):
        cr = [r for r in rows if r["city"] == city]
        if city in EXCLUDE_TREND:
            out.append((city.title(), "—", "—", "excluded", "grid artifact"))
            continue
        e = [r for r in cr if 1901 <= int(r["year"]) <= 1930]
        l = [r for r in cr if thisyear - 30 <= int(r["year"]) <= thisyear - 1]
        me = sum(float(r["heavy_days"]) for r in e) / len(e)
        ml = sum(float(r["heavy_days"]) for r in l) / len(l)
        mx = max(cr, key=lambda r: float(r["max_1day_mm"]))
        chg = f"{100 * (ml - me) / me:+.0f}%" if me else "n/a"
        out.append((city.title(), f"{me:.2f}", f"{ml:.2f}", chg,
                    f"{mx['max_1day_mm']} mm ({mx['year']})"))
    out.sort(key=lambda t: (t[3] == "excluded", -float(t[3].rstrip("%"))
                            if t[3] not in ("excluded", "n/a") else 0))
    return out


def send_mail(year):
    env = {}
    for line in open(CREDS):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v.strip().strip('"')
    tr = "\n".join(
        f"<tr><td style='padding:4px 10px'>{c}</td><td align=right>{a}</td>"
        f"<td align=right>{b}</td><td align=right><b>{d}</b></td>"
        f"<td style='padding:4px 10px'>{m}</td></tr>"
        for c, a, b, d, m in build_table())
    html = f"""<div style="font-family:Georgia,serif;max-width:680px;margin:auto">
<h2 style="border-bottom:2px solid #1a5276;padding-bottom:6px">
Annual refresh: India 12-city rainfall through {year}</h2>
<p>IMD finalized {year}; grids re-pulled ({year - 1} revised + {year}), city
series and metrics recomputed. Heavy-rain days/yr (&ge;64.5 mm), 1901–30 vs
last 30 years:</p>
<table style="border-collapse:collapse;font-size:14px">
<tr style="background:#eaf2f8"><th style="padding:4px 10px">City</th>
<th>1901–30</th><th>last 30y</th><th>Change</th>
<th style="padding:4px 10px">Wettest day on record</th></tr>{tr}</table>
<p style="font-size:13px">Caveats: analysis/DATA_CAVEATS.md in
<a href="https://github.com/herrrickshaw/kalki_flooding">kalki_flooding</a>.
Grids: dropbox:imd-rainfall-grid.</p></div>"""
    msg = EmailMessage()
    msg["Subject"] = f"Annual rainfall refresh — 12 cities through {year}"
    msg["From"] = env["GMAIL_USER"]
    msg["To"] = MAIL_TO
    msg.set_content("HTML report — open in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    for f in ["city_extreme_days.png", "city_monsoon_trend.png", "city_rain_metrics.csv"]:
        p = ANALYSIS / f
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        mt, st = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=mt, subtype=st, filename=p.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(env["GMAIL_USER"], env["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
    print(f"mailed {MAIL_TO}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-mail", action="store_true")
    args = ap.parse_args()

    today = date.today()
    year = today.year - 1
    marker = DATA / f".refresh_done_{year}"
    if not args.force:
        if today.month > 2:
            print(f"not Jan/Feb — skip (marker: {marker.name})")
            return
        if marker.exists():
            print(f"{marker.name} exists — already refreshed")
            return

    refresh_years([year - 1, year])
    # Pune STATION series (GSOD, the usable Pune record — grid cell is
    # artifact-ridden): append/refresh the two newest years the same way
    sys.path.insert(0, str(ANALYSIS))
    from pune_station_series import fetch_gsod
    p = DATA / "city_daily_pune_station.csv"
    kept = [r for r in csv.reader(open(p))
            if r and (r[0] == "date"
                      or not r[0].startswith((str(year - 1), str(year))))]
    fresh = []
    for y in (year - 1, year):
        fresh += [[d, "pune_station", v] for d, v in sorted(fetch_gsod(y).items())]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerows(kept)
        w.writerows(fresh)
    print(f"pune_station: refreshed {year - 1}-{year} ({len(fresh)} rows)")

    subprocess.run([VENV_PY, str(ANALYSIS / "city_rain_metrics.py")],
                   cwd=str(ANALYSIS), check=True)
    for fn in ("city_daily.csv", "city_daily_more.csv", "city_daily_pune_station.csv"):
        subprocess.run(["rclone", "copyto", str(DATA / fn), f"{REMOTE}/{fn}"],
                       check=True)
    if not args.no_mail:
        send_mail(year)
    subprocess.run(["git", "add", "analysis/"], cwd=str(BASE), check=True)
    r = subprocess.run(["git", "commit", "-m",
                        f"annual refresh: IMD final data through {year}\n\n"
                        "Co-Authored-By: annual_refresh.py (scheduled)"],
                       cwd=str(BASE), capture_output=True, text=True)
    if r.returncode == 0:
        subprocess.run(["git", "push", "origin", "main"], cwd=str(BASE))
    marker.touch()
    print(f"done — marker {marker.name} written")


if __name__ == "__main__":
    main()
