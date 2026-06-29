# Feature Matrix — Reference

**Project:** ML-Based Anomaly and Attack Detection for Smart-Meter Telemetry
**Stage:** 3 (Feature engineering)
**Module:** `src/features.py` (families 1–3 + temporal) and `src/profile.py` (family 4)

The feature matrix is built **per meter** from the cleaned `consumption` series: one row
per hour, one column per feature. It is the shared input for both the **baseline
detector** (Stage 3) and the **ML methods** (Isolation Forest, LOF — Weeks 4–5), so the
same matrix serves every detector and keeps their scores comparable.

The four feature families map onto the **point / contextual / collective** anomaly
framework — that mapping is *why* all four are needed.

---

## Family 1 — Raw / instantaneous 

| Column | Meaning |
|---|---|
| `consumption` | Hourly usage (m³), from cleaning. The value being judged. |

**Catches:** gross **point** anomalies (a single absurd value).
**Used by:** every detector, as the quantity under test.

---

## Family 2 — Statistical / windowed  

Rolling statistics over **24 h** (daily) and **168 h** (weekly) windows, plus **lag**
features. All are **NaN-aware** (`min_periods`) and **lag by one hour** (`shift(1)`) so a
reading never describes itself.

| Column(s) | Meaning |
|---|---|
| `roll_mean_24`, `roll_mean_168` | Mean usage over the prior day / week. |
| `roll_std_24`, `roll_std_168` | Variability (spread) over the prior day / week. |
| `roll_min_24`, `roll_max_24`, `roll_min_168`, `roll_max_168` | Range over the window. |
| `lag_1` | Same value one hour ago (immediate continuity). |
| `lag_24` | Same hour yesterday (daily periodicity). |
| `lag_168` | Same hour last week (weekly periodicity). |

**Catches:** **collective** anomalies — a value fine alone but wrong for its
neighbourhood (e.g. sustained drift, a value out of step with yesterday/last week).
**Used by:** the **ML methods** primarily (they consume mean/std/min/max/lags directly).
Note: the **baseline z-score does NOT use `roll_mean`/`roll_std`** — it computes its own
*robust* median/MAD statistics (see Family-adjacent note below), per the design.

---

## Family 3 — Temporal / contextual

Derived from each row's timestamp. Encode **when** a reading happened — the basis for
contextual judgement.

| Column(s) | Meaning |
|---|---|
| `hour_of_day` (0–23) | Hour. |
| `day_of_week` (0=Mon … 6=Sun) | Weekday. |
| `is_weekend` (0/1) | Saturday/Sunday flag. |
| `is_holiday` (0/1) | Cyprus public holiday (fixed + moving Orthodox dates, 2024–26). |
| `hour_sin`, `hour_cos` | **Cyclical** encoding of hour, so 23:00 sits next to 00:00. |
| `dow_sin`, `dow_cos` | Cyclical encoding of day-of-week. |

**Catches:** enables **contextual** anomalies (a value normal in magnitude but wrong for
its time — see Family 4, which uses these).
**Used by:** the ML methods (as context features); the temporal fields also define the
profile buckets in Family 4.

*Why cyclical encoding:* a raw integer hour would place 23 and 0 far apart, though they
are adjacent in time. The sin/cos pair puts each hour on a circle, so the model sees time
of day as smooth and periodic.

---

## Family 4 — Profile-deviation  *(`profile.py`)*

The single most valuable contextual feature. Built in two parts:

**(a) The per-meter profile** — a **24 × 7 = 168-cell** lookup table: for each
`(hour_of_day, day_of_week)` bucket, the **median** consumption over the meter's own
history. This is the meter's *weekly fingerprint* ("what is typical for this meter on a
Tuesday at 3 am"). Median (not mean) keeps the expected value robust to occasional
bursts, so real anomalies stand out instead of inflating the baseline.

**(b) The deviation feature:**

```
deviation_t = consumption_t − profile[hour(t), day_of_week(t)]
```

| Column | Meaning |
|---|---|
| `profile_expected` | The meter's median usage for this hour-of-week bucket. |
| `deviation` | `consumption − profile_expected` — how far this hour is from normal *for this time*. |

**Catches:** **contextual** anomalies — e.g. 0.4 m³ is normal at 8 am but a night-time
burst at 3 am.
**Used by:** the baseline's **profile-deviation check** and **profile-aware stuck check**;
the ML methods (as a context feature); and the 168-vector profile itself later **seeds the
behavioural clustering** (validated against the true customer category if the EOA MySQL
join is obtained).

---

## Note: the baseline's own robust statistics  *(`baseline.py`)*

The robust rolling z-score does **not** reuse `roll_mean`/`roll_std` (those are mean-based,
kept for the ML methods). The baseline computes its **own** lagging, NaN-aware statistics:

- rolling **median** and **MAD** (median absolute deviation) over the window;
- modified z-score `z = (x − median) / max(1.4826·MAD, σ_min)` with a denominator **floor**
  so near-zero (idle) windows cannot manufacture infinite scores.

These live in `baseline.py` (not `features.py`) because they are the detector's internal
statistics, not general-purpose features. This keeps the design rule visible in code:
**baseline = median/MAD; the mean-based columns are for the ML methods.**

---

## How the matrix is used downstream

| Consumer | Uses |
|---|---|
| **Baseline detector** (Stage 3) | `consumption`, `deviation`/`profile_expected`, temporal fields, the manufacturer-rule inputs; computes its own median/MAD internally. |
| **ML methods** — Isolation Forest, LOF | The full engineered matrix (windowed stats, lags, cyclical temporal, deviation). |
| **Behavioural clustering** | The 168-cell profile vectors per meter. |
| **Evaluation** (Week 8) | Detector scores (kept **continuous**, not collapsed to flags) for fair AUC comparison across baseline vs ML. |

---

## Build / NaN notes

- One meter in, one matrix out — loop over meters (consistent with `clean_meter`).
- Rolling/lag columns are **NaN during warm-up** (no prior window / lag at series start)
  and across cleaned gaps. This is expected, not a bug; detectors use NaN-aware ops.
- `feature_columns(df)` returns just the engineered column names (excludes `flow`,
  `consumption`, `deviceId`, etc.) — used to hand the right columns to the ML methods.

---

## Parameters (current)

| Parameter | Value | Where |
|---|---|---|
| Rolling windows | 24 h, 168 h | `features.WINDOWS` |
| Lags | 1, 24, 168 h | `features.LAGS` |
| Profile buckets | 168 (hour × day-of-week), median | `profile.py` |
| Robust z threshold | ≈ 3.5 (modified z) | `baseline.py` |
| Denominator floor `σ_min` | set per meter from non-zero scale | `baseline.py` |

> Cyprus moving-holiday dates (Green Monday, Good Friday, Easter Monday, Kataklysmos for
> 2024–26) are hardcoded in `features.py`
