"""POS ↔ session correlation — the North Star (Offline Conversion Rate).

Rule (from the spec): a visitor who was in the BILLING zone within the
5-minute window *before* a POS transaction timestamp, in the SAME store, counts
as a converted visitor for that session. There is no customer_id, so this is a
time-window + store correlation, not an identity join.

Attribution policy (documented in CHOICES.md): we use *presence-based*
attribution — every session present in billing during [T-5min, T] for a
transaction at T is flagged converted. We deliberately do NOT force a strict
1:1 transaction→visitor match, because (a) the spec phrases it as a per-session
flag and (b) one transaction can serve a group (family shops, one pays). This
can slightly over-count when a queue is dense; we note that as a known bias.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from .sessions import Session
from .storage import PosRow
from .timewindows import ensure_utc


def mark_conversions(
    sessions: Iterable[Session], pos_rows: Iterable[PosRow], window_minutes: int = 5
) -> None:
    """Mutate sessions in place, setting .converted where billing presence
    falls inside any transaction's pre-window."""
    window = timedelta(minutes=window_minutes)
    # Index POS by store for cheap lookup.
    by_store: dict[str, list] = {}
    for t in pos_rows:
        by_store.setdefault(t.store_id, []).append(ensure_utc(t.timestamp))
    for lst in by_store.values():
        lst.sort()

    for s in sessions:
        if not s.billing_times:
            continue
        txns = by_store.get(s.store_id)
        if not txns:
            continue
        for bt in s.billing_times:
            bt = ensure_utc(bt)
            # Converted if some txn T satisfies bt in [T-window, T] → T in [bt, bt+window].
            if any(bt <= T <= bt + window for T in txns):
                s.converted = True
                break
