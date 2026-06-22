"""
cleaning.py
-----------
Stage 2: turn a loaded meter (cumulative flow time series) into an
analysis-ready hourly CONSUMPTION series, plus data-quality events and a
coverage assessment.

Input contract (from data_loading.load_meter_csv):
    - DatetimeIndex named 'dataTime', sorted ascending
    - columns: deviceId, flow (cumulative), signalCsq, isWaring

Pipeline inside clean_meter, in order:
  1. Commissioning trim   -- drop the leading run before first real flow
     ('meter on but reporting flat zeros' before being plumbed in).
  2. Consumption          -- consumption = flow.diff().
  3. Rollback handling    -- negative diff (counter reset): true usage is
     unknown, so set consumption to NaN AND record it as a rule event.
  4. Hourly reindex       -- continuous 1h grid; missing hours become NaN.
  5. Coverage / service-start trim -- SCALABLE handling of the 'sparse
     preamble' case (a meter that barely reports for months before real
     service). Uses reporting density, not a hardcoded date, so it works
     identically on 5 meters or 2,031, and is a NO-OP on healthy meters.
  6. Usability flag       -- classify good / sparse / unusable from the
     post-trim reporting rate, so the pipeline can auto-exclude hopeless
     meters instead of being inspected by hand.

Commissioning (reporting-but-flat) and sparse-preamble (not-reporting) are
DIFFERENT problems, handled by separate explicit steps.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class CleanResult:
    df: pd.DataFrame
    device_id: str
    report: dict = field(default_factory=dict)
    rollback_events: list = field(default_factory=list)


def _as_indexed(df: pd.DataFrame) -> pd.DataFrame:
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


def service_start_time(flow_on_grid: pd.Series,
                       window_hours: int = 168,
                       density_threshold: float = 0.5):
    """
    First timestamp at which the meter is REPORTING consistently: reporting
    density (fraction of non-NaN hours over a centred rolling window) first
    reaches `density_threshold`. Runs on the reindexed hourly grid (missing
    hours are NaN). Returns None if the meter never reaches the threshold.
    """
    reported = flow_on_grid.notna().astype(float)
    density = reported.rolling(window_hours, center=True,
                               min_periods=max(1, window_hours // 4)).mean()
    dense = (density >= density_threshold)
    return density.index[dense.values.argmax()] if dense.any() else None


def clean_meter(df: pd.DataFrame,
                reindex_hourly: bool = True,
                rollback_to: str = "nan",
                coverage_window_hours: int = 168,
                density_threshold: float = 0.5,
                usable_min_reporting: float = 0.70) -> CleanResult:
    work = _as_indexed(df)
    device_id = str(work["deviceId"].iloc[0]) if "deviceId" in work.columns and len(work) else "?"
    n_raw = len(work)
    if "flow" not in work.columns:
        raise KeyError("Expected a 'flow' column.")

    # 1. Commissioning trim
    conn = first_flow_time(work["flow"])
    if conn is not None:
        work = work.loc[conn:]
    commissioning_dropped = n_raw - len(work)

    # 2. Consumption
    work["consumption"] = work["flow"].diff()
    
    # Invalidate diffs that span reporting gaps
    time_delta = work.index.to_series().diff()
    gap_mask = time_delta > pd.Timedelta(hours=1)

    work.loc[gap_mask, "consumption"] = float("nan")

    # 3. Rollbacks
    rollback_mask = work["consumption"] < 0
    n_rollbacks = int(rollback_mask.sum())
    rollback_events = []
    for ts, row in work.loc[rollback_mask].iterrows():
        rollback_events.append({
            "time": ts.isoformat(),
            "asset_id": device_id,
            "anomaly_type": "counter_rollback",
            "method": "rule",
            "observed_value": float(row["consumption"]),
            "evidence": "cumulative flow decreased between consecutive readings",
        })
    repl = float("nan") if rollback_to == "nan" else 0.0
    work.loc[rollback_mask, "consumption"] = repl

    # 4. Reindex to continuous hourly grid
    n_gaps = 0
    if reindex_hourly and len(work) > 1:
        full = pd.date_range(work.index.min(), work.index.max(), freq="h")
        n_gaps = len(full) - len(work.index.unique())
        work = work.reindex(full)
        work.index.name = "dataTime"
        if "deviceId" in work.columns:
            work["deviceId"] = work["deviceId"].ffill().bfill()

    # 5. Coverage-based service-start trim (no-op if healthy)
    svc = service_start_time(work["flow"], coverage_window_hours, density_threshold)
    low_coverage_dropped = 0
    if svc is not None and svc > work.index.min():
        before = len(work)
        work = work.loc[svc:]
        low_coverage_dropped = before - len(work)

    # 6. Usability classification
    n_final = len(work)
    reporting_rate = float(work["flow"].notna().mean()) if n_final else 0.0
    if svc is None or reporting_rate < usable_min_reporting:
        usability = "unusable"
    elif reporting_rate < 0.90:
        usability = "sparse"
    else:
        usability = "good"

    cons = work["consumption"]
    zero_hours = int((cons == 0).sum())
    nonzero_hours = int((cons > 0).sum())
    report = {
        "device_id": device_id,
        "rows_raw": n_raw,
        "commissioning_dropped": commissioning_dropped,
        "low_coverage_dropped": low_coverage_dropped,
        "first_flow": conn.isoformat() if conn is not None else None,
        "service_start": svc.isoformat() if svc is not None else None,
        "rows_final": n_final,
        "reporting_rate": round(reporting_rate, 3),
        "usability": usability,
        "rollbacks": n_rollbacks,
        "gaps_filled": int(n_gaps),
        "zero_consumption_hours": zero_hours,
        "nonzero_consumption_hours": nonzero_hours,
        "nan_consumption_hours": int(cons.isna().sum()),
        "zero_rate": round(zero_hours / n_final, 3) if n_final else None,
        "consumption_mean": round(float(cons.mean()), 4) if nonzero_hours else 0.0,
        "consumption_max": round(float(cons.max()), 4) if cons.notna().any() else None,
    }
    return CleanResult(df=work, device_id=device_id,
                       report=report, rollback_events=rollback_events)


def report_table(results):
    return pd.DataFrame([r.report for r in results]).set_index("device_id")


if __name__ == "__main__":
    import sys
    from data_loading import load_meter_csv
    if len(sys.argv) > 1:
        res = clean_meter(load_meter_csv(sys.argv[1]))
        for k, v in res.report.items():
            print(f"{k:>24}: {v}")
    else:
        print("Usage: python src/cleaning.py <meter.csv>")