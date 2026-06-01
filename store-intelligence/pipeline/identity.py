"""Identity resolution — the layer that assigns a stable visitor_id.

This single module addresses the three "vendor problems" the brief calls out:

  • Re-entry inflation: a visitor who exits and returns gets the SAME visitor_id
    and a REENTRY event, not a second ENTRY. Matched within a time window by a
    weak appearance feature + the fact that they re-cross the entry line inbound.
  • Cross-camera double counting: the entry and floor cameras overlap, so a
    person seen on the floor shortly after entering is associated to the existing
    visitor rather than counted again.
  • Group handling: nothing here merges simultaneous distinct tracks — each
    person crossing the threshold gets their own id, so a group of 3 produces 3
    ENTRY events.

Design choice (see CHOICES.md): because faces are blurred, the appearance
"feature" is intentionally weak (a coarse colour/aspect descriptor). We lean on
TIME + DIRECTION + GATING rather than trusting an embedding. The honest failure
mode — two similar-looking people through the same door within a few seconds —
is gated by a minimum-gap rule and surfaced as a low-confidence stitch.

No numpy dependency: feature vectors are plain lists and similarity is cosine.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Optional

Vec = list[float]


def cosine_sim(a: Optional[Vec], b: Optional[Vec]) -> float:
    """Cosine similarity in [-1,1]; if either feature missing, return 0 (unknown)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class VisitorRecord:
    visitor_id: str
    entered_at: float
    last_seen: float
    feature: Optional[Vec] = None
    status: str = "inside"          # inside | exited
    exited_at: Optional[float] = None
    reentries: int = 0


@dataclass
class IdentityResolver:
    reentry_window_s: float = 180.0       # how long after EXIT a return counts as re-entry
    reentry_sim_threshold: float = 0.6    # appearance similarity to call it the same person
    reentry_min_gap_s: float = 2.0        # below this, a "return" is more likely a new person
    crosscam_window_s: float = 8.0        # entry∩floor overlap horizon
    crosscam_sim_threshold: float = 0.5
    visitors: dict[str, VisitorRecord] = field(default_factory=dict)

    def _new_id(self) -> str:
        return "VIS_" + uuid.uuid4().hex[:6]

    # -- entry-line crossing (inbound) -------------------------------------
    def resolve_entry(self, ts: float, feature: Optional[Vec] = None) -> tuple[str, bool]:
        """Return (visitor_id, is_reentry) for an inbound threshold crossing."""
        best_id, best_sim = None, 0.0
        for vid, rec in self.visitors.items():
            if rec.status != "exited" or rec.exited_at is None:
                continue
            gap = ts - rec.exited_at
            if gap < self.reentry_min_gap_s or gap > self.reentry_window_s:
                continue
            sim = cosine_sim(feature, rec.feature)
            if sim > best_sim:
                best_id, best_sim = vid, sim
        if best_id is not None and best_sim >= self.reentry_sim_threshold:
            rec = self.visitors[best_id]
            rec.status = "inside"
            rec.last_seen = ts
            rec.reentries += 1
            if feature:
                rec.feature = feature
            return best_id, True

        vid = self._new_id()
        self.visitors[vid] = VisitorRecord(vid, entered_at=ts, last_seen=ts, feature=feature)
        return vid, False

    def note_exit(self, visitor_id: str, ts: float) -> None:
        rec = self.visitors.get(visitor_id)
        if rec:
            rec.status = "exited"
            rec.exited_at = ts
            rec.last_seen = ts

    # -- cross-camera association (floor sees someone already inside) ------
    def resolve_crosscam(self, ts: float, feature: Optional[Vec] = None) -> Optional[str]:
        """Match an on-floor observation to an inside visitor, else None.

        Returning None means 'not confidently the same as anyone inside' — the
        orchestrator then treats it as a track local to that camera (and will
        only mint a new visitor on an actual entry-line crossing, never on the
        floor camera, so the overlap region cannot inflate the count).
        """
        best_id, best_sim = None, 0.0
        for vid, rec in self.visitors.items():
            if rec.status != "inside":
                continue
            if ts - rec.last_seen > self.crosscam_window_s:
                continue
            sim = cosine_sim(feature, rec.feature)
            if sim > best_sim:
                best_id, best_sim = vid, sim
        if best_id is not None and best_sim >= self.crosscam_sim_threshold:
            self.visitors[best_id].last_seen = ts
            return best_id
        return None

    def touch(self, visitor_id: str, ts: float) -> None:
        rec = self.visitors.get(visitor_id)
        if rec:
            rec.last_seen = ts

    @property
    def unique_entries(self) -> int:
        """Distinct physical visitors (re-entries do not increment this)."""
        return len(self.visitors)
