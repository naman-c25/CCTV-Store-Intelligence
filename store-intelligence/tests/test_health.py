# PROMPT: "Write pytest tests for a /health endpoint that reports DB status and,
# per store, the last event timestamp with a STALE_FEED warning when the latest
# event is older than 10 minutes."
# CHANGES MADE: The model only checked HTTP 200; I added assertions on the
# per-store feed list and a deterministic STALE_FEED case driven by an injected
# 'now', since the brief stresses this endpoint must be accurate for on-call.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.health import compute_health
from app.ingestion import ingest_events

from .conftest import make_event

STORE = "STORE_BLR_002"


def test_health_ok_on_fresh_feed(client):
    client.post("/events/ingest", json=[make_event()])
    body = client.get("/health").json()
    assert body["database"] == "up"
    assert any(f["store_id"] == STORE for f in body["feeds"])


def test_stale_feed_flagged(repo):
    old = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    now = old + timedelta(minutes=30)
    ingest_events([make_event(timestamp=old.isoformat())], repo)
    body = compute_health(repo, now=now)
    feed = next(f for f in body["feeds"] if f["store_id"] == STORE)
    assert feed["status"] == "STALE_FEED"
    assert body["status"] == "degraded"
