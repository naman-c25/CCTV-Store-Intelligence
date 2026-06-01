"""Operational anomaly detection.

Three detectors, each emitting a severity (INFO/WARN/CRITICAL) and a concrete
suggested_action an operator can act on:
  • BILLING_QUEUE_SPIKE — current queue depth above threshold.
  • CONVERSION_DROP     — today's conversion materially below a rolling baseline.
  • DEAD_ZONE           — a normally-active zone with no visits for 30+ minutes
                          while the store is otherwise active.

The "7-day average" baseline degrades gracefully: with little history we widen
the band and lower severity rather than firing false alarms or crashing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import Settings, get_settings
from .conversion import mark_conversions
from .metrics import _current_queue_depth
from .sessions import customer_sessions, reconstruct_sessions
from .storage import Repository
from .timewindows import ensure_utc
from .windows import resolve_window


def _conversion_for_window(repo, store_id, start, end, window_min, billing_zones) -> Optional[float]:
    events = repo.query_events(store_id=store_id, start=start, end=end)
    if not events:
        return None
    pos = repo.query_pos(store_id=store_id, start=start, end=end)
    sessions = customer_sessions(reconstruct_sessions(events, billing_zones=billing_zones))
    if not sessions:
        return None
    mark_conversions(sessions, pos, window_minutes=window_min)
    converted = sum(1 for s in sessions if s.converted)
    return converted / len(sessions)


def compute_anomalies(
    repo: Repository,
    store_id: str,
    settings: Optional[Settings] = None,
    now: Optional[datetime] = None,
    billing_zones: Optional[set[str]] = None,
) -> dict:
    settings = settings or get_settings()
    # Anchor 'now' to the latest event (data-relative) when not injected.
    if now is None:
        now = repo.latest_event_time(store_id) or datetime.now(timezone.utc)
    now = ensure_utc(now)
    start, end = resolve_window(repo, store_id, settings, now=now)
    today_events = repo.query_events(store_id=store_id, start=start, end=end)

    anomalies = []

    # --- BILLING_QUEUE_SPIKE ------------------------------------------------
    depth = _current_queue_depth(today_events)
    if depth >= settings.QUEUE_SPIKE_DEPTH:
        severity = "CRITICAL" if depth >= settings.QUEUE_SPIKE_DEPTH * 2 else "WARN"
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": severity,
                "detail": f"Billing queue depth is {depth} (threshold {settings.QUEUE_SPIKE_DEPTH}).",
                "metric": {"queue_depth": depth},
                "suggested_action": "Open an additional billing counter or redirect staff to checkout.",
            }
        )

    # --- CONVERSION_DROP vs rolling baseline --------------------------------
    today_conv = _conversion_for_window(
        repo, store_id, start, end, settings.CONVERSION_WINDOW_MIN, billing_zones
    )
    baseline_vals = []
    for d in range(1, 8):
        b_start = start - timedelta(days=d)
        b_end = end - timedelta(days=d)
        c = _conversion_for_window(repo, store_id, b_start, b_end, settings.CONVERSION_WINDOW_MIN, billing_zones)
        if c is not None:
            baseline_vals.append(c)
    if today_conv is not None and baseline_vals:
        baseline = sum(baseline_vals) / len(baseline_vals)
        if baseline > 0 and today_conv < baseline * (1 - settings.CONVERSION_DROP_PCT):
            drop_pct = round((baseline - today_conv) / baseline * 100, 1)
            # Fewer baseline days → lower confidence → cap severity at WARN.
            severity = "CRITICAL" if (drop_pct >= 50 and len(baseline_vals) >= 3) else "WARN"
            anomalies.append(
                {
                    "type": "CONVERSION_DROP",
                    "severity": severity,
                    "detail": f"Conversion {round(today_conv*100,1)}% is {drop_pct}% below the "
                    f"{len(baseline_vals)}-day baseline of {round(baseline*100,1)}%.",
                    "metric": {"today": round(today_conv, 4), "baseline": round(baseline, 4)},
                    "suggested_action": "Check staffing, promotions, and stock on the main floor; "
                    "review funnel for the largest drop-off stage.",
                }
            )

    # --- DEAD_ZONE ----------------------------------------------------------
    last_visit_by_zone: dict[str, datetime] = {}
    last_any_activity = None
    for ev in today_events:
        ts = ensure_utc(ev.timestamp)
        last_any_activity = ts if last_any_activity is None else max(last_any_activity, ts)
        if ev.zone_id:
            prev = last_visit_by_zone.get(ev.zone_id)
            last_visit_by_zone[ev.zone_id] = ts if prev is None else max(prev, ts)

    store_active = last_any_activity is not None and (now - last_any_activity).total_seconds() <= settings.DEAD_ZONE_MINUTES * 60
    if store_active:
        for zone, last_ts in sorted(last_visit_by_zone.items()):
            idle = (now - last_ts).total_seconds()
            if idle > settings.DEAD_ZONE_MINUTES * 60:
                anomalies.append(
                    {
                        "type": "DEAD_ZONE",
                        "severity": "INFO",
                        "detail": f"Zone {zone} has had no visits for {round(idle/60)} minutes "
                        f"while the store is active.",
                        "metric": {"zone_id": zone, "idle_minutes": round(idle / 60, 1)},
                        "suggested_action": f"Check {zone} for blocked access, poor merchandising, or a camera issue.",
                    }
                )

    return {
        "store_id": store_id,
        "generated_at": now.isoformat(),
        "active_anomalies": anomalies,
        "count": len(anomalies),
    }
