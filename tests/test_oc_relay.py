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
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oc_relay import (  # noqa: E402
    DEFAULT_TARGET,
    RelayServer,
    apply_fallback_model,
    apply_selected_model,
    clear_selected_model,
    load_config,
    read_selected_model,
    write_selected_model,
    ModelSelectionError,
    main,
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

    def test_selected_model_persists_and_rejects_unsupported_or_corrupt_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "selected-model"
            self.assertIsNone(read_selected_model(path))
            write_selected_model(path, "mimo-v2.6-flash")
            self.assertEqual(read_selected_model(path), "mimo-v2.6-flash")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ModelSelectionError):
                write_selected_model(path, "minimax-m3")
            path.write_text("unknown-model\n", encoding="utf-8")
            with self.assertRaises(ModelSelectionError):
                read_selected_model(path)
            path.write_text("mimo-v2.6-flash\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(ModelSelectionError):
                read_selected_model(path)
            clear_selected_model(path)
            self.assertIsNone(read_selected_model(path))

    def test_model_cli_rejects_messages_models_and_clears_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("oc_relay.user_paths", return_value={"selection": Path(directory) / "selected-model"}):
                self.assertEqual(main(["model", "set", "minimax-m3"]), 1)
                self.assertEqual(main(["model", "set", "glm-5.3"]), 0)
                self.assertEqual(read_selected_model(Path(directory) / "selected-model"), "glm-5.3")
                self.assertEqual(main(["model", "clear"]), 0)
                self.assertIsNone(read_selected_model(Path(directory) / "selected-model"))

    def test_selected_model_changes_only_model_field(self) -> None:
        body = b'{"model":"client-model","messages":[],"stream":true}'
        changed = apply_selected_model(body, "application/json", "kimi-k2.7-code")
        self.assertEqual(json.loads(changed), {
            "model": "kimi-k2.7-code", "messages": [], "stream": True,
        })
        with self.assertRaises(ModelSelectionError):
            apply_selected_model(b"not JSON", "application/json", "kimi-k2.7-code")


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    received_headers: dict[str, str] = {}
    received_body = b""
    received_bodies: list[bytes] = []
    first_sent = threading.Event()
    release_second = threading.Event()

    def do_POST(self) -> None:
        type(self).received_headers = dict(self.headers.items())
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received_body = self.rfile.read(length)
        type(self).received_bodies.append(type(self).received_body)
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
        UpstreamHandler.received_bodies = []
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
        self.state_directory = tempfile.TemporaryDirectory()
        self.selection_path = Path(self.state_directory.name) / "selected-model"
        self.relay = RelayServer(
            ("127.0.0.1", 0), config, logger,
            selection_path=self.selection_path,
        )
        self.relay_thread = threading.Thread(target=self.relay.serve_forever, daemon=True)
        self.relay_thread.start()

    def tearDown(self) -> None:
        UpstreamHandler.release_second.set()
        self.relay.shutdown()
        self.relay.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.state_directory.cleanup()

    def test_health_is_local_and_reports_runtime(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.relay.server_port}/health", timeout=2
        ) as response:
            payload = json.load(response)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["port"], self.relay.server_port)
        self.assertEqual(payload["model_selection"]["version"], 1)
        self.assertIsNone(payload["model_selection"]["selected"])
        self.assertIn("mimo-v2.6-flash", payload["model_selection"]["models"])
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
            received_headers["host"],
            f"127.0.0.1:{self.upstream.server_port}",
        )
        self.assertEqual(
            json.loads(UpstreamHandler.received_body),
            {"messages": [], "model": "fallback-model"},
        )
        logs = self.log_stream.getvalue()
        self.assertNotIn("test-secret", logs)
        self.assertNotIn("trace=not-logged", logs)

    def test_selection_overrides_next_chat_request_but_not_other_routes(self) -> None:
        write_selected_model(self.selection_path, "mimo-v2.6-flash")
        connection = http.client.HTTPConnection("127.0.0.1", self.relay.server_port, timeout=3)
        connection.request(
            "POST", "/chat/completions",
            body=b'{"model":"client-model","messages":[],"stream":true}',
            headers={"Content-Type": "application/json"},
        )
        first = connection.getresponse()
        self.assertTrue(UpstreamHandler.first_sent.wait(1))
        self.assertEqual(json.loads(UpstreamHandler.received_bodies[0])["model"], "mimo-v2.6-flash")

        write_selected_model(self.selection_path, "kimi-k2.7-code")
        second_connection = http.client.HTTPConnection("127.0.0.1", self.relay.server_port, timeout=3)
        second_connection.request(
            "POST", "/chat/completions",
            body=b'{"model":"client-model","messages":[],"stream":true}',
            headers={"Content-Type": "application/json"},
        )
        second = second_connection.getresponse()
        self.assertEqual(json.loads(UpstreamHandler.received_bodies[1])["model"], "kimi-k2.7-code")
        UpstreamHandler.release_second.set()
        self.assertIn(b"data: second", first.read())
        second.read()
        connection.close()
        second_connection.close()

        other_connection = http.client.HTTPConnection("127.0.0.1", self.relay.server_port, timeout=3)
        other_connection.request(
            "POST", "/responses", body=b'{"model":"client-model"}',
            headers={"Content-Type": "application/json"},
        )
        other_connection.getresponse().read()
        self.assertEqual(json.loads(UpstreamHandler.received_bodies[2])["model"], "client-model")
        other_connection.close()

        clear_selected_model(self.selection_path)
        passthrough = http.client.HTTPConnection("127.0.0.1", self.relay.server_port, timeout=3)
        passthrough.request(
            "POST", "/chat/completions", body=b'{"model":"client-model"}',
            headers={"Content-Type": "application/json"},
        )
        passthrough.getresponse().read()
        self.assertEqual(json.loads(UpstreamHandler.received_bodies[3])["model"], "client-model")
        passthrough.close()


if __name__ == "__main__":
    unittest.main()
