from __future__ import annotations

import http.client
import http.server
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oc_relay import (  # noqa: E402
    DEFAULT_TARGET,
    RelayServer,
    apply_fallback_model,
    load_config,
)


class ConfigTests(unittest.TestCase):
    def test_loads_defaults_and_rejects_invalid_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(load_config(path)["target_url"], DEFAULT_TARGET)
            path.write_text('{"port": 0}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "port"):
                load_config(path)

    def test_fallback_model_only_fills_missing_json_model(self) -> None:
        body = apply_fallback_model(b'{"messages":[]}', "application/json", "model-a")
        self.assertEqual(json.loads(body or b"{}"), {"messages": [], "model": "model-a"})
        existing = b'{"model":"model-b"}'
        self.assertIs(
            apply_fallback_model(existing, "application/json", "model-a"), existing
        )


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    received_headers: dict[str, str] = {}
    received_body = b""
    first_sent = threading.Event()
    release_second = threading.Event()

    def do_POST(self) -> None:
        type(self).received_headers = dict(self.headers.items())
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received_body = self.rfile.read(length)
        first = b"data: first\n\n"
        second = b"data: second\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(first)
        self.wfile.flush()
        type(self).first_sent.set()
        type(self).release_second.wait(2)
        self.wfile.write(second)
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class RelayIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        UpstreamHandler.received_headers = {}
        UpstreamHandler.received_body = b""
        UpstreamHandler.first_sent.clear()
        UpstreamHandler.release_second.clear()
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), UpstreamHandler
        )
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()
        self.log_stream = io.StringIO()
        logger = logging.getLogger(f"test-relay-{id(self)}")
        logger.handlers = [logging.StreamHandler(self.log_stream)]
        logger.setLevel(logging.INFO)
        config = {
            "port": 0,
            "target_url": f"http://127.0.0.1:{self.upstream.server_port}",
            "fallback_model": "fallback-model",
        }
        self.relay = RelayServer(("127.0.0.1", 0), config, logger)
        self.relay_thread = threading.Thread(target=self.relay.serve_forever, daemon=True)
        self.relay_thread.start()

    def tearDown(self) -> None:
        UpstreamHandler.release_second.set()
        self.relay.shutdown()
        self.relay.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def test_health_is_local_and_reports_runtime(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.relay.server_port}/health", timeout=2
        ) as response:
            payload = json.load(response)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["port"], self.relay.server_port)
        self.assertEqual(self.relay.server_address[0], "127.0.0.1")

    def test_injects_headers_applies_fallback_and_streams_incrementally(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.relay.server_port, timeout=2
        )
        connection.request(
            "POST",
            "/chat/completions?trace=not-logged",
            body=b'{"messages":[]}',
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        self.assertTrue(UpstreamHandler.first_sent.wait(1))

        first = b"data: first\n\n"
        received: list[bytes] = []
        read_done = threading.Event()

        def read_first() -> None:
            received.append(response.read(len(first)))
            read_done.set()

        reader = threading.Thread(target=read_first, daemon=True)
        reader.start()
        self.assertTrue(read_done.wait(1), "relay buffered the streaming response")
        self.assertEqual(received, [first])
        UpstreamHandler.release_second.set()
        self.assertEqual(response.read(), b"data: second\n\n")
        connection.close()

        self.assertEqual(
            UpstreamHandler.received_headers["Authorization"], "Bearer test-secret"
        )
        received_headers = {
            key.lower(): value for key, value in UpstreamHandler.received_headers.items()
        }
        self.assertEqual(
            received_headers["x-opencode-session"], self.relay.session_id
        )
        self.assertEqual(
            json.loads(UpstreamHandler.received_body),
            {"messages": [], "model": "fallback-model"},
        )
        logs = self.log_stream.getvalue()
        self.assertNotIn("test-secret", logs)
        self.assertNotIn("trace=not-logged", logs)


if __name__ == "__main__":
    unittest.main()
