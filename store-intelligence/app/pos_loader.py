"""Load pos_transactions.csv into the POS table.

Used both at startup (if POS_CSV is set and the file exists) and by a small
admin endpoint. Parsing is tolerant: rows that fail to parse are skipped and
counted rather than aborting the whole load.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .storage import Repository
from .timewindows import ensure_utc


def _parse_ts(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return ensure_utc(dt)


def load_pos_csv(path: str | Path, repo: Repository) -> dict:
    path = Path(path)
    loaded = skipped = 0
    if not path.exists():
        return {"loaded": 0, "skipped": 0, "found": False, "path": str(path)}

    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                repo.upsert_pos(
                    transaction_id=row["transaction_id"].strip(),
                    store_id=row["store_id"].strip(),
                    timestamp=_parse_ts(row["timestamp"]),
                    basket_value_inr=float(row["basket_value_inr"]),
                )
                loaded += 1
            except (KeyError, ValueError, TypeError):
                skipped += 1
    return {"loaded": loaded, "skipped": skipped, "found": True, "path": str(path)}
