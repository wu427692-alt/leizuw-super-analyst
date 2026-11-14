# -*- coding: utf-8 -*-
"""全量 API 端点冒烟测试（后端门禁的「功能可用性」层）。

为什么需要它：仓库里有 350+ 个 pytest 文件，但没有任何一处系统性地保证「全部端点（当前 302 个）
都还挂着、鉴权边界都还对、合法调用不会 500」。改了路由、中间件或某个 router 的
依赖后，单元测试可能全绿而线上某个端点已经 404/500。这个文件把整份端点清单
（``tests/fixtures/api_endpoints.json``，由 ``scripts/list_api_endpoints.py`` 生成）
逐个参数化跑一遍，作为发布前的最低保障。

三层断言（每个端点都会跑到）：

1. ``test_endpoint_registered_and_schema_serializable``
   端点仍注册在 OpenAPI 里，operation schema 可 JSON 序列化（前端 SDK / 契约生成的前提）。
2. ``test_unauthenticated_requests_are_rejected``
   打开鉴权后，非白名单端点必须返回 401/403，而不是 404/405/500 —— 说明「路由存在
   且被鉴权中间件挡住」，不是被悄悄删掉或绕过。
3. ``test_authenticated_endpoint_does_not_return_5xx``
   鉴权关闭（legacy 模式）下用最小参数真实调用：返回 4xx 可以（参数不合法、资源不存在
   都说明路由通），5xx 才是故障。响应必须是可序列化的 JSON（或文件类响应）。

离线与安全约束：
- 本文件用 socket 层拦截所有外网连接（抛 requests.ConnectionError），因此
  ``-m "not network"`` 下可重复运行，不会真的访问行情/公告站点。
- 写接口（POST/PUT/PATCH/DELETE）**不会带业务载荷**：有必填字段的只发 ``{}``、
  字段全可选的只发非法 JSON，保证 handler 不执行、不会改数据或起后台任务。
  流式端点（SSE）与两个离线会重试数十秒的行情端点显式跳过，原因写在
  ``tests/fixtures/api_endpoint_smoke_policy.json``。
- 数据库用临时 SQLite（``DATABASE_PATH`` 指向 tmp），不碰生产库；
  报告/年报/下载目录也重定向到 tmp，避免写坏本机 ``/app`` 之类路径。

已知问题：端点当前确实会 5xx 的，记录在策略文件 ``known_issues`` 里，用 ``xfail``
标记（不阻断发布），修好或状态变化后 xfail 消息会提示从策略文件移除。
详见 ``docs/api-endpoints.md`` 与 ``scripts/list_api_endpoints.py --check-coverage``。
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_smoke_policy as policy_mod  # noqa: E402  (需要在 sys.path 调整后导入)

ROOT = Path(__file__).resolve().parent.parent

BASELINE = policy_mod.load_baseline()
POLICY = policy_mod.load_policy()
ENDPOINTS = BASELINE["endpoints"]
SMOKE = policy_mod.summarize(ENDPOINTS, POLICY)
INVOKED_ENDPOINTS = SMOKE["invoked"]
KNOWN_ISSUES = {entry["endpoint"]: entry for entry in POLICY.get("known_issues", [])}

ENDPOINT_IDS = [policy_mod.endpoint_key(endpoint) for endpoint in ENDPOINTS]
INVOKED_IDS = [policy_mod.endpoint_key(endpoint) for endpoint in INVOKED_ENDPOINTS]

# 路径参数取值：尽量用真实格式（A 股代码 / 日期 / 文件名），让 handler 走到「资源不存在」
# 这一层而不是卡在参数校验上；真正的业务载荷由契约测试负责。
PATH_VALUES = {
    "symbol": "600519",
    "code": "600519",
    "stock_code": "600519",
    "ts_code": "600519.SH",
    "session_id": "smoke-session",
    "task_id": "smoke-task",
    "job_id": "smoke-job",
    "record_id": "1",
    "project_id": "smoke-project",
    "topic_id": "smoke-topic",
    "question_id": "smoke-question",
    "template_id": "smoke-template",
    "source_key": "smoke-source",
    "theme_id": "1",
    "filename": "smoke.txt",
    "api_name": "daily",
    "kind": "audio",
    "asset_id": "smoke-asset",
    "file_id": "smoke-file",
    "workspace": "default",
}
QUERY_VALUES = {
    "symbols": "600519",
    "symbol": "600519",
    "start_date": "2025-01-01",
    "end_date": "2025-01-31",
    "topic": "半导体",
    "workspace": "default",
    "task_id": "smoke-task",
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _isolate_runtime_paths(monkeypatch, tmp):
    """把「import 时就固化 env」的服务目录常量改到 tmp。

    这些模块在 import 时就把 ``os.getenv(...)`` 读进模块常量（生产只 import 一次，
    所以没问题）。但 pytest 收集阶段会 import 一堆测试模块，其中有的会顺带 import
    这些服务，于是常量可能已经固化成本机 /app/data；任何 500 都会变成跑测试顺序的
    函数。冒烟要可重复，这里统一改到 tmp（该默认值问题另行记录，不在测试里修）。
    """
    import importlib

    patches = {
        "src.services.report_pack_service": {
            "DOWNLOAD_DIR": tmp / "downloads",
            "CACHE_DIR": tmp / "report_cache",
            "DB_PATH": str(tmp / "smoke.db"),
        },
        "src.services.annual_report_service": {"DOWNLOAD_DIR": tmp / "annual_reports"},
    }
    for module_name, attrs in patches.items():
        module = importlib.import_module(module_name)
        for attr, value in attrs.items():
            monkeypatch.setattr(module, attr, value, raising=False)



@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """离线的 FastAPI 应用 + 临时数据库 + 隔离的报告目录。"""
    tmp = tmp_path_factory.mktemp("api-endpoint-smoke")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("ENV_FILE", str(tmp / "unused.env"))  # 不读本机 .env 的配置
    monkeypatch.setenv("DATABASE_PATH", str(tmp / "smoke.db"))
    monkeypatch.setenv("USER_ACCESS_ENABLED", "false")
    # 报告/年包/下载目录在部分服务里默认是 Docker 的 /app/data，非容器环境不可写；
    # 冒烟要跨环境可重复，统一指到 tmp（该默认值问题见交付报告，不在本文件修复）。
    monkeypatch.setenv("ANNUAL_REPORT_DIR", str(tmp / "annual_reports"))
    monkeypatch.setenv("REPORT_DOWNLOAD_DIR", str(tmp / "downloads"))
    monkeypatch.setenv("REPORT_CACHE_DIR", str(tmp / "report_cache"))
    monkeypatch.setenv("ALPHASIFT_DATA_DIR", str(tmp / "alphasift"))

    from src.config import Config
    from src.storage import DatabaseManager

    Config.reset_instance()
    DatabaseManager.reset_instance()
    DatabaseManager(db_url=f"sqlite:///{tmp / 'smoke.db'}")

    _isolate_runtime_paths(monkeypatch, tmp)

    from api.app import create_app
    from api.v1.endpoints import stock_logo

    # logo 缓存默认写仓库 data/ 目录；冒烟要只读，指到 tmp 并清空进程内负缓存。
    stock_logo._CACHE_DIR = tmp / "stock_logos"  # noqa: SLF001 - 测试隔离
    stock_logo._misses.clear()  # noqa: SLF001

    app = create_app(static_dir=tmp / "static")
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(client=client, app=app, tmp=tmp)
    finally:
        monkeypatch.undo()
        DatabaseManager.reset_instance()
        Config.reset_instance()


@pytest.fixture(scope="module", autouse=True)
def offline_network():
    """拦截所有外网连接，保证冒烟离线可重复、不会真的访问数据源。"""
    import requests

    class OfflineNetworkBlocked(requests.exceptions.ConnectionError):
        """离线冒烟专用的连接异常（既是 requests 异常，也是 OSError）。"""

    def blocked(*_args, **_kwargs):
        raise OfflineNetworkBlocked("OFFLINE_SMOKE_NETWORK_BLOCKED")

    saved = {
        ("socket", "socket", "connect"): socket.socket.connect,
        ("socket", "socket", "connect_ex"): socket.socket.connect_ex,
        ("socket", "create_connection"): socket.create_connection,
        ("socket", "getaddrinfo"): socket.getaddrinfo,
    }
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    try:
        yield
    finally:
        socket.socket.connect = saved[("socket", "socket", "connect")]
        socket.socket.connect_ex = saved[("socket", "socket", "connect_ex")]
        socket.create_connection = saved[("socket", "create_connection")]
        socket.getaddrinfo = saved[("socket", "getaddrinfo")]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_request(endpoint):
    """把端点模板变成可调用的 (path, query_params)。"""
    path = endpoint["path"]
    for param in endpoint.get("path_params", []):
        name = param["name"]
        if param.get("type") in ("integer", "number"):
            value = "1"
        else:
            value = PATH_VALUES.get(name, "smoke-value")
        path = path.replace("{" + name + "}", value)
    params = {name: QUERY_VALUES.get(name, "smoke-value") for name in endpoint.get("query_required", [])}
    return path, params


def _request(client, endpoint):
    """按策略发出「最小、无副作用」的请求。"""
    kind, _reason = policy_mod.classify(endpoint, POLICY)
    path, params = _build_request(endpoint)
    method = endpoint["method"]
    if kind == policy_mod.INVOKE_READ:
        return client.request(method, path, params=params or None)
    if kind == policy_mod.INVOKE_BODY_EMPTY:
        return client.request(method, path, params=params or None, json={})
    if kind == policy_mod.INVOKE_BODY_MALFORMED:
        return client.request(
            method,
            path,
            params=params or None,
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
    raise AssertionError(f"{policy_mod.endpoint_key(endpoint)} 被策略标记为不调用，不应出现在 INVOKED 里")


def _assert_serializable_json(response, key):
    """响应必须可被读取和重新序列化（前端拿到的就是这份 JSON）。"""
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        # 文件类响应（CSV/图片/ZIP/HEAD）：至少保证字节可读、不是空壳。
        assert isinstance(response.content, bytes), f"{key} 响应不可读"
        return
    payload = response.json()
    dumped = json.dumps(payload, ensure_ascii=False)
    assert dumped is not None, f"{key} 响应无法序列化"
    assert response.content.decode("utf-8") is not None


def _load_endpoint_script():
    """加载 scripts/list_api_endpoints.py（用于基线漂移校验）。"""
    import importlib.util

    script_path = ROOT / "scripts" / "list_api_endpoints.py"
    spec = importlib.util.spec_from_file_location("_list_api_endpoints", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. 基线自身的一致性
# ---------------------------------------------------------------------------


def test_endpoint_baseline_matches_live_openapi(api):
    """端点清单必须和实时 OpenAPI 一致，否则冒烟会测到过期的端点集合。"""
    script = _load_endpoint_script()
    live = script.collect_endpoints()
    live_endpoints = live["endpoints"]
    live_keys = [policy_mod.endpoint_key(endpoint) for endpoint in live_endpoints]
    baseline_keys = ENDPOINT_IDS

    missing = sorted(set(baseline_keys) - set(live_keys))
    added = sorted(set(live_keys) - set(baseline_keys))
    assert not missing and not added, (
        "端点基线已过期，请运行 `.venv/bin/python scripts/list_api_endpoints.py` 重新生成 "
        f"tests/fixtures/api_endpoints.json / docs/api-endpoints.md。\n"
        f"新增（未入基线）：{added}\n删除（基线里还有）：{missing}"
    )

    live_by_key = {policy_mod.endpoint_key(endpoint): endpoint for endpoint in live_endpoints}
    changed = []
    for key in baseline_keys:
        stored = next(endpoint for endpoint in ENDPOINTS if policy_mod.endpoint_key(endpoint) == key)
        current = live_by_key[key]
        comparable = ("tag", "auth", "has_body", "body_required_fields", "query_required")
        if any(stored.get(field) != current.get(field) for field in comparable):
            changed.append(key)
    assert not changed, (
        "端点元信息（tag/鉴权/请求体契约）已变化，请重新生成端点基线。\n"
        f"变化端点：{changed}"
    )
    assert live["totals"]["endpoints"] == BASELINE["totals"]["endpoints"]


def test_smoke_policy_entries_reference_existing_endpoints():
    """策略文件里的每条记录都必须指向真实端点，避免死条目长期堆积。"""
    baseline_keys = set(ENDPOINT_IDS)
    for entry in POLICY.get("not_invoked", []):
        assert entry["endpoint"] in baseline_keys, f"not_invoked 端点不存在：{entry['endpoint']}"
        assert entry.get("category"), f"{entry['endpoint']} 缺少 category"
        assert entry.get("reason"), f"{entry['endpoint']} 缺少 reason"
    for entry in POLICY.get("known_issues", []):
        assert entry["endpoint"] in baseline_keys, f"known_issues 端点不存在：{entry['endpoint']}"
        assert isinstance(entry.get("expected_status"), int), f"{entry['endpoint']} 缺少 expected_status"
        assert entry.get("title") and entry.get("detail"), f"{entry['endpoint']} 缺少 title/detail"
        assert entry["endpoint"] not in {item["endpoint"] for item in POLICY.get("not_invoked", [])}, (
            f"{entry['endpoint']} 同时出现在 not_invoked 和 known_issues"
        )


def test_smoke_coverage_is_wide_enough():
    """冒烟覆盖率不能悄悄缩水：默认要求至少 75% 的端点被真正调用一次。"""
    counts = SMOKE["counts"]
    total = counts["total"]
    assert total == len(ENDPOINTS)
    ratio = counts["invoked"] / total if total else 0.0
    assert ratio >= 0.75, (
        f"冒烟实际调用覆盖率跌到 {ratio:.1%}（{counts['invoked']}/{total}）；"
        "新增的跳过项应写清原因，不能默认为“测不到就不测”。"
    )


# ---------------------------------------------------------------------------
# 2. 每个端点：路由注册 + schema 可序列化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=ENDPOINT_IDS)
def test_endpoint_registered_and_schema_serializable(api, endpoint):
    schema = api.app.openapi()
    operations = schema.get("paths", {}).get(endpoint["path"])
    key = policy_mod.endpoint_key(endpoint)
    assert operations is not None, f"{key} 未出现在 OpenAPI paths 里（路由丢了？）"
    operation = operations.get(endpoint["method"].lower())
    assert operation is not None, f"{key} 未注册该 method"
    assert json.dumps(operation, ensure_ascii=False), f"{key} 的 operation schema 不可序列化"

    # 路径参数必须宣言完整，否则前端/客户端无法拼 URL。
    declared = {param["name"] for param in endpoint.get("path_params", [])}
    template_params = {
        segment.split("}")[0]
        for segment in endpoint["path"].split("{")[1:]
    }
    assert declared == template_params, f"{key} 路径参数声明不完整：{declared} != {template_params}"


# ---------------------------------------------------------------------------
# 3. 每个端点：未鉴权必须 401/403（公开白名单豁免）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=ENDPOINT_IDS)
def test_unauthenticated_requests_are_rejected(api, endpoint, monkeypatch):
    monkeypatch.setattr("api.middlewares.auth.is_auth_enabled", lambda: True)
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")

    path, params = _build_request(endpoint)
    response = api.client.request(endpoint["method"], path, params=params or None)
    key = policy_mod.endpoint_key(endpoint)

    if endpoint["auth"] == "public":
        # 白名单端点：必须能在未登录状态下被路由到（不是 404/405/5xx）。
        assert response.status_code < 500, f"{key} 公开端点返回 {response.status_code}：{response.text[:200]}"
        assert response.status_code not in (404, 405), f"{key} 公开端点路由异常：{response.status_code}"
        return

    assert response.status_code in (401, 403), (
        f"{key} 未登录时应返回 401/403，实际 {response.status_code}：{response.text[:300]}。"
        "若这是新增的公开端点，请更新 api/middlewares/auth.py 的 EXEMPT_PATHS 并重新生成端点基线。"
    )
    if endpoint["method"] == "HEAD":
        # HEAD 按 HTTP 语义没有响应体，无法校验错误 JSON。
        return
    payload = response.json()
    assert isinstance(payload, dict), f"{key} 401/403 响应不是 JSON 对象"
    assert payload.get("error"), f"{key} 401/403 响应缺少 error 字段：{payload}"
    expected_error = "unauthorized" if endpoint["auth"] == "admin" else "user_login_required"
    assert payload["error"] == expected_error, (
        f"{key} 鉴权错误码应为 {expected_error}，实际 {payload['error']}"
    )


# ---------------------------------------------------------------------------
# 4. 每个可调用端点：合法调用不返回 5xx，响应可序列化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", INVOKED_ENDPOINTS, ids=INVOKED_IDS)
def test_authenticated_endpoint_does_not_return_5xx(api, endpoint):
    key = policy_mod.endpoint_key(endpoint)
    response = _request(api.client, endpoint)

    issue = KNOWN_ISSUES.get(key)
    if issue is not None:
        expected = issue["expected_status"]
        if response.status_code == expected:
            pytest.xfail(f"已知问题[{key}] {issue['title']}：{issue['detail'][:120]}")
        pytest.xfail(
            f"已知问题状态变化[{key}]：期望 {expected}，实际 {response.status_code}。"
            f"若已修复请从 tests/fixtures/api_endpoint_smoke_policy.json 移除该条目。"
        )

    assert response.status_code < 500, (
        f"{key} 返回 {response.status_code}（合法调用不应 5xx）：{response.text[:400]}"
    )
    assert response.status_code != 405, f"{key} 返回 405：路径存在但 method 未注册"
    if endpoint["method"] == "HEAD":
        # HEAD 按 HTTP 语义没有响应体，只校验状态与可读性。
        return

    if response.status_code == 404:
        # 路由通、handler 明确说资源不存在 = 正常；FastAPI 默认的 plain 404 说明路径没匹配上。
        body = response.json()
        assert body != {"detail": "Not Found"}, (
            f"{key} 返回了 FastAPI 默认 404（路由未匹配），而不是 handler 的结构化 404"
        )
        assert isinstance(body, (dict, list)), f"{key} 404 响应结构异常：{body!r}"

    _assert_serializable_json(response, key)


# ---------------------------------------------------------------------------
# 5. 公开健康检查别名（不在 OpenAPI schema 里，但部署探针依赖）
# ---------------------------------------------------------------------------


def test_health_aliases_remain_public(api, monkeypatch):
    monkeypatch.setattr("api.middlewares.auth.is_auth_enabled", lambda: True)
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    for path in ("/health", "/api/health", "/api/v1/health"):
        response = api.client.get(path)
        assert response.status_code == 200, f"{path} 健康检查应免鉴权且返回 200，实际 {response.status_code}"
        payload = response.json()
        assert payload.get("status") == "ok", f"{path} 响应缺少 status=ok：{payload}"
        assert "timestamp" in payload
