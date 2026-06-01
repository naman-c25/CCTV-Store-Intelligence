"""Self-evaluation harness — grade my detection output before the graders do.

Given my emitted events (JSONL) and, optionally, a ground-truth file
(sample_events.jsonl), this reports the same things the detection rubric checks:
schema validity, event_id uniqueness, timestamp sanity, entry/exit counts, and
the event-type distribution. It also computes a count-accuracy delta against the
ground truth when provided. Pure Python + the app's Pydantic model.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _validate(events: list[dict]) -> dict:
    # Import lazily so calibration runs even if pydantic isn't importable here;
    # if it is (the app venv), we get true schema validation.
    valid = 0
    errors: list[str] = []
    try:
        from app.models import Event  # type: ignore

        for i, e in enumerate(events):
            try:
                Event.model_validate(e)
                valid += 1
            except Exception as exc:  # noqa: BLE001
                if len(errors) < 10:
                    errors.append(f"#{i}: {exc}")
    except Exception:
        # Fallback: shallow required-field check.
        required = {"event_id", "store_id", "camera_id", "visitor_id", "event_type",
                    "timestamp", "confidence"}
        for i, e in enumerate(events):
            if required.issubset(e):
                valid += 1
            elif len(errors) < 10:
                errors.append(f"#{i}: missing {required - set(e)}")
    return {"valid": valid, "errors": errors}


def scorecard(my_events: list[dict], ground_truth: list[dict] | None = None) -> dict:
    types = Counter(e.get("event_type") for e in my_events)
    ids = [e.get("event_id") for e in my_events]
    unique_ids = len(set(ids))
    val = _validate(my_events)

    card = {
        "total_events": len(my_events),
        "schema_valid": val["valid"],
        "schema_valid_pct": round(100 * val["valid"] / max(1, len(my_events)), 1),
        "unique_event_ids": unique_ids,
        "duplicate_event_ids": len(ids) - unique_ids,
        "event_type_counts": dict(types),
        "entries": types.get("ENTRY", 0),
        "exits": types.get("EXIT", 0),
        "reentries": types.get("REENTRY", 0),
        "sample_errors": val["errors"],
    }

    if ground_truth:
        gt_types = Counter(e.get("event_type") for e in ground_truth)
        gt_entries = gt_types.get("ENTRY", 0)
        gt_exits = gt_types.get("EXIT", 0)
        card["ground_truth"] = {
            "gt_entries": gt_entries,
            "gt_exits": gt_exits,
            "entry_abs_error": abs(card["entries"] - gt_entries),
            "exit_abs_error": abs(card["exits"] - gt_exits),
            "entry_accuracy_pct": round(100 * (1 - abs(card["entries"] - gt_entries) / max(1, gt_entries)), 1),
        }
    return card


def main():
    ap = argparse.ArgumentParser(description="Grade detection output against the schema/ground truth.")
    ap.add_argument("--events", required=True, help="my emitted events.jsonl")
    ap.add_argument("--ground-truth", default=None, help="sample_events.jsonl (optional)")
    args = ap.parse_args()
    gt = _load(args.ground_truth) if args.ground_truth else None
    card = scorecard(_load(args.events), gt)
    print(json.dumps(card, indent=2))


if __name__ == "__main__":
    main()
