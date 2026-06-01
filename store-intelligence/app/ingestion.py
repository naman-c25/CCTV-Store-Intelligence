"""Event ingestion: validate, deduplicate, persist — with partial success.

Each event in a batch is validated independently. A malformed event is rejected
with a structured per-item error while valid events in the same batch are still
accepted. Deduplication is by event_id at the storage layer, which also makes
the whole endpoint idempotent: replaying an identical batch yields the same
state with every event reported as a duplicate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .models import Event, IngestItemError, IngestResponse
from .storage import Repository


def ingest_events(raw_events: list[dict[str, Any]], repo: Repository) -> IngestResponse:
    accepted = duplicates = rejected = 0
    errors: list[IngestItemError] = []
    seen_in_batch: set[str] = set()
    now = datetime.now(timezone.utc)

    for idx, raw in enumerate(raw_events):
        try:
            event = Event.model_validate(raw)
        except ValidationError as exc:
            rejected += 1
            errors.append(
                IngestItemError(
                    index=idx,
                    event_id=raw.get("event_id") if isinstance(raw, dict) else None,
                    error="; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()),
                )
            )
            continue
        except Exception as exc:  # defensive: never let one bad item 5xx the batch
            rejected += 1
            errors.append(IngestItemError(index=idx, error=f"unprocessable: {exc}"))
            continue

        # Intra-batch dedup (same event_id twice in one payload).
        if event.event_id in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(event.event_id)

        result = repo.insert_event(event, ingested_at=now)
        if result == "accepted":
            accepted += 1
        else:
            duplicates += 1

    return IngestResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        received=len(raw_events),
        errors=errors,
    )
