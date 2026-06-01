"""Shared test fixtures.

Each test gets an isolated SQLite-backed repository (temp file) and a TestClient
whose module-level repo is swapped for that isolated one, so tests never share
state and never need Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app.storage import Repository


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "test.db"
    r = Repository(f"sqlite:///{db}")
    r.create_all()
    return r


@pytest.fixture
def client(repo, monkeypatch):
    monkeypatch.setattr(main_mod, "repo", repo)
    with TestClient(main_mod.app) as c:
        yield c


# --- event builders --------------------------------------------------------
def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_0001",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    base.update(overrides)
    return base


def visitor_journey(visitor_id, store_id="STORE_BLR_002", base_time=None, reached_billing=False,
                    converted_zone="SKINCARE", is_staff=False, dwell_ms=40000):
    """A realistic ordered journey for one visitor: ENTRY → ZONE → (BILLING)."""
    t = base_time or datetime.now(timezone.utc)
    evs = [
        make_event(visitor_id=visitor_id, store_id=store_id, event_type="ENTRY",
                   timestamp=t.isoformat(), is_staff=is_staff,
                   metadata={"session_seq": 1}),
        make_event(visitor_id=visitor_id, store_id=store_id, event_type="ZONE_ENTER",
                   zone_id=converted_zone, camera_id="CAM_FLOOR_01",
                   timestamp=(t + timedelta(seconds=10)).isoformat(), is_staff=is_staff,
                   metadata={"sku_zone": converted_zone, "session_seq": 2}),
        make_event(visitor_id=visitor_id, store_id=store_id, event_type="ZONE_DWELL",
                   zone_id=converted_zone, camera_id="CAM_FLOOR_01", dwell_ms=dwell_ms,
                   timestamp=(t + timedelta(seconds=40)).isoformat(), is_staff=is_staff,
                   metadata={"sku_zone": converted_zone, "session_seq": 3}),
    ]
    if reached_billing:
        evs.append(make_event(visitor_id=visitor_id, store_id=store_id,
                              event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
                              camera_id="CAM_BILLING_01",
                              timestamp=(t + timedelta(seconds=120)).isoformat(),
                              is_staff=is_staff,
                              metadata={"queue_depth": 2, "session_seq": 4}))
    return evs
