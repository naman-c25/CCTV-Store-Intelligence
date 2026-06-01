"""Pydantic v2 schemas — the canonical event contract and API response models.

The Event model is the single source of truth for what the detection pipeline
emits and what the API ingests. Validation here is deliberately strict on shape
but lenient on confidence (we NEVER reject low-confidence events — the challenge
explicitly requires emitting them; filtering is a read-side concern).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

    # Tolerate forward-compatible extra metadata keys rather than rejecting them.
    model_config = {"extra": "allow"}


class Event(BaseModel):
    """One behavioural event emitted by the detection pipeline."""

    event_id: str = Field(..., min_length=1, description="Globally unique (uuid-v4).")
    store_id: str = Field(..., min_length=1)
    camera_id: str = Field(..., min_length=1)
    visitor_id: str = Field(..., min_length=1, description="Re-ID token, stable across re-entry.")
    event_type: EventType
    timestamp: datetime = Field(..., description="ISO-8601 UTC.")
    zone_id: Optional[str] = Field(None, description="null for ENTRY/EXIT events.")
    dwell_ms: int = Field(0, ge=0)
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    model_config = {"extra": "allow"}

    @field_validator("event_type", mode="before")
    @classmethod
    def _coerce_event_type(cls, v: Any) -> Any:
        # Accept arbitrary string casing from heterogeneous producers.
        if isinstance(v, str):
            return v.strip().upper()
        return v


class IngestRequest(BaseModel):
    events: list[Event] = Field(default_factory=list)


class IngestItemError(BaseModel):
    index: int
    event_id: Optional[str] = None
    error: str


class IngestResponse(BaseModel):
    """Partial-success response: some events may be accepted while others fail."""

    accepted: int
    duplicates: int
    rejected: int
    received: int
    errors: list[IngestItemError] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Structured error envelope — never leak stack traces to clients."""

    error: str
    detail: Optional[str] = None
    trace_id: Optional[str] = None
