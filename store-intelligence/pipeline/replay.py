"""Temporal replay engine — feed events into the API at (simulated) real time.

Reads a JSONL event file and POSTs events to /events/ingest paced by the gaps
between their own timestamps, divided by --speed. So a 20-minute clip can be
replayed live in 20s at --speed 60 while the dashboard updates in real time.
This is the proof that the pipeline and API are genuinely connected, not batch
loaded.

Can also synthesise a store on the fly (--synth) and pre-load its POS rows, so
`python pipeline/replay.py --synth` is a one-command end-to-end demo.

Dependencies: standard library only (urllib) — nothing to install.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone


def _post(api: str, path: str, payload) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(api.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_events(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def replay(events: list[dict], api: str, speed: float, batch: int = 1, quiet: bool = False) -> None:
    events.sort(key=lambda e: e["timestamp"])
    if not events:
        print("no events to replay")
        return
    t_prev = _parse_ts(events[0]["timestamp"])
    buf: list[dict] = []
    sent = 0

    def flush():
        nonlocal sent, buf
        if not buf:
            return
        res = _post(api, "/events/ingest", buf)
        sent += res.get("accepted", 0)
        if not quiet:
            print(f"  +{res.get('accepted',0)} accepted (total {sent})  "
                  f"dup={res.get('duplicates',0)} rej={res.get('rejected',0)}")
        buf = []

    for ev in events:
        t = _parse_ts(ev["timestamp"])
        gap = (t - t_prev).total_seconds() / max(speed, 0.001)
        if gap > 0:
            flush()
            time.sleep(min(gap, 5.0))  # cap waits so empty periods don't stall the demo
        t_prev = t
        buf.append(ev)
        if len(buf) >= batch:
            flush()
    flush()
    print(f"replay complete: {sent} events ingested into {api}")


def main():
    ap = argparse.ArgumentParser(description="Replay events into the API at simulated real time.")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--events", default=None, help="JSONL of events to replay")
    ap.add_argument("--pos", default=None, help="JSON array of POS rows to preload")
    ap.add_argument("--speed", type=float, default=60.0, help="time compression factor")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--synth", action="store_true", help="generate a synthetic store and replay it")
    ap.add_argument("--store", default="STORE_BLR_002")
    ap.add_argument("--minutes", type=int, default=20)
    args = ap.parse_args()

    pos_rows = None
    if args.synth:
        from synth import generate  # type: ignore
        out = generate(args.store, datetime.now(timezone.utc), args.minutes)
        events = out.events
        pos_rows = out.pos
        print(f"synthesised {len(events)} events / {len(pos_rows)} pos rows for {args.store}")
    else:
        if not args.events:
            ap.error("provide --events <file.jsonl> or use --synth")
        events = load_events(args.events)
        if args.pos:
            pos_rows = json.load(open(args.pos, encoding="utf-8"))

    # Preload POS so conversion can be computed as billing events arrive.
    if pos_rows:
        res = _post(args.api, "/pos/ingest", pos_rows)
        print(f"loaded POS: {res}")

    print(f"replaying at {args.speed}x -> open {args.api}/dashboard to watch it live")
    replay(events, args.api, args.speed, args.batch)


if __name__ == "__main__":
    main()
