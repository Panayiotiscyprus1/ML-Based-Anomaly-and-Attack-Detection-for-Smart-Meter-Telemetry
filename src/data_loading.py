"""
data_loading.py
---------------
Loading functions:
---
Design principle: for the rest of the pipeline WHERE data comes from should not be a metter. 

V16/06: Handles CSV exported from the operational platform;
  --> Handles the common CSV issues:
  - delimiter may be ';' instead of ','
  - decimals may use ',' instead of '.'
  - dates may be day-first
  - encoding may be latin-1 / utf-8-sig (BOM)  

--> Later versions may handle a database query. 

Only the functions in this file change when the source changes -- everything downstream should keeps working.

"""

from __future__ import annotations
import csv
from pathlib import Path
import pandas as pd


def _sniff_delimiter(path: Path, encoding: str) -> str:
    #Guess the delimiter by sampling the first few KB of the file.
    with open(path, "r", encoding=encoding, errors="replace") as f:
        sample = f.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except csv.Error:
        # Fall back to whichever common delimiter appears most
        counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
        return max(counts, key=counts.get)
    
def _clean_numeric(series: pd.Series) -> pd.Series:
    """Strip whitespace/tabs and convert to float; bad values become NaN."""
    return pd.to_numeric(
        series.astype(str).str.strip().replace({"": None, "nan": None}),
        errors="coerce",
    )


def load_snapshot(path: str | Path) -> pd.DataFrame:
    """
    Load the all meters snapshot.
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
            numeric_cols = ["Normal Flow(m³)", "Back Flow(m³)", "Flow Rate(m³/h)",
                            "RSRP(dBm)", "Temperature(℃)", "Longitude", "Latitude"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = _clean_numeric(df[col])
            print(f"[load_snapshot] loaded {len(df)} rows, {len(df.columns)} cols "
                  f"(encoding={encoding}, sep='{sep}')")
            return df
        except Exception as e:  # noqa: BLE001 -- we want to try the next encoding
            last_err = e
    raise RuntimeError(f"Could not parse {path}: {last_err}")


def inventory_summary(df: pd.DataFrame,
                      sn_col: str = "Device SN*",
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


if __name__ == "__main__":
    # Quick manual test: python src/data_loading.py data/raw/snapshot.csv
    import sys
    if len(sys.argv) > 1:
        df = load_snapshot(sys.argv[1])
        print(df.head())
        inventory_summary(df)
    else:
        print("Usage: python src/data_loading.py <path-to-snapshot.csv>")
