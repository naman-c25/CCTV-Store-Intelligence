"""Conversion funnel — Entry → Zone Visit → Billing Queue → Purchase.

The session is the unit (not raw events), so a visitor who re-enters is counted
once. Each stage count is monotonically a subset of the previous, and drop-off %
is computed stage-to-stage. Staff are excluded.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .config import Settings, get_settings
from .conversion import mark_conversions
from .sessions import customer_sessions, reconstruct_sessions
from .storage import Repository
from .windows import resolve_window


def _drop_off(prev: int, curr: int) -> float:
    if prev <= 0:
        return 0.0
    return round((prev - curr) / prev * 100.0, 2)


def compute_funnel(
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

    entered = len(customers)
    zone_visit = sum(1 for s in customers if s.visited_any_zone)
    billing_queue = sum(1 for s in customers if s.reached_billing)
    purchase = sum(1 for s in customers if s.converted)

    stages = [
        {"stage": "ENTRY", "count": entered, "drop_off_pct": 0.0},
        {"stage": "ZONE_VISIT", "count": zone_visit, "drop_off_pct": _drop_off(entered, zone_visit)},
        {"stage": "BILLING_QUEUE", "count": billing_queue, "drop_off_pct": _drop_off(zone_visit, billing_queue)},
        {"stage": "PURCHASE", "count": purchase, "drop_off_pct": _drop_off(billing_queue, purchase)},
    ]

    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "stages": stages,
        "overall_conversion_pct": round(purchase / entered * 100.0, 2) if entered else 0.0,
        "unit": "session",
    }
