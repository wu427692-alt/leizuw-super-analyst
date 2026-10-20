"""Dedicated container-loopback Jev relay; the credential arrives only on stdin.

The parent must keep stdin open. EOF ends the relay so a dead supervisor cannot
leave an orphan listener holding a stale key. No credential is read from a file,
environment variable, command argument, request body, or host keychain here.
"""
from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.services.jev_decisions_client import JevClientError, JevDecisionsClient, _validate_state
from src.services.jev_relay_client import (
    MAX_BODY_BYTES, MAX_RESPONSE_BYTES, PROTOCOL, decode_json, safe_error_code,
    validate_relay_result, validate_usage,
)

MAX_KEY_BYTES = 8192


def read_key_frame(stream) -> str:
    raw = stream.readline(MAX_KEY_BYTES + 2)
    if not raw.endswith(b"\n") or not 1 <= len(raw) - 1 <= MAX_KEY_BYTES:
        raise JevClientError("missing_or_invalid_credential")
    value = raw[:-1]
    if any(byte < 33 or byte > 126 for byte in value):
        raise JevClientError("missing_or_invalid_credential")
    return value.decode("ascii")


class RelayApplication:
    def __init__(self, client, max_concurrent=2, event_client=None, live_context_client=None):
        self.client = client
        self.event_client = event_client
        self.live_context_client = live_context_client
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def health(self):
        return {"ready": True, "protocol": PROTOCOL, "model": self.client.model,
                "endpoint": self.client.endpoint,
                "input_price_per_million": self.client.input_price_per_million}

    @staticmethod
    def error(code, usage=None):
        error = {"code": safe_error_code(code)}
        if usage is not None:
            try:
                error["usage"] = validate_usage(usage)
            except Exception:
                pass
        return {"error": error}

    def decide(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"state"}:
            return 400, self.error("relay_invalid_request")
        try:
            candidate = payload["state"]
            use_event = isinstance(candidate, dict) and candidate.get("schema") == "cb-t0-factors-v1"
            client, label = self.client, "price_volume_only"
            if isinstance(candidate, dict) and candidate.get("schema") == "jev-live-context-v1":
                from src.services.jev_live_context_client import LABEL, validate_live_context_state
                if self.live_context_client is None:
                    return 503, self.error("relay_unavailable")
                state, client, label = validate_live_context_state(candidate), self.live_context_client, LABEL
            elif use_event:
                from src.services.jev_cb_decisions_client import validate_cb_state
                if self.event_client is None:
                    return 503, self.error("relay_unavailable")
                state, client, label = validate_cb_state(candidate), self.event_client, "price_volume_factors"
            else:
                state = _validate_state(candidate)
        except Exception:
            return 400, self.error("invalid_price_volume_state")
        if not self._slots.acquire(blocking=False):
            return 503, self.error("relay_busy")
        try:
            result = validate_relay_result(client.decide(state), client.model, label=label)
            return 200, result
        except JevClientError as exc:
            return 502, self.error(exc.code, exc.usage)
        except Exception:
            return 502, self.error("relay_internal_error")
        finally:
            self._slots.release()


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "JevRelay/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.settimeout(5)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        super().setup()

    def log_message(self, format, *args):
        # Never log request lines, paths, bodies, credentials, or upstream text.
        pass

    def reply(self, status, payload):
        encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            status = 502
            encoded = b'{"error":{"code":"response_too_large"}}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        # A rejected request may leave unread body bytes; close rather than
        # interpreting them as a second request on the keep-alive connection.
        if status >= 400:
            self.close_connection = True
        self.send_header("Connection", "close" if self.close_connection else "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except OSError:
            pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, RelayApplication.error("relay_invalid_request"))

    def do_GET(self):
        if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Length", []):
            self.reply(400, RelayApplication.error("relay_invalid_request"))
            return
        if self.path == "/health":
            self.reply(200, self.server.application.health())
        else:
            self.reply(404, RelayApplication.error("relay_invalid_request"))

    def do_POST(self):
        if self.path != "/decide":
            self.reply(404, RelayApplication.error("relay_invalid_request"))
            return
        # Reject ambiguous HTTP framing rather than forwarding it upstream.
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            self.reply(400, RelayApplication.error("relay_invalid_request"))
            return
        try:
            length = int(lengths[0])
        except (TypeError, ValueError):
            length = -1
        if not 0 < length <= MAX_BODY_BYTES:
            self.reply(413, RelayApplication.error("request_too_large"))
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.reply(415, RelayApplication.error("relay_invalid_request"))
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError
            payload = decode_json(body)
        except Exception:
            self.reply(400, RelayApplication.error("relay_invalid_request"))
            return
        status, result = self.server.application.decide(payload)
        self.reply(status, result)


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # HTTP server's default traceback can retain request/provider objects.
        pass


def serve_from_stdin(port: int, stream, *, client_factory=JevDecisionsClient, server_factory=RelayServer):
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise JevClientError("invalid_configuration")
    key = read_key_frame(stream)
    client = client_factory(api_key=key)
    from src.services.jev_cb_decisions_client import JevCBDecisionsClient
    event_client = JevCBDecisionsClient(api_key=key)
    from src.services.jev_live_context_client import JevLiveContextDecisionsClient
    live_context_client = JevLiveContextDecisionsClient(api_key=key)
    key = None
    server = server_factory(("127.0.0.1", port), RelayHandler)
    server.application = RelayApplication(client, event_client=event_client, live_context_client=live_context_client)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
    thread.start()
    try:
        # No additional credential or control frames are accepted. The open pipe
        # is the parent lifetime lease; EOF or any unexpected byte ends it.
        stream.read(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for current in (client, event_client, live_context_client):
            close = getattr(current, "close", None)
            if close is not None:
                close()
        client = event_client = live_context_client = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jev loopback relay; credential must arrive on stdin")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)

    def stop(signum, frame):
        raise KeyboardInterrupt

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    try:
        serve_from_stdin(args.port, sys.stdin.buffer)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Jev relay unavailable; credential or runtime setup failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
