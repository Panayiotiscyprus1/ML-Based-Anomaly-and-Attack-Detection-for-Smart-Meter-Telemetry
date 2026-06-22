"""
Stage 2: turn a loaded meter (cumulative flow time series) into an
analysis-ready hourly CONSUMPTION series, plus a record of data-quality
events found along the way.

Input contract (from data_loading.load_meter_csv):
    - DatetimeIndex named 'dataTime', sorted ascending
    - columns: deviceId, flow (cumulative), signalCsq, isWaring

What clean_meter does, in order:
  
  1. Trim the commissioning period  -- drop the leading run of readings
     before the meter is plumbed in (everything before first real flow).

  2. Derive consumption -- consumption = flow.diff() (the core
     transformation: cumulative counter -> hourly usage).
  
  3. Handle counter rollbacks -- where the diff is negative the counter
     reset; the true usage that hour is UNKNOWN, so set consumption to NaN
     (do not let a spurious negative poison rolling stats / model scaling).
     The rollback is still RECORDED as an event so it is not lost.
  
  4. Reindex to a continuous hourly grid -- so downstream rolling windows
     behave predictably; missing hours become NaN consumption.

Design notes:
  - The function is pure: raw-loaded df in, cleaned df out, plus a small
    report dict. No files written here (saving happens in the notebook /
    pipeline, into data/processed/).
  - Two-layer rollback handling: the consumption series is cleaned, AND the
    rollback is reported as a rule-detected anomaly event. Deterministic,
    certain anomalies are caught by rules here -- not left for the ML model.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


# Result container
# --------------------------------------------------------------------------
@dataclass
class CleanResult:
    """Holds the cleaned series and a record of what cleaning did/found."""
    df: pd.DataFrame                      # cleaned, time-indexed: flow, consumption, ...
    device_id: str
    report: dict = field(default_factory=dict)   # data-quality summary
    rollback_events: list = field(default_factory=list)  # rule-detected anomalies (rollback anomaly)


# Helpers
# --------------------------------------------------------------------------
def _as_indexed(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure dataTime is the DatetimeIndex and the frame is time-sorted."""
    if df.index.name == "dataTime":
        out = df.copy()
    elif "dataTime" in df.columns:
        out = df.set_index("dataTime").copy()
    else:
        raise KeyError("Expected 'dataTime' as index or column.")
    return out.sort_index()


def first_flow_time(flow: pd.Series):
    """First timestamp at which cumulative flow increases (start of service)."""
    moved = flow.diff() > 0
    return moved.idxmax() if moved.any() else None


# Main entry point
# --------------------------------------------------------------------------
def clean_meter(df: pd.DataFrame,
                reindex_hourly: bool = True,
                rollback_to: str = "nan") -> CleanResult:
    """
    Clean one loaded meter into an analysis-ready consumption series.

    Parameters
    ----------
    df : DataFrame
        Output of load_meter_csv (DatetimeIndex 'dataTime', 'flow', ...).
    reindex_hourly : bool
        If True, reindex onto a continuous 1-hour grid (missing hours -> NaN).
    rollback_to : {'nan', 'zero'}
        What to replace a rollback hour's consumption with. 'nan' is the
        honest default (true usage is unknown); 'zero' if you prefer.

    Returns
    -------
    CleanResult
    """
  
    work = _as_indexed(df)
    device_id = str(work["deviceId"].iloc[0]) if "deviceId" in work.columns and len(work) else "?"
    n_raw = len(work)

    if "flow" not in work.columns:
        raise KeyError("Expected a 'flow' column.")

    # --- 1. Trim commissioning period (before first real flow) ---
    conn = first_flow_time(work["flow"])
    if conn is not None:
        work = work.loc[conn:]
    n_after_trim = len(work)
    commissioning_dropped = n_raw - n_after_trim

    # --- 2. Derive hourly consumption ---
    work["consumption"] = work["flow"].diff()

    # --- 3. Handle counter rollbacks (negative diff) ---
    rollback_mask = work["consumption"] < 0
    n_rollbacks = int(rollback_mask.sum())
    rollback_events = []
    for ts, row in work.loc[rollback_mask].iterrows():
        rollback_events.append({
            "time": ts.isoformat(),
            "asset_id": device_id,
            "anomaly_type": "counter_rollback",
            "method": "rule",
            "observed_value": float(row["consumption"]),  # the negative diff
            "evidence": "cumulative flow decreased between consecutive readings",
        })
    # remove the corrupt value from the consumption series
    repl = float("nan") if rollback_to == "nan" else 0.0
    work.loc[rollback_mask, "consumption"] = repl

    # --- 4. Reindex onto a continuous hourly grid ---
    n_gaps = 0
    if reindex_hourly and len(work) > 1:
        full = pd.date_range(work.index.min(), work.index.max(), freq="h")
        n_gaps = len(full) - len(work.index.unique())
        work = work.reindex(full)
        work.index.name = "dataTime"
        # deviceId is constant -> forward-fill it for the inserted rows
        if "deviceId" in work.columns:
            work["deviceId"] = work["deviceId"].ffill().bfill()

    # --- 5. Data-quality report ---
    cons = work["consumption"]
    n_final = len(work)
    zero_hours = int((cons == 0).sum())
    nonzero_hours = int((cons > 0).sum())
    nan_hours = int(cons.isna().sum())

    report = {
        "device_id": device_id,
        "rows_raw": n_raw,
        "rows_after_trim": n_after_trim,
        "commissioning_rows_dropped": commissioning_dropped,
        "first_flow": conn.isoformat() if conn is not None else None,
        "rows_final": n_final,
        "rollbacks": n_rollbacks,
        "gaps_filled": int(n_gaps),
        "zero_consumption_hours": zero_hours,
        "nonzero_consumption_hours": nonzero_hours,
        "nan_consumption_hours": nan_hours,
        "zero_rate": round(zero_hours / n_final, 3) if n_final else None,
        "consumption_mean": round(float(cons.mean()), 4) if nonzero_hours else 0.0,
        "consumption_max": round(float(cons.max()), 4) if cons.notna().any() else None,
    }

    return CleanResult(df=work, device_id=device_id,
                       report=report, rollback_events=rollback_events)


def report_table(results: list[CleanResult]) -> pd.DataFrame:
    """Build a one-row-per-meter data-quality table from several CleanResults."""
    return pd.DataFrame([r.report for r in results]).set_index("device_id")


if __name__ == "__main__":
    # Manual test:  python src/cleaning.py data/raw/meter_xxx.csv
    import sys
    from data_loading import load_meter_csv
    if len(sys.argv) > 1:
        loaded = load_meter_csv(sys.argv[1])
        res = clean_meter(loaded)
        print("\n--- cleaning report ---")
        for k, v in res.report.items():
            print(f"{k:>28}: {v}")
        print(f"\nrollback events recorded: {len(res.rollback_events)}")
        print(res.df.head())
    else:
        print("Usage: python src/cleaning.py <meter.csv>")
