"""Real-time store metrics — projections over the event log for 'today'.

Everything here excludes staff (is_staff=true) from customer-facing numbers and
returns well-formed zeros for zero-traffic stores (never null, never a crash).
Rates carry a Wilson confidence interval + a data_confidence band so consumers
see uncertainty, not just a point estimate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .config import Settings, get_settings
from .conversion import mark_conversions
from .sessions import customer_sessions, reconstruct_sessions
from .stats import confidence_band, wilson_interval
from .storage import Repository
from .windows import resolve_window


def compute_metrics(
    repo: Repository,
    store_id: str,
    settings: Optional[Settings] = None,
    now: Optional[datetime] = None,
    billing_zones: Optional[set[str]] = None,
    date: Optional[str] = None,
    frm: Optional[str] = None,
    to: Optional[str] = None,
) -> dict:
    settings = settings or get_settings()
    start, end = resolve_window(repo, store_id, settings, now=now, date=date, frm=frm, to=to)

    events = repo.query_events(store_id=store_id, start=start, end=end)
    pos = repo.query_pos(store_id=store_id, start=start, end=end)

    sessions = reconstruct_sessions(events, billing_zones=billing_zones)
    customers = customer_sessions(sessions)
    mark_conversions(customers, pos, window_minutes=settings.CONVERSION_WINDOW_MIN)

    total = len(customers)
    converted = sum(1 for s in customers if s.converted)
    reached_billing = [s for s in customers if s.reached_billing]
    abandoned = sum(1 for s in reached_billing if s.abandoned)

    point, lower, upper = wilson_interval(converted, total)

    # Average dwell per zone (seconds), staff excluded.
    zone_dwell_totals: dict[str, list[int]] = {}
    for s in customers:
        for zone, ms in s.dwell_ms_by_zone.items():
            zone_dwell_totals.setdefault(zone, []).append(ms)
    avg_dwell_per_zone = {
        zone: round(sum(vals) / len(vals) / 1000.0, 2) for zone, vals in zone_dwell_totals.items()
    }

    # Current queue depth: most recent reported queue_depth in the window.
    queue_depth = _current_queue_depth(events)

    abandonment_rate = round(abandoned / len(reached_billing), 4) if reached_billing else 0.0

    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "unique_visitors": total,
        "converted_visitors": converted,
        "conversion_rate": {
            "value": point,
            "ci_low": lower,
            "ci_high": upper,
            "data_confidence": confidence_band(total, settings.MIN_SESSIONS_FOR_CONFIDENCE),
        },
        "avg_dwell_seconds_per_zone": avg_dwell_per_zone,
        "queue_depth": queue_depth,
        "abandonment_rate": abandonment_rate,
        "total_transactions": len(pos),
        "revenue_inr": round(sum(p.basket_value_inr for p in pos), 2),
        "generated_at": (now or datetime.now()).astimezone().isoformat()
        if now
        else datetime.utcnow().isoformat() + "Z",
    }


def _current_queue_depth(events) -> int:
    """The latest non-null queue_depth reported by a billing event."""
    latest_ts = None
    latest_depth = 0
    for ev in events:
        depth = None
        md = ev.metadata
        if hasattr(md, "queue_depth"):
            depth = md.queue_depth
        elif isinstance(md, dict):
            depth = md.get("queue_depth")
        if depth is None:
            continue
        if latest_ts is None or ev.timestamp >= latest_ts:
            latest_ts = ev.timestamp
            latest_depth = int(depth)
    return latest_depth
