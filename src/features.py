"""
features.py
-----------
Stage 3 (part A): build a per-meter FEATURE MATRIX from a cleaned meter.

Input  : a cleaned meter df (from cleaning.clean_meter -> CleanResult.df),
         hourly DatetimeIndex 'dataTime', with a 'consumption' column.
Output : the same frame with feature columns added (one row per hour).

Feature families (this file covers 1-3; family 4 = profile-deviation is added
in profile.py / Day 2):

  1. Raw / instantaneous   -- consumption itself.
  2. Statistical / windowed -- rolling mean/std/min/max over 24h and 168h,
     plus lag features (t-1, t-24, t-168). All NaN-aware (min_periods), and
     all rolling stats LAG by one hour (shift(1)) so the current value is not
     used to describe itself -- this matters for the baseline detectors built
     on top.
  3. Temporal / contextual -- hour_of_day, day_of_week, is_weekend,
     is_holiday, with cyclical (sin/cos) encoding for hour and day-of-week so
     that adjacent times are adjacent in feature space (hour 23 ~ hour 0).

Design: one meter in, one feature matrix out -- loop over meters (consistent
with clean_meter). Pure function; no files written.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

# Rolling windows (hours) and lags (hours)
WINDOWS = [24, 168]            # daily, weekly
LAGS = [1, 24, 168]           # previous hour, same hour yesterday, same hour last week

# Cyprus public holidays for the years the data spans (2024-2026).
# Fixed-date holidays plus the MOVING Orthodox holidays (Green Monday, Good
# Friday, Easter Monday, Kataklysmos) whose dates change each year.
# NOTE: verify the moving dates if the data range changes.
CYPRUS_HOLIDAYS = {
    # fixed
    "01-01",  # New Year's Day
    "01-06",  # Epiphany
    "03-25",  # Greek Independence Day
    "04-01",  # Cyprus National Day
    "05-01",  # Labour Day
    "08-15",  # Assumption / Dormition
    "10-01",  # Cyprus Independence Day
    "10-28",  # Ohi Day
    "12-25",  # Christmas
    "12-26",  # Boxing Day
}
# Moving (Orthodox-calendar) holidays, explicit per year:
CYPRUS_MOVING_HOLIDAYS = {
    # 2024
    "2024-03-18",  # Green Monday
    "2024-05-03",  # Good Friday
    "2024-05-06",  # Easter Monday
    "2024-06-24",  # Kataklysmos (Pentecost Monday)
    # 2025
    "2025-03-03",  # Green Monday
    "2025-04-18",  # Good Friday
    "2025-04-21",  # Easter Monday
    "2025-06-09",  # Kataklysmos
    # 2026
    "2026-02-23",  # Green Monday
    "2026-04-10",  # Good Friday
    "2026-04-13",  # Easter Monday
    "2026-06-01",  # Kataklysmos
}


def _is_holiday(index: pd.DatetimeIndex) -> pd.Series:
    """Boolean Series: True where the date is a Cyprus public holiday."""
    md = index.strftime("%m-%d")
    ymd = index.strftime("%Y-%m-%d")
    fixed = pd.Series(md, index=index).isin(CYPRUS_HOLIDAYS)
    moving = pd.Series(ymd, index=index).isin(CYPRUS_MOVING_HOLIDAYS)
    return (fixed | moving)


def build_features(df: pd.DataFrame,
                   windows: list[int] = WINDOWS,
                   lags: list[int] = LAGS) -> pd.DataFrame:
    """
    Build families 1-3 of the feature matrix for one cleaned meter.

    Parameters
    ----------
    df : cleaned meter (hourly DatetimeIndex, 'consumption' column).
    windows, lags : rolling-window sizes and lag offsets, in hours.

    Returns
    -------
    A new DataFrame: the original columns plus the feature columns.
    """
    if "consumption" not in df.columns:
        raise KeyError("Expected a 'consumption' column (run clean_meter first).")
    f = df.copy()
    c = f["consumption"]

    # --- Family 1: raw / instantaneous ---
    # consumption is already present; nothing to add.

    # --- Family 2: statistical / windowed ---
    # Rolling stats LAG by one hour (shift(1)) so the current reading does not
    # describe itself; min_periods keeps them NaN-aware on short/gappy windows.
    for w in windows:
        roll = c.shift(1).rolling(w, min_periods=max(2, w // 4))
        f[f"roll_mean_{w}"] = roll.mean()
        f[f"roll_std_{w}"]  = roll.std()
        f[f"roll_min_{w}"]  = roll.min()
        f[f"roll_max_{w}"]  = roll.max()
    # Lag features
    for L in lags:
        f[f"lag_{L}"] = c.shift(L)

    # --- Family 3: temporal / contextual ---
    idx = f.index
    hour = idx.hour
    dow = idx.dayofweek                      # 0 = Monday ... 6 = Sunday
    f["hour_of_day"] = hour
    f["day_of_week"] = dow
    f["is_weekend"]  = (dow >= 5).astype(int)
    f["is_holiday"]  = _is_holiday(idx).astype(int).values
    # cyclical encodings
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["dow_sin"]  = np.sin(2 * np.pi * dow / 7)
    f["dow_cos"]  = np.cos(2 * np.pi * dow / 7)

    return f


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Return just the engineered feature column names (helper for modelling)."""
    base = {"deviceId", "flow", "consumption", "signalCsq", "isWaring"}
    return [c for c in df.columns if c not in base]


if __name__ == "__main__":
    import sys
    from cleaning import clean_meter
    from data_loading import load_meter_csv
    if len(sys.argv) > 1:
        res = clean_meter(load_meter_csv(sys.argv[1]))
        feats = build_features(res.df)
        print(f"feature matrix: {feats.shape[0]} rows x {feats.shape[1]} cols")
        print("engineered features:", feature_columns(feats))
        print(feats.tail(3).T)
    else:
        print("Usage: python src/features.py <meter.csv>")
