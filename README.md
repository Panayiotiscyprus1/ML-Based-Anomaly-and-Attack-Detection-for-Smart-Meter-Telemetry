# CYENS Internship 2026 — Water-Meter Anomaly Detection

ML-based anomaly and attack detection for smart-meter telemetry.
Detected anomalies are exported as OCSF-inspired JSONL events for a downstream
Event Correlation Engine (built in parallel by another intern).

## Project structure

```
src/                 reusable pipeline code (import this from notebooks)
  data_loading.py    load CSV exports; swap this when DB access arrives
notebooks/           exploration + presentation (import from src/)
data/
  raw/               original exports — NEVER edited, NEVER committed
  processed/         cleaned outputs (regenerated from raw)
outputs/events/      JSONL anomaly events
reports/             design note, final report
```

## Current data situation (Week 1)

- **Snapshot export (works):** all meters, latest value only. Used as meter
  inventory / context table / current alarm state. Not time-series.
- **Historical export (blocked):** single-meter hourly history exists in the
  platform UI but the export throws a server-side database error.
  Escalated to supervisor — historical time-series access is the critical path.
- **Pipeline scaffolding** is being validated against the public SKAB dataset
  while real historical access is arranged.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Quick test

```bash
python src/data_loading.py data/raw/snapshot.csv
```
