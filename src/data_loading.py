"""
data_loading.py
---------------
Loading functions for the CYENS water-meter anomaly detection project.

Design principle: the rest of the pipeline should not care WHERE data comes
from. Today it's a CSV exported from the operational platform; later it may be
a database query. Only the functions in this file change when the source
changes -- everything downstream keeps working.

Handles the common "European CSV" gotchas:
  - delimiter may be ';' instead of ','
  - decimals may use ',' instead of '.'
  - dates may be day-first
  - encoding may be latin-1 / utf-8-sig (BOM)
"""

from __future__ import annotations
import csv
from pathlib import Path
import pandas as pd


def _sniff_delimiter(path: Path, encoding: str) -> str:
    """Guess the delimiter by sampling the first few KB of the file."""
    with open(path, "r", encoding=encoding, errors="replace") as f:
        sample = f.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except csv.Error:
        # Fall back to whichever common delimiter appears most
        counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
        return max(counts, key=counts.get)


def load_snapshot(path: str | Path) -> pd.DataFrame:
    """
    Load the 'all meters, latest value' snapshot export (View B).

    This is the fleet snapshot: one row per meter, current state only.
    Useful as a meter inventory / context table / current alarm state.
    It is NOT time-series data and cannot be used for temporal anomaly
    detection on its own.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No file at {path}")

    # Try a couple of encodings; operational exports vary
    last_err = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            sep = _sniff_delimiter(path, encoding)
            df = pd.read_csv(
                path,
                sep=sep,
                encoding=encoding,
                decimal=".",          # change to "," if your export uses comma decimals
                engine="python",      # tolerant parser
            )
            print(f"[load_snapshot] loaded {len(df)} rows, {len(df.columns)} cols "
                  f"(encoding={encoding}, sep='{sep}')")
            return df
        except Exception as e:  # noqa: BLE001 -- we want to try the next encoding
            last_err = e
    raise RuntimeError(f"Could not parse {path}: {last_err}")


def inventory_summary(df: pd.DataFrame,
                      sn_col: str = "Device SN",
                      name_col: str = "Device Name",
                      last_comm_col: str = "Last communication time",
                      alarm_col: str = "Alarm") -> None:
    """
    Print a quick human-readable summary of the snapshot:
      - how many meters
      - how many have names / alarms
      - how stale the communications are
    Column names default to what the platform export showed; adjust if yours
    differ. Missing columns are skipped with a note rather than crashing.
    """
    print("\n=== SNAPSHOT INVENTORY SUMMARY ===")
    print(f"Total meters (rows): {len(df)}")

    if sn_col in df.columns:
        print(f"Unique device SNs:   {df[sn_col].nunique()}")
    else:
        print(f"(no '{sn_col}' column found)")

    if name_col in df.columns:
        named = df[name_col].notna().sum()
        print(f"Meters with a name:  {named}")
        # show a few example names for context
        examples = df[name_col].dropna().unique()[:8]
        if len(examples):
            print("  example names:", ", ".join(map(str, examples)))

    if alarm_col in df.columns:
        alarm_str = df[alarm_col].fillna("").astype(str).str.strip().str.lower()
        is_alarm = ~alarm_str.isin(["", "nan", "none"])
        print(f"Meters with an alarm flag: {int(is_alarm.sum())}")
        alarm_types = (df[alarm_col].dropna().astype(str).str.strip()
                       .replace("", pd.NA).dropna().value_counts())
        if len(alarm_types):
            print("  alarm types seen:")
            for k, v in alarm_types.items():
                print(f"    {v:>4}  {k}")

    if last_comm_col in df.columns:
        ts = pd.to_datetime(df[last_comm_col], errors="coerce", dayfirst=False)
        n_bad = ts.isna().sum()
        if n_bad:
            print(f"  ({n_bad} timestamps could not be parsed -- check date format)")
        if ts.notna().any():
            newest, oldest = ts.max(), ts.min()
            print(f"Last communication: newest={newest}, oldest={oldest}")
            # flag meters silent for a while relative to the newest reading
            stale = ts < (newest - pd.Timedelta(days=3))
            print(f"Meters silent >3 days vs newest: {int(stale.sum())}")
    print("==================================\n")


# =====================================================================
# Historical per-meter time series (View A / backend dump)
# =====================================================================
#
# Raw schema (9 cols): params, id, updateAt, deviceId, flow, dataTime,
#                       signalCsq, isWaring, paValue
#   dataTime   -> measurement timestamp (hourly) -- THE time index
#   flow       -> cumulative Normal Flow counter (m^3); monotonically
#                 non-decreasing under normal operation
#   deviceId   -> meter serial number
#   signalCsq  -> CSQ signal quality (often 0 / not reported)
#   isWaring   -> warning/alarm flag (often NaN)
#   updateAt   -> transmission/export time (kept aside, not used downstream)
#   params, paValue, id -> not needed for analysis
#
# IMPORTANT: the export is newest-first, so we MUST sort ascending by
# dataTime, or every diff and rolling window downstream is reversed.

# Columns we keep; everything else is dropped on load.
_METER_KEEP = ["dataTime", "deviceId", "flow", "signalCsq", "isWaring"]


def load_meter_csv(path: str | Path,
                   set_index: bool = True) -> pd.DataFrame:
    """
    Load one meter's historical hourly time series.

    Returns a tidy, time-sorted (ascending) DataFrame containing the
    measurement time, meter id, cumulative flow, signal, and warning flag.

    Parameters
    ----------
    path : str | Path
        CSV file for a single meter.
    set_index : bool
        If True (default), use the parsed dataTime as a DatetimeIndex.
        If False, keep dataTime as a regular column.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No file at {path}")

    # Reuse the encoding/delimiter handling from the snapshot loader
    last_err = None
    df = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            sep = _sniff_delimiter(path, encoding)
            df = pd.read_csv(path, sep=sep, encoding=encoding, engine="python")
            break
        except Exception as e:  # noqa: BLE001 -- try next encoding
            last_err = e
    if df is None:
        raise RuntimeError(f"Could not parse {path}: {last_err}")

    # --- parse the measurement timestamp ---
    if "dataTime" not in df.columns:
        raise KeyError(f"'dataTime' not found. Columns are: {list(df.columns)}")
    df["dataTime"] = pd.to_datetime(df["dataTime"], errors="coerce")
    n_bad_ts = df["dataTime"].isna().sum()
    if n_bad_ts:
        print(f"[load_meter_csv] {n_bad_ts} unparseable timestamps dropped")
        df = df[df["dataTime"].notna()]

    # --- coerce flow to numeric (defensive: strip stray tabs/spaces) ---
    if "flow" in df.columns:
        df["flow"] = pd.to_numeric(
            df["flow"].astype(str).str.strip(), errors="coerce"
        )

    # --- keep only the useful columns (those that exist) ---
    keep = [c for c in _METER_KEEP if c in df.columns]
    df = df[keep].copy()

    # --- CRITICAL: sort oldest-first by measurement time ---
    df = df.sort_values("dataTime").reset_index(drop=True)

    # --- drop exact duplicate timestamps, keeping the first ---
    n_dupes = df["dataTime"].duplicated().sum()
    if n_dupes:
        print(f"[load_meter_csv] {n_dupes} duplicate timestamps dropped")
        df = df.drop_duplicates(subset="dataTime", keep="first").reset_index(drop=True)

    if set_index:
        df = df.set_index("dataTime")

    meter = df["deviceId"].iloc[0] if "deviceId" in df.columns and len(df) else "?"
    print(f"[load_meter_csv] meter {meter}: {len(df)} rows "
          f"({df.index.min() if set_index else df['dataTime'].min()} -> "
          f"{df.index.max() if set_index else df['dataTime'].max()})")
    return df


def load_meters(folder: str | Path,
                pattern: str = "meter_*.csv") -> dict[str, pd.DataFrame]:
    """
    Load every per-meter CSV in a folder into {deviceId: DataFrame}.

    Convenience wrapper around load_meter_csv for the 5-meter set.
    """
    folder = Path(folder)
    files = sorted(folder.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No files matching {pattern} in {folder}")
    out: dict[str, pd.DataFrame] = {}
    for f in files:
        df = load_meter_csv(f)
        key = str(df["deviceId"].iloc[0]) if "deviceId" in df.columns else f.stem
        out[key] = df
    print(f"[load_meters] loaded {len(out)} meters")
    return out


def meter_check(df: pd.DataFrame) -> None:
    """
    Print the Stage-0 sanity checks for a loaded meter time series.
    Confirms the loader produced clean, correctly-ordered data.
    """
    flow_in_index = df.index.name == "dataTime"
    print("\n=== METER LOAD CHECK ===")
    print(f"Rows:                  {len(df)}")
    print(f"Time-sorted ascending: {df.index.is_monotonic_increasing if flow_in_index else 'dataTime not index'}")
    if "flow" in df.columns:
        print(f"flow dtype numeric:    {pd.api.types.is_numeric_dtype(df['flow'])}")
        diffs = df["flow"].diff()
        print(f"Counter rollbacks:     {int((diffs < 0).sum())}  (flow decreased)")
        print(f"Flat hours (diff==0):  {int((diffs == 0).sum())}  (possible stuck / zero-usage)")
    # gap check: expected hourly spacing
    if flow_in_index and len(df) > 1:
        gaps = df.index.to_series().diff().dropna()
        non_hourly = (gaps != pd.Timedelta(hours=1)).sum()
        print(f"Non-hourly gaps:       {int(non_hourly)}  (missing/irregular intervals)")
    print("========================\n")


if __name__ == "__main__":
    # Quick manual test:
    #   python src/data_loading.py data/raw/snapshot.csv          (snapshot)
    #   python src/data_loading.py data/raw/meter_xxx.csv --meter (time series)
    import sys
    if len(sys.argv) > 1 and "--meter" in sys.argv:
        df = load_meter_csv(sys.argv[1])
        print(df.head())
        meter_check(df)
    elif len(sys.argv) > 1:
        df = load_snapshot(sys.argv[1])
        print(df.head())
        inventory_summary(df)
    else:
        print("Usage:")
        print("  python src/data_loading.py <snapshot.csv>")
        print("  python src/data_loading.py <meter.csv> --meter")