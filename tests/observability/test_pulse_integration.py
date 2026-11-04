"""Exercise Pulse wiring without lifespan, database, identity, or trading calls."""
import io
import json
import logging
import unittest
from types import ModuleType
from unittest.mock import patch

import httpx

# This environment lacks the Azure SDK. Isolate the provider boundary; these
# tests exercise HTTP wiring and must never initialize identity or call Foundry.
provider = ModuleType("app.foundry_client")
provider.FoundryError = RuntimeError

def unexpected_provider_call(*args, **kwargs):
    raise AssertionError("No provider calls allowed")

provider.foundry_reply = unexpected_provider_call
with patch.dict("sys.modules", {"app.foundry_client": provider}):
    from app.main import app


class PulseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingestion_precedes_static_mount_and_preserves_cors(self):
        output = io.StringIO()
        handlers = logging.getLogger("pulse.observability").handlers
        streams = [(handler, handler.stream) for handler in handlers]
        for handler, _ in streams:
            handler.setStream(output)
        try:
            with patch("app.main.database_connection", side_effect=AssertionError("No database allowed")):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://pulse.test"
                ) as client:
                    page = await client.get("/")
                    self.assertEqual(page.status_code, 200)
                    self.assertLess(page.text.index('/observability.js'), page.text.index('https://code.jquery.com'))
                    script = await client.get("/observability.js")
                    self.assertEqual(script.status_code, 200)
                    response = await client.post("/api/client-events", json={
                        "event": "js_error", "transport": "browser",
                        "route_group": "unknown", "timing_scope": "event",
                    }, headers={"Origin": "http://pulse.test"})
                    self.assertEqual(response.status_code, 204)
                    self.assertEqual(response.headers["access-control-allow-origin"], "*")
                    self.assertIn("x-request-id", response.headers)
                    preflight = await client.options("/api/client-events", headers={
                        "Origin": "http://pulse.test", "Access-Control-Request-Method": "POST",
                    })
                    self.assertEqual(preflight.status_code, 200)
                    self.assertIn("x-request-id", preflight.headers)
                    missing = await client.get("/missing/synthetic-private-path?private=marker")
                    self.assertEqual(missing.status_code, 404)
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            frontend = [row for row in rows if row["event"] == "frontend_event"]
            self.assertEqual(len(frontend), 1)
            self.assertEqual(frontend[0]["trust"], "client_reported")
            self.assertNotIn("synthetic-private-path", output.getvalue())
            self.assertNotIn("private=marker", output.getvalue())
        finally:
            for handler, stream in streams:
                handler.setStream(stream)
