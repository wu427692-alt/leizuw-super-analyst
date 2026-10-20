"""Relay contracts and lifecycle tested without sockets, credentials or Docker."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email.message import Message
from io import BytesIO
import json
import subprocess
from types import SimpleNamespace
import threading

import pytest

from scripts import jev_keychain_relay_host as host
from src.services.jev_decisions_client import JevClientError
from src.services.jev_relay_client import (
    JevRelayClient, MAX_BODY_BYTES, PROTOCOL, validate_relay_result,
)
from src.services.jev_runtime_relay import (
    RelayApplication, RelayHandler, read_key_frame, serve_from_stdin,
)

STATE = {"timeframe": "1min", "lookback_bars": 20,
         "normalized_bars": [{"open": 1, "high": 1.02, "low": .98, "close": 1, "volume": 1}] * 20,
         "indicators": {"rsi14": .5}}
RESULT = {"action": "BUY", "status": "ok", "confidence": .8,
          "probabilities": {"BUY": .8, "SELL": .1, "HOLD": .1},
          "labels": ["price_volume_only"], "model": "Qwen/Qwen3.5-9B", "latency_ms": 12,
          "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100,
                    "cost": .0000042, "cost_is_estimate": False}}


class FakeClient:
    model = "Qwen/Qwen3.5-9B"
    endpoint = "https://jev.worldseek-ai.com/v1/systemone"
    input_price_per_million = 1.0

    def __init__(self):
        self.calls = []
        self.before = lambda: None

    def decide(self, state):
        self.calls.append(state)
        self.before()
        return deepcopy(RESULT)


class Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.history = []
        self.body = json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


def test_relay_roundtrip_uses_no_key_and_preserves_cache_identity():
    app = RelayApplication(FakeClient())
    calls = []

    def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return Response(app.health())
        status, payload = app.decide(kwargs["json"])
        return Response(payload, status)

    client = JevRelayClient("http://127.0.0.1:19876", transport=transport)
    assert client.configured
    assert client.health()["protocol"] == PROTOCOL
    assert client.decide(STATE) == RESULT
    assert client.endpoint == FakeClient.endpoint
    assert client.model == FakeClient.model
    assert all("Authorization" not in call[2]["headers"] for call in calls)
    assert all(call[2]["allow_redirects"] is False for call in calls)
    assert not hasattr(client, "_api_key")


@pytest.mark.parametrize("url", [
    "http://localhost:1000", "http://0.0.0.0:1000", "http://127.0.0.1:1000@attacker.example",
    "https://127.0.0.1:1000", "http://127.0.0.1", "http://127.0.0.1:0",
    "http://127.0.0.1:1000/decide", "http://127.0.0.1:1000?next=evil",
    "http://user:pass@127.0.0.1:1000", "http://[::1]:1000",
])
def test_relay_adapter_rejects_non_exact_loopback_addresses(url):
    with pytest.raises(JevClientError):
        JevRelayClient(url)


def test_default_loopback_transport_ignores_environment_proxies():
    client = JevRelayClient("http://127.0.0.1:19876")
    assert client._session.trust_env is False
    client._session.close()


def test_health_mismatch_is_not_treated_as_configured():
    health = RelayApplication(FakeClient()).health() | {"model": "wrong/model"}
    client = JevRelayClient("http://127.0.0.1:19876", transport=lambda *a, **kw: Response(health))
    assert client.configured is False


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(model="wrong/model"),
    lambda r: r.update(confidence=float("nan")),
    lambda r: r.update(status="unavailable"),
    lambda r: r["probabilities"].update(OTHER=0),
    lambda r: r.update(action="SELL"),
    lambda r: r.update(labels=["SECRET TEXT"]),
    lambda r: r["usage"].update(cost=-1),
    lambda r: r["usage"].update(cost_is_estimate="false"),
    lambda r: r["usage"].update(extra="SECRET TEXT"),
    lambda r: r.update(latency_ms=True),
])
def test_normalized_relay_output_is_strictly_validated(mutate):
    result = deepcopy(RESULT)
    mutate(result)
    with pytest.raises(JevClientError) as caught:
        validate_relay_result(result, FakeClient.model)
    assert "SECRET TEXT" not in str(caught.value)


def test_proxy_returns_only_safe_errors_and_preserves_known_charge():
    client = FakeClient()

    def fail():
        raise JevClientError("timeout", deepcopy(RESULT["usage"]))

    client.before = fail
    app = RelayApplication(client)
    status, error = app.decide({"state": STATE})
    adapter = JevRelayClient("http://127.0.0.1:19876", transport=lambda *a, **kw: Response(error, status))
    with pytest.raises(JevClientError) as caught:
        adapter.decide(STATE)
    assert caught.value.code == "timeout"
    assert caught.value.usage == RESULT["usage"]

    def unsafe():
        raise RuntimeError("UPSTREAM_KEY_BODY_MUST_NOT_LEAK")

    client.before = unsafe
    status, error = app.decide({"state": STATE})
    assert status == 502
    assert error == {"error": {"code": "relay_internal_error"}}
    assert "UPSTREAM_KEY" not in str(error)


def test_application_has_no_generic_proxy_or_account_state_path():
    client = FakeClient()
    app = RelayApplication(client)
    for payload in ({"url": "https://other.example", "state": STATE}, {"state": STATE | {"cash": 500}},
                    {"state": STATE | {"news": "private"}}, {"api_key": "key", "state": STATE}):
        status, _ = app.decide(payload)
        assert status == 400
    assert client.calls == []
    assert set(app.health()) == {"ready", "protocol", "model", "endpoint", "input_price_per_million"}


def test_paid_concurrency_is_bounded_and_busy_requests_are_not_forwarded():
    entered, finish = threading.Event(), threading.Event()
    client = FakeClient()

    def block():
        entered.set()
        assert finish.wait(2)

    client.before = block
    app = RelayApplication(client, max_concurrent=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(app.decide, {"state": STATE})
        assert entered.wait(2)
        try:
            status, error = app.decide({"state": STATE})
            assert status == 503
            assert error["error"]["code"] == "relay_busy"
            assert len(client.calls) == 1
        finally:
            finish.set()
        assert first.result()[0] == 200


def handler(payload=None, *, path="/decide", length=None, extra_headers=()):
    body = json.dumps(payload or {"state": STATE}).encode()
    value = RelayHandler.__new__(RelayHandler)
    value.server = SimpleNamespace(application=RelayApplication(FakeClient()))
    value.path = path
    value.rfile = BytesIO(body)
    value.headers = Message()
    value.headers["Content-Type"] = "application/json"
    value.headers["Content-Length"] = str(len(body) if length is None else length)
    for key, text in extra_headers:
        value.headers[key] = text
    replies = []
    value.reply = lambda status, result: replies.append((status, result))
    return value, replies


def test_http_framing_size_and_path_are_rejected_before_provider():
    cases = [handler(path="http://other.example/decide"), handler(length=MAX_BODY_BYTES + 1),
             handler(extra_headers=[("Transfer-Encoding", "chunked")]),
             handler(extra_headers=[("Content-Length", "1")])]
    for value, replies in cases:
        value.do_POST()
        assert replies[0][0] in (400, 404, 413)
        assert value.server.application.client.calls == []
    value, replies = handler()
    value.do_POST()
    assert replies == [(200, RESULT)]


@pytest.mark.parametrize("frame", [b"", b"unterminated", b"bad key\n", b"bad\r\n", b"x" * 8193 + b"\n"])
def test_stdin_credential_frame_is_bounded_and_never_echoed(frame):
    with pytest.raises(JevClientError) as caught:
        read_key_frame(BytesIO(frame))
    assert "bad key" not in str(caught.value)


def test_stdin_eof_shuts_down_relay_and_binds_only_loopback_without_real_socket():
    events = []

    class FakeServer:
        def __init__(self, address, handler_cls):
            events.append(("bind", address))

        def serve_forever(self, **kwargs):
            events.append("serve")

        def shutdown(self):
            events.append("shutdown")

        def server_close(self):
            events.append("close")

    keys = []

    def factory(*, api_key):
        keys.append(api_key)
        return FakeClient()

    serve_from_stdin(19876, BytesIO(b"unit-test-key\n"), client_factory=factory, server_factory=FakeServer)
    assert keys == ["unit-test-key"]
    assert events[0] == ("bind", ("127.0.0.1", 19876))
    assert "shutdown" in events and "close" in events


def host_args():
    return SimpleNamespace(service="test.service", account="test-account", docker="/usr/local/bin/docker",
                           context="test-context", container="test-server", port=19876)


def test_keychain_command_is_read_only_and_secret_never_enters_argv():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"unit-test-key\n")

    assert host.load_key("test.service", "test-account", run=run) == b"unit-test-key"
    assert calls[0][0] == ["/usr/bin/security", "find-generic-password", "-s", "test.service", "-a", "test-account", "-w"]
    assert b"unit-test-key".decode() not in str(calls)
    assert calls[0][1]["stderr"] == subprocess.DEVNULL


def test_host_keeps_key_pipe_open_until_exec_ends_without_env_or_file_injection():
    calls = []

    class Process:
        def __init__(self):
            self.stdin = BytesIO()
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                assert not self.stdin.closed
                assert self.stdin.getvalue() == b"unit-test-key\n"
            return 0

    process = Process()

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    assert host.supervise(host_args(), key_loader=lambda *args: b"unit-test-key", popen=popen) == 0
    command, options = calls[0]
    assert "unit-test-key" not in str(calls)
    assert "env" not in options
    assert options["stdin"] == subprocess.PIPE
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert command[3:10] == ["exec", "-i", "-u", "1000", "-w", "/app", "test-server"]
    assert process.stdin.closed


def test_host_closes_pipe_on_signal_before_forced_process_termination():
    events = []

    class Pipe(BytesIO):
        def close(self):
            events.append("close")
            super().close()

    class Process:
        stdin = Pipe()

        def wait(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt
            events.append("wait-after-eof")
            return 0

    with pytest.raises(KeyboardInterrupt):
        host.supervise(host_args(), key_loader=lambda *args: b"unit-test-key", popen=lambda *a, **kw: Process())
    assert events == ["close", "wait-after-eof"]
