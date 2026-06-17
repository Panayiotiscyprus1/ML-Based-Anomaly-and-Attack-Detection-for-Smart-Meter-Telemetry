### snapshot_eoa_nicosia.csv
- Operator's view: All-meters snapshot 
- Exported on: 2026-06-16, ~14:30
- Filters applied: none / all meters
- Time range covered: latest communication only (no history)
- Notes: Columns populated — Device SN, Last communication time, Normal Flow,
  Back Flow, Alarm, Flow Rate, RSRP, Temperature, Device Name, lat/long.
  ~2031 meters. Only 3 have reliable building type labels. Used as inventory + context.


### Historical per-meter exports (5 files)
Files: meter_202405101935.csv, meter_202405101899.csv, meter_202405101132.csv,
       meter_202405100909.csv, meter_202405101906.csv
- Source view: Historical Data (single-meter hourly history), obtained as a
  backend CSV dump from the platform (using scraper.py)
- Exported on: 2026-06-17, ~12:30
- Filters applied: none (full available history per meter)
- Time range covered: installation date -- last communication
- Columns (9): params, id, updateAt, deviceId, flow, dataTime, signalCsq, isWaring, paValue
  - dataTime = measurement timestamp (hourly); flow = cumulative Normal Flow counter;
    deviceId = meter SN; updateAt = transmission/export time; signalCsq = CSQ signal;
    isWaring = warning/alarm flag; params/paValue = empty for these meters
- Notes: Rows are newest-first (must sort ascending by dataTime). ~10,441 rows for
  202405101132 (~14 months hourly). signalCsq all 0 and isWaring/paValue all NaN for
  that meter (not reported). These are the time-series files for pipeline development.