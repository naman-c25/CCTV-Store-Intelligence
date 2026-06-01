"""Health projection — the first thing on-call checks.

Reports DB reachability and, per store, the last event timestamp plus a
STALE_FEED flag when the most recent event is older than the configured lag
(default 10 min). 'now' is injectable so tests are deterministic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .config import Settings, get_settings
from .storage import Repository
from .timewindows import ensure_utc


def compute_health(repo: Repository, settings: Optional[Settings] = None, now: Optional[datetime] = None) -> dict:
    settings = settings or get_settings()
    now = ensure_utc(now or datetime.now(timezone.utc))

    db_ok = repo.ping()
    feeds = []
    overall = "ok" if db_ok else "degraded"

    if db_ok:
        last_by_store = repo.last_event_time_per_store()
        for store_id, last_ts in sorted(last_by_store.items()):
            last_ts = ensure_utc(last_ts)
            lag_seconds = (now - last_ts).total_seconds()
            stale = lag_seconds > settings.STALE_FEED_MINUTES * 60
            feeds.append(
                {
                    "store_id": store_id,
                    "last_event_at": last_ts.isoformat(),
                    "lag_seconds": round(lag_seconds, 1),
                    "status": "STALE_FEED" if stale else "live",
                }
            )
        if any(f["status"] == "STALE_FEED" for f in feeds):
            overall = "degraded"

    return {
        "status": overall,
        "database": "up" if db_ok else "down",
        "now": now.isoformat(),
        "stale_feed_threshold_minutes": settings.STALE_FEED_MINUTES,
        "feeds": feeds,
    }
