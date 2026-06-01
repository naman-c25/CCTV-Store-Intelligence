"""Time-window helpers.

'Today' is defined in the store's business timezone (IST for Apex Retail), then
converted to UTC for querying, because the event log stores UTC. Keeping this in
one place avoids off-by-a-day conversion bugs across endpoints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _tz(offset_hours: float) -> timezone:
    return timezone(timedelta(hours=offset_hours))


def today_window(offset_hours: float, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return [start, end) of the current business day as UTC datetimes."""
    tz = _tz(offset_hours)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Normalise any datetime to timezone-aware UTC (naive assumed UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def recent_window(minutes: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    end = now or datetime.now(timezone.utc)
    return end - timedelta(minutes=minutes), end
