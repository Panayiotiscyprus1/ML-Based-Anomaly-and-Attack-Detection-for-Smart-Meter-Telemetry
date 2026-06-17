# CYENS Internship 2026 — Water-Meter Anomaly Detection

ML-based anomaly and attack detection for smart-meter telemetry (EOA Flowmeter platform, Nicosia).
Detected anomalies are exported as OCSF-inspired JSONL events for a downstream
Event Correlation Engine (built in parallel by another intern).

---

## Project structure

```
scraper.py           pulls per-meter historical data from the platform API → CSV
src/
  data_loading.py    load & validate CSV exports; swap this when DB access arrives
  plotting.py        Stage 1 visualisation (cumulative flow + hourly consumption)
notebooks/
  01_explore_snapshot.ipynb   inventory the 2031-meter snapshot; load & check all 5 meter CSVs
data/
  raw/               original exports — NEVER edited, NEVER committed
    snapshot_eoa_nic.csv         fleet snapshot: 2031 meters, latest values, exported 2026-06-16
    meter_202405101132.csv       10 441 rows, hourly, 2025-04-02 → 2026-06-16 (~14 months)
    meter_202405101935.csv        8 016 rows, hourly, 2024-09-30 → 2026-06-17
    meter_202405101899.csv        7 992 rows, hourly, 2024-09-30 → 2026-06-16
    meter_202405100909.csv       10 314 rows, hourly, 2025-04-08 → 2026-06-16
    meter_202405101891.csv        8 904 rows, hourly, 2024-09-30 → 2026-06-16
    EXPORT_LOG.MD    documents what was exported, when, and what the columns mean
  processed/         cleaned outputs (regenerated from raw, not committed)
outputs/events/      JSONL anomaly events (downstream input)
reports/             design note, final report
```

---

## Data overview

### Snapshot (`snapshot_eoa_nic.csv`)
- **2 031 meters**, one row each — latest reading only, not time-series
- 24 columns: Device SN, Last communication time, Normal Flow, Back Flow, Alarm, Flow Rate, RSRP, Temperature, lat/long, address, and others
- **701 meters** have active alarm flags (leakage, air tube, logic leakage, battery low, transducer error)
- Only **3 meters** have human-readable names; the rest are identified by serial number
- Used as meter inventory / context table / current alarm state

### Historical per-meter CSVs (`meter_*.csv`)
Scraped via `scraper.py` from the platform's backend API on 2026-06-17.

- 9 columns: `dataTime` (hourly timestamp), `flow` (cumulative m³ counter), `deviceId`, `signalCsq`, `isWaring`, plus `updateAt`, `id`, `params`, `paValue` (dropped on load)
- **Exported newest-first** — `load_meter_csv()` sorts ascending before returning
- `signalCsq` is 0 and `isWaring`/`paValue` are NaN for most meters (not reported by these devices)

Sanity check results (Stage 0):

| Meter | Rows | Date range | Rollbacks | Flat hours | Non-hourly gaps |
|---|---|---|---|---|---|
| 202405101132 | 10 441 | 2025-04-02 → 2026-06-16 | 0 | 5 413 | 1 |
| 202405101935 | 8 016 | 2024-09-30 → 2026-06-17 | 1 | 7 623 | 2 |
| 202405101899 | 7 992 | 2024-09-30 → 2026-06-16 | 1 | 7 631 | 2 |
| 202405100909 | 10 314 | 2025-04-08 → 2026-06-16 | 0 | 7 268 | 2 |
| 202405101891 | 8 904 | 2024-09-30 → 2026-06-16 | 5 | 2 242 | 2 |

---

## Pipeline stages

| Stage | Description | Status |
|---|---|---|
| 0 — Ingest | Scrape API → CSV; load & validate | **Done** |
| 1 — Explore | Snapshot inventory; per-meter visual inspection | **Done** |
| 2 — Clean | Trim commissioning period; handle counter rollbacks; resample to regular hourly grid | Pending |
| 3 — Features | Hourly consumption, rolling stats, gap flags, alarm state | Pending |
| 4 — Model | Anomaly / attack detector (isolation forest / statistical baseline / TBD) | Pending |
| 5 — Output | OCSF-inspired JSONL event export for Event Correlation Engine | Pending |

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root with your JWT from the platform:

```
JWT="eyJ..."
```

The token expires when your session ends. To get a fresh one: log in to the platform → DevTools → Network → copy the `Authorization: Bearer ...` header value.

---

## Scraper

Pull the full history for a single meter (set `DEVICE_ID` in the script):

```bash
python scraper.py
```

Output: `meter_<DEVICE_ID>.csv` in the current directory. Move it to `data/raw/` and log it in `data/raw/EXPORT_LOG.MD`.

---

## Quick tests

```bash
# Snapshot loader
python src/data_loading.py data/raw/snapshot_eoa_nic.csv

# Single meter loader + sanity checks
python src/data_loading.py data/raw/meter_202405101132.csv --meter

# Visualise a meter (saves meter_plot.png)
python src/plotting.py data/raw/meter_202405101132.csv
```
