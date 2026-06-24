#!/usr/bin/env python3
"""Model dependability scorecard.

Reads models_log.csv (the daily forecast panel logged by planting_monitor.py),
verifies each model's forecasts against NASA POWER satellite actuals, and ranks
the models by accuracy. Writes scorecard.json + scorecard.html.

Metrics per model (over all verifiable district-day forecasts):
  MAE   - mean absolute error of daily rainfall (mm)   [lower better]
  bias  - mean (forecast - actual) (mm)                 [near 0 best]
  CSI   - critical success index for rain>=1mm days     [higher better]
  POD   - probability of detection (caught real rain)
  FAR   - false alarm ratio (cried wolf)
  acc   - rain/no-rain hit rate

Meaningful once a few weeks of data accrue (POWER lags ~3 days). Run daily.
"""
import csv, os, json, datetime, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "planting_monitor_out")
LOG  = os.path.join(OUT, "models_log.csv")
ACT  = os.path.join(OUT, "power_actuals.csv")
CLIM = os.path.join(HERE, "clim_baseline.json")
WET, LAG, MIN_PAIRS = 1.0, 3, 60     # POWER ~3-day lag; need >=60 pairs/model to rank

def power_daily(lat, lon, start, end):
    url = "https://power.larc.nasa.gov/api/temporal/daily/point?" + urllib.parse.urlencode(
        {"parameters": "PRECTOTCORR", "community": "AG", "longitude": lon, "latitude": lat,
         "start": start, "end": end, "format": "JSON"})
    with urllib.request.urlopen(url, timeout=40) as r:
        d = json.load(r)
    out = {}
    for k, v in d["properties"]["parameter"]["PRECTOTCORR"].items():
        if v is not None and v > -900:
            out[f"{k[:4]}-{k[4:6]}-{k[6:]}"] = v
    return out

def main():
    if not os.path.exists(LOG):
        return write_html([], 0, "no forecasts logged yet")
    rows = list(csv.DictReader(open(LOG)))
    D = json.load(open(CLIM))["districts"]
    cutoff = (datetime.date.today() - datetime.timedelta(days=LAG)).isoformat()
    vr = [r for r in rows if r["target_date"] <= cutoff]
    if not vr:
        return write_html([], 0, "collecting — no verifiable forecasts yet (POWER lags ~3 days)")

    # load + extend the actuals cache from NASA POWER
    act = {}
    if os.path.exists(ACT):
        for r in csv.DictReader(open(ACT)):
            act[(r["district"], r["date"])] = float(r["mm"])
    need = {(r["district"], r["target_date"]) for r in vr} - set(act.keys())
    bydist = {}
    for dist, date in need:
        bydist.setdefault(dist, []).append(date)
    for dist, dates in bydist.items():
        c = D.get(dist)
        if not c: continue
        try:
            dd = power_daily(c["clat"], c["clon"], min(dates).replace("-", ""), max(dates).replace("-", ""))
        except Exception:
            continue
        for date, mm in dd.items():
            act[(dist, date)] = mm
    with open(ACT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["district", "date", "mm"])
        for (dist, date), mm in sorted(act.items()):
            w.writerow([dist, date, round(mm, 2)])

    # score each model
    st = {}
    for r in vr:
        a = act.get((r["district"], r["target_date"]))
        if a is None: continue
        fc = float(r["fc_mm"]); m = r["model"]
        s = st.setdefault(m, {"n": 0, "abs": 0.0, "bias": 0.0, "a": 0, "b": 0, "c": 0, "d": 0})
        s["n"] += 1; s["abs"] += abs(fc - a); s["bias"] += (fc - a)
        fr, ar = fc >= WET, a >= WET
        s["a" if (fr and ar) else "b" if fr else "c" if ar else "d"] += 1

    table = []
    for m, s in st.items():
        n = s["n"]; a, b, c = s["a"], s["b"], s["c"]
        table.append({
            "model": m, "n": n,
            "mae": round(s["abs"]/n, 2), "bias": round(s["bias"]/n, 2),
            "csi": round(a/(a+b+c), 2) if (a+b+c) else 0.0,
            "pod": round(a/(a+c), 2) if (a+c) else 0.0,
            "far": round(b/(a+b), 2) if (a+b) else 0.0,
            "acc": round((a+s["d"])/n, 2)})
    table.sort(key=lambda x: x["mae"])
    json.dump(table, open(os.path.join(OUT, "scorecard.json"), "w"), indent=1)
    enough = table and min(t["n"] for t in table) >= MIN_PAIRS
    write_html(table, len(vr),
               "ok" if enough else f"early — {min((t['n'] for t in table), default=0)} pairs/model so far (ranking firms up at {MIN_PAIRS}+)")
    for t in table: print(t)

NICE = {"ecmwf_ifs025": "ECMWF (Europe)", "ecmwf_aifs025_single": "ECMWF-AIFS (AI)",
        "gfs_seamless": "GFS (USA)", "icon_seamless": "ICON (Germany)",
        "ukmo_seamless": "UKMO (UK)", "meteofrance_seamless": "Météo-France",
        "jma_seamless": "JMA (Japan)", "gem_seamless": "GEM (Canada)", "knmi_seamless": "KNMI (NL)"}

def write_html(table, npairs, note):
    today = datetime.date.today().isoformat()
    body = ""
    for i, t in enumerate(table):
        medal = ["#1D9E75", "#5DCAA5", "#9FE1CB"][i] if i < 3 and note == "ok" else "transparent"
        body += (f'<tr><td style="padding:5px 8px;border-bottom:1px solid #eee">'
                 f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{medal};margin-right:6px"></span>'
                 f'{NICE.get(t["model"], t["model"])}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:500">{t["mae"]}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right">{t["bias"]:+}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right">{t["csi"]}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right">{t["pod"]}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right">{t["far"]}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;color:#888">{t["n"]}</td></tr>')
    tbl = (f'<table style="border-collapse:collapse;width:100%;font-size:13px;min-width:520px">'
           f'<thead><tr style="color:#666;font-size:12px">'
           f'<th style="text-align:left;padding:5px 8px">Model</th>'
           f'<th style="text-align:right;padding:5px 8px">MAE&nbsp;mm</th>'
           f'<th style="text-align:right;padding:5px 8px">Bias</th>'
           f'<th style="text-align:right;padding:5px 8px">CSI</th>'
           f'<th style="text-align:right;padding:5px 8px">POD</th>'
           f'<th style="text-align:right;padding:5px 8px">FAR</th>'
           f'<th style="text-align:right;padding:5px 8px">n</th></tr></thead>'
           f'<tbody>{body}</tbody></table>') if table else "<p style='color:#666'>No scores yet.</p>"
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Model scorecard</title></head>
<body style="font-family:system-ui,sans-serif;max-width:760px;margin:0 auto;padding:14px;color:#222;background:#f6f6f4">
<p style="margin:0 0 2px"><a href="dashboard.html" style="color:#185FA5;text-decoration:none">&larr; back to dashboard</a></p>
<h1 style="font-size:20px;margin:0 0 2px">Forecast model dependability</h1>
<div style="font-size:13px;color:#666;margin-bottom:6px">Updated {today} · {npairs} verifiable forecast-days · status: <b>{note}</b></div>
<div style="font-size:12px;color:#666;margin-bottom:12px">Each global model's daily rainfall forecast scored against NASA POWER satellite actuals at your district centroids. <b>MAE</b> = average error (mm, lower better); <b>Bias</b> = wet(+)/dry(&minus;) tendency; <b>CSI</b> = rain-day hit skill (higher better); <b>POD</b> = caught real rain; <b>FAR</b> = false alarms. Ranked best-MAE first.</div>
{tbl}
<div style="font-size:11px;color:#999;margin-top:14px;line-height:1.5">Truth source is satellite (NASA POWER), itself an estimate — once your rain gauges are running, gauge readings become the truth and these scores get sharper. Consumer apps (AccuWeather etc.) aren't here: they need paid keys and forbid storing forecasts; these are the raw national-met-center models they're built on.</div>
</body></html>"""
    open(os.path.join(OUT, "scorecard.html"), "w").write(html)

if __name__ == "__main__":
    main()
