"""Application configuration, loaded from environment with sane defaults.

Everything operationally relevant is overridable via env so the same image
runs locally (SQLite), in docker-compose (Postgres), and in tests.
"""
from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    # Storage. Default to local SQLite so the app boots with zero infra;
    # docker-compose overrides this with a Postgres URL.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./store_intel.db")

    # Business rules (kept as config so reviewers can see/tune the knobs).
    CONVERSION_WINDOW_MIN: int = int(os.getenv("CONVERSION_WINDOW_MIN", "5"))
    DWELL_EMIT_SECONDS: int = int(os.getenv("DWELL_EMIT_SECONDS", "30"))
    DEAD_ZONE_MINUTES: int = int(os.getenv("DEAD_ZONE_MINUTES", "30"))
    STALE_FEED_MINUTES: int = int(os.getenv("STALE_FEED_MINUTES", "10"))
    LOW_CONFIDENCE_THRESHOLD: float = float(os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.40"))
    MIN_SESSIONS_FOR_CONFIDENCE: int = int(os.getenv("MIN_SESSIONS_FOR_CONFIDENCE", "20"))
    MAX_INGEST_BATCH: int = int(os.getenv("MAX_INGEST_BATCH", "500"))

    # Anomaly thresholds.
    QUEUE_SPIKE_DEPTH: int = int(os.getenv("QUEUE_SPIKE_DEPTH", "5"))
    CONVERSION_DROP_PCT: float = float(os.getenv("CONVERSION_DROP_PCT", "0.30"))

    # "Today" is evaluated in this timezone offset (IST for Apex Retail).
    BUSINESS_TZ_OFFSET_HOURS: float = float(os.getenv("BUSINESS_TZ_OFFSET_HOURS", "5.5"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
