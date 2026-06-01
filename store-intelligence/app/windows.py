"""Resolve the analytics time window for an endpoint.

'Today' is intentionally *data-relative*: it anchors to the most recent event
the store has produced, not the server's wall clock. In a live feed the latest
event is "now", so this behaves exactly like today; but it also makes the API
correct when replaying or grading historical clips (whose timestamps are fixed
in the past) — the same events always land in the same window.

Callers may override with an explicit ISO `frm`/`to` range or a business-day
`date` (YYYY-MM-DD). `now` (used by tests) takes precedence as the anchor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .config import Settings
from .timewindows import ensure_utc, today_window


def resolve_window(
    repo,
    store_id: str,
    settings: Settings,
    *,
    now: Optional[datetime] = None,
    date: Optional[str] = None,
    frm: Optional[str] = None,
    to: Optional[str] = None,
) -> tuple[datetime, datetime]:
    # 1. Explicit range wins.
    if frm and to:
        return ensure_utc(datetime.fromisoformat(frm.replace("Z", "+00:00"))), ensure_utc(
            datetime.fromisoformat(to.replace("Z", "+00:00"))
        )

    # 2. Explicit business day.
    if date:
        anchor = datetime.fromisoformat(f"{date}T12:00:00+00:00")
        return today_window(settings.BUSINESS_TZ_OFFSET_HOURS, now=anchor)

    # 3. Anchor: test-injected now → else latest event in the data → else wall clock.
    if now is not None:
        anchor = now
    else:
        anchor = repo.latest_event_time(store_id) or datetime.now(timezone.utc)
    return today_window(settings.BUSINESS_TZ_OFFSET_HOURS, now=ensure_utc(anchor))
