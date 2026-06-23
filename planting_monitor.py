#!/usr/bin/env python3
"""Living planting-decision monitor for northern Uganda nursery districts.

Combines a fixed CHIRPS climatology baseline (per district: plant-by deadline,
season cessation, current-week dry-spell risk) with live Open-Meteo forecasts
(ECMWF IFS 14-day + ICON 40-member ensemble) to emit a GREEN / AMBER / RED
planting recommendation per district each day.

Outputs (in OUTDIR):
  status_today.csv   - one row per district, today's decision + supporting numbers
  status.json        - same, for the dashboard
  history.csv        - appended every run (audit trail + future forecast validation)
  dashboard.html     - self-contained mobile-friendly page

Run daily. No API key required (Open-Meteo non-commercial).
"""
import json, csv, os, sys, urllib.request, urllib.parse, datetime

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
RAINS_ACTIVE_MM  = 20    # >= this much observed in last 10 days -> rains established

def fetch(url):
    with urllib.request.urlopen(url, timeout=45) as r:
        return json.load(r)

def get(base, params):
    return fetch(base + "?" + urllib.parse.urlencode(params))

def max_dry_run(daily_mm):
    best = cur = 0
    for x in daily_mm:
        if x is None:    # unknown day breaks the run conservatively
            cur = 0; continue
        if x < WET: cur += 1; best = max(best, cur)
        else: cur = 0
    return best

DASH_CSS = {"GREEN": ("#1D9E75", "#04342C"), "AMBER": ("#EF9F27", "#412402"), "RED": ("#E24B4A", "#fff")}
DASH_LABEL = {"GREEN": "GO", "AMBER": "WATCH", "RED": "HOLD"}

def write_dashboard(rows, today, path):
    pri = {"RED": 0, "AMBER": 1, "GREEN": 2}
    rows = sorted(rows, key=lambda r: (pri[r["status"]], -r["fc_next7_mm"]))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("GREEN", "AMBER", "RED")}
    cards = []
    for r in rows:
        bg, fg = DASH_CSS[r["status"]]
        cards.append(f"""<div style="border:1px solid #ddd;border-left:6px solid {bg};border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff">
<div style="display:flex;justify-content:space-between;align-items:center">
<span style="font-size:17px;font-weight:600;color:#222">{r['district']}</span>
<span style="background:{bg};color:{fg};font-weight:600;font-size:13px;padding:3px 10px;border-radius:6px">{DASH_LABEL[r['status']]}</span></div>
<div style="font-size:13px;color:#444;margin:6px 0 8px">{r['reason']}</div>
<div style="font-size:12px;color:#666;display:flex;flex-wrap:wrap;gap:10px">
<span>Rain next 7d: <b>{r['fc_next7_mm']} mm</b></span>
<span>Rain prob: <b>{r['fc_rain_prob_7d_%']}%</b></span>
<span>Forecast dry-run: <b>{r['fc_dryspell_days']} d</b></span>
<span>Rains active: <b>{r['rains_active']}</b></span>
<span>Plant by: <b>{r['plant_by']}</b></span></div></div>""")
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Planting monitor - northern Uganda</title></head>
<body style="font-family:system-ui,sans-serif;max-width:760px;margin:0 auto;padding:14px;color:#222;background:#f6f6f4">
<h1 style="font-size:20px;margin:0 0 2px">Planting go / no-go - northern Uganda</h1>
<div style="font-size:13px;color:#666;margin-bottom:10px">Updated {today} (EAT) · ECMWF IFS + ICON ensemble via Open-Meteo, on CHIRPS climatology</div>
<div style="display:flex;gap:8px;margin-bottom:6px">
<div style="flex:1;text-align:center;background:#1D9E75;color:#fff;border-radius:8px;padding:8px"><div style="font-size:22px;font-weight:700">{counts['GREEN']}</div><div style="font-size:12px">GO</div></div>
<div style="flex:1;text-align:center;background:#EF9F27;color:#412402;border-radius:8px;padding:8px"><div style="font-size:22px;font-weight:700">{counts['AMBER']}</div><div style="font-size:12px">WATCH</div></div>
<div style="flex:1;text-align:center;background:#E24B4A;color:#fff;border-radius:8px;padding:8px"><div style="font-size:22px;font-weight:700">{counts['RED']}</div><div style="font-size:12px">HOLD</div></div></div>
{''.join(cards)}
<div style="font-size:11px;color:#999;margin-top:14px;line-height:1.5">GO = plant now. WATCH = hold a few days / gate on local rains. HOLD = do not plant (dry spell ahead or season window closed).
Forecast skill is low beyond ~7 days in the tropics; treat the 1-7 day window as the actionable signal and confirm rains on the ground before large batches.</div>
</body></html>"""
    open(path, "w").write(html)

def main():
    os.makedirs(OUTDIR, exist_ok=True)
    clim = json.load(open(CLIM_FILE))           # {district: {clat,clon,pbw,cw,wk_risk[52]}}
    order = clim["order"]; D = clim["districts"]
    cur_wk = min(51, (datetime.date.today().timetuple().tm_yday - 1) // 7)
    today = datetime.date.today().isoformat()

    lats = ",".join(str(D[d]["clat"]) for d in order)
    lons = ",".join(str(D[d]["clon"]) for d in order)

    fc = get("https://api.open-meteo.com/v1/forecast", {
        "latitude": lats, "longitude": lons,
        "daily": "precipitation_sum,precipitation_probability_max",
        "models": "ecmwf_ifs025", "forecast_days": 14, "past_days": 10, "timezone": TZ})
    ens = get("https://ensemble-api.open-meteo.com/v1/ensemble", {
        "latitude": lats, "longitude": lons, "daily": "precipitation_sum",
        "models": "icon_seamless", "forecast_days": 7, "timezone": TZ})
    fc = fc if isinstance(fc, list) else [fc]
    ens = ens if isinstance(ens, list) else [ens]

    rows = []
    for i, d in enumerate(order):
        cd = fc[i]["daily"]
        t = cd["time"]; psum = cd["precipitation_sum"]; pprob = cd["precipitation_probability_max"]
        ti = t.index(today) if today in t else 10          # today's index (past_days=10)
        past10 = [x for x in psum[max(0, ti-10):ti] if x is not None]
        next7  = [x for x in psum[ti:ti+7]  if x is not None]
        next14 = psum[ti:ti+14]
        prob7  = [x for x in pprob[ti:ti+7] if x is not None]
        past10_mm = round(sum(past10), 1)
        next7_mm  = round(sum(next7), 1)
        fc_dry    = max_dry_run(next14)
        prob_mean = round(sum(prob7)/len(prob7)) if prob7 else None

        ed = ens[i]["daily"]
        members = [k for k in ed if k.startswith("precipitation_sum")]
        dry_members = 0
        for m in members:
            if max_dry_run(ed[m][:7]) >= 5: dry_members += 1
        ens_dry_prob = round(dry_members/len(members), 2) if members else None

        clim_now = D[d]["wk_risk"][cur_wk]
        pbw, cw = D[d]["pbw"], D[d]["cw"]
        rains_active = past10_mm >= RAINS_ACTIVE_MM

        # ---- decision ----
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

        rows.append({
            "date": today, "district": d, "status": status, "reason": why,
            "rains_active": "yes" if rains_active else "no",
            "obs_last10_mm": past10_mm, "fc_next7_mm": next7_mm,
            "fc_rain_prob_7d_%": prob_mean, "fc_dryspell_days": fc_dry,
            "ens_dryspell_prob": ens_dry_prob, "clim_risk_thisweek_%": clim_now,
            "plant_by": clim["wk_label"][pbw], "lat": D[d]["clat"], "lon": D[d]["clon"],
        })

    cols = list(rows[0].keys())
    with open(os.path.join(OUTDIR, "status_today.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    json.dump({"generated": today, "rows": rows}, open(os.path.join(OUTDIR, "status.json"), "w"), indent=1)
    hist = os.path.join(OUTDIR, "history.csv")
    prior = []
    if os.path.exists(hist):
        prior = [r for r in csv.DictReader(open(hist)) if r.get("date") != today]  # drop today's old run
    with open(hist, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        w.writerows(prior); w.writerows(rows)

    write_dashboard(rows, today, os.path.join(OUTDIR, "dashboard.html"))

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("GREEN", "AMBER", "RED")}
    print(f"{today}  GREEN={counts['GREEN']} AMBER={counts['AMBER']} RED={counts['RED']}")
    for r in rows:
        print(f"  {r['status']:5} {r['district']:9} {r['reason']}")
    return rows

if __name__ == "__main__":
    main()
