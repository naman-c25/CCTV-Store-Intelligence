"""FastAPI entrypoint — the Intelligence API surface.

Wires the event-sourced store to the analytics projections and enforces the
production concerns: structured logging, a sanitized error envelope (no stack
traces), and HTTP 503 when the backing store is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import anomalies as anomalies_mod
from . import funnel as funnel_mod
from . import health as health_mod
from . import heatmap as heatmap_mod
from . import metrics as metrics_mod
from .config import get_settings
from .ingestion import ingest_events
from .logging_setup import RequestLoggingMiddleware, configure_logging
from .models import ErrorResponse
from .pos_loader import load_pos_csv
from .storage import Repository, StorageUnavailable

configure_logging()
logger = logging.getLogger("store_intel.app")

app = FastAPI(
    title="Apex Store Intelligence API",
    version="0.1.0",
    description="A web-analytics engine for physical retail stores.",
)
app.add_middleware(RequestLoggingMiddleware)

repo = Repository()


@app.on_event("startup")
def _startup() -> None:
    try:
        repo.create_all()
    except StorageUnavailable as exc:
        # Don't crash the process — /health will report the DB as down and
        # endpoints will return 503. This keeps the container up for diagnosis.
        logger.error("startup_db_unavailable: %s", exc)
    pos_csv = os.getenv("POS_CSV")
    if pos_csv:
        try:
            result = load_pos_csv(pos_csv, repo)
            logger.info("pos_loaded: %s", result)
        except Exception as exc:  # noqa: BLE001 - best-effort load
            logger.error("pos_load_failed: %s", exc)


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


@app.exception_handler(StorageUnavailable)
async def _storage_unavailable_handler(request: Request, exc: StorageUnavailable):
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(
            error="storage_unavailable",
            detail="The analytics datastore is currently unreachable. Retry shortly.",
            trace_id=_trace_id(request),
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    # Never leak a stack trace to the client; log it with the trace_id instead.
    logger.exception("unhandled_error trace_id=%s", _trace_id(request))
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="internal_error",
            detail="An unexpected error occurred.",
            trace_id=_trace_id(request),
        ).model_dump(),
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.post("/events/ingest")
async def ingest(request: Request):
    settings = get_settings()
    body: Any = await request.json()
    if isinstance(body, dict) and "events" in body:
        raw_events = body["events"]
    elif isinstance(body, list):
        raw_events = body
    else:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="bad_request",
                detail="Body must be a list of events or an object with an 'events' array.",
                trace_id=_trace_id(request),
            ).model_dump(),
        )

    if not isinstance(raw_events, list):
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="bad_request", detail="'events' must be an array.", trace_id=_trace_id(request)).model_dump(),
        )

    if len(raw_events) > settings.MAX_INGEST_BATCH:
        return JSONResponse(
            status_code=413,
            content=ErrorResponse(
                error="batch_too_large",
                detail=f"Batch of {len(raw_events)} exceeds limit of {settings.MAX_INGEST_BATCH}.",
                trace_id=_trace_id(request),
            ).model_dump(),
        )

    request.state.event_count = len(raw_events)
    result = ingest_events(raw_events, repo)
    return JSONResponse(status_code=200, content=result.model_dump())


@app.post("/pos/ingest")
async def pos_ingest(request: Request):
    """Ingest POS transactions (batch). Idempotent by transaction_id.

    Accepts a list of {store_id, transaction_id, timestamp, basket_value_inr}
    or an object with a 'transactions' array. Used by the replay/demo tooling
    and as an alternative to the startup CSV load.
    """
    from datetime import datetime

    body: Any = await request.json()
    rows = body.get("transactions") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return JSONResponse(status_code=400, content=ErrorResponse(error="bad_request", detail="Expected a list of transactions.", trace_id=_trace_id(request)).model_dump())

    accepted = rejected = 0
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
            repo.upsert_pos(str(r["transaction_id"]), str(r["store_id"]), ts, float(r["basket_value_inr"]))
            accepted += 1
        except (KeyError, ValueError, TypeError):
            rejected += 1
    return {"accepted": accepted, "rejected": rejected, "received": len(rows)}


@app.get("/stores/{id}/metrics")
async def store_metrics(id: str, date: str | None = None, frm: str | None = None, to: str | None = None):
    return metrics_mod.compute_metrics(repo, id, date=date, frm=frm, to=to)


@app.get("/stores/{id}/funnel")
async def store_funnel(id: str, date: str | None = None, frm: str | None = None, to: str | None = None):
    return funnel_mod.compute_funnel(repo, id, date=date, frm=frm, to=to)


@app.get("/stores/{id}/heatmap")
async def store_heatmap(id: str, date: str | None = None, frm: str | None = None, to: str | None = None):
    return heatmap_mod.compute_heatmap(repo, id, date=date, frm=frm, to=to)


@app.get("/stores/{id}/anomalies")
async def store_anomalies(id: str):
    return anomalies_mod.compute_anomalies(repo, id)


@app.get("/health")
async def health():
    return health_mod.compute_health(repo)


# --------------------------------------------------------------------------
# Live dashboard (Part E): SSE stream + a zero-dependency web UI.
# --------------------------------------------------------------------------
def _live_snapshot(store_id: str) -> dict:
    """A compact, dashboard-friendly slice of the projections."""
    m = metrics_mod.compute_metrics(repo, store_id)
    f = funnel_mod.compute_funnel(repo, store_id)
    a = anomalies_mod.compute_anomalies(repo, store_id)
    return {
        "store_id": store_id,
        "unique_visitors": m["unique_visitors"],
        "converted_visitors": m["converted_visitors"],
        "conversion_rate": m["conversion_rate"],
        "queue_depth": m["queue_depth"],
        "abandonment_rate": m["abandonment_rate"],
        "revenue_inr": m["revenue_inr"],
        "funnel": f["stages"],
        "anomalies": a["active_anomalies"],
        "window": m["window"],
    }


@app.get("/stores/{id}/stream")
async def stream(id: str):
    async def event_gen():
        while True:
            try:
                snapshot = await asyncio.to_thread(_live_snapshot, id)
                yield f"data: {json.dumps(snapshot)}\n\n"
            except StorageUnavailable:
                yield 'data: {"error":"storage_unavailable"}\n\n'
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    if _DASHBOARD.exists():
        return HTMLResponse(_DASHBOARD.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard asset missing</h1>", status_code=404)


@app.get("/")
async def root():
    return {
        "service": "Apex Store Intelligence API",
        "version": "0.1.0",
        "endpoints": [
            "POST /events/ingest",
            "GET /stores/{id}/metrics",
            "GET /stores/{id}/funnel",
            "GET /stores/{id}/heatmap",
            "GET /stores/{id}/anomalies",
            "GET /health",
        ],
    }
