"""Event-sourced storage layer.

Design: the `events` table is an append-only, immutable log — the single source
of truth. All analytics (metrics, funnel, heatmap, anomalies) are *projections*
computed by reading this log. `event_id` carries a UNIQUE constraint, which is
what makes ingestion idempotent: re-inserting a seen event is a no-op, not a
duplicate row.

The repository is deliberately storage-engine agnostic (SQLite for tests/local,
Postgres in docker-compose) and converts any driver-level failure into a
`StorageUnavailable` error so the API can answer 503 instead of leaking a stack.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Iterator, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

from .config import get_settings
from .models import Event


class StorageUnavailable(RuntimeError):
    """Raised when the backing store cannot be reached → surfaces as HTTP 503."""


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    store_id: Mapped[str] = mapped_column(String, index=True)
    camera_id: Mapped[str] = mapped_column(String, index=True)
    visitor_id: Mapped[str] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    zone_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    # When the API received the event (vs. when it occurred). Drives STALE_FEED.
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_event(self) -> Event:
        return Event(
            event_id=self.event_id,
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=self.visitor_id,
            event_type=self.event_type,
            timestamp=self.timestamp,
            zone_id=self.zone_id,
            dwell_ms=self.dwell_ms,
            is_staff=self.is_staff,
            confidence=self.confidence,
            metadata=self.event_metadata or {},
        )


class PosRow(Base):
    __tablename__ = "pos_transactions"

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    store_id: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    basket_value_inr: Mapped[float] = mapped_column(Float, default=0.0)


class Repository:
    def __init__(self, database_url: Optional[str] = None):
        url = database_url or get_settings().DATABASE_URL
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        # pool_pre_ping lets us notice a dropped Postgres connection and fail fast.
        self.engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        try:
            Base.metadata.create_all(self.engine)
        except SQLAlchemyError as exc:  # pragma: no cover - infra failure path
            raise StorageUnavailable(str(exc)) from exc

    @contextmanager
    def session(self) -> Iterator[Session]:
        try:
            with self._Session() as s:
                yield s
        except IntegrityError:
            # A constraint violation (e.g. duplicate event_id) is a normal,
            # expected signal for idempotent writes — let callers handle it.
            raise
        except SQLAlchemyError as exc:
            # Anything else (connection refused, etc.) means the store is down.
            raise StorageUnavailable(str(exc)) from exc

    def ping(self) -> bool:
        from sqlalchemy import text

        try:
            with self._Session() as s:
                s.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    # ---- writes -----------------------------------------------------------
    def insert_event(self, event: Event, ingested_at: datetime) -> str:
        """Insert one event. Returns 'accepted' or 'duplicate'.

        Idempotency is enforced at the DB level via the primary key on event_id.
        """
        row = EventRow(
            event_id=event.event_id,
            store_id=event.store_id,
            camera_id=event.camera_id,
            visitor_id=event.visitor_id,
            event_type=event.event_type.value if hasattr(event.event_type, "value") else event.event_type,
            timestamp=event.timestamp,
            zone_id=event.zone_id,
            dwell_ms=event.dwell_ms,
            is_staff=event.is_staff,
            confidence=event.confidence,
            event_metadata=event.metadata.model_dump() if hasattr(event.metadata, "model_dump") else dict(event.metadata),
            ingested_at=ingested_at,
        )
        try:
            with self.session() as s:
                s.add(row)
                s.commit()
            return "accepted"
        except IntegrityError:
            return "duplicate"

    # ---- reads ------------------------------------------------------------
    def query_events(
        self,
        store_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        include_staff: bool = True,
        event_types: Optional[Iterable[str]] = None,
    ) -> list[Event]:
        stmt = select(EventRow)
        if store_id is not None:
            stmt = stmt.where(EventRow.store_id == store_id)
        if start is not None:
            stmt = stmt.where(EventRow.timestamp >= start)
        if end is not None:
            stmt = stmt.where(EventRow.timestamp <= end)
        if not include_staff:
            stmt = stmt.where(EventRow.is_staff.is_(False))
        if event_types is not None:
            stmt = stmt.where(EventRow.event_type.in_(list(event_types)))
        stmt = stmt.order_by(EventRow.timestamp.asc())
        with self.session() as s:
            return [r.to_event() for r in s.execute(stmt).scalars().all()]

    def last_event_time_per_store(self) -> dict[str, datetime]:
        from sqlalchemy import func

        stmt = select(EventRow.store_id, func.max(EventRow.timestamp)).group_by(EventRow.store_id)
        with self.session() as s:
            return {sid: ts for sid, ts in s.execute(stmt).all()}

    def latest_event_time(self, store_id: Optional[str] = None) -> Optional[datetime]:
        from sqlalchemy import func

        stmt = select(func.max(EventRow.timestamp))
        if store_id is not None:
            stmt = stmt.where(EventRow.store_id == store_id)
        with self.session() as s:
            return s.execute(stmt).scalar_one_or_none()

    def known_store_ids(self) -> list[str]:
        stmt = select(EventRow.store_id).distinct()
        with self.session() as s:
            return [r for (r,) in s.execute(stmt).all()]

    # ---- POS --------------------------------------------------------------
    def upsert_pos(self, transaction_id: str, store_id: str, timestamp: datetime, basket_value_inr: float) -> str:
        row = PosRow(
            transaction_id=transaction_id,
            store_id=store_id,
            timestamp=timestamp,
            basket_value_inr=basket_value_inr,
        )
        try:
            with self.session() as s:
                s.merge(row)  # idempotent by transaction_id
                s.commit()
            return "accepted"
        except IntegrityError:
            return "duplicate"

    def query_pos(
        self, store_id: Optional[str] = None, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> list[PosRow]:
        stmt = select(PosRow)
        if store_id is not None:
            stmt = stmt.where(PosRow.store_id == store_id)
        if start is not None:
            stmt = stmt.where(PosRow.timestamp >= start)
        if end is not None:
            stmt = stmt.where(PosRow.timestamp <= end)
        stmt = stmt.order_by(PosRow.timestamp.asc())
        with self.session() as s:
            rows = s.execute(stmt).scalars().all()
            # Detach lightweight copies so callers can use them after session close.
            return [
                PosRow(
                    transaction_id=r.transaction_id,
                    store_id=r.store_id,
                    timestamp=r.timestamp,
                    basket_value_inr=r.basket_value_inr,
                )
                for r in rows
            ]
