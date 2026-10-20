"""Reusable private model worker with a hard deadline for every paid request."""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import threading
import time

from src.services.jev_cb_decisions_client import JevCBDecisionsClient, validate_cb_state
from src.services.jev_live_context_client import (
    LABEL, SCHEMA, JevLiveContextDecisionsClient, validate_live_context_state,
)
from src.services.jev_decisions_client import JevClientError
from src.services.jev_relay_client import decode_json, safe_error_code, validate_relay_result, validate_usage

MAX_STATE_BYTES = 96000
MAX_RESULT_BYTES = 65536


def _prepare(state):
    if isinstance(state, dict) and state.get("schema") == SCHEMA:
        return validate_live_context_state(state), LABEL
    return validate_cb_state(state), "price_volume_factors"


def _worker_command():
    return [sys.executable, "-u", "-m", "src.services.jev_live_t_client", "--worker"]


class BoundedLiveDecisionClient:
    def __init__(self):
        config = JevCBDecisionsClient(api_key="")
        self.model = config.model
        self.input_price_per_million = config.input_price_per_million
        self.timeout = min(config.timeout_seconds + 12, 40)
        self._process = None
        self._process_lock = threading.Lock()
        self._flight = threading.Lock()
        self._closed = False

    def build_budget_body(self, state):
        """Return the exact typed provider envelope used for input-cost reservation."""
        state, label = _prepare(state)
        cls = JevLiveContextDecisionsClient if label == LABEL else JevCBDecisionsClient
        config = cls(api_key="", model=self.model, input_price_per_million=self.input_price_per_million)
        return {"state": state, "questions": config._build_questions()}

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=.3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
            except ProcessLookupError:
                pass
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()

    def _discard(self, process):
        with self._process_lock:
            if self._process is not process:
                return
            self._process = None
        self._terminate(process)

    def close(self):
        # Never wait on _flight: manual stop can kill a blocked request promptly.
        with self._process_lock:
            self._closed = True
            process, self._process = self._process, None
        self._terminate(process)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _get_process(self):
        with self._process_lock:
            if self._closed:
                raise JevClientError("invalid_configuration")
            if self._process is None:
                self._process = subprocess.Popen(_worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                 stderr=subprocess.DEVNULL, bufsize=0, close_fds=True)
                os.set_blocking(self._process.stdin.fileno(), False)
                os.set_blocking(self._process.stdout.fileno(), False)
            return self._process

    def _exchange(self, process, request, deadline):
        # Both pipe writes and reads are nonblocking, including a worker stuck
        # before reading stdin. No timeout path ever resubmits this request.
        response, sent = bytearray(), 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JevClientError("timeout")
                ready = selector.select(remaining)
                if not ready:
                    raise JevClientError("timeout")
                for event, _ in ready:
                    if event.fileobj is process.stdin:
                        try:
                            count = os.write(process.stdin.fileno(), request[sent:])
                        except BlockingIOError:
                            continue
                        if not count:
                            raise JevClientError("transport_or_response_error")
                        sent += count
                        if sent == len(request):
                            selector.unregister(process.stdin)
                    else:
                        try:
                            chunk = os.read(process.stdout.fileno(), MAX_RESULT_BYTES + 2)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            raise JevClientError("transport_or_response_error")
                        response.extend(chunk)
                        if len(response) > MAX_RESULT_BYTES + 1:
                            raise JevClientError("response_too_large")
                        if b"\n" in response:
                            if sent != len(request) or response.count(b"\n") != 1 or response[-1:] != b"\n":
                                raise JevClientError("invalid_response")
                            if time.monotonic() > deadline:
                                raise JevClientError("timeout")
                            return bytes(response[:-1])

    def decide(self, state):
        state, label = _prepare(state)
        request = json.dumps(state, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(request) > MAX_STATE_BYTES:
            raise JevClientError("request_too_large")
        if not self._flight.acquire(blocking=False):
            raise JevClientError("relay_busy")
        process = None
        try:
            deadline = time.monotonic() + self.timeout
            process = self._get_process()
            raw = decode_json(self._exchange(process, request + b"\n", deadline))
            if isinstance(raw, dict) and "error" in raw:
                if not set(raw) <= {"error", "usage"}:
                    raise JevClientError("invalid_response")
                usage = validate_usage(raw["usage"]) if raw.get("usage") is not None else None
                raise JevClientError(safe_error_code(raw["error"]), usage)
            return validate_relay_result(raw, self.model, label=label)
        except JevClientError:
            self._discard(process)
            raise
        except Exception:
            self._discard(process)
            raise JevClientError("transport_or_response_error") from None
        finally:
            self._flight.release()


def _serve_requests(stream, output, *, persistent=False):
    clients = {}
    key = None
    try:
        while True:
            raw = stream.readline(MAX_STATE_BYTES + 2) if persistent else stream.read(MAX_STATE_BYTES + 1)
            if not raw:
                return
            try:
                if len(raw) > MAX_STATE_BYTES + int(persistent) or (persistent and not raw.endswith(b"\n")):
                    raise JevClientError("request_too_large")
                state, label = _prepare(decode_json(raw))
                if label not in clients:
                    if os.getenv("JEV_RELAY_URL"):
                        from src.services.jev_relay_client import JevEventRelayClient, JevLiveContextRelayClient
                        cls = JevLiveContextRelayClient if label == LABEL else JevEventRelayClient
                        clients[label] = cls()
                    else:
                        from src.services.jev_backtest_service import load_credential
                        if key is None:
                            key = load_credential()
                        cls = JevLiveContextDecisionsClient if label == LABEL else JevCBDecisionsClient
                        clients[label] = cls(api_key=key)
                result = clients[label].decide(state)
            except JevClientError as exc:
                result = {"error": safe_error_code(exc.code), "usage": exc.usage}
            except Exception:
                result = {"error": "transport_or_response_error"}
            encoded = json.dumps(result, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_RESULT_BYTES:
                encoded = b'{"error":"response_too_large"}'
            output.write(encoded + b"\n")
            output.flush()
            if not persistent or "error" in result:
                return
    finally:
        for client in clients.values():
            client.close()
        key = None


if __name__ == "__main__":
    _serve_requests(sys.stdin.buffer, sys.stdout.buffer, persistent=sys.argv[1:] == ["--worker"])
