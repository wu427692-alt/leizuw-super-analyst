"""Check real HTTP probe semantics while isolating all service mutations."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest


_GUARD = Path(__file__).resolve().parents[1] / "docker/systemd/leziwu-health-guard.sh"
_BASH = shutil.which("bash")
_CURL = shutil.which("curl")
pytestmark = pytest.mark.skipif(not _BASH or not _CURL, reason="bash and curl are required")


@contextmanager
def _http_probe(responses):
    """Serve each response once, repeating the last for further attempts."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            status, body = responses[min(len(requests) - 1, len(responses) - 1)]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status in (301, 302):
                # Following this redirect must not make the original probe healthy.
                self.send_header("Location", "/redirect-target")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/health", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _run_guard(tmp_path, url, *, python_missing=False):
    actions = tmp_path / "actions"
    env = dict(os.environ)
    env.update({
        "GUARD_SCRIPT": str(_GUARD),
        "ACTIONS": str(actions),
        "LEZIWU_GUARD_LOCK_FILE": str(tmp_path / "guard.lock"),
        "LEZIWU_GUARD_HEALTH_URL": url,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    if python_missing:
        # An actual executable-search failure, not a mocked unhealthy HTTP reply.
        executable_dir = tmp_path / "executables-without-python"
        executable_dir.mkdir()
        (executable_dir / "bash").symlink_to(_BASH)
        (executable_dir / "curl").symlink_to(_CURL)
        env["PATH"] = str(executable_dir)
    wrapper = r'''
docker() {
  case "$*" in
    *State.Status*) echo running ;;
    *State.Health*) echo healthy ;;
    *State.StartedAt*) echo 'same-container|same-start-time' ;;
    restart*) printf '%s\n' "$*" >> "$ACTIONS" ;;
    *) return 77 ;;
  esac
}
logger() { :; }
sleep() { :; }
flock() { :; }
export -f docker logger sleep flock
bash "$GUARD_SCRIPT"
'''
    result = subprocess.run(
        [_BASH, "-c", wrapper], env=env, capture_output=True, text=True, timeout=8,
    )
    recorded = actions.read_text().splitlines() if actions.exists() else []
    return result, recorded


def test_guard_default_probe_is_direct_backend_health():
    assert "${LEZIWU_GUARD_HEALTH_URL:-http://127.0.0.1:8000/health}" in _GUARD.read_text()


@pytest.mark.parametrize("body", [
    b'{"status":"ok"}',
    b'  {\n "status": "ok", "timestamp": "test", "extra": 1\n}\n',
])
def test_guard_accepts_200_json_ok_without_restart(tmp_path, body):
    with _http_probe([(200, body)]) as (url, requests):
        result, actions = _run_guard(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert requests == ["/health"]
    assert actions == []


@pytest.mark.parametrize("status,body", [
    (301, b'{"status":"ok"}'),
    (302, b'{"status":"ok"}'),
    (200, b'<!doctype html><html>status ok</html>'),
    (200, b''),
    (200, b'{invalid json'),
    (200, b'{"status":"degraded"}'),
    (200, b'{"timestamp":"test"}'),
    (200, b'["ok"]'),
    (503, b'{"status":"ok"}'),
])
def test_guard_rejects_invalid_health_after_exactly_three_attempts(tmp_path, status, body):
    with _http_probe([(status, body)]) as (url, requests):
        result, actions = _run_guard(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert requests == ["/health"] * 3
    assert actions == ["restart stock-server"]


def test_guard_transient_failure_then_success_does_not_restart(tmp_path):
    with _http_probe([(503, b'{}'), (200, b'{"status":"ok"}')]) as (url, requests):
        result, actions = _run_guard(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert requests == ["/health"] * 2
    assert actions == []


def test_guard_missing_python_is_probe_error_without_restart(tmp_path):
    with _http_probe([(200, b'{"status":"ok"}')]) as (url, _requests):
        result, actions = _run_guard(tmp_path, url, python_missing=True)
    assert result.returncode != 0
    assert actions == []
