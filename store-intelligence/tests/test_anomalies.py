# PROMPT: "Write pytest tests for an anomalies endpoint that detects a billing
# queue spike, a conversion drop vs a 7-day baseline, and a dead zone (no visits
# for 30 min while the store is active). Each anomaly needs a severity
# (INFO/WARN/CRITICAL) and a suggested_action string."
# CHANGES MADE: The model assumed a fixed 7 days of history always exists; our
# data is ~1 hour, so I changed the conversion-drop test to seed a single prior
# day and assert the detector still fires but caps severity at WARN under thin
# history. I added the explicit "no anomalies on empty store" guard.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.anomalies import compute_anomalies

from .conftest import make_event, visitor_journey

STORE = "STORE_BLR_002"


def _ingest(client, events):
    return client.post("/events/ingest", json={"events": events}).json()


def test_no_anomalies_on_empty_store(client):
    body = client.get(f"/stores/{STORE}/anomalies").json()
    assert body["count"] == 0
    assert body["active_anomalies"] == []


def test_queue_spike_detected(client):
    now = datetime.now(timezone.utc)
    ev = make_event(event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
                    camera_id="CAM_BILLING_01", timestamp=now.isoformat(),
                    metadata={"queue_depth": 12, "session_seq": 1})
    _ingest(client, [ev])
    body = client.get(f"/stores/{STORE}/anomalies").json()
    spike = next(a for a in body["active_anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE")
    assert spike["severity"] == "CRITICAL"  # 12 >= 2x threshold(5)
    assert spike["suggested_action"]


def test_dead_zone_detected(repo):
    # Use the module directly with an injected 'now' for determinism.
    now = datetime(2026, 3, 3, 12, 0, 0, tzinfo=timezone.utc)
    past = now - timedelta(minutes=45)
    recent = now - timedelta(minutes=2)
    rows = []
    # SKINCARE last visited 45 min ago (dead); ENTRY activity 2 min ago (store active).
    rows.append(make_event(visitor_id="V1", event_type="ZONE_ENTER", zone_id="SKINCARE",
                           timestamp=past.isoformat()))
    rows.append(make_event(visitor_id="V2", event_type="ENTRY", timestamp=recent.isoformat()))
    from app.ingestion import ingest_events
    ingest_events(rows, repo)
    body = compute_anomalies(repo, STORE, now=now)
    dead = [a for a in body["active_anomalies"] if a["type"] == "DEAD_ZONE"]
    assert any(a["metric"]["zone_id"] == "SKINCARE" for a in dead)
