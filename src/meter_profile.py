"""

Stage 3 (part A, family 4): the per-meter behavioural PROFILE and the
profile-deviation feature.
 
The profile is a 24 x 7 = 168-cell lookup table: for each
(hour_of_day, day_of_week) bucket, the MEDIAN consumption over the meter's
own history. It is the meter's "weekly fingerprint" -- its typical usage for,
e.g., "Tuesday 3 am".
 
Median (not mean) per bucket keeps the expected value robust to occasional
bursts, so real anomalies stand out instead of inflating the baseline.
 
The profile is a REUSABLE object, not only a feature:
  - the deviation feature (this file) feeds the feature matrix;
  - the baseline's profile-deviation and profile-aware stuck checks consume
    the profile table directly (Stage 3, baseline.py);
  - the 168-vector per meter later seeds the behavioural clustering, validated
    against the true customer category if the EOA MySQL join is obtained.
 
Design: built from the cleaned consumption series. Zeros are KEPT (a genuine
idle hour is real data); only NaNs (gaps/rollbacks) are excluded from the
median, so missing hours don't bias the typical-usage estimate.
"""
 
from __future__ import annotations
import numpy as np
import pandas as pd


def build_profile(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the 168-cell (hour x day-of-week) median-consumption profile for one
    cleaned meter.
 
    Returns
    -------
    A DataFrame indexed by hour_of_day (0-23), columns day_of_week (0=Mon..6=Sun),
    each cell = median consumption for that (hour, dow) bucket. NaN cells mean a
    bucket with no observed (non-NaN) readings yet.
    """
    if "consumption" not in df.columns:
        raise KeyError("Expected a 'consumption' column (run clean_meter first).")
 
    c = df["consumption"]
    g = pd.DataFrame({
        "consumption": c.values,
        "hour": df.index.hour,
        "dow": df.index.dayofweek,
    })
    # NaNs (gaps/rollbacks) are dropped by median(); operational zeros are kept.
    profile = (g.pivot_table(index="hour", columns="dow",
                             values="consumption", aggfunc="median"))
    # ensure a full 24x7 grid even if some buckets are empty
    profile = profile.reindex(index=range(24), columns=range(7))
    profile.index.name = "hour_of_day"
    profile.columns.name = "day_of_week"
    return profile
 
 
def profile_vector(profile: pd.DataFrame) -> np.ndarray:
    """
    Flatten the 168-cell profile to a 1-D vector (length 168), for clustering.
    Order is (hour 0..23) x (dow 0..6). NaN buckets -> 0.0 for distance use.
    """
    return np.nan_to_num(profile.values.flatten(), nan=0.0)
 
 
def expected_from_profile(df: pd.DataFrame, profile: pd.DataFrame) -> pd.Series:
    """
    Map each row of df to its profile-expected value via (hour, dow) lookup.
    Returns a Series aligned to df.index.
    """
    hours = df.index.hour
    dows = df.index.dayofweek
    # vectorised lookup into the 24x7 grid
    vals = profile.values[hours, dows]
    return pd.Series(vals, index=df.index, name="profile_expected")


def add_deviation(df: pd.DataFrame,
                  profile: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Attach the family-4 columns to a meter frame:
      - 'profile_expected' : the meter's median usage for this (hour, dow) bucket
      - 'deviation'        : consumption - profile_expected
 
    If `profile` is None it is built from `df` itself.
    """
    if profile is None:
        profile = build_profile(df)
    out = df.copy()
    out["profile_expected"] = expected_from_profile(out, profile)
    out["deviation"] = out["consumption"] - out["profile_expected"]
    return out
 
 
if __name__ == "__main__":
    import sys
    from cleaning import clean_meter
    from data_loading import load_meter_csv
    if len(sys.argv) > 1:
        res = clean_meter(load_meter_csv(sys.argv[1]))
        prof = build_profile(res.df)
        print("profile (rows=hour, cols=day-of-week), median consumption:")
        print(prof.round(3))
        feats = add_deviation(res.df, prof)
        print("\nsample deviation rows:")
        print(feats[["consumption", "profile_expected", "deviation"]].dropna().head())
    else:
        print("Usage: python src/profile.py <meter.csv>")