"""
events.py
---------
Minimal Stage-7 event emitter (built early for the rollback case).

Scope: this is intentionally small. It converts the rule-detected anomalies
that clean_meter already produces (currently: counter rollbacks) into
OCSF-inspired JSONL records matching the Week 1 design-note schema, so a
real sample can be shared with the Event Correlation Engine intern now.

It does NOT yet handle ML-detected anomalies, severity mapping from scores,
multi-method consolidation, or schema validation against a formal spec --
those are the full Week 7 event layer. This is the seed of that layer.

Design choices for RULE-detected events (documented for the intern, open to
change):
  - method        = "rule"            (deterministic detector, not ML)
  - anomaly_score = null              (a rule has no continuous "degree";
                                        score applies to ML detectors only)
  - confidence    = 1.0               (the rule is certain the event occurred:
                                        the counter definitely decreased)
  - metric        = "cumulative_flow_m3"  (a rollback is a counter event, not
                                            a consumption value)
  - observed_value = the negative jump in cumulative flow
  - expected_value = null             (true value during a reset is unknown)
  - severity      = "medium"          (data-integrity / possible tamper; not an
                                        active leak)  <-- DISCUSS with intern

Schema convention -- two event families (documented for the intern):
  - VALUE-BASED anomalies (e.g. consumption spike, night burst) populate
    observed_value AND expected_value as a comparable pair in the same metric
    (the deviation between them is the anomaly).
  - STRUCTURAL anomalies (counter rollback, transmission gap) have no
    "expected value" in that sense, so expected_value = null. A rollback's
    defining quantity is the negative jump (observed_value); a gap's defining
    quantity is its length, carried in context.duration_hours (observed_value
    is null for gaps to avoid implying a comparable metric reading).
"""

from __future__ import annotations
import json
from pathlib import Path


# Fields the schema defines, in canonical order.
SCHEMA_FIELDS = [
    "time", "source_type", "asset_id", "metric", "observed_value",
    "expected_value", "anomaly_score", "method", "severity", "confidence",
    "anomaly_type", "context", "evidence", "metadata",
]

REQUIRED_FIELDS = ["time", "source_type", "asset_id", "metric",
                   "method", "anomaly_type"]

ROLLBACK_SEVERITY = "medium"   # discussion point with the correlation intern


def gap_severity(duration_hours: int) -> str:
    """
    Map a transmission-gap duration to a severity, anchored on the platform's
    own manufacturer thresholds:
      - 'Sleeping Device'     : data missing > 3 days (72h)
      - 'Communication Alarm' : data missing >= 4 days (96h)
    Short gaps are routine (meters batch-transmit ~twice daily), so anything
    under a day is low severity.  <-- thresholds open to discussion with intern
    """
    if duration_hours >= 96:      # >= 4 days  (Communication Alarm)
        return "high"
    if duration_hours >= 72:      # 3-4 days   (Sleeping Device)
        return "medium"
    if duration_hours >= 24:      # >= 1 day
        return "low"
    return "low"


def build_rollback_event(raw: dict,
                         source_type: str = "operational_view",
                         metadata: dict | None = None) -> dict:
    """
    Turn one rollback record (from CleanResult.rollback_events) into a
    schema-conformant event dict.
    """
    event = {
        "time":           raw["time"],
        "source_type":    source_type,
        "asset_id":       str(raw["asset_id"]),
        "metric":         "cumulative_flow_m3",
        "observed_value": round(float(raw["observed_value"]), 4),  # negative jump
        "expected_value": None,                            # unknown after reset
        "anomaly_score":  None,                            # rule -> no score
        "method":         raw.get("method", "rule"),
        "severity":       ROLLBACK_SEVERITY,
        "confidence":     1.0,                             # rule is certain
        "anomaly_type":   raw.get("anomaly_type", "counter_rollback"),
        "context":        {},                              # none for a rollback
        "evidence":       raw.get("evidence", ""),
        "metadata":       {"data_source": "EOA", **(metadata or {})},
    }
    # keep canonical field order
    return {k: event[k] for k in SCHEMA_FIELDS}


def emit_rollback_events(clean_results,
                         source_type: str = "operational_view") -> list[dict]:
    """
    Collect rollback events from one or more CleanResult objects and
    convert them all to schema-conformant event dicts.

    Accepts a single CleanResult or a list of them.
    """
    if not isinstance(clean_results, (list, tuple)):
        clean_results = [clean_results]
    events = []
    for res in clean_results:
        for raw in getattr(res, "rollback_events", []):
            events.append(build_rollback_event(raw, source_type=source_type))
    return events


def build_gap_event(raw: dict,
                    source_type: str = "operational_view",
                    metadata: dict | None = None) -> dict:
    """
    Turn one gap episode (from cleaning.find_gap_events) into a
    schema-conformant event dict. A gap is a COLLECTIVE anomaly (a run of
    missing hours), so duration is carried in metadata and severity is derived
    from it via the manufacturer thresholds.
    """
    dur = int(raw["duration_hours"])
    event = {
        "time":           raw["time"],                 # gap start
        "source_type":    source_type,
        "asset_id":       str(raw["asset_id"]),
        "metric":         "reporting_status",
        "observed_value": None,                        # structural: gap length lives in context.duration_hours
        "expected_value": None,                        # structural anomaly: no comparable expected value
        "anomaly_score":  None,                         # rule -> no score
        "method":         raw.get("method", "rule"),
        "severity":       gap_severity(dur),
        "confidence":     1.0,                          # rule is certain
        "anomaly_type":   raw.get("anomaly_type", "transmission_gap"),
        "context":        {"end_time": raw.get("end_time"),
                           "duration_hours": dur},
        "evidence":       raw.get("evidence", ""),
        "metadata":       {"data_source": "EOA", **(metadata or {})},
    }
    return {k: event[k] for k in SCHEMA_FIELDS}


def emit_gap_events(clean_results, source_type: str = "operational_view",
                    min_gap_hours: int = 24) -> list[dict]:
    """
    Detect and emit transmission-gap events from one or more CleanResults.

    Default min_gap_hours=24: ignore sub-day gaps, since meters batch-transmit
    ~twice daily and short silences are routine (not anomalies). Tune as needed.
    """
    try:
        from .cleaning import find_gap_events
    except ImportError:
        from cleaning import find_gap_events
    if not isinstance(clean_results, (list, tuple)):
        clean_results = [clean_results]
    events = []
    for res in clean_results:
        for raw in find_gap_events(res, min_gap_hours=min_gap_hours):
            events.append(build_gap_event(raw, source_type=source_type))
    return events


def validate_event(event: dict) -> list[str]:
    """Return a list of problems with an event (empty list = valid)."""
    problems = []
    for f in REQUIRED_FIELDS:
        if f not in event or event[f] in (None, ""):
            problems.append(f"missing/empty required field: {f}")
    if set(event.keys()) - set(SCHEMA_FIELDS):
        problems.append(f"unexpected fields: {set(event.keys()) - set(SCHEMA_FIELDS)}")
    if event.get("severity") not in {"low", "medium", "high", "critical"}:
        problems.append(f"bad severity: {event.get('severity')}")
    return problems


def write_jsonl(events: list[dict], path: str | Path) -> int:
    """Write events to a .jsonl file (one JSON object per line). Returns count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return len(events)


if __name__ == "__main__":
    # Demo with a couple of synthetic rollback records
    demo = [
        {"time": "2025-05-13T12:00:00", "asset_id": "202405101891",
         "anomaly_type": "counter_rollback", "method": "rule",
         "observed_value": -3.884,
         "evidence": "cumulative flow decreased between consecutive readings"},
    ]
    events = [build_rollback_event(r) for r in demo]
    for e in events:
        print(json.dumps(e, indent=2))
        print("valid:", not validate_event(e))