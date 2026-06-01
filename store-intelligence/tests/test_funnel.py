# PROMPT: "Write pytest tests for a conversion funnel endpoint with stages
# Entry -> Zone Visit -> Billing Queue -> Purchase. The session is the unit, and
# a customer who re-enters (REENTRY event, same visitor_id) must be counted once,
# not twice. Include drop-off percentages."
# CHANGES MADE: The model counted raw ENTRY events for the first stage, which
# would double-count re-entries. I rewrote the re-entry test to assert that a
# visitor with ENTRY + EXIT + REENTRY contributes exactly 1 to the Entry stage,
# since session de-duplication is the whole point of that requirement.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import make_event, visitor_journey

STORE = "STORE_BLR_002"


def _ingest(client, events):
    return client.post("/events/ingest", json={"events": events}).json()


def _stage(body, name):
    return next(s for s in body["stages"] if s["stage"] == name)


def test_funnel_stages_are_monotonic(client):
    now = datetime.now(timezone.utc)
    events = []
    for i in range(5):
        events += visitor_journey(f"VIS_{i}", base_time=now, reached_billing=(i < 2))
    _ingest(client, events)
    body = client.get(f"/stores/{STORE}/funnel").json()
    assert _stage(body, "ENTRY")["count"] == 5
    assert _stage(body, "ZONE_VISIT")["count"] == 5
    assert _stage(body, "BILLING_QUEUE")["count"] == 2
    # drop-off from zone visit to billing = (5-2)/5 = 60%
    assert _stage(body, "BILLING_QUEUE")["drop_off_pct"] == 60.0


def test_reentry_not_double_counted(client):
    now = datetime.now(timezone.utc)
    events = visitor_journey("VIS_RE", base_time=now)
    # Same visitor exits and re-enters: REENTRY reuses the SAME visitor_id.
    events.append(make_event(visitor_id="VIS_RE", event_type="EXIT",
                             timestamp=(now + timedelta(minutes=2)).isoformat()))
    events.append(make_event(visitor_id="VIS_RE", event_type="REENTRY",
                             timestamp=(now + timedelta(minutes=5)).isoformat()))
    _ingest(client, events)
    body = client.get(f"/stores/{STORE}/funnel").json()
    assert _stage(body, "ENTRY")["count"] == 1  # one physical visitor, not two


def test_empty_funnel_is_safe(client):
    body = client.get(f"/stores/{STORE}/funnel").json()
    assert _stage(body, "ENTRY")["count"] == 0
    assert body["overall_conversion_pct"] == 0.0
