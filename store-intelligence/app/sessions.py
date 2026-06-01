"""Session reconstruction — turning the raw event log into visitor journeys.

A *session* is the unit of analytics (funnel, conversion, dwell). One physical
visitor = one session, keyed by visitor_id. Re-entry is handled here: because
the detection layer reuses the same visitor_id and emits REENTRY (instead of a
second ENTRY), collapsing events by visitor_id automatically prevents the
"re-entry inflation" that double-counts a returning customer.

Staff sessions are reconstructed too (ops may want them) but tagged is_staff so
the API can exclude them from customer-facing metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from .models import Event, EventType


def _is_billing(event: Event, billing_zones: set[str]) -> bool:
    et = event.event_type.value if hasattr(event.event_type, "value") else event.event_type
    if et in (EventType.BILLING_QUEUE_JOIN.value, EventType.BILLING_QUEUE_ABANDON.value):
        return True
    if event.zone_id and event.zone_id in billing_zones:
        return True
    # Heuristic fallback when layout isn't supplied: zone name mentions billing.
    if event.zone_id and "BILL" in event.zone_id.upper():
        return True
    return False


@dataclass
class Session:
    visitor_id: str
    store_id: str
    is_staff: bool = False
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    reentry_count: int = 0
    zones_visited: set[str] = field(default_factory=set)
    dwell_ms_by_zone: dict[str, int] = field(default_factory=dict)
    billing_times: list[datetime] = field(default_factory=list)
    reached_billing: bool = False
    abandoned: bool = False
    converted: bool = False  # set later by POS correlation
    event_count: int = 0
    min_confidence: float = 1.0

    @property
    def visited_any_zone(self) -> bool:
        return len(self.zones_visited) > 0

    def total_dwell_ms(self) -> int:
        return sum(self.dwell_ms_by_zone.values())


def reconstruct_sessions(
    events: Iterable[Event], billing_zones: Optional[set[str]] = None
) -> dict[str, Session]:
    billing_zones = billing_zones or set()
    sessions: dict[str, Session] = {}

    for ev in sorted(events, key=lambda e: e.timestamp):
        s = sessions.get(ev.visitor_id)
        if s is None:
            s = Session(visitor_id=ev.visitor_id, store_id=ev.store_id, is_staff=ev.is_staff)
            sessions[ev.visitor_id] = s

        # Staff status: if any event flags staff, treat the whole session as staff.
        s.is_staff = s.is_staff or ev.is_staff
        s.event_count += 1
        s.min_confidence = min(s.min_confidence, ev.confidence)

        et = ev.event_type.value if hasattr(ev.event_type, "value") else ev.event_type

        if et == EventType.ENTRY.value:
            if s.entry_time is None:
                s.entry_time = ev.timestamp
        elif et == EventType.REENTRY.value:
            s.reentry_count += 1
        elif et == EventType.EXIT.value:
            s.exit_time = ev.timestamp
        elif et in (EventType.ZONE_ENTER.value, EventType.ZONE_DWELL.value) and ev.zone_id:
            s.zones_visited.add(ev.zone_id)
            if ev.dwell_ms:
                # ZONE_DWELL is re-emitted every 30s; keep the max as the
                # best estimate of continuous dwell rather than summing pulses.
                prev = s.dwell_ms_by_zone.get(ev.zone_id, 0)
                s.dwell_ms_by_zone[ev.zone_id] = max(prev, ev.dwell_ms)

        if _is_billing(ev, billing_zones):
            s.reached_billing = True
            s.billing_times.append(ev.timestamp)
            if et == EventType.BILLING_QUEUE_ABANDON.value:
                s.abandoned = True

    return sessions


def customer_sessions(sessions: dict[str, Session]) -> list[Session]:
    """Sessions that count toward customer metrics (staff excluded)."""
    return [s for s in sessions.values() if not s.is_staff]
