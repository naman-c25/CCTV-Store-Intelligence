# PROMPT: "Add pytest coverage for the remaining units: JSONL event writing, the
# POS CSV loader (including a missing file), the staff colour-histogram heuristic
# and its no-VLM fallback, and the API's POS ingest / root / dashboard / malformed
# body paths."
# CHANGES MADE: The model called the real VLM in the staff test; I forced
# use_vlm=False so the suite is hermetic and deterministic (the VLM path is
# integration-only). I added the missing-CSV case because the loader must report
# found=False rather than raising.
from __future__ import annotations

from app.pos_loader import load_pos_csv
from pipeline.emit import write_jsonl
from pipeline.staff import decide_staff, histogram_staff_score

from .conftest import make_event


def test_write_jsonl(tmp_path):
    events = [make_event(visitor_id=f"V{i}") for i in range(3)]
    out = tmp_path / "e.jsonl"
    n = write_jsonl(events, out)
    assert n == 3
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_pos_loader_reads_csv(tmp_path, repo):
    csv = tmp_path / "pos.csv"
    csv.write_text(
        "store_id,transaction_id,timestamp,basket_value_inr\n"
        "STORE_BLR_002,TXN_1,2026-03-03T14:38:12Z,1240.00\n"
        "STORE_BLR_002,TXN_2,2026-03-03T14:41:55Z,680.00\n",
        encoding="utf-8",
    )
    result = load_pos_csv(csv, repo)
    assert result["loaded"] == 2 and result["found"] is True


def test_pos_loader_missing_file_is_safe(tmp_path, repo):
    result = load_pos_csv(tmp_path / "nope.csv", repo)
    assert result["found"] is False and result["loaded"] == 0


def test_staff_histogram_and_fallback():
    uniform = [0.0, 0.0, 1.0, 0.0]
    same = [0.0, 0.0, 1.0, 0.0]
    diff = [1.0, 0.0, 0.0, 0.0]
    assert histogram_staff_score(same, uniform) > 0.9
    assert histogram_staff_score(diff, uniform) < 0.5
    d = decide_staff(torso_hist=same, uniform_hist=uniform, hist_threshold=0.6, use_vlm=False)
    assert d.is_staff is True and d.source == "histogram"


def test_pos_ingest_endpoint(client):
    rows = [{"store_id": "STORE_BLR_002", "transaction_id": "T1",
             "timestamp": "2026-03-03T14:38:12Z", "basket_value_inr": 1240.0}]
    r = client.post("/pos/ingest", json=rows)
    assert r.status_code == 200 and r.json()["accepted"] == 1


def test_root_and_dashboard(client):
    assert client.get("/").json()["service"].startswith("Apex")
    assert client.get("/dashboard").status_code == 200


def test_ingest_rejects_garbage_body(client):
    r = client.post("/events/ingest", json={"not_events": 1})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"
