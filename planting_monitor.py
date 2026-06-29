#!/usr/bin/env python3
"""Living planting-decision monitor for northern & eastern Uganda nursery districts.

Combines a fixed rainfall climatology baseline (per district: plant-by deadline,
season cessation, week-by-week dry-spell risk) with live data:
  - ECMWF IFS 14-day forecast (primary go/no-go signal) - Open-Meteo
  - 4-model agreement: ECMWF + GFS + ICON + UKMO (a confidence rating) - Open-Meteo
  - ICON 40-member ensemble (near-term dry-spell probability) - Open-Meteo
  - NASA POWER satellite-corrected rainfall (independent "rains active" check)
...and emits a GREEN / AMBER / RED planting recommendation + confidence per district.

Outputs (in OUTDIR): status_today.csv, status.json, history.csv, dashboard.html
Run daily. No API key required.
"""
import json, csv, os, time, urllib.request, urllib.parse, datetime

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "planting_monitor_out")
CLIM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clim_baseline.json")
TZ = "Africa/Kampala"
WET = 1.0  # mm; a "wet day"

# ---- decision thresholds (transparent + tunable) ----
GO_NEXT7_MM      = 20    # >= this much rain forecast next 7 days
GO_MAX_CLIMRISK  = 12    # climatological dry-spell risk this week <= this (%)
HOLD_DRYSPELL    = 10    # forecast >= this many consecutive dry days -> hold
HOLD_NEXT7_MM    = 8     # < this much forecast rain next 7 days -> hold
HOLD_ENS_DRYPROB = 0.40  # ensemble P(>=5-day dry run next 7d) >= this -> hold
RAINS_ACTIVE_MM  = 20    # >= this much observed (satellite) recently -> rains established
MODEL_WET_MM     = 15    # a model "votes wet" if its next-7-day total >= this
MODELS           = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "ukmo_seamless"]
# full panel logged daily for the dependability scorecard (scorecard.py scores these)
PANEL = ["ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless", "icon_seamless",
         "ukmo_seamless", "meteofrance_seamless", "jma_seamless", "gem_seamless", "knmi_seamless"]

def fetch(url, timeout=45, retries=3):
    """GET + parse JSON, retrying transient network/SSL failures (cloud runners flake)."""
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(3 * (i + 1))
    raise last

def get(base, params, timeout=45):
    return fetch(base + "?" + urllib.parse.urlencode(params), timeout)

def max_dry_run(daily_mm):
    best = cur = 0
    for x in daily_mm:
        if x is None:
            cur = 0; continue
        if x < WET: cur += 1; best = max(best, cur)
        else: cur = 0
    return best

def nasa_power_recent(lat, lon, end_date, ndays=14):
    """Satellite-corrected daily precip (mm) for the last `ndays`; -999 -> dropped."""
    start = (end_date - datetime.timedelta(days=ndays)).strftime("%Y%m%d")
    try:
        d = get("https://power.larc.nasa.gov/api/temporal/daily/point", {
            "parameters": "PRECTOTCORR", "community": "AG",
            "longitude": lon, "latitude": lat,
            "start": start, "end": end_date.strftime("%Y%m%d"), "format": "JSON"}, timeout=30)
        vals = d["properties"]["parameter"]["PRECTOTCORR"]
        good = [v for v in vals.values() if v is not None and v > -900]
        return round(sum(good[-10:]), 1), len(good)   # last 10 valid days
    except Exception:
        return None, 0

def log_models(order, D, today_s):
    """Append each model's next-7-day forecast per district to models_log.csv.
    This is the raw material the scorecard verifies against actuals over time."""
    lats = ",".join(str(D[d]["clat"]) for d in order)
    lons = ",".join(str(D[d]["clon"]) for d in order)
    pan = get("https://api.open-meteo.com/v1/forecast", {
        "latitude": lats, "longitude": lons, "daily": "precipitation_sum",
        "models": ",".join(PANEL), "forecast_days": 7, "timezone": TZ})
    pan = pan if isinstance(pan, list) else [pan]
    path = os.path.join(OUTDIR, "models_log.csv")
    cols = ["issue_date", "target_date", "district", "model", "fc_mm"]
    prior = []
    if os.path.exists(path):
        prior = [r for r in csv.DictReader(open(path)) if r.get("issue_date") != today_s]
    out = []
    for i, d in enumerate(order):
        du = pan[i]["daily"]; t = du["time"]
        for m in PANEL:
            key = "precipitation_sum_" + m
            if key not in du: continue
            for k, date in enumerate(t):
                if k == 0: continue                     # skip same-day analysis
                v = du[key][k]
                if v is None: continue
                out.append({"issue_date": today_s, "target_date": date,
                            "district": d, "model": m, "fc_mm": round(v, 2)})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in prior: w.writerow({c: r.get(c, "") for c in cols})
        w.writerows(out)

def enso_context():
    """Current ENSO state (NOAA ONI) + plain-language seasonal tilt for East Africa.
    ENSO is the dominant basin-scale driver here; IOD is co-important but lacks a
    reliable key-free live feed, so we point to ICPAC for the authoritative outlook."""
    try:
        import urllib.request as u
        with u.urlopen("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", timeout=20) as r:
            lines = r.read().decode().strip().splitlines()
        seas, yr, _tot, anom = lines[-1].split()
        oni = float(anom)
        if oni >= 0.5:
            state = "El Niño"; tilt = ("tends to ENHANCE the Aug–Dec rains in this region — "
                                       "lean toward a normal-to-wet remaining season")
        elif oni <= -0.5:
            state = "La Niña"; tilt = ("tends to SUPPRESS the Aug–Dec rains — watch for a drier "
                                       "second season; tighten late-batch criteria")
        else:
            state = "Neutral"; tilt = ("no strong basin-scale push — rely on climatology + the "
                                       "short-range forecast")
        return {"oni": oni, "season": f"{seas} {yr}", "state": state,
                "text": f"ENSO {state} (ONI {oni:+.2f}, {seas} {yr}) — {tilt}. "
                        f"Confirm with ICPAC's seasonal outlook (covers the IOD too)."}
    except Exception:
        return {"oni": None, "state": "unknown",
                "text": "ENSO state unavailable today — check ICPAC's seasonal outlook for context."}

DASH_CSS = {"GREEN": ("#1D9E75", "#04342C"), "AMBER": ("#EF9F27", "#412402"), "RED": ("#E24B4A", "#fff")}
DASH_LABEL = {"GREEN": "GO", "AMBER": "WATCH", "RED": "HOLD"}
CONF_CSS = {"HIGH": "#1D9E75", "MED": "#BA7517", "LOW": "#E24B4A"}

def write_simple_feed(rows, D, today, path):
    """Compact public status feed for downstream consumers (e.g. an assistant pulling
    into another dashboard). Additive — does not affect any other output."""
    key = {"GREEN": "go", "AMBER": "watch", "RED": "hold"}
    counts = {"go": 0, "watch": 0, "hold": 0}
    out_d = []
    for r in rows:
        counts[key[r["status"]]] += 1
        pbw = D[r["district"]]["pbw"]
        plant_by = (datetime.date(today.year, 1, 1) + datetime.timedelta(days=pbw * 7)).isoformat()
        out_d.append({"name": r["district"], "status": DASH_LABEL[r["status"]],
                      "mm": r["fc_next7_mm"], "plant_by": plant_by})
    json.dump({"updated": today.isoformat(), "counts": counts, "districts": out_d},
              open(path, "w"), indent=2)

def cell_color(v):
    if v is None: return ("#eee", "#777")
    if v <= 5:  return ("#1D9E75", "#fff")
    if v <= 15: return ("#EF9F27", "#412402")
    if v <= 25: return ("#BA7517", "#fff")
    return ("#E24B4A", "#fff")

def forecast_table_html(rows):
    """Per-district 2-week forecast rainfall + rain probability + climatological risk."""
    pri = {"RED": 0, "AMBER": 1, "GREEN": 2}
    rs = sorted(rows, key=lambda r: (pri[r["status"]], -r["fc_next7_mm"]))
    dot = {"GREEN": "#1D9E75", "AMBER": "#EF9F27", "RED": "#E24B4A"}
    body = ""
    for r in rs:
        bg, fg = cell_color(r["clim_risk_thisweek_%"])
        prob = r["fc_rain_prob_7d_%"]; prob = f"{prob}%" if prob is not None else "–"
        body += (f'<tr>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;white-space:nowrap">'
                 f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{dot[r["status"]]};margin-right:6px"></span>{r["district"]}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:500">{r["fc_next7_mm"]} mm</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;color:#666">{r["fc_week2_mm"]} mm</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right">{prob}</td>'
                 f'<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:center">'
                 f'<span style="background:{bg};color:{fg};padding:1px 7px;border-radius:4px;font-size:12px">{r["clim_risk_thisweek_%"]}%</span></td></tr>')
    return f"""<h2 style="font-size:16px;margin:22px 0 4px">Two-week rainfall outlook</h2>
<div style="font-size:12px;color:#666;margin-bottom:8px">Forecast rainfall per district, updates daily. <b>This week</b> = next 7 days, <b>next week</b> = days 8–14 (lower confidence). Rain prob = chance of rain in the next 7 days. Clim risk = this week's historical dry-spell risk.</div>
<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px;min-width:430px">
<thead><tr style="color:#666;font-size:12px">
<th style="text-align:left;padding:5px 8px">District</th><th style="text-align:right;padding:5px 8px">This week</th><th style="text-align:right;padding:5px 8px">Next week</th><th style="text-align:right;padding:5px 8px">Rain prob</th><th style="text-align:center;padding:5px 8px">Clim risk</th></tr></thead>
<tbody>{body}</tbody></table></div>"""

def weekly_calendar_html(clim, cur_wk):
    """Self-contained HTML table: per-district weekly dry-spell-risk climatology."""
    W0, W1 = 9, 47                       # weeks 10..47 -> early Mar .. late Nov
    labels = clim["wk_label"]
    order = clim["order"]; D = clim["districts"]
    # month header spans
    mhead = '<th style="border:0"></th>'
    i = W0
    while i < W1:
        m = labels[i][3:]; j = i
        while j < W1 and labels[j][3:] == m: j += 1
        mhead += f'<th colspan="{j-i}" style="font-weight:400;font-size:10px;color:#888;text-align:left;border:0">{m}</th>'
        i = j
    now_col = cur_wk - W0
    rows_html = ""
    for idx, d in enumerate(order):
        wk = D[d]["wk_risk"]; pbw = D[d]["pbw"]
        tag = " (east)" if idx >= 14 else ""
        cells = ""
        for w in range(W0, W1):
            v = wk[w] if w < len(wk) else None
            bg, fg = cell_color(v)
            extra = ""
            if w == cur_wk: extra += "outline:2px solid #111;outline-offset:-1px;"
            if w == pbw:    extra += "border-left:3px solid #111;"
            cells += (f'<td title="{d} · week of {labels[w]} · {v}% risk" '
                      f'style="background:{bg};color:{fg};width:17px;height:20px;text-align:center;'
                      f'font-size:8px;border-radius:2px;{extra}">{v}</td>')
        rows_html += (f'<tr><td style="font-size:11px;font-weight:500;white-space:nowrap;'
                      f'padding-right:6px;border:0">{d}{tag}</td>{cells}</tr>')
    now_label = labels[cur_wk] if 0 <= cur_wk < len(labels) else ""
    return f"""<h2 style="font-size:16px;margin:22px 0 4px">Seasonal dry-spell risk calendar</h2>
<div style="font-size:12px;color:#666;margin-bottom:8px">Climatology (18-19 yr). Each cell = chance a 10-day dry spell follows planting that week. <b>Black outline</b> = this week ({now_label}); <b>black notch</b> = plant-by deadline.</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:#666;margin-bottom:8px">
<span><span style="display:inline-block;width:11px;height:11px;border-radius:2px;background:#1D9E75;vertical-align:-1px"></span> go &le;5%</span>
<span><span style="display:inline-block;width:11px;height:11px;border-radius:2px;background:#EF9F27;vertical-align:-1px"></span> watch 5-15%</span>
<span><span style="display:inline-block;width:11px;height:11px;border-radius:2px;background:#BA7517;vertical-align:-1px"></span> caution 15-25%</span>
<span><span style="display:inline-block;width:11px;height:11px;border-radius:2px;background:#E24B4A;vertical-align:-1px"></span> stop &gt;25%</span></div>
<div style="overflow-x:auto;-webkit-overflow-scrolling:touch"><table style="border-collapse:separate;border-spacing:2px"><thead><tr>{mhead}</tr></thead><tbody>{rows_html}</tbody></table></div>"""

def write_dashboard(rows, today, path, seasonal=None, clim=None, cur_wk=0):
    pri = {"RED": 0, "AMBER": 1, "GREEN": 2}
    rows = sorted(rows, key=lambda r: (pri[r["status"]], -r["fc_next7_mm"]))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("GREEN", "AMBER", "RED")}
    banner = ""
    if seasonal and seasonal.get("text"):
        banner = (f'<div style="background:#eef3f8;border:1px solid #cfe0f0;border-radius:8px;'
                  f'padding:9px 12px;margin:0 0 12px;font-size:13px;color:#234">'
                  f'<b>Seasonal context:</b> {seasonal["text"]}</div>')
    cards = []
    for r in rows:
        bg, fg = DASH_CSS[r["status"]]
        cc = CONF_CSS[r["confidence"]]
        cards.append(f"""<div style="border:1px solid #ddd;border-left:6px solid {bg};border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff">
<div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
<span style="font-size:17px;font-weight:600;color:#222">{r['district']}</span>
<span style="background:{bg};color:{fg};font-weight:600;font-size:13px;padding:3px 10px;border-radius:6px;flex-shrink:0;white-space:nowrap">{DASH_LABEL[r['status']]}</span></div>
<div style="margin:5px 0 0"><span style="font-size:11px;color:{cc};border:1px solid {cc};padding:2px 7px;border-radius:6px;display:inline-block">{r['confidence']} confidence · {r['models_agree']}</span></div>
<div style="font-size:13px;color:#444;margin:6px 0 8px">{r['reason']}</div>
<div style="font-size:12px;color:#666;display:flex;flex-wrap:wrap;gap:10px">
<span>Forecast next 7d: <b>{r['fc_next7_mm']} mm</b> <span style="color:#999">(±{r['fc_spread_mm']} across {r['sample_points']} pts)</span></span>
<span>Rain prob: <b>{r['fc_rain_prob_7d_%']}%</b></span>
<span>Forecast dry-run: <b>{r['fc_dryspell_days']} d</b></span>
<span>Satellite last 10d: <b>{r['sat_obs_last10_mm']} mm</b></span>
<span>Rains active: <b>{r['rains_active']}</b></span>
<span>Plant by: <b>{r['plant_by']}</b></span></div></div>""")
    forecast_tbl = forecast_table_html(rows)
    calendar = weekly_calendar_html(clim, cur_wk) if clim else ""
    # client-side freshness check: runs in the VIEWER's browser, so it flags stale
    # data even if the whole pipeline silently stopped days ago.
    fresh_script = (
        '<script>(function(){var GEN="' + today + '";'
        'var g=new Date(GEN+"T06:00:00+03:00");'
        'var days=Math.floor((Date.now()-g.getTime())/86400000);'
        'var el=document.getElementById("fresh");if(!el)return;var bg,fg,msg;'
        'if(days<=1){bg="#e1f5ee";fg="#04342c";msg="\\u2713 Live \\u2014 updated "+(days<=0?"today":"yesterday")+" ("+GEN+")";}'
        'else if(days==2){bg="#faeeda";fg="#412402";msg="\\u26a0 Data is 2 days old (last update "+GEN+") \\u2014 check it is still running";}'
        'else{bg="#fceaea";fg="#791f1f";msg="\\u26a0 STALE: data is "+days+" days old (last update "+GEN+"). The daily update has likely stopped \\u2014 do not rely on this until refreshed.";}'
        'el.style.cssText="padding:8px 12px;border-radius:8px;margin:0 0 12px;font-size:13px;font-weight:500;background:"+bg+";color:"+fg;'
        'el.textContent=msg;})();</script>')
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Planting monitor - Uganda</title></head>
<body style="font-family:system-ui,sans-serif;max-width:760px;margin:0 auto;padding:14px;color:#222;background:#f6f6f4">
<div id="fresh"></div>
<h1 style="font-size:20px;margin:0 0 2px">Planting go / no-go - Uganda nurseries</h1>
<div style="font-size:13px;color:#666;margin-bottom:10px">Updated {today} (EAT) · 4-model forecast (ECMWF/GFS/ICON/UKMO, multi-point) + ICON ensemble + NASA POWER satellite + ENSO, on rainfall climatology</div>
{banner}
<div style="font-size:13px;margin:0 0 10px"><a href="scorecard.html" style="color:#185FA5;text-decoration:none">→ Forecast model dependability scorecard</a>&nbsp;&nbsp;·&nbsp;&nbsp;<a href="nursery_map.html" style="color:#185FA5;text-decoration:none">→ Nursery locations map</a>&nbsp;&nbsp;·&nbsp;&nbsp;<a href="nursery_match_map.html" style="color:#185FA5;text-decoration:none">→ Field vs DB reconciliation</a></div>
<div style="display:flex;gap:8px;margin-bottom:6px">
<div style="flex:1;text-align:center;background:#1D9E75;color:#fff;border-radius:8px;padding:8px"><div style="font-size:22px;font-weight:700">{counts['GREEN']}</div><div style="font-size:12px">GO</div></div>
<div style="flex:1;text-align:center;background:#EF9F27;color:#412402;border-radius:8px;padding:8px"><div style="font-size:22px;font-weight:700">{counts['AMBER']}</div><div style="font-size:12px">WATCH</div></div>
<div style="flex:1;text-align:center;background:#E24B4A;color:#fff;border-radius:8px;padding:8px"><div style="font-size:22px;font-weight:700">{counts['RED']}</div><div style="font-size:12px">HOLD</div></div></div>
{''.join(cards)}
{forecast_tbl}
{calendar}
<div style="font-size:11px;color:#999;margin-top:14px;line-height:1.5">GO = plant now. WATCH = hold a few days / confirm local rains. HOLD = do not plant (dry spell ahead or season window closed).
Confidence = how many of the 4 models agree with the lead forecast. Satellite (NASA POWER) has a ~3-day lag, so it confirms rains have <i>started</i>, not today's sky.
Forecast skill is low beyond ~7 days in the tropics; treat the 1-7 day window as the actionable signal and confirm rains on the ground before large batches.</div>
{fresh_script}
</body></html>"""
    open(path, "w").write(html)

def main():
    os.makedirs(OUTDIR, exist_ok=True)
    clim = json.load(open(CLIM_FILE))
    order = clim["order"]; D = clim["districts"]
    cur_wk = min(51, (datetime.date.today().timetuple().tm_yday - 1) // 7)
    today = datetime.date.today()
    today_s = today.isoformat()

    seasonal = enso_context()

    # centroids (used for multi-model, ensemble, satellite)
    lats = ",".join(str(D[d]["clat"]) for d in order)
    lons = ",".join(str(D[d]["clon"]) for d in order)
    # all sample points (used for primary ECMWF -> spatial spread per district)
    pt_lat, pt_lon, span = [], [], {}
    for d in order:
        pts = D[d].get("pts") or [[D[d]["clat"], D[d]["clon"]]]
        span[d] = (len(pt_lat), len(pt_lat) + len(pts))
        for la, lo in pts:
            pt_lat.append(str(la)); pt_lon.append(str(lo))

    # primary: ECMWF 14-day (precip + probability), with 10 past days, at every sample point
    fc = get("https://api.open-meteo.com/v1/forecast", {
        "latitude": ",".join(pt_lat), "longitude": ",".join(pt_lon),
        "daily": "precipitation_sum,precipitation_probability_max",
        "models": "ecmwf_ifs025", "forecast_days": 14, "past_days": 10, "timezone": TZ})
    # agreement: 4 models, 7-day precip (centroid)
    mm = get("https://api.open-meteo.com/v1/forecast", {
        "latitude": lats, "longitude": lons, "daily": "precipitation_sum",
        "models": ",".join(MODELS), "forecast_days": 7, "timezone": TZ})
    # ensemble: ICON 40 members, 7-day (centroid)
    ens = get("https://ensemble-api.open-meteo.com/v1/ensemble", {
        "latitude": lats, "longitude": lons, "daily": "precipitation_sum",
        "models": "icon_seamless", "forecast_days": 7, "timezone": TZ})
    fc  = fc  if isinstance(fc, list)  else [fc]
    mm  = mm  if isinstance(mm, list)  else [mm]
    ens = ens if isinstance(ens, list) else [ens]

    rows = []
    for i, d in enumerate(order):
        a, b = span[d]                       # this district's sample-point range in fc
        c0 = fc[a]["daily"]                  # centroid point (first)
        t = c0["time"]; ti = t.index(today_s) if today_s in t else 10
        prob7 = [x for x in c0["precipitation_probability_max"][ti:ti+7] if x is not None]
        fc_dry = max_dry_run(c0["precipitation_sum"][ti:ti+14])
        prob_mean = round(sum(prob7)/len(prob7)) if prob7 else None
        # rain at every sample point -> district means (week 1 = next 7d, week 2 = days 8-14)
        pt_next7, pt_week2 = [], []
        for j in range(a, b):
            ps = fc[j]["daily"]["precipitation_sum"]
            v1 = [x for x in ps[ti:ti+7]   if x is not None]
            v2 = [x for x in ps[ti+7:ti+14] if x is not None]
            if v1: pt_next7.append(sum(v1))
            if v2: pt_week2.append(sum(v2))
        next7_mm = round(sum(pt_next7)/len(pt_next7), 1) if pt_next7 else 0.0
        week2_mm = round(sum(pt_week2)/len(pt_week2), 1) if pt_week2 else 0.0
        spread_mm = round(max(pt_next7) - min(pt_next7), 1) if len(pt_next7) > 1 else 0.0
        n_pts = len(pt_next7)

        # ICON ensemble dry-spell probability
        ed = ens[i]["daily"]
        members = [k for k in ed if k.startswith("precipitation_sum")]
        dry_members = sum(1 for m in members if max_dry_run(ed[m][:7]) >= 5)
        ens_dry_prob = round(dry_members/len(members), 2) if members else None

        # 4-model agreement -> confidence
        md = mm[i]["daily"]
        model_next7 = []
        for mdl in MODELS:
            key = "precipitation_sum_" + mdl
            if key in md:
                vv = [x for x in md[key][:7] if x is not None]
                if vv: model_next7.append(sum(vv))
        n_models = len(model_next7)
        votes_wet = sum(1 for v in model_next7 if v >= MODEL_WET_MM)
        ecmwf_wet = next7_mm >= MODEL_WET_MM
        agree = sum(1 for v in model_next7 if (v >= MODEL_WET_MM) == ecmwf_wet)
        frac = agree / n_models if n_models else 0
        confidence = "HIGH" if frac >= 0.75 else ("MED" if frac >= 0.5 else "LOW")
        models_agree = f"{votes_wet}/{n_models} models wet"

        # satellite recent rainfall (independent observation)
        sat_mm, sat_days = nasa_power_recent(D[d]["clat"], D[d]["clon"], today)
        rains_active = (sat_mm is not None and sat_mm >= RAINS_ACTIVE_MM)

        clim_now = D[d]["wk_risk"][cur_wk]
        pbw, cw = D[d]["pbw"], D[d]["cw"]

        # ---- base decision ----
        if cur_wk > cw:
            status, why = "RED", "season ended (past cessation)"
        elif cur_wk > pbw:
            status, why = "RED", "past plant-by deadline; too little season left"
        elif fc_dry >= HOLD_DRYSPELL:
            status, why = "RED", f"forecast dry spell of {fc_dry} days ahead"
        elif next7_mm < HOLD_NEXT7_MM or (ens_dry_prob is not None and ens_dry_prob >= HOLD_ENS_DRYPROB):
            status, why = "AMBER", f"thin rain ahead ({next7_mm}mm/7d, ens dry-risk {int((ens_dry_prob or 0)*100)}%)"
        elif next7_mm >= GO_NEXT7_MM and fc_dry < 7 and clim_now <= GO_MAX_CLIMRISK:
            status, why = "GREEN", f"{next7_mm}mm forecast next 7d, no dry spell, low seasonal risk"
        else:
            status, why = "AMBER", f"marginal ({next7_mm}mm/7d, clim risk {clim_now}%)"

        # ---- confidence modifier: a GO the models don't back becomes WATCH ----
        if status == "GREEN" and confidence == "LOW":
            status = "AMBER"; why = f"models split on the forecast ({models_agree}); treat as watch"
        if spread_mm >= 20 and n_pts > 1:
            why += f" · rain varies ~{spread_mm}mm across the district — check local sites"

        rows.append({
            "date": today_s, "district": d, "status": status, "confidence": confidence,
            "models_agree": models_agree, "reason": why,
            "rains_active": "yes" if rains_active else "no",
            "sat_obs_last10_mm": sat_mm if sat_mm is not None else "n/a",
            "fc_next7_mm": next7_mm, "fc_week2_mm": week2_mm, "fc_spread_mm": spread_mm,
            "sample_points": n_pts,
            "fc_rain_prob_7d_%": prob_mean, "fc_dryspell_days": fc_dry,
            "ens_dryspell_prob": ens_dry_prob, "clim_risk_thisweek_%": clim_now,
            "plant_by": clim["wk_label"][pbw], "lat": D[d]["clat"], "lon": D[d]["clon"],
        })

    cols = list(rows[0].keys())
    with open(os.path.join(OUTDIR, "status_today.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    json.dump({"generated": today_s, "seasonal": seasonal, "rows": rows},
              open(os.path.join(OUTDIR, "status.json"), "w"), indent=1)
    write_simple_feed(rows, D, today, os.path.join(OUTDIR, "status_simple.json"))
    hist = os.path.join(OUTDIR, "history.csv")
    prior = [r for r in csv.DictReader(open(hist))] if os.path.exists(hist) else []
    prior = [r for r in prior if r.get("date") != today_s]
    # align prior rows to current columns (schema may have grown)
    with open(hist, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in prior:
            w.writerow({c: r.get(c, "") for c in cols})
        w.writerows(rows)

    write_dashboard(rows, today_s, os.path.join(OUTDIR, "dashboard.html"), seasonal, clim, cur_wk)
    try:
        log_models(order, D, today_s)
    except Exception as e:
        print("model log skipped:", e)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("GREEN", "AMBER", "RED")}
    print(f"{today_s}  GREEN={counts['GREEN']} AMBER={counts['AMBER']} RED={counts['RED']}")
    for r in rows:
        print(f"  {r['status']:5} {r['confidence']:4} {r['district']:11} {r['reason']}")
    return rows

if __name__ == "__main__":
    main()
