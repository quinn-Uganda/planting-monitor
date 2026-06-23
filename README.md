# Planting monitor — northern Uganda

A self-updating planting **go / no-go** decision tool for the tree-nursery districts in
northern Uganda. It combines a fixed rainfall **climatology baseline** (from CHIRPS, per
district: plant-by deadline, season cessation, week-by-week dry-spell risk) with **live
forecasts** (ECMWF IFS 14-day + ICON 40-member ensemble, via the free Open-Meteo API) and
emits a GREEN / AMBER / RED recommendation per district each day.

## What it produces (in `planting_monitor_out/`)
- `dashboard.html` — mobile-friendly daily board (serve via GitHub Pages)
- `status_today.csv` — one row per district (the Google Sheet pulls this)
- `status.json` — same data, for any custom front-end
- `history.csv` — appended every run: the audit trail **and** the dataset that later proves
  which forecast source was actually right

## Run it
```
python3 planting_monitor.py
```
No API key, no third-party packages (standard library only). Needs internet (Open-Meteo).

## Decision rule (see top of `planting_monitor.py` to tune)
- **GREEN (go):** in the planting window, ≥20 mm rain forecast next 7 days, no dry spell, low seasonal risk.
- **AMBER (watch):** thin/uncertain rain ahead, or elevated seasonal dry-spell risk — hold a few days / confirm rains locally.
- **RED (hold):** a ≥10-day dry spell forecast, or the season window has closed (past plant-by / cessation).

> Forecast skill is low beyond ~7 days in the tropics. The 1–7 day window is the actionable
> signal; always confirm rains on the ground before committing large batches.

## Make it a living document

### 1. Web dashboard (GitHub Pages)
Enable Pages on this repo (Settings → Pages → deploy from branch). The dashboard is then at
`https://<user>.github.io/<repo>/planting_monitor_out/dashboard.html`.

### 2. Google Sheet mirror (no auth)
In a Google Sheet cell:
```
=IMPORTDATA("https://raw.githubusercontent.com/<user>/<repo>/main/planting_monitor_out/status_today.csv")
```
It auto-refreshes ~hourly. Add conditional formatting on the `status` column (GREEN/AMBER/RED).

### 3. Daily refresh — scheduled Claude routine
Schedule a daily routine (~05:00 EAT) whose task is:
> Clone/pull this repo, run `python3 planting_monitor.py`, then commit & push the changed
> files in `planting_monitor_out/`.

That single push updates both surfaces (Pages re-serves the dashboard; the Sheet re-imports
the CSV).

## Refresh cadence
- **Daily:** forecast + observed-rain layers (this script).
- **Monthly:** sanity-check against the ICPAC / UNMA seasonal outlook and ENSO state.
- **Once a year:** rebuild `clim_baseline.json` from the latest CHIRPS record (and extend to
  the western / eastern bimodal districts — not yet covered).

## Not yet covered
Western (Fort Portal) and eastern sites fall outside the CHIRPS files used to build the
baseline. They are **bimodal** (two rain seasons) and need their own baseline before being
added here.
