"""Zone heatmap — visit frequency + avg dwell, normalised 0–100 for rendering.

Two normalised channels per zone: visit_score (frequency) and dwell_score
(engagement). A data_confidence flag is attached when the window has fewer than
the configured number of sessions, so the UI can grey-out low-trust cells.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .config import Settings, get_settings
from .sessions import customer_sessions, reconstruct_sessions
from .stats import confidence_band, normalise_0_100
from .storage import Repository
from .windows import resolve_window


def compute_heatmap(
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
    sessions = reconstruct_sessions(events, billing_zones=billing_zones)
    customers = customer_sessions(sessions)

    visit_counts: dict[str, int] = {}
    dwell_avgs: dict[str, list[int]] = {}
    for s in customers:
        for zone in s.zones_visited:
            visit_counts[zone] = visit_counts.get(zone, 0) + 1
        for zone, ms in s.dwell_ms_by_zone.items():
            dwell_avgs.setdefault(zone, []).append(ms)

    max_visits = max(visit_counts.values(), default=0)
    avg_dwell = {z: sum(v) / len(v) for z, v in dwell_avgs.items()}
    max_dwell = max(avg_dwell.values(), default=0.0)

    zones = []
    for zone in sorted(set(visit_counts) | set(avg_dwell)):
        zones.append(
            {
                "zone_id": zone,
                "visits": visit_counts.get(zone, 0),
                "avg_dwell_seconds": round(avg_dwell.get(zone, 0.0) / 1000.0, 2),
                "visit_score": normalise_0_100(visit_counts.get(zone, 0), max_visits),
                "dwell_score": normalise_0_100(avg_dwell.get(zone, 0.0), max_dwell),
            }
        )

    n_sessions = len(customers)
    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "session_count": n_sessions,
        "data_confidence": confidence_band(n_sessions, settings.MIN_SESSIONS_FOR_CONFIDENCE),
        "low_confidence": n_sessions < settings.MIN_SESSIONS_FOR_CONFIDENCE,
        "zones": zones,
    }
