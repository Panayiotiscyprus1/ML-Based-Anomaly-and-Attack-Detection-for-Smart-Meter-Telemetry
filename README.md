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
  cleaning.py        Stage 2 cleaning pipeline (commissioning trim, rollbacks,
                       hourly reindex, coverage trim, usability classification)
  events.py          OCSF-inspired JSONL event emitter (rule-based: rollbacks, gaps)
  features.py        Stage 3 feature matrix builder (families 1–3: raw, windowed
                       stats, temporal/cyclical); optional profile-deviation via
                       meter_profile.py
  meter_profile.py   Stage 3 per-meter behavioural profile (24×7 median table,
                       deviation feature, 168-vector for clustering)
notebooks/
  01_explore_snapshot.ipynb   inventory the 2031-meter snapshot; load, check, clean,
                              and visualise all 5 development meters; emit events
docs/
  cleaning.md        detailed cleaning methodology, findings, and parameter docs
  features.md        feature matrix reference: all columns, design rationale, NaN notes
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
outputs/events/      JSONL anomaly events (downstream input for Event Correlation Engine)
  rollback_events.jsonl   7 counter-rollback events across 5 meters
  events_sample.jsonl     11 events total (7 rollbacks + 4 transmission gaps)
data_reports/        aggregate data-quality summaries (no raw data)
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
| 2 — Clean | Commissioning trim, rollback handling, hourly reindex, coverage-based service-start trim, usability classification, gap-boundary diff guard | **Done** |
| 3 — Features | Per-meter feature matrix: raw, windowed stats, temporal/cyclical, profile-deviation | **Done** |
| 4 — Model | Anomaly / attack detector (isolation forest / statistical baseline / TBD) | Pending |
| 5 — Output | OCSF-inspired JSONL event export for Event Correlation Engine | **Partial** — rule-based events (rollbacks, gaps) working; ML-detected events pending |

---

## Stage 2 — Cleaning pipeline

`src/cleaning.py` transforms raw cumulative-flow time series into analysis-ready hourly consumption series. The pipeline runs in order:

1. **Commissioning trim** — drops the leading flat-zero period before the meter was plumbed in
2. **Consumption differencing** — `consumption = flow.diff()` converts the cumulative counter to hourly usage
3. **Counter-rollback handling** — negative diffs (counter resets) are set to `NaN` and recorded as rule-based anomaly events
4. **Hourly reindex** — fills the series onto a continuous 1h grid; missing hours become explicit `NaN` rows
5. **Coverage-based service-start trim** — uses rolling reporting density (7-day window) to detect and trim sparse preambles; no-op on healthy meters, scales to 2 031 meters without per-meter configuration
6. **Usability classification** — labels each meter `good` / `sparse` / `unusable` based on post-trim reporting rate
7. **Gap-boundary diff guard** — consumption values spanning a transmission gap are set to `NaN` to prevent false spikes

Full methodology, findings, and parameter documentation: [`docs/cleaning.md`](docs/cleaning.md)

### Cleaning results (5 development meters)

| Meter | Raw rows | After cleaning | Reporting rate | Usability | Rollbacks | Zero-rate | Mean consumption (m³/h) |
|---|---|---|---|---|---|---|---|
| 202405101132 | 10 441 | 10 309 | 98.8% | good | 0 | 50.1% | 0.0085 |
| 202405101935 | 8 016 | 1 213 | 100% | good | 1 | 67.7% | 0.0064 |
| 202405101899 | 7 992 | 8 016 | 98.8% | good | 1 | 94.4% | 0.0015 |
| 202405100909 | 10 314 | 10 262 | 99.0% | good | 0 | 69.3% | 0.0053 |
| 202405101891 | 8 904 | 8 952 | 98.9% | good | 5 | 24.5% | 0.0107 |

All 5 meters classified as `good` after cleaning. Zero-consumption rate ranges from 24.5% to 94.4% — wide behavioural variation that the anomaly detector must handle (zero-inflation).

---

## Stage 3 — Feature matrix

`src/features.py` and `src/meter_profile.py` build a per-meter feature matrix from the cleaned consumption series: one row per hour, one column per feature. The same matrix serves every downstream detector (baseline and ML), keeping scores comparable.

Features are organised into four families that map onto the **point / contextual / collective** anomaly taxonomy:

### Family 1 — Raw / instantaneous

| Column | Meaning |
|---|---|
| `consumption` | Hourly usage (m³), from cleaning. The value under test. |

### Family 2 — Statistical / windowed

Rolling statistics over **24 h** (daily) and **168 h** (weekly) windows, plus lag features. All are NaN-aware (`min_periods`) and lag by one hour (`shift(1)`) so a reading never describes itself.

| Column(s) | Meaning |
|---|---|
| `roll_mean_24`, `roll_mean_168` | Mean usage over the prior day / week |
| `roll_std_24`, `roll_std_168` | Variability over the prior day / week |
| `roll_min_24`, `roll_max_24`, `roll_min_168`, `roll_max_168` | Range over the window |
| `lag_1` | Value one hour ago |
| `lag_24` | Same hour yesterday |
| `lag_168` | Same hour last week |

### Family 3 — Temporal / contextual

| Column(s) | Meaning |
|---|---|
| `hour_of_day` (0–23) | Hour of day |
| `day_of_week` (0=Mon … 6=Sun) | Weekday |
| `is_weekend` (0/1) | Saturday/Sunday flag |
| `is_holiday` (0/1) | Cyprus public holiday (fixed + moving Orthodox dates, 2024–26) |
| `hour_sin`, `hour_cos` | Cyclical encoding of hour (23:00 adjacent to 00:00 in feature space) |
| `dow_sin`, `dow_cos` | Cyclical encoding of day-of-week |

### Family 4 — Profile-deviation (`meter_profile.py`)

A **24 × 7 = 168-cell** lookup table storing the **median** consumption for each `(hour_of_day, day_of_week)` bucket — the meter's weekly fingerprint. Median (not mean) keeps the expected value robust to bursts. Optionally passed into `build_features()` via the `profile=` argument.

| Column | Meaning |
|---|---|
| `profile_expected` | Median usage for this hour-of-week bucket |
| `deviation` | `consumption − profile_expected` — distance from contextual normal |

The 168-cell profile vector is also the seed for **behavioural clustering** (Week 5+).

Full column reference, design rationale, and NaN notes: [`docs/features.md`](docs/features.md)

---

## Stage 5 (partial) — Event output

`src/events.py` emits OCSF-inspired JSONL events for detected anomalies, designed for consumption by the downstream Event Correlation Engine.

### Supported event types

| Event type | Method | Severity | Description |
|---|---|---|---|
| `counter_rollback` | rule | medium | Cumulative flow counter decreased between consecutive readings |
| `transmission_gap` | rule | low–high | No reported readings for 24+ consecutive hours; severity scales with duration (≥72h medium, ≥96h high) |

### Event schema

Each event follows a 14-field OCSF-inspired schema:

```
time, source_type, asset_id, metric, observed_value, expected_value,
anomaly_score, method, severity, confidence, anomaly_type, context,
evidence, metadata
```

Two event families by convention:
- **Value-based** anomalies (future: consumption spikes, night bursts) — `observed_value` and `expected_value` as a comparable pair
- **Structural** anomalies (rollbacks, gaps) — `expected_value = null`; the defining quantity is in `observed_value` (rollbacks) or `context.duration_hours` (gaps)

### Current output

- `outputs/events/rollback_events.jsonl` — 7 counter-rollback events across 3 meters
- `outputs/events/events_sample.jsonl` — 11 total events (7 rollbacks + 4 transmission gaps ≥24h)

---

## Key data findings

- **Zero-consumption (idle) proportion ranges from ~25% to ~94%** across meters — the baseline detector must handle zero-inflation explicitly
- **Counter rollbacks are rare** (0–5 per meter, 7 total across 5 meters) — small negative jumps (−0.001 to −0.006 m³), likely counter glitches not tamper
- **Gap-boundary false spikes** — when a meter misses transmissions, the first reading after reconnection reflects all accumulated usage, creating a false one-hour spike. Fixed by nulling consumption at gap boundaries
- **Meter 202405101899** had a 9-month sparse preamble (8 real readings out of 6 541 hours) before entering consistent service — automatically trimmed by the coverage-based service-start rule
- **Meter 202405101935** had ~6 800 commissioning rows trimmed — very long flat-zero period before plumbing

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

## Usage

### Scraper

Pull the full history for a single meter (set `DEVICE_ID` in the script):

```bash
python scraper.py
```

Output: `meter_<DEVICE_ID>.csv` in the current directory. Move it to `data/raw/` and log it in `data/raw/EXPORT_LOG.MD`.

### Data loading

```bash
# Snapshot loader + inventory summary
python src/data_loading.py data/raw/snapshot_eoa_nic.csv

# Single meter loader + sanity checks
python src/data_loading.py data/raw/meter_202405101132.csv --meter
```

### Cleaning

```bash
# Clean a single meter and print the data-quality report
python src/cleaning.py data/raw/meter_202405101132.csv
```

### Visualisation

```bash
# Plot cumulative flow + hourly consumption (saves meter_plot.png)
python src/plotting.py data/raw/meter_202405101132.csv
```

### Feature matrix

```bash
# Build feature matrix for one meter and print a summary
python src/features.py data/raw/meter_202405101132.csv
```

```python
from src.data_loading import load_meter_csv
from src.cleaning import clean_meter
from src.meter_profile import build_profile
from src.features import build_features, feature_columns

res = clean_meter(load_meter_csv("data/raw/meter_202405101132.csv"))
profile = build_profile(res.df)          # 24×7 median table
feats = build_features(res.df, profile=profile)   # full 4-family matrix
print(feats.shape, feature_columns(feats))
```

Without the `profile=` argument, families 1–3 are built; `profile_expected` and `deviation` are omitted.

### Event generation

```bash
# Demo: print a sample OCSF event to stdout
python src/events.py
```
