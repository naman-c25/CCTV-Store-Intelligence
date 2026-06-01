# PROMPT: "Write pytest tests for a CCTV detection pipeline's pure logic without
# needing video or YOLO: (1) point-in-polygon + entry-line crossing direction,
# (2) an identity resolver that reuses a visitor_id on re-entry but assigns a new
# id to a different person, and prevents cross-camera double counting, (3) an
# event state machine that emits ENTRY, ZONE_ENTER, ZONE_DWELL every 30s, and
# BILLING_QUEUE_JOIN/ABANDON."
# CHANGES MADE: The model's re-entry test reused the id for ANY return within the
# window; I added the documented failure-boundary case — a return below the
# minimum gap with a dissimilar feature must NOT be stitched (new id) — because
# that is the exact edge the follow-up questions probe. I also tightened the
# dwell test to assert the 30s cadence (two emissions at 65s), not just ">0".
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline.events_builder import EventBuilder
from pipeline.identity import IdentityResolver, cosine_sim
from pipeline.zones import StoreLayout, CameraCfg, Zone, point_in_polygon

T0 = datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc)


# --- zones / geometry ------------------------------------------------------
def test_point_in_polygon():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon((5, 5), square)
    assert not point_in_polygon((20, 5), square)


def test_entry_line_crossing_direction():
    layout = StoreLayout(
        store_id="S",
        cameras={"CAM_ENTRY_01": CameraCfg("CAM_ENTRY_01", role="entry",
                                           entry_line=((0, 5), (10, 5)), inside_sign=1.0)},
    )
    # Moving from y=2 (below) to y=8 (above) crosses the line.
    assert layout.crossing_direction("CAM_ENTRY_01", (5, 2), (5, 8)) in ("ENTRY", "EXIT")
    inbound = layout.crossing_direction("CAM_ENTRY_01", (5, 2), (5, 8))
    outbound = layout.crossing_direction("CAM_ENTRY_01", (5, 8), (5, 2))
    assert inbound != outbound  # opposite directions classified differently
    # No crossing when staying on one side.
    assert layout.crossing_direction("CAM_ENTRY_01", (5, 6), (5, 8)) is None


def test_zone_for_billing_fallback():
    layout = StoreLayout(store_id="S",
                         cameras={"CAM_BILLING_01": CameraCfg("CAM_BILLING_01", role="billing")})
    z = layout.zone_for("CAM_BILLING_01", (3, 3))
    assert z is not None and z.is_billing


# --- identity resolution ---------------------------------------------------
def test_reentry_reuses_visitor_id():
    r = IdentityResolver(reentry_window_s=300, reentry_sim_threshold=0.5, reentry_min_gap_s=2)
    feat = [1.0, 0.0, 0.0]
    vid, is_re = r.resolve_entry(0.0, feat)
    assert not is_re
    r.note_exit(vid, 60.0)
    vid2, is_re2 = r.resolve_entry(120.0, feat)  # same look, 60s after exit
    assert vid2 == vid and is_re2 is True
    assert r.unique_entries == 1  # re-entry did not create a new visitor


def test_different_person_same_door_gets_new_id():
    # The documented failure boundary: a dissimilar person returning quickly
    # must NOT be stitched to the exited visitor.
    r = IdentityResolver(reentry_window_s=300, reentry_sim_threshold=0.6, reentry_min_gap_s=2)
    vid, _ = r.resolve_entry(0.0, [1.0, 0.0, 0.0])
    r.note_exit(vid, 10.0)
    vid2, is_re = r.resolve_entry(13.0, [0.0, 1.0, 0.0])  # different feature
    assert vid2 != vid and is_re is False
    assert r.unique_entries == 2


def test_crosscam_associates_inside_visitor():
    r = IdentityResolver(crosscam_window_s=10, crosscam_sim_threshold=0.5)
    feat = [0.2, 0.9, 0.1]
    vid, _ = r.resolve_entry(0.0, feat)
    matched = r.resolve_crosscam(3.0, feat)  # floor sees same person 3s later
    assert matched == vid  # not double-counted


def test_cosine_sim_handles_missing_feature():
    assert cosine_sim(None, [1, 2]) == 0.0
    assert abs(cosine_sim([1, 0], [1, 0]) - 1.0) < 1e-9


# --- event state machine ---------------------------------------------------
def test_entry_zone_dwell_cadence_and_exit():
    b = EventBuilder(store_id="STORE_BLR_002")
    b.entry("V1", T0, "CAM_ENTRY_01", 0.9)
    zone = Zone("SKINCARE", "CAM_FLOOR_01", sku_zone="MOISTURISER")
    b.update_zone("V1", T0 + timedelta(seconds=5), "CAM_FLOOR_01", zone, 0.8)   # ZONE_ENTER
    # Same zone at +65s should produce TWO ZONE_DWELL pulses (30s, 60s).
    b.update_zone("V1", T0 + timedelta(seconds=70), "CAM_FLOOR_01", zone, 0.8)
    b.exit("V1", T0 + timedelta(seconds=120), "CAM_ENTRY_01", 0.85)

    types = [e["event_type"] for e in b.events]
    assert types[0] == "ENTRY"
    assert types.count("ZONE_ENTER") == 1
    assert types.count("ZONE_DWELL") == 2          # 30s cadence
    assert types.count("ZONE_EXIT") == 1           # closed on exit
    assert types[-1] == "EXIT"
    # session_seq is strictly increasing.
    seqs = [e["metadata"]["session_seq"] for e in b.events]
    assert seqs == sorted(seqs) and seqs[0] == 1


def test_billing_queue_join_and_abandon():
    b = EventBuilder(store_id="STORE_BLR_002")
    b.entry("V2", T0, "CAM_ENTRY_01", 0.9)
    billing = Zone("BILLING", "CAM_BILLING_01", is_billing=True)
    b.update_zone("V2", T0 + timedelta(seconds=10), "CAM_BILLING_01", billing, 0.8, queue_depth=4)
    # Leave billing for another zone -> abandon (no purchase recorded yet).
    other = Zone("FRAGRANCE", "CAM_FLOOR_01")
    b.update_zone("V2", T0 + timedelta(seconds=40), "CAM_FLOOR_01", other, 0.8)
    types = [e["event_type"] for e in b.events]
    assert "BILLING_QUEUE_JOIN" in types
    assert "BILLING_QUEUE_ABANDON" in types
    join = next(e for e in b.events if e["event_type"] == "BILLING_QUEUE_JOIN")
    assert join["metadata"]["queue_depth"] == 4


def test_calibrate_scorecard():
    from pipeline.calibrate import scorecard
    events = [
        {"event_id": "a", "store_id": "S", "camera_id": "C", "visitor_id": "V",
         "event_type": "ENTRY", "timestamp": "2026-03-03T14:00:00Z", "confidence": 0.9},
        {"event_id": "b", "store_id": "S", "camera_id": "C", "visitor_id": "V",
         "event_type": "EXIT", "timestamp": "2026-03-03T14:05:00Z", "confidence": 0.9},
    ]
    card = scorecard(events, ground_truth=events)
    assert card["entries"] == 1 and card["exits"] == 1
    assert card["duplicate_event_ids"] == 0
    assert card["ground_truth"]["entry_accuracy_pct"] == 100.0
