"""Event emission sinks — write events to JSONL and/or POST to the API.

Kept separate from detection so the same events can be batch-written for grading
or streamed live for the dashboard. Batches POSTs to respect the API's 500-event
limit. Standard library only.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Iterable


def write_jsonl(events: Iterable[dict], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
            n += 1
    return n


def post_events(events: list[dict], api: str, batch: int = 500) -> dict:
    accepted = duplicates = rejected = 0
    for i in range(0, len(events), batch):
        chunk = events[i:i + batch]
        data = json.dumps(chunk).encode()
        req = urllib.request.Request(
            api.rstrip("/") + "/events/ingest", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = json.loads(resp.read().decode())
        accepted += r.get("accepted", 0)
        duplicates += r.get("duplicates", 0)
        rejected += r.get("rejected", 0)
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}
