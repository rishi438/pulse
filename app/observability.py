"""Bounded, metadata-only HTTP and browser telemetry for FastAPI applications."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import math
import re
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_LOGGER = logging.getLogger("pulse.observability")
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "observability_request_id", default=None
)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_ERROR_TYPES = frozenset(
    {
        "RuntimeError",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "TimeoutError",
        "ConnectionError",
        "OSError",
        "AssertionError",
        "Exception",
        "ExceptionGroup",
        "CancelledError",
    }
)
_MAX_BODY_BYTES = 2048


class _ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event: Literal[
        "api_error", "api_slow", "js_error", "promise_rejection", "resource_error"
    ]
    transport: Literal["fetch", "xhr", "browser"]
    route_group: Literal[
        "auth",
        "portfolio",
        "quotes",
        "orders",
        "assistant",
        "admin",
        "roles",
        "users",
        "unknown",
    ]
    timing_scope: Literal["headers", "response", "event"]
    method: (
        Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "OTHER"]
        | None
    ) = None
    status: int | None = Field(default=None, ge=0, le=599)
    duration_ms: float | None = Field(
        default=None, ge=0, le=600000, allow_inf_nan=False
    )
    request_id: UUID | None = None
    error_type: (
        Literal[
            "Error",
            "TypeError",
            "RangeError",
            "ReferenceError",
            "SyntaxError",
            "URIError",
            "EvalError",
            "NetworkError",
            "AbortError",
            "Other",
        ]
        | None
    ) = None
    line: int | None = Field(default=None, ge=0, le=10000000)
    column: int | None = Field(default=None, ge=0, le=10000000)


class _RateLimit:
    """One bounded process-wide counter, with no user/IP dictionary."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window = time.monotonic()
        self._count = 0

    def accept(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._window >= 60:
                self._window, self._count = now, 0
            if self._count >= 120:
                return False
            self._count += 1
            return True


class _ResponseInterrupted(RuntimeError):
    """Public, sanitized stream failure; deliberately omits the original cause."""


def _emit(event: dict[str, object]) -> None:
    _LOGGER.info("%s", json.dumps(event, separators=(",", ":"), allow_nan=False))


def _base_event(service: str, event: str, source: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event,
        "source": source,
        "service": service,
    }


def _request_id(scope: Scope) -> str:
    values = [
        value
        for key, value in scope.get("headers", [])
        if key.lower() == b"x-request-id"
    ]
    if len(values) == 1 and len(values[0]) <= 45:
        try:
            return str(UUID(values[0].decode("ascii")))
        except (ValueError, UnicodeDecodeError):
            return str(uuid4())
    return str(uuid4())


class _RequestTelemetry:
    def __init__(self, app: ASGIApp, service_name: str, slow_ms: float) -> None:
        self.app, self.service, self.slow_ms = app, service_name, slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id(scope)
        token = _REQUEST_ID.set(request_id)
        started_at = time.perf_counter()
        finished_at: float | None = None
        started = completed = False
        status = 500
        error_type: str | None = None

        async def observed_send(message: Message) -> None:
            nonlocal status, started, completed, finished_at
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
                await send(message)
                started = True
                return
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                completed = True
                finished_at = time.perf_counter()

        try:
            await self.app(scope, receive, observed_send)
        except asyncio.CancelledError:
            error_type = "CancelledError"
            raise
        except Exception as exc:  # noqa: BLE001 - sanitizing HTTP failure boundary
            # This HTTP boundary owns reporting; never send exception messages or
            # the original traceback to Uvicorn's outer error handler.
            name = type(exc).__name__
            error_type = name if name in _ERROR_TYPES else "Other"
            if started:
                raise _ResponseInterrupted("HTTP response interrupted") from None
            await JSONResponse({"detail": "Internal server error"}, status_code=500)(
                scope, receive, observed_send
            )
        finally:
            duration_ms = round(
                ((finished_at or time.perf_counter()) - started_at) * 1000, 3
            )
            route = getattr(scope.get("route"), "path", None)
            event = _base_event(self.service, "backend_request", "backend")
            event.update(
                request_id=request_id,
                method=scope.get("method")
                if scope.get("method") in _METHODS
                else "OTHER",
                route=route if isinstance(route, str) else "unmatched",
                status=status,
                duration_ms=duration_ms,
                slow=duration_ms >= self.slow_ms,
                completed=completed,
            )
            if error_type is not None:
                event["error_type"] = error_type
            try:
                # Rate-limit rejection must not become another unbounded log
                # source when a faulty browser repeatedly sends telemetry.
                if not (route == "/api/client-events" and status == 429):
                    _emit(event)
            finally:
                _REQUEST_ID.reset(token)


def install_observability(
    app: FastAPI, service_name: str = "pulse", slow_ms: float = 1000
) -> None:
    """Install once, before the app starts and before catch-all static mounts.

    Logs JSON metadata to stdout and adds ``POST /api/client-events``. Timings
    include response streaming. The browser endpoint is unauthenticated, bounded
    to 2 KiB and 120 requests/minute per installed app process. Events reported by
    clients are explicitly untrusted. Existing application logging is untouched.
    """
    if getattr(app.state, "pulse_observability_installed", False):
        return
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", service_name):
        raise ValueError("service_name must be a short service identifier")
    if isinstance(slow_ms, bool) or not math.isfinite(slow_ms) or slow_ms < 0:
        raise ValueError("slow_ms must be finite and nonnegative")
    if any(
        getattr(route, "path", None) == "/api/client-events" for route in app.routes
    ):
        raise ValueError("/api/client-events is already registered")
    if not any(
        getattr(handler, "_pulse_observability", False) for handler in _LOGGER.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._pulse_observability = True
        _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    limiter = _RateLimit()

    async def client_events(request: Request) -> Response:
        if not limiter.accept():
            return Response(status_code=429, headers={"Retry-After": "60"})
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return JSONResponse({"detail": "Invalid telemetry"}, status_code=400)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_BODY_BYTES:
                return JSONResponse({"detail": "Telemetry too large"}, status_code=413)
            body.extend(chunk)
        try:
            payload = _ClientEvent.model_validate_json(body)
        except ValidationError:
            return JSONResponse({"detail": "Invalid telemetry"}, status_code=400)
        event = _base_event(service_name, "frontend_event", "frontend")
        fields = payload.model_dump(mode="json", exclude_none=True)
        event.update(fields)
        event["client_event"] = fields["event"]
        event["event"] = "frontend_event"
        event["trust"] = "client_reported"
        event["related_request_id"] = fields.get("request_id")
        event["request_id"] = _REQUEST_ID.get()
        _emit(event)
        return Response(status_code=204)

    app.add_api_route(
        "/api/client-events", client_events, methods=["POST"], include_in_schema=False
    )
    app.add_middleware(
        _RequestTelemetry, service_name=service_name, slow_ms=float(slow_ms)
    )
    app.state.pulse_observability_installed = True
