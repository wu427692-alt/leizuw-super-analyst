"""Real local sockets and private subprocesses; no provider, keychain or paid call."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import socket
import sys
import threading
import time

import pytest
import requests

from src.services.jev_decisions_client import JevClientError, JevDecisionsClient, no_retry_session
from src.services.jev_live_t_client import BoundedLiveDecisionClient
from src.services.jev_relay_client import JevEventRelayClient
from src.services.jev_runtime_relay import RelayApplication, RelayHandler, RelayServer
from tests.test_jev_event_relay import EventClient, state
from tests.test_jev_runtime_relay import FakeClient


class CountingServer(RelayServer):
    def __init__(self, *args):
        self.connections = 0
        super().__init__(*args)

    def get_request(self):
        value = super().get_request()
        self.connections += 1
        return value


@contextmanager
def server_context(handler=RelayHandler, application=None):
    server = CountingServer(("127.0.0.1", 0), handler)
    if application is not None:
        server.application = application
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


@pytest.fixture
def relay(monkeypatch):
    event = EventClient()
    with server_context(application=RelayApplication(FakeClient(), event_client=event)) as (server, url):
        monkeypatch.setenv("JEV_RELAY_URL", url)
        # If routing regresses it cannot read a real credential from an env var.
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        yield event, server, url


def test_private_sessions_disable_all_retries_proxies_and_keep_tls_verification():
    session = no_retry_session()
    try:
        assert session.trust_env is False and session.verify is True
        for scheme in ("http://", "https://"):
            retry = session.get_adapter(scheme).max_retries
            assert (retry.total, retry.connect, retry.read, retry.status, retry.redirect, retry.other) == (0,) * 6
    finally:
        session.close()


def test_persistent_worker_reuses_pid_and_relay_connection_without_credential(relay):
    event, server, _ = relay
    client = BoundedLiveDecisionClient()
    try:
        first = client.decide(state())
        process = client._process
        assert first["labels"] == ["price_volume_factors"]
        for _ in range(3):
            assert client.decide(state()) == first
            assert client._process is process and process.poll() is None
        assert len(event.calls) == 4 and server.connections == 1
        assert process.args[-1] == "--worker" and "Authorization" not in str(process.args)
    finally:
        client.close()
    assert process.poll() is not None
    with pytest.raises(JevClientError):
        client.decide(state())
    assert len(event.calls) == 4


def test_relay_health_and_decisions_share_connection(relay):
    event, server, url = relay
    client = JevEventRelayClient(url)
    try:
        assert client.health()["ready"] is True
        client.decide(state())
        client.decide(state())
        assert server.connections == 1 and len(event.calls) == 2
    finally:
        client.close()


def test_worker_deadline_kills_process_and_never_replays_unknown_outcome(relay):
    event, _, _ = relay
    entered, release = threading.Event(), threading.Event()
    client = BoundedLiveDecisionClient()
    client.decide(state())
    event.calls.clear()
    event.before = lambda: (entered.set(), release.wait(2))
    client.timeout = .4
    started = time.monotonic()
    try:
        with pytest.raises(JevClientError) as caught:
            client.decide(state())
        assert caught.value.code == "timeout"
        assert entered.is_set() and len(event.calls) == 1
        assert client._process is None and time.monotonic() - started < 1.5
    finally:
        release.set()
        client.close()


def test_close_interrupts_inflight_call_without_waiting_for_flight_lock(relay):
    event, _, _ = relay
    entered, release = threading.Event(), threading.Event()
    event.before = lambda: (entered.set(), release.wait(2))
    client = BoundedLiveDecisionClient()
    client.timeout = 10
    errors = []

    def call():
        try:
            client.decide(state())
        except JevClientError as exc:
            errors.append(exc.code)

    thread = threading.Thread(target=call)
    thread.start()
    try:
        assert entered.wait(2)
        process = client._process
        with pytest.raises(JevClientError, match="relay_busy"):
            client.decide(state())
        started = time.monotonic()
        client.close()
        thread.join(1)
        assert time.monotonic() - started < 1 and not thread.is_alive()
        assert process.poll() is not None and errors and len(event.calls) == 1
    finally:
        release.set()
        client.close()
        thread.join(2)


def test_upstream_error_is_not_retried_and_next_explicit_request_is_new_worker(relay):
    event, server, _ = relay
    def fail():
        raise JevClientError("http_503")
    event.before = fail
    client = BoundedLiveDecisionClient()
    try:
        with pytest.raises(JevClientError, match="http_503"):
            client.decide(state())
        assert len(event.calls) == 1 and server.connections == 1 and client._process is None
        event.before = lambda: None
        assert client.decide(state())["action"] == "BUY"
        assert len(event.calls) == 2 and server.connections == 2
    finally:
        client.close()


@pytest.mark.parametrize("program,expected", [
    ("import time; time.sleep(10)", "timeout"),
    ("import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write(b'x'*65538); sys.stdout.flush()", "response_too_large"),
    ("import sys; sys.stdin.buffer.readline(); print('{}'); print('{}')", "invalid_response"),
])
def test_worker_pipe_is_bounded_and_deadline_includes_stalled_worker(monkeypatch, program, expected):
    monkeypatch.setattr("src.services.jev_live_t_client._worker_command", lambda: [sys.executable, "-u", "-c", program])
    client = BoundedLiveDecisionClient()
    client.timeout = .2
    try:
        with pytest.raises(JevClientError) as caught:
            client.decide(state())
        assert caught.value.code == expected and client._process is None
    finally:
        client.close()


def test_invalid_private_state_is_rejected_before_any_worker_spawn(monkeypatch):
    client = BoundedLiveDecisionClient()
    monkeypatch.setattr(client, "_get_process", lambda: pytest.fail("private state reached worker"))
    with pytest.raises(JevClientError):
        client.decide({**state(), "account": "private"})
    client.close()


def test_deadline_also_covers_a_full_stdin_pipe(monkeypatch):
    monkeypatch.setattr("src.services.jev_live_t_client._worker_command", lambda: [
        sys.executable, "-u", "-c", "import time; time.sleep(10)"])
    client = BoundedLiveDecisionClient()
    process = client._get_process()
    started = time.monotonic()
    try:
        # Exceeds the local pipe buffer; a blocking write would miss the deadline.
        with pytest.raises(JevClientError, match="timeout"):
            client._exchange(process, b"x" * 96000, started + .2)
        assert time.monotonic() - started < 1
    finally:
        client.close()
    assert process.poll() is not None


class LocalUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args):
        pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.posts += 1
        if self.path == "/disconnect":
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        body = b'{}'
        self.send_response(503 if self.path == "/error" else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_provider_pool_reuses_connection_and_does_not_retry_status_or_lost_response():
    # Transport-only exercise: full decide still strictly requires HTTPS.
    with server_context(handler=LocalUpstreamHandler) as (server, url):
        server.posts = 0
        client = JevDecisionsClient(api_key="synthetic-local-only")
        try:
            for path in ("/ok", "/ok", "/error"):
                with client._pooled_post(url + path, json={}, timeout=1, allow_redirects=False) as response:
                    assert response.status_code == (503 if path == "/error" else 200)
            assert server.connections == 1 and server.posts == 3
            with pytest.raises(requests.RequestException):
                client._pooled_post(url + "/disconnect", json={}, timeout=1, allow_redirects=False)
            assert server.posts == 4 and server.connections == 1
        finally:
            client.close()


def test_provider_session_is_thread_private_and_close_covers_active_threads(monkeypatch):
    sessions = []
    class Session:
        closed = False
        def post(self, *args, **kwargs):
            return id(self)
        def close(self):
            self.closed = True
    def factory():
        value = Session()
        sessions.append(value)
        return value
    monkeypatch.setattr("src.services.jev_decisions_client.no_retry_session", factory)
    client = JevDecisionsClient(api_key="synthetic-local-only")
    barrier, release = threading.Barrier(3), threading.Event()
    results = []
    def call():
        results.append((client._pooled_post(), client._pooled_post()))
        barrier.wait()
        release.wait(2)
    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    try:
        assert len(sessions) == 2 and results[0][0] != results[1][0]
        assert all(first == second for first, second in results)
        client.close()
        assert all(session.closed for session in sessions)
    finally:
        release.set()
        for thread in threads:
            thread.join(2)


def test_health_with_unread_body_is_rejected_and_connection_closed(relay):
    event, _, url = relay
    with no_retry_session() as session:
        response = session.get(url + "/health", data=b"unread", timeout=1)
        assert response.status_code == 400 and response.headers["Connection"] == "close"
        assert event.calls == []
