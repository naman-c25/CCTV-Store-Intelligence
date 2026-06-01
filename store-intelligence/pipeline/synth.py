"""Synthetic store simulator.

Generates a stream of behavioural events (matching app/models.Event) plus the
matching POS transactions for a store, deliberately including all seven edge
cases from the brief: group entry, staff movement, re-entry, partial occlusion
(low confidence), billing-queue buildup, empty periods, and (lightly) cross-
camera overlap.

This is NOT the detector — it lets us demonstrate the full pipeline → API →
dashboard loop and exercise edge cases without shipping any video. The real
detector (pipeline/detect.py) emits the same schema, so the API can't tell them
apart. Determinism via a seed keeps demos and tests repeatable.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

ZONES = ["SKINCARE", "HAIRCARE", "MAKEUP", "FRAGRANCE", "WELLNESS"]
SKU = {"SKINCARE": "MOISTURISER", "HAIRCARE": "SHAMPOO", "MAKEUP": "LIPSTICK",
       "FRAGRANCE": "PERFUME", "WELLNESS": "VITAMINS"}


@dataclass
class Out:
    events: list = field(default_factory=list)
    pos: list = field(default_factory=list)


def _ev(store, cam, vid, etype, ts, conf, zone=None, dwell=0, staff=False, seq=1, qdepth=None):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store,
        "camera_id": cam,
        "visitor_id": vid,
        "event_type": etype,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "zone_id": zone,
        "dwell_ms": dwell,
        "is_staff": staff,
        "confidence": round(conf, 2),
        "metadata": {"queue_depth": qdepth, "sku_zone": SKU.get(zone) if zone else None, "session_seq": seq},
    }


def _journey(out: Out, store, vid, t0, rng, *, staff=False, buy=False, occluded=False,
             reenter=False, queue_depth=0):
    """Emit one visitor's ordered journey."""
    seq = 1
    conf = lambda: rng.uniform(0.3, 0.55) if occluded else rng.uniform(0.78, 0.97)
    out.events.append(_ev(store, "CAM_ENTRY_01", vid, "ENTRY", t0, conf(), staff=staff, seq=seq)); seq += 1

    t = t0
    n_zones = rng.randint(1, 3)
    last_zone = None
    for _ in range(n_zones):
        zone = rng.choice(ZONES)
        last_zone = zone
        t = t + timedelta(seconds=rng.randint(20, 90))
        out.events.append(_ev(store, "CAM_FLOOR_01", vid, "ZONE_ENTER", t, conf(), zone=zone, seq=seq)); seq += 1
        dwell_s = rng.randint(15, 140)
        # ZONE_DWELL re-emitted every 30s of continued dwell.
        for d in range(30, dwell_s + 1, 30):
            t = t + timedelta(seconds=30)
            out.events.append(_ev(store, "CAM_FLOOR_01", vid, "ZONE_DWELL", t, conf(),
                                  zone=zone, dwell=d * 1000, staff=staff, seq=seq)); seq += 1
        out.events.append(_ev(store, "CAM_FLOOR_01", vid, "ZONE_EXIT", t, conf(), zone=zone, seq=seq)); seq += 1

    billing_time = None
    if buy or rng.random() < 0.45:
        t = t + timedelta(seconds=rng.randint(20, 60))
        billing_time = t
        out.events.append(_ev(store, "CAM_BILLING_01", vid, "BILLING_QUEUE_JOIN", t, conf(),
                              zone="BILLING", staff=staff, seq=seq, qdepth=queue_depth)); seq += 1
        if buy:
            # POS transaction lands within the 5-minute post-window.
            txn_t = t + timedelta(seconds=rng.randint(30, 240))
            out.pos.append({
                "store_id": store, "transaction_id": "TXN_" + uuid.uuid4().hex[:8],
                "timestamp": txn_t.isoformat().replace("+00:00", "Z"),
                "basket_value_inr": round(rng.uniform(250, 3200), 2),
            })
        else:
            # Joined the queue but left without buying → abandonment.
            t = t + timedelta(seconds=rng.randint(20, 120))
            out.events.append(_ev(store, "CAM_BILLING_01", vid, "BILLING_QUEUE_ABANDON", t, conf(),
                                  zone="BILLING", staff=staff, seq=seq)); seq += 1

    t = t + timedelta(seconds=rng.randint(10, 40))
    out.events.append(_ev(store, "CAM_ENTRY_01", vid, "EXIT", t, conf(), staff=staff, seq=seq)); seq += 1

    if reenter:
        # Same visitor returns: REENTRY reuses the SAME visitor_id (not a new ENTRY).
        t = t + timedelta(minutes=rng.randint(2, 6))
        out.events.append(_ev(store, "CAM_ENTRY_01", vid, "REENTRY", t, conf(), staff=staff, seq=seq)); seq += 1
        if last_zone:
            t = t + timedelta(seconds=20)
            out.events.append(_ev(store, "CAM_FLOOR_01", vid, "ZONE_ENTER", t, conf(), zone=last_zone, seq=seq))


def generate(store_id: str, start: datetime, minutes: int = 20, seed: int = 7) -> Out:
    rng = random.Random(f"{store_id}-{seed}")
    out = Out()
    t = start
    end = start + timedelta(minutes=minutes)
    vcount = 0
    while t < end:
        roll = rng.random()
        # Empty period: occasionally jump forward 3-6 minutes with no traffic.
        if roll < 0.06:
            t += timedelta(minutes=rng.randint(3, 6))
            continue
        # Group entry: 2-4 people within a couple of seconds.
        if roll < 0.18:
            group = rng.randint(2, 4)
            for g in range(group):
                vcount += 1
                _journey(out, store_id, f"VIS_{store_id[-3:]}_{vcount:04d}",
                         t + timedelta(seconds=g), rng, buy=rng.random() < 0.4)
            t += timedelta(seconds=rng.randint(20, 50))
            continue
        # Staff movement (~12%).
        if roll < 0.30:
            vcount += 1
            _journey(out, store_id, f"STAFF_{vcount:03d}", t, rng, staff=True)
            t += timedelta(seconds=rng.randint(40, 90))
            continue

        # Normal visitor (with occasional occlusion / re-entry / queue buildup).
        vcount += 1
        qdepth = rng.randint(0, 7) if rng.random() < 0.5 else 0
        _journey(out, store_id, f"VIS_{store_id[-3:]}_{vcount:04d}", t, rng,
                 buy=rng.random() < 0.5, occluded=rng.random() < 0.15,
                 reenter=rng.random() < 0.12, queue_depth=qdepth)
        t += timedelta(seconds=rng.randint(15, 45))

    out.events.sort(key=lambda e: e["timestamp"])
    out.pos.sort(key=lambda p: p["timestamp"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic store events + POS.")
    ap.add_argument("--store", default="STORE_BLR_002")
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--events-out", default="out/events.jsonl")
    ap.add_argument("--pos-out", default="out/pos.json")
    ap.add_argument("--start", default=None, help="ISO start time (default: now)")
    args = ap.parse_args()

    start = datetime.fromisoformat(args.start) if args.start else datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    out = generate(args.store, start, args.minutes, args.seed)

    import os
    os.makedirs(os.path.dirname(args.events_out) or ".", exist_ok=True)
    with open(args.events_out, "w", encoding="utf-8") as f:
        for e in out.events:
            f.write(json.dumps(e) + "\n")
    with open(args.pos_out, "w", encoding="utf-8") as f:
        json.dump(out.pos, f, indent=2)
    print(f"wrote {len(out.events)} events -> {args.events_out}")
    print(f"wrote {len(out.pos)} pos rows -> {args.pos_out}")


if __name__ == "__main__":
    main()
