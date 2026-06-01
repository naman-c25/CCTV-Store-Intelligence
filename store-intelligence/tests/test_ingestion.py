# PROMPT: "Write pytest tests for a FastAPI POST /events/ingest endpoint that
# accepts batches up to 500 events, is idempotent by event_id, returns partial
# success when some events are malformed, and rejects oversized batches. Cover
# the edge cases the challenge names explicitly."
# CHANGES MADE: The model's first draft asserted the whole batch failed on one
# bad event; I rewrote those to assert PARTIAL success (good events accepted,
# bad ones reported in `errors`) because that is the actual contract. I also
# added the intra-batch duplicate case (same event_id twice in one payload) and
# the idempotent-replay case, which the model omitted.
from __future__ import annotations

from .conftest import make_event


def test_ingest_accepts_valid_batch(client):
    events = [make_event(visitor_id=f"VIS_{i}") for i in range(5)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 5
    assert body["rejected"] == 0
    assert body["received"] == 5


def test_ingest_is_idempotent_on_replay(client):
    events = [make_event(event_id="FIXED_1"), make_event(event_id="FIXED_2")]
    first = client.post("/events/ingest", json={"events": events}).json()
    second = client.post("/events/ingest", json={"events": events}).json()
    assert first["accepted"] == 2
    assert second["accepted"] == 0
    assert second["duplicates"] == 2  # replay is a no-op


def test_ingest_partial_success_on_malformed(client):
    good = make_event()
    bad = make_event()
    del bad["confidence"]  # required field missing → this one should be rejected
    r = client.post("/events/ingest", json={"events": [good, bad]})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert body["errors"][0]["index"] == 1


def test_ingest_dedupes_within_batch(client):
    dup = make_event(event_id="SAME")
    r = client.post("/events/ingest", json={"events": [dup, dict(dup)]})
    body = r.json()
    assert body["accepted"] == 1
    assert body["duplicates"] == 1


def test_ingest_rejects_oversized_batch(client):
    events = [make_event(visitor_id=f"VIS_{i}") for i in range(501)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 413
    assert r.json()["error"] == "batch_too_large"


def test_ingest_accepts_bare_array(client):
    r = client.post("/events/ingest", json=[make_event()])
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_ingest_low_confidence_event_is_not_dropped(client):
    # The challenge says: do NOT suppress low-confidence events.
    r = client.post("/events/ingest", json=[make_event(confidence=0.05)])
    assert r.json()["accepted"] == 1
