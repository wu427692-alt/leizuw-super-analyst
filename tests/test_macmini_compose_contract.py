"""Offline contracts; no Docker, application startup or actual private .env reads."""

from __future__ import annotations

import ast
import base64
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker" / "docker-compose.macmini.yml"
COMPOSE = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
LIVE_SERVER_SWITCHES = {
    "MARKET_DATA_AUTO_START", "ESSAY_ANALYSIS_AUTO_START",
    "ESSAY_DAILY_REPORT_AUTO_START", "ZSXQ_MCP_AUTO_START",
    "CNINFO_WATCHLIST_AUTO_START", "SYNC_WATCHDOG_AUTO_START",
    "APP_STARTUP_WARMUP",
}


def test_macmini_is_independent_and_requires_a_local_image() -> None:
    assert set(COMPOSE["services"]) == {"server", "monitor-worker"}
    assert COMPOSE["name"] == "${DSA_MACMINI_PROJECT_NAME:-dsa-macmini}"
    for service in COMPOSE["services"].values():
        assert service["image"].startswith("${DSA_MACMINI_IMAGE:?")
        assert service["pull_policy"] == "never"
        assert service["restart"] == "${DSA_MACMINI_RESTART_POLICY:-no}"
        for forbidden in (
            "extends", "build", "platform", "container_name", "network_mode",
            "user", "entrypoint", "privileged", "env_file",
        ):
            assert forbidden not in service
    assert "docker-compose.cloud.yml" not in str(COMPOSE)


def test_macmini_mounts_only_restored_bundle_paths() -> None:
    expected = {
        "data": ("/app/data", False),
        "logs": ("/app/logs", False),
        "reports": ("/app/reports", False),
        "strategies": ("/app/strategies", True),
        "config": ("/app/config", "${DSA_MACMINI_CONFIG_READ_ONLY:-true}"),
        "tyc": ("/home/dsa/.tyc", "${DSA_MACMINI_TYC_READ_ONLY:-true}"),
        "longbridge_tokens": ("/home/dsa/.longbridge", False),
    }
    for service in COMPOSE["services"].values():
        volumes = service["volumes"]
        assert len(volumes) == len(expected)
        for volume in volumes:
            name = volume["source"].rsplit("/", 1)[1]
            target, read_only = expected[name]
            assert volume["source"].startswith("${DSA_MACMINI_RUNTIME_DIR:?")
            assert volume["type"] == "bind"
            assert volume["target"] == target
            assert volume.get("read_only", False) == read_only
            assert volume["bind"]["create_host_path"] is False


def test_macmini_is_loopback_only_and_network_isolated_by_default() -> None:
    server = COMPOSE["services"]["server"]
    assert len(server["ports"]) == 1
    assert server["ports"][0]["host_ip"] == "127.0.0.1"
    assert server["ports"][0]["published"] == "${DSA_MACMINI_HOST_PORT:-18000}"
    assert "ports" not in COMPOSE["services"]["monitor-worker"]
    assert COMPOSE["networks"]["macmini"]["internal"] == "${DSA_MACMINI_NETWORK_INTERNAL:-true}"
    for service in COMPOSE["services"].values():
        assert service["networks"] == ["macmini"]


def test_macmini_disables_every_known_api_auto_start_switch() -> None:
    # Source-only discovery also catches future startup switches, without
    # importing api.app or opening the actual runtime configuration/database.
    tree = ast.parse((REPO_ROOT / "api" / "app.py").read_text(encoding="utf-8"))
    switches = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getenv"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.endswith("_AUTO_START")
    }
    assert switches  # Avoid a vacuous pass if source discovery changes.
    environment = COMPOSE["services"]["server"]["environment"]
    for name in switches | {
        "SCHEDULE_ENABLED", "SCHEDULE_RUN_IMMEDIATELY", "RUN_IMMEDIATELY",
        "APP_STARTUP_WARMUP", "MARKET_STARTUP_BOOTSTRAP_ENABLED",
        "FEISHU_STREAM_ENABLED", "DINGTALK_STREAM_ENABLED",
        "DSA_RUNTIME_SCHEDULER_FORCE_ENABLED",
        "DSA_RUNTIME_SCHEDULER_RUN_IMMEDIATELY",
    }:
        expected = "${DSA_MACMINI_LIVE_JOBS:-false}" if name in LIVE_SERVER_SWITCHES else "false"
        assert environment[name] == expected, name
    assert environment["DSA_RUNTIME_SCHEDULER_SUPPRESS_START"] == "true"
    assert environment["INVESTMENT_MONITOR_RUN_MODE"] == "external"


def test_macmini_disables_unconditional_lifespan_work_in_preview() -> None:
    server = COMPOSE["services"]["server"]
    assert server["command"][4:8] == ["python", "-m", "uvicorn", "server:app"]
    assert server["command"][-2:] == ["--lifespan", "${DSA_MACMINI_LIFESPAN:-off}"]
    assert server["environment"]["DSA_MACMINI_LIFESPAN"] == "${DSA_MACMINI_LIFESPAN:-off}"
    assert server["environment"]["DSA_MACMINI_PRODUCTION_CONFIRMED"].endswith(":-false}")


def test_macmini_collector_needs_profile_and_separate_opt_in() -> None:
    worker = COMPOSE["services"]["monitor-worker"]
    assert worker["profiles"] == ["collector"]
    assert worker["command"][3:] == [
        "collector", "python", "-m", "src.services.investment_monitor_daemon",
    ]
    assert worker["environment"]["DSA_MACMINI_COLLECTOR_ENABLED"].endswith(":-false}")
    assert worker["environment"]["INVESTMENT_MONITOR_RUN_MODE"] == "embedded"
    for name, value in worker["environment"].items():
        if name.endswith("_AUTO_START") and name != "INVESTMENT_MONITOR_AUTO_START":
            expected = "${DSA_MACMINI_LIVE_JOBS:-false}" if name == "CONCEPT_THEME_AUTO_START" else "false"
            assert value == expected, name
    assert worker["healthcheck"] == {"disable": True}


def test_macmini_enforces_auth_from_the_private_file() -> None:
    for service in COMPOSE["services"].values():
        env = service["environment"]
        assert env["ENV_FILE"] == "/app/config/.env"
        assert env["ADMIN_AUTH_ENABLED"] == "true"
        assert env["USER_ACCESS_ENABLED"] == "true"
        assert env["CORS_ALLOW_ALL"] == "false"
        assert env["DSA_DESKTOP_MODE"] == "false"
        assert service["command"][:3] == ["python", "-c", COMPOSE["x-startup-guard"]]


def _run_guard(tmp_path: Path, *, mode: str = "server", config_text: str | None = None,
               credentials: bool = True, session: bool = True, database: bool = True,
               user_secret: bool = True,
               auth_hash: str | None = None, session_secret: bytes = b"y" * 32,
               overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # Synthetic fixtures only: never inherit the developer's environment or
    # invoke the guarded application. Success execs a harmless marker process.
    config_path = tmp_path / "config.env"
    if config_text is not None:
        config_path.write_text(config_text, encoding="utf-8")
    if credentials:
        part = base64.b64encode(b"x" * 32).decode("ascii")
        value = f"{part}:{part}" if auth_hash is None else auth_hash
        (tmp_path / ".admin_password_hash").write_text(value, encoding="utf-8")
    if session:
        (tmp_path / ".session_secret").write_bytes(session_secret)
    if user_secret:
        (tmp_path / ".user_auth_secret").write_bytes(b"z" * 32)
    if database:
        (tmp_path / "stock_analysis.db").touch()
    env = {
        "ENV_FILE": str(config_path),
        "DATABASE_PATH": str(tmp_path / "stock_analysis.db"),
        "ADMIN_AUTH_ENABLED": "true",
        "USER_ACCESS_ENABLED": "true",
    }
    env.update(overrides or {})
    return subprocess.run(
        [sys.executable, "-c", COMPOSE["x-startup-guard"], mode,
         sys.executable, "-c", "print('GUARD_ACCEPTED')"],
        env=env, capture_output=True, text=True, check=False, timeout=10,
    )


GOOD_CONFIG = "ADMIN_AUTH_ENABLED=true\nUSER_ACCESS_ENABLED=true\n"


@pytest.mark.parametrize("config_text", [
    None, "", "ADMIN_AUTH_ENABLED=false\nUSER_ACCESS_ENABLED=true\n",
    "ADMIN_AUTH_ENABLED=true\nUSER_ACCESS_ENABLED=false\n",
])
def test_macmini_guard_rejects_missing_or_disabled_file_auth(tmp_path, config_text) -> None:
    result = _run_guard(tmp_path, config_text=config_text)
    assert result.returncode != 0
    assert "GUARD_ACCEPTED" not in result.stdout
    assert "startup refused" in result.stderr


@pytest.mark.parametrize("missing", ["credentials", "session", "database", "user_secret"])
def test_macmini_guard_requires_restored_auth_state(tmp_path, missing) -> None:
    result = _run_guard(tmp_path, config_text=GOOD_CONFIG, **{missing: False})
    assert result.returncode != 0
    assert "GUARD_ACCEPTED" not in result.stdout


@pytest.mark.parametrize("overrides", [
    {"auth_hash": "not-a-valid-hash"},
    {"auth_hash": "eA==:eA=="},
    {"session_secret": b"too-short"},
    {"overrides": {"ADMIN_AUTH_ENABLED": "false"}},
    {"overrides": {"USER_ACCESS_ENABLED": "false"}},
])
def test_macmini_guard_rejects_corrupt_credentials_and_disabled_process_auth(tmp_path, overrides) -> None:
    result = _run_guard(tmp_path, config_text=GOOD_CONFIG, **overrides)
    assert result.returncode != 0
    assert "GUARD_ACCEPTED" not in result.stdout
    assert "not-a-valid-hash" not in result.stderr


@pytest.mark.parametrize("mode,overrides,accepted", [
    ("server", {}, True),
    ("server", {"DSA_MACMINI_LIFESPAN": "on"}, False),
    ("server", {"DSA_MACMINI_LIFESPAN": "auto"}, False),
    ("server", {"DSA_MACMINI_LIFESPAN": "on", "DSA_MACMINI_PRODUCTION_CONFIRMED": "true"}, True),
    ("server", {"DSA_MACMINI_LIVE_JOBS": "true"}, False),
    ("server", {"DSA_MACMINI_LIVE_JOBS": "invalid"}, False),
    ("server", {"DSA_MACMINI_LIVE_JOBS": "true", "DSA_MACMINI_PRODUCTION_CONFIRMED": "true"}, False),
    ("server", {"DSA_MACMINI_LIFESPAN": "on", "DSA_MACMINI_PRODUCTION_CONFIRMED": "true",
                "DSA_MACMINI_LIVE_JOBS": "true", "DSA_MACMINI_CONFIG_READ_ONLY": "false",
                "DSA_MACMINI_TYC_READ_ONLY": "false"}, True),
    ("collector", {}, False),
    ("collector", {"DSA_MACMINI_COLLECTOR_ENABLED": "true"}, True),
    ("collector", {"DSA_MACMINI_COLLECTOR_ENABLED": "true", "DSA_MACMINI_LIVE_JOBS": "true"}, False),
    ("collector", {"DSA_MACMINI_COLLECTOR_ENABLED": "true", "DSA_MACMINI_LIVE_JOBS": "true",
                   "DSA_MACMINI_PRODUCTION_CONFIRMED": "true", "DSA_MACMINI_CONFIG_READ_ONLY": "false",
                   "DSA_MACMINI_TYC_READ_ONLY": "false"}, True),
    ("unknown", {}, False),
])
def test_macmini_guard_never_implicitly_enables_background_work(tmp_path, mode, overrides, accepted) -> None:
    result = _run_guard(tmp_path, config_text=GOOD_CONFIG, mode=mode, overrides=overrides)
    assert (result.returncode == 0) is accepted
    assert ("GUARD_ACCEPTED" in result.stdout) is accepted


def test_macmini_example_contains_only_nonsecret_deployment_parameters() -> None:
    example = (REPO_ROOT / "docker" / "macmini.env.example").read_text(encoding="utf-8")
    names = set()
    for line in example.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        assert re.fullmatch(r"DSA_MACMINI_[A-Z_]+", name)
        assert not any(word in name for word in ("PASSWORD", "TOKEN", "SECRET", "KEY"))
        names.add(name)
        if name in {"DSA_MACMINI_RUNTIME_DIR", "DSA_MACMINI_IMAGE"}:
            assert value == ""
    referenced = set(re.findall(r"\$\{(DSA_MACMINI_[A-Z_]+)", COMPOSE_PATH.read_text(encoding="utf-8")))
    assert referenced == names


def test_macmini_live_jobs_restores_cloud_worker_roles_without_extra_autostarts() -> None:
    server_env = COMPOSE["services"]["server"]["environment"]
    for name in LIVE_SERVER_SWITCHES:
        assert server_env[name] == "${DSA_MACMINI_LIVE_JOBS:-false}"
    for name in (
        "ESSAY_QUANT_AUTO_START", "CONCEPT_THEME_AUTO_START",
        "DATA_STORAGE_MAINTENANCE_AUTO_START", "ICLOUD_KNOWLEDGE_AUTO_START",
        "MARKET_STARTUP_BOOTSTRAP_ENABLED", "INVESTMENT_MONITOR_AUTO_START",
    ):
        assert server_env[name] == "false"
    assert server_env["INVESTMENT_MONITOR_RUN_MODE"] == "external"
    worker_env = COMPOSE["services"]["monitor-worker"]["environment"]
    assert worker_env["CONCEPT_THEME_AUTO_START"] == "${DSA_MACMINI_LIVE_JOBS:-false}"
    assert worker_env["CONCEPT_THEME_RUN_MODE"] == "embedded"


@pytest.mark.parametrize("key", ["DSA_MACMINI_CONFIG_READ_ONLY", "DSA_MACMINI_TYC_READ_ONLY"])
@pytest.mark.parametrize("value,confirmed,accepted", [
    ("true", "false", True),
    ("false", "false", False),
    ("false", "true", True),
    ("invalid", "true", False),
])
def test_macmini_writable_private_mounts_require_production_confirmation(
    tmp_path, key, value, confirmed, accepted,
) -> None:
    result = _run_guard(tmp_path, config_text=GOOD_CONFIG, overrides={
        key: value, "DSA_MACMINI_PRODUCTION_CONFIRMED": confirmed,
    })
    assert (result.returncode == 0) is accepted


@pytest.mark.parametrize("read_only_key", ["DSA_MACMINI_CONFIG_READ_ONLY", "DSA_MACMINI_TYC_READ_ONLY"])
def test_macmini_full_production_refuses_function_losing_read_only_mounts(tmp_path, read_only_key) -> None:
    overrides = {
        "DSA_MACMINI_LIVE_JOBS": "true", "DSA_MACMINI_LIFESPAN": "on",
        "DSA_MACMINI_PRODUCTION_CONFIRMED": "true",
        "DSA_MACMINI_CONFIG_READ_ONLY": "false", "DSA_MACMINI_TYC_READ_ONLY": "false",
    }
    overrides[read_only_key] = "true"
    result = _run_guard(tmp_path, config_text=GOOD_CONFIG, overrides=overrides)
    assert result.returncode != 0
    assert "require writable config and tyc runtime copies" in result.stderr


def test_macmini_private_mount_flags_match_guard_environment() -> None:
    for service in COMPOSE["services"].values():
        mounts = {item["target"]: item for item in service["volumes"]}
        for target, key in (
            ("/app/config", "DSA_MACMINI_CONFIG_READ_ONLY"),
            ("/home/dsa/.tyc", "DSA_MACMINI_TYC_READ_ONLY"),
        ):
            assert mounts[target]["read_only"] == service["environment"][key]


def test_macmini_env_file_can_be_saved_atomically_in_writable_runtime_copy(tmp_path, monkeypatch) -> None:
    # ConfigManager is a side-effect-free file utility; do not import Config,
    # server or the API lifecycle. All reads/writes below are synthetic fixtures.
    from src.core.config_manager import ConfigManager

    config_dir = tmp_path / "runtime" / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / ".env"
    config_file.write_text(GOOD_CONFIG + "LOG_LEVEL=INFO\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(config_file))
    replacements = []
    original_replace = os.replace

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", record_replace)
    manager = ConfigManager()
    assert manager.env_path == config_file
    assert manager.read_config_map()["LOG_LEVEL"] == "INFO"
    manager.apply_updates([("LOG_LEVEL", "WARNING")], sensitive_keys=set(), mask_token="***")
    assert replacements == [(config_dir / ".env.tmp", config_file)]
    assert manager.read_config_map()["LOG_LEVEL"] == "WARNING"
    assert manager.read_config_map()["ADMIN_AUTH_ENABLED"] == "true"
    assert not (config_dir / ".env.tmp").exists()


def test_macmini_settings_save_requires_writable_parent_not_just_env_file(tmp_path, monkeypatch) -> None:
    from src.core.config_manager import ConfigManager

    config_file = tmp_path / ".env"
    config_file.write_text(GOOD_CONFIG + "LOG_LEVEL=INFO\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(config_file))
    original_open = Path.open

    def deny_sibling_creation(path, mode="r", *args, **kwargs):
        if path == tmp_path / ".env.tmp" and "w" in mode:
            raise PermissionError("synthetic read-only directory")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_sibling_creation)
    manager = ConfigManager()
    with pytest.raises(PermissionError, match="synthetic read-only"):
        manager.apply_updates([("LOG_LEVEL", "WARNING")], sensitive_keys=set(), mask_token="***")
    assert manager.read_config_map()["LOG_LEVEL"] == "INFO"


def test_macmini_healthcheck_requires_application_success_not_redirect() -> None:
    test = COMPOSE["services"]["server"]["healthcheck"]["test"]
    assert test[:3] == ["CMD", "python", "-c"]
    assert "r.status == 200" in test[3]
    assert "json.loads(r.read()).get('status') == 'ok'" in test[3]
    assert "HTTPConnection" in test[3]  # Does not follow 301/302 redirects.


@pytest.mark.parametrize("status,payload,accepted", [
    (200, {"status": "ok"}, True),
    (301, {"status": "ok"}, False),
    (200, {"status": "error"}, False),
    (503, {"status": "ok"}, False),
])
def test_macmini_healthcheck_executes_expected_status_contract(monkeypatch, status, payload, accepted) -> None:
    class Response:
        def read(self):
            return json.dumps(payload).encode("utf-8")

    response = Response()
    response.status = status

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 8000, 5)

        def request(self, method, path):
            assert (method, path) == ("GET", "/api/health")

        def getresponse(self):
            return response

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    monkeypatch.setenv("API_PORT", "8000")
    code = COMPOSE["services"]["server"]["healthcheck"]["test"][3]
    if accepted:
        exec(compile(code, "<macmini-healthcheck>", "exec"), {})
    else:
        with pytest.raises(AssertionError):
            exec(compile(code, "<macmini-healthcheck>", "exec"), {})
