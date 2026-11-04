"""Behavioral tests: synthetic requests only, no external services or real data."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import traceback
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from app.observability import install_observability

SECRET = "synthetic-private-marker-123"


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = FastAPI()
        install_observability(self.app, slow_ms=10)
        self.output = io.StringIO()
        self.streams = []
        for handler in logging.getLogger("pulse.observability").handlers:
            if isinstance(handler, logging.StreamHandler):
                self.streams.append((handler, handler.stream))
                handler.setStream(self.output)

        @self.app.get("/items/{item_id}")
        async def item(item_id: str, request: Request) -> dict:
            await request.body()
            return {"item": item_id}

        @self.app.get("/denied")
        async def denied() -> None:
            raise HTTPException(status_code=401, detail=SECRET)

        @self.app.get("/broken")
        async def broken() -> None:
            raise RuntimeError(SECRET)

        @self.app.get("/stream")
        async def stream() -> StreamingResponse:
            async def chunks():
                yield b"first"
                await asyncio.sleep(0.025)
                yield b"last"

            return StreamingResponse(chunks())

        @self.app.get("/broken-stream")
        async def broken_stream() -> StreamingResponse:
            async def chunks():
                yield b"first"
                raise RuntimeError(SECRET)

            return StreamingResponse(chunks())

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://synthetic.test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        for handler, stream in self.streams:
            handler.setStream(stream)

    def events(self, event: str | None = None) -> list[dict]:
        rows = [json.loads(line) for line in self.output.getvalue().splitlines()]
        return [row for row in rows if event is None or row["event"] == event]

    def payload(self, **updates) -> dict:
        return {
            "event": "api_slow",
            "transport": "fetch",
            "route_group": "portfolio",
            "timing_scope": "response",
            "method": "GET",
            "status": 200,
            "duration_ms": 12.3,
            **updates,
        }

    async def test_request_metadata_redacts_body_query_headers_and_route_values(
        self,
    ) -> None:
        response = await self.client.request(
            "GET",
            f"/items/{SECRET}?token={SECRET}",
            content=SECRET,
            headers={
                "Authorization": f"Bearer {SECRET}",
                "Cookie": f"session={SECRET}",
            },
        )
        self.assertEqual(response.status_code, 200)
        row = self.events()[0]
        self.assertEqual(row["route"], "/items/{item_id}")
        self.assertEqual(row["status"], 200)
        self.assertTrue(row["completed"])
        self.assertEqual(row["request_id"], response.headers["x-request-id"])
        UUID(row["request_id"])
        self.assertNotIn(SECRET, self.output.getvalue())
        self.assertEqual(
            set(row),
            {
                "schema_version",
                "timestamp",
                "event",
                "source",
                "service",
                "request_id",
                "method",
                "route",
                "status",
                "duration_ms",
                "slow",
                "completed",
            },
        )

    async def test_uuid_headers_are_canonicalized_or_replaced(self) -> None:
        wanted = str(uuid4())
        for incoming, expected in ((wanted.upper(), wanted), (SECRET, None)):
            response = await self.client.get(
                "/items/1", headers={"X-Request-ID": incoming}
            )
            actual = response.headers["x-request-id"]
            self.assertEqual(str(UUID(actual)), actual)
            if expected:
                self.assertEqual(actual, expected)
            else:
                self.assertNotEqual(actual, incoming)
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_duplicate_request_ids_are_replaced(self) -> None:
        ids = [str(uuid4()), str(uuid4())]
        response = await self.client.get(
            "/items/1", headers=[("X-Request-ID", value) for value in ids]
        )
        self.assertNotIn(response.headers["x-request-id"], ids)

    async def test_unmatched_path_is_never_logged(self) -> None:
        response = await self.client.get(f"/missing/{SECRET}?password={SECRET}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.events()[0]["route"], "unmatched")
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_http_error_preserves_status_without_logging_detail(self) -> None:
        response = await self.client.get("/denied")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.events()[0]["status"], 401)
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_unhandled_error_has_generic_response_and_no_original_exception(
        self,
    ) -> None:
        response = await self.client.get("/broken")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal server error"})
        row = self.events()[0]
        self.assertEqual(row["error_type"], "RuntimeError")
        self.assertTrue(row["completed"])
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_full_stream_duration_marks_slow(self) -> None:
        response = await self.client.get("/stream")
        self.assertEqual(response.content, b"firstlast")
        row = self.events()[0]
        self.assertGreaterEqual(row["duration_ms"], 20)
        self.assertTrue(row["slow"])
        self.assertTrue(row["completed"])

    async def test_post_response_work_does_not_inflate_response_duration(self) -> None:
        clock = [0.0]

        async def background() -> None:
            clock[0] = 1000.0

        @self.app.get("/background")
        async def with_background() -> Response:
            clock[0] = 0.001
            return Response(background=BackgroundTask(background))

        with patch("app.observability.time.perf_counter", side_effect=lambda: clock[0]):
            response = await self.client.get("/background")
        self.assertEqual(response.status_code, 200)
        row = self.events()[0]
        self.assertEqual(row["duration_ms"], 1.0)
        self.assertTrue(row["completed"])

    async def test_stream_failure_has_sanitized_traceback_and_incomplete_status(
        self,
    ) -> None:
        try:
            await self.client.get("/broken-stream")
        except RuntimeError as exc:
            text = "".join(traceback.format_exception(exc))
        else:
            self.fail("Broken stream should fail the transport")
        self.assertNotIn(SECRET, text)
        self.assertIn("HTTP response interrupted", text)
        self.assertFalse(self.events()[0]["completed"])
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_cancellation_is_preserved_and_logged_incomplete(self) -> None:
        entered = asyncio.Event()

        @self.app.get("/cancel")
        async def cancel() -> Response:
            entered.set()
            await asyncio.Event().wait()
            return Response()

        task = asyncio.create_task(self.client.get("/cancel"))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        row = self.events()[0]
        self.assertFalse(row["completed"])
        self.assertEqual(row["error_type"], "CancelledError")

    async def test_cancellation_during_stream_preserves_sent_status(self) -> None:
        streaming = asyncio.Event()

        @self.app.get("/cancel-stream")
        async def cancel_stream() -> StreamingResponse:
            async def chunks():
                yield b"first"
                streaming.set()
                await asyncio.Event().wait()

            return StreamingResponse(chunks())

        task = asyncio.create_task(self.client.get("/cancel-stream"))
        await asyncio.wait_for(streaming.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        row = self.events()[0]
        self.assertFalse(row["completed"])
        self.assertEqual(row["status"], 200)
        self.assertEqual(row["error_type"], "CancelledError")

    async def test_telemetry_correlation_and_client_trust(self) -> None:
        related = str(uuid4())
        ingestion = str(uuid4())
        response = await self.client.post(
            "/api/client-events",
            json=self.payload(request_id=related),
            headers={"X-Request-ID": ingestion, "Cookie": f"session={SECRET}"},
        )
        self.assertEqual(response.status_code, 204)
        row = self.events("frontend_event")[0]
        self.assertEqual(row["request_id"], ingestion)
        self.assertEqual(row["related_request_id"], related)
        self.assertEqual(row["trust"], "client_reported")
        self.assertEqual(row["client_event"], "api_slow")
        self.assertEqual(row["source"], "frontend")
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_forged_fields_and_invalid_values_are_rejected_without_echo(
        self,
    ) -> None:
        invalid = [
            {"message": SECRET},
            {"url": SECRET},
            {"source": "backend"},
            {"event": SECRET},
            {"request_id": SECRET},
            {"status": 600},
            {"status": True},
            {"duration_ms": float("nan")},
            {"duration_ms": float("inf")},
            {"duration_ms": -1},
            {"line": 10000001},
            {"column": -1},
            {"error_type": SECRET},
        ]
        for update in invalid:
            with self.subTest(update=update):
                response = await self.client.post(
                    "/api/client-events",
                    content=json.dumps(self.payload(**update)),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"detail": "Invalid telemetry"})
        self.assertFalse(self.events("frontend_event"))
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_telemetry_body_size_and_content_type_are_bounded(self) -> None:
        valid = json.dumps(self.payload()).encode()
        response = await self.client.post(
            "/api/client-events",
            content=valid + b" " * (2048 - len(valid)),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        self.assertEqual(response.status_code, 204)
        self.output.truncate(0)
        self.output.seek(0)

        async def chunks():
            yield b"x" * 1024
            yield b"x" * 1025

        response = await self.client.post(
            "/api/client-events",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        response = await self.client.post(
            "/api/client-events", content=json.dumps(self.payload())
        )
        self.assertEqual(response.status_code, 400)
        response = await self.client.post(
            "/api/client-events",
            content=b"{invalid",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.events("frontend_event"))

    async def test_telemetry_rate_limit_and_rejected_request_log_volume(self) -> None:
        for _ in range(120):
            response = await self.client.post("/api/client-events", json=self.payload())
            self.assertEqual(response.status_code, 204)
        count = len(self.events())
        for _ in range(4):
            response = await self.client.post("/api/client-events", json=self.payload())
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(len(self.events()), count)

    async def test_installation_is_idempotent(self) -> None:
        install_observability(self.app)
        response = await self.client.get("/items/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(
            len(
                [
                    route
                    for route in self.app.routes
                    if route.path == "/api/client-events"
                ]
            ),
            1,
        )

    async def test_non_http_lifespan_failures_are_not_suppressed(self) -> None:
        @asynccontextmanager
        async def lifespan(app):
            raise RuntimeError("synthetic-startup-failure")
            yield

        app = FastAPI(lifespan=lifespan)
        install_observability(app)

        messages = []

        async def receive() -> dict:
            return {"type": "lifespan.startup"}

        async def send(message: dict) -> None:
            messages.append(message)

        with self.assertRaisesRegex(RuntimeError, "synthetic-startup-failure"):
            await app({"type": "lifespan", "state": {}}, receive, send)
        self.assertTrue(
            any(message["type"] == "lifespan.startup.failed" for message in messages)
        )


if __name__ == "__main__":
    unittest.main()
