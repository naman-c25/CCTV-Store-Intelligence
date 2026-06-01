"""Structured JSON logging + a request middleware.

Every request emits one JSON line with: trace_id, store_id, endpoint,
latency_ms, event_count (for ingest), status_code. This is what an on-call
engineer greps. trace_id is also returned to the client (header + error body)
so a user-reported failure can be tied to a log line.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("trace_id", "store_id", "endpoint", "latency_ms", "event_count", "status_code"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


access_logger = logging.getLogger("store_intel.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
        request.state.trace_id = trace_id
        start = time.perf_counter()

        store_id = request.path_params.get("id") if request.path_params else None
        # store_id from path is only known after routing, so re-read post-call too.

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            # The global exception handler will turn this into a 503/500; we
            # still log it here with timing so nothing is silent.
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            access_logger.error(
                "request_failed",
                extra={
                    "trace_id": trace_id,
                    "store_id": store_id,
                    "endpoint": request.url.path,
                    "latency_ms": latency_ms,
                    "status_code": 500,
                },
            )
            raise

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["x-trace-id"] = trace_id
        access_logger.info(
            "request",
            extra={
                "trace_id": trace_id,
                "store_id": request.path_params.get("id") if request.path_params else store_id,
                "endpoint": request.url.path,
                "latency_ms": latency_ms,
                "event_count": getattr(request.state, "event_count", None),
                "status_code": status_code,
            },
        )
        return response
