"""Event state machine — track observations -> the event catalogue.

Given a stable visitor_id (from identity.py) and the zone a visitor is in over
time (from zones.py), this emits schema-correct events:
  ENTRY / REENTRY, ZONE_ENTER, ZONE_DWELL (every 30s of continued dwell),
  ZONE_EXIT, BILLING_QUEUE_JOIN (when joining a non-empty billing queue),
  BILLING_QUEUE_ABANDON (leaving billing having queued), EXIT.

`session_seq` increments per visitor across the whole session (re-entries keep
counting). `confidence` is passed straight through and never suppressed. Pure
Python — no CV dependency — so it unit-tests directly.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .zones import Zone


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


@dataclass
class _VState:
    zone_id: Optional[str] = None
    zone_enter_ts: Optional[datetime] = None
    last_dwell_emit_s: int = 0
    joined_queue: bool = False
    seq: int = 0


@dataclass
class EventBuilder:
    store_id: str
    dwell_emit_s: int = 30
    events: list[dict] = field(default_factory=list)
    _state: dict[str, _VState] = field(default_factory=dict)

    def _vs(self, vid: str) -> _VState:
        return self._state.setdefault(vid, _VState())

    def _emit(self, vid, camera, etype, ts, conf, *, zone=None, dwell_ms=0,
              is_staff=False, queue_depth=None, sku_zone=None) -> dict:
        vs = self._vs(vid)
        vs.seq += 1
        ev = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": camera,
            "visitor_id": vid,
            "event_type": etype,
            "timestamp": _iso(ts),
            "zone_id": zone,
            "dwell_ms": int(dwell_ms),
            "is_staff": bool(is_staff),
            "confidence": round(float(conf), 3),
            "metadata": {"queue_depth": queue_depth, "sku_zone": sku_zone, "session_seq": vs.seq},
        }
        self.events.append(ev)
        return ev

    # -- threshold crossings -----------------------------------------------
    def entry(self, vid, ts, camera, conf, is_staff=False, is_reentry=False) -> None:
        self._emit(vid, camera, "REENTRY" if is_reentry else "ENTRY", ts, conf, is_staff=is_staff)

    def exit(self, vid, ts, camera, conf, is_staff=False) -> None:
        vs = self._vs(vid)
        if vs.zone_id is not None:
            # Close any open zone first.
            if vs.joined_queue:
                self._emit(vid, camera, "BILLING_QUEUE_ABANDON", ts, conf, zone=vs.zone_id, is_staff=is_staff)
            self._emit(vid, camera, "ZONE_EXIT", ts, conf, zone=vs.zone_id, is_staff=is_staff)
        self._emit(vid, camera, "EXIT", ts, conf, is_staff=is_staff)
        vs.zone_id = None
        vs.zone_enter_ts = None
        vs.last_dwell_emit_s = 0
        vs.joined_queue = False

    # -- per-observation zone update ---------------------------------------
    def update_zone(self, vid, ts, camera, zone: Optional[Zone], conf,
                    is_staff=False, queue_depth: int = 0) -> None:
        vs = self._vs(vid)
        new_zone_id = zone.zone_id if zone else None

        if new_zone_id != vs.zone_id:
            # Leaving the previous zone.
            if vs.zone_id is not None:
                if vs.joined_queue:
                    self._emit(vid, camera, "BILLING_QUEUE_ABANDON", ts, conf, zone=vs.zone_id, is_staff=is_staff)
                    vs.joined_queue = False
                self._emit(vid, camera, "ZONE_EXIT", ts, conf, zone=vs.zone_id, is_staff=is_staff)
            # Entering a new zone.
            if zone is not None:
                self._emit(vid, camera, "ZONE_ENTER", ts, conf, zone=new_zone_id,
                           is_staff=is_staff, sku_zone=zone.sku_zone)
                if zone.is_billing and queue_depth > 0:
                    self._emit(vid, camera, "BILLING_QUEUE_JOIN", ts, conf, zone=new_zone_id,
                               is_staff=is_staff, queue_depth=queue_depth)
                    vs.joined_queue = True
            vs.zone_id = new_zone_id
            vs.zone_enter_ts = ts if zone is not None else None
            vs.last_dwell_emit_s = 0
            return

        # Same zone continuing -> emit ZONE_DWELL every dwell_emit_s seconds.
        if zone is not None and vs.zone_enter_ts is not None:
            elapsed = int((ts - vs.zone_enter_ts).total_seconds())
            while elapsed >= vs.last_dwell_emit_s + self.dwell_emit_s:
                vs.last_dwell_emit_s += self.dwell_emit_s
                self._emit(vid, camera, "ZONE_DWELL", ts, conf, zone=new_zone_id,
                           dwell_ms=vs.last_dwell_emit_s * 1000, is_staff=is_staff,
                           sku_zone=zone.sku_zone)
