# PROMPT: "Write pytest tests for a /heatmap endpoint returning per-zone visit
# frequency and avg dwell normalised 0-100, with a data_confidence flag when the
# window has fewer than 20 sessions."
# CHANGES MADE: The model normalised against a hard-coded max; I changed the
# assertion to verify the busiest zone scores exactly 100 (we normalise against
# the observed max), and added the low-confidence flag assertion for a thin window.
from __future__ import annotations

from datetime import datetime, timezone

from .conftest import visitor_journey

STORE = "STORE_BLR_002"


def _ingest(client, events):
    return client.post("/events/ingest", json={"events": events}).json()


def test_heatmap_normalises_and_flags_low_confidence(client):
    now = datetime.now(timezone.utc)
    events = []
    # SKINCARE visited by 3 visitors, HAIRCARE by 1 → SKINCARE should score 100.
    for i in range(3):
        events += visitor_journey(f"VIS_S{i}", base_time=now, converted_zone="SKINCARE")
    events += visitor_journey("VIS_H0", base_time=now, converted_zone="HAIRCARE")
    _ingest(client, events)

    body = client.get(f"/stores/{STORE}/heatmap").json()
    zones = {z["zone_id"]: z for z in body["zones"]}
    assert zones["SKINCARE"]["visit_score"] == 100.0
    assert zones["SKINCARE"]["visits"] == 3
    assert body["low_confidence"] is True  # only 4 sessions < 20
    assert body["data_confidence"] in ("LOW", "MEDIUM")


def test_empty_heatmap_is_safe(client):
    body = client.get(f"/stores/{STORE}/heatmap").json()
    assert body["zones"] == []
    assert body["session_count"] == 0
    assert body["low_confidence"] is True
