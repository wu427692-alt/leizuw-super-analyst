"""No real Docker, network, app imports or credentials in preflight tests."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "macmini_preflight", Path(__file__).parents[1] / "scripts/macmini_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_architecture():
    assert preflight.architecture("arm64") == preflight.architecture("aarch64") == "arm64"
    assert preflight.architecture("x86_64") == "amd64"
    assert preflight.architecture("other") == "unknown"


def test_remote_docker_host_skipped():
    with patch.dict("os.environ", {"DOCKER_HOST": "ssh://remote"}, clear=True), \
            patch.object(preflight, "command") as call:
        assert not preflight.local_docker_endpoint()
        call.assert_not_called()


def test_context_takes_priority_over_host():
    with patch.dict("os.environ", {"DOCKER_HOST": "unix:///local", "DOCKER_CONTEXT": "remote"}, clear=True), \
            patch.object(preflight, "command", return_value="ssh://remote") as call:
        assert not preflight.local_docker_endpoint()
        assert call.call_args.args[0][-1] == "remote"


def test_local_context():
    with patch.dict("os.environ", {}, clear=True), \
            patch.object(preflight, "command", return_value="unix:///local/docker.sock"):
        assert preflight.local_docker_endpoint()


def test_runtime_checks_no_file_contents(tmp_path):
    for name in ("data", "logs", "reports", "strategies", "config", "tyc", "longbridge_tokens"):
        (tmp_path / name).mkdir()
    for name in ("data/stock_analysis.db", "config/.env", "data/.admin_password_hash"):
        (tmp_path / name).write_bytes(b"TEST-PRIVATE-VALUE")
    for name in ("data/.session_secret", "data/.user_auth_secret"):
        (tmp_path / name).write_bytes(b"x" * 32)
    with patch.object(Path, "read_bytes", side_effect=AssertionError("No reads")), \
            patch.object(Path, "read_text", side_effect=AssertionError("No reads")):
        result = preflight.runtime_inventory(tmp_path)
    assert result["missing_or_invalid_files"] == []
    assert result["content_not_verified"]
    assert "TEST-PRIVATE-VALUE" not in json.dumps(result)


def test_runtime_missing(tmp_path):
    assert "data/stock_analysis.db" in preflight.runtime_inventory(tmp_path)["missing_or_invalid_files"]


def test_inventory_never_ready(tmp_path):
    with patch.object(preflight, "command", return_value=None), \
            patch.object(preflight.shutil, "which", return_value=None), \
            patch.object(preflight, "port_in_use", return_value=False):
        result = preflight.inspect_host(tmp_path)
    assert result["production_ready"] is False
    assert result["inventory_only"] is True
    assert result["runtime"] is None


def test_docker_commands_are_read_only_and_local(tmp_path):
    calls = []

    def fake_command(args):
        calls.append(args)
        if args[:2] == ["sysctl", "-n"]:
            return str(16 * preflight.GIB)
        if args[1:3] == ["context", "inspect"]:
            return "unix:///docker.sock"
        if args[1] == "info":
            return '{"architecture":"aarch64","memory_bytes":8000000000}'
        if args[1:3] == ["image", "inspect"]:
            return "arm64"
        return "test-version"

    with patch.dict("os.environ", {}, clear=True), \
            patch.object(preflight, "command", side_effect=fake_command), \
            patch.object(preflight.platform, "machine", return_value="arm64"), \
            patch.object(preflight.shutil, "which", return_value="/test/binary"), \
            patch.object(preflight, "port_in_use", return_value=False):
        result = preflight.inspect_host(tmp_path, image="local:test")
    assert result["docker"]["requested_image_native"]
    assert all(args[1] in ("-n", "context", "compose", "info", "image") for args in calls)
    assert not {"up", "build", "pull", "start", "restart", "run"}.intersection(sum(calls, []))
