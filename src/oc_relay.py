#!/usr/bin/env python3
"""Local OpenAI-compatible relay for LM Studio and OpenCode Go."""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid


LABEL = "com.gabrielblacher.oc-relay"
DEFAULT_TARGET = "https://opencode.ai/zen/go/v1"
DEFAULT_PORT = 8788
DEFAULT_CONFIG: dict[str, Any] = {
    "port": DEFAULT_PORT,
    "target_url": DEFAULT_TARGET,
    "fallback_model": None,
}
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def user_paths(home: Path | None = None) -> dict[str, Path]:
    home = home or Path.home()
    return {
        "config": home / ".config" / "oc-relay" / "config.json",
        "log": home / "Library" / "Logs" / "oc-relay.log",
        "plist": home / "Library" / "LaunchAgents" / f"{LABEL}.plist",
    }


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or user_paths()["config"]
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read config {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"config {path} must contain a JSON object")
        config.update(loaded)

    port = config.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("config port must be an integer from 1 to 65535")

    target_url = config.get("target_url")
    if not isinstance(target_url, str):
        raise ValueError("config target_url must be a string")
    target_url = target_url.rstrip("/")
    parsed = urllib.parse.urlsplit(target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("config target_url must be an absolute HTTP(S) URL")
    config["target_url"] = target_url

    fallback_model = config.get("fallback_model")
    if fallback_model is not None and (
        not isinstance(fallback_model, str) or not fallback_model.strip()
    ):
        raise ValueError("config fallback_model must be null or a non-empty string")
    return config


def apply_fallback_model(
    body: bytes | None, content_type: str, fallback_model: str | None
) -> bytes | None:
    if body is None or fallback_model is None or "application/json" not in content_type:
        return body
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict) or payload.get("model"):
        return body
    payload["model"] = fallback_model
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class RelayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: dict[str, Any],
        logger: logging.Logger,
    ) -> None:
        super().__init__(server_address, RelayHandler)
        self.target_url = config["target_url"]
        self.fallback_model = config.get("fallback_model")
        self.session_id = str(uuid.uuid4())
        self.logger = logger


class RelayHandler(http.server.BaseHTTPRequestHandler):
    server: RelayServer
    protocol_version = "HTTP/1.0"

    def _read_chunked(self) -> bytes:
        chunks: list[bytes] = []
        while True:
            size_line = self.rfile.readline().strip()
            if not size_line:
                continue
            try:
                size = int(size_line.split(b";", 1)[0], 16)
            except ValueError as exc:
                raise ValueError("invalid chunked request body") from exc
            if size == 0:
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return b"".join(chunks)
            chunks.append(self.rfile.read(size))
            if self.rfile.read(2) != b"\r\n":
                raise ValueError("invalid chunk delimiter")

    def _read_body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            return self._read_chunked()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        return self.rfile.read(length) if length else None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _health(self) -> None:
        self._send_json(
            200,
            {
                "status": "ok",
                "pid": os.getpid(),
                "port": self.server.server_port,
                "target_url": self.server.target_url,
            },
        )

    def _proxy(self) -> None:
        try:
            raw_body = self._read_body()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        raw_body = apply_fallback_model(
            raw_body,
            self.headers.get("Content-Type", ""),
            self.server.fallback_model,
        )
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        headers["x-opencode-session"] = self.server.session_id
        headers["User-Agent"] = "oc-relay/1.0"
        if raw_body is not None:
            headers["Content-Length"] = str(len(raw_body))

        url = self.server.target_url + self.path
        request = urllib.request.Request(
            url, data=raw_body, headers=headers, method=self.command
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                reader = getattr(response, "read1", response.read)
                while True:
                    chunk = reader(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            content_type = exc.headers.get("Content-Type", "application/json")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.server.logger.error("upstream request failed: %s", type(exc).__name__)
            self._send_json(502, {"error": "upstream unavailable"})
        finally:
            self.close_connection = True

    def do_GET(self) -> None:
        if urllib.parse.urlsplit(self.path).path == "/health":
            self._health()
        else:
            self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self._proxy()

    def log_message(self, format: str, *args: object) -> None:
        del format, args
        path = urllib.parse.urlsplit(self.path).path
        self.server.logger.info("%s %s", self.command, path)


def build_logger() -> logging.Logger:
    logger = logging.getLogger("oc-relay")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def run_server(config: dict[str, Any]) -> None:
    logger = build_logger()
    server = RelayServer(("127.0.0.1", config["port"]), config, logger)
    logger.info(
        "relay listening on http://127.0.0.1:%d -> %s",
        server.server_port,
        server.target_url,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("relay stopped")


def launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def launchctl_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"{launchctl_domain()}/{LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def start_service(config: dict[str, Any]) -> int:
    plist = user_paths()["plist"]
    if not plist.exists():
        print(f"LaunchAgent is not installed: {plist}", file=sys.stderr)
        return 1
    if launchctl_loaded():
        command = ["launchctl", "kickstart", "-k", f"{launchctl_domain()}/{LABEL}"]
    else:
        command = ["launchctl", "bootstrap", launchctl_domain(), str(plist)]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        return result.returncode
    for _ in range(20):
        if health(config):
            return 0
        time.sleep(0.25)
    print("oc-relay was loaded but did not become healthy", file=sys.stderr)
    return 1


def stop_service() -> int:
    if not launchctl_loaded():
        print("oc-relay is already stopped")
        return 0
    return subprocess.run(
        ["launchctl", "bootout", f"{launchctl_domain()}/{LABEL}"], check=False
    ).returncode


def restart_service(config: dict[str, Any]) -> int:
    result = stop_service()
    return start_service(config) if result == 0 else result


def health(config: dict[str, Any]) -> dict[str, Any] | None:
    url = f"http://127.0.0.1:{config['port']}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.load(response)
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def tail_log(path: Path, lines: int = 10) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def status_service(config: dict[str, Any]) -> int:
    paths = user_paths()
    loaded = launchctl_loaded()
    health_payload = health(config)
    print(f"Service: {'loaded' if loaded else 'stopped'}")
    print(f"Health:  {'ok' if health_payload else 'unavailable'}")
    print(f"Base URL: http://127.0.0.1:{config['port']}")
    if health_payload and health_payload.get("pid"):
        print(f"PID:      {health_payload['pid']}")
    print(f"Config:   {paths['config']}")
    print(f"Log:      {paths['log']}")
    recent = tail_log(paths["log"])
    print("Recent log lines:")
    if recent:
        for line in recent:
            print(f"  {line}")
    else:
        print("  (none)")
    return 0 if loaded and health_payload else 1


def show_logs(follow: bool) -> int:
    path = user_paths()["log"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    command = ["tail", "-n", "50"]
    if follow:
        command.append("-f")
    command.append(str(path))
    return subprocess.run(command, check=False).returncode


def list_models(config: dict[str, Any]) -> int:
    url = config["target_url"] + "/models"
    request = urllib.request.Request(url, headers={"User-Agent": "oc-relay/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Could not fetch models: {type(exc).__name__}", file=sys.stderr)
        return 1
    models = payload.get("data") if isinstance(payload, dict) else None
    identifiers = sorted(
        item["id"]
        for item in models or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )
    if not identifiers:
        print("The endpoint returned no model IDs.", file=sys.stderr)
        return 1
    print("\n".join(identifiers))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oc-relay")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help=argparse.SUPPRESS)
    subparsers.add_parser("start", help="load or restart the LaunchAgent")
    subparsers.add_parser("stop", help="stop and unload the LaunchAgent")
    subparsers.add_parser("restart", help="restart the LaunchAgent")
    subparsers.add_parser("status", help="show service health and recent logs")
    logs_parser = subparsers.add_parser("logs", help="show service logs")
    logs_parser.add_argument("-f", "--follow", action="store_true")
    subparsers.add_parser("models", help="print live OpenCode Go model IDs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except ValueError as exc:
        print(f"oc-relay: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        run_server(config)
        return 0
    if args.command == "start":
        return start_service(config)
    if args.command == "stop":
        return stop_service()
    if args.command == "restart":
        return restart_service(config)
    if args.command == "status":
        return status_service(config)
    if args.command == "logs":
        return show_logs(args.follow)
    if args.command == "models":
        return list_models(config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
