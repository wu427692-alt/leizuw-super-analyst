# -*- coding: utf-8 -*-
"""关键业务端点的结构化契约测试（前端依赖字段的「不许悄悄改名」保护层）。

冒烟测试（``tests/test_api_endpoint_smoke.py``）只保证「端点还在、鉴权还对、不 500」，
但前端真正依赖的是**响应里的字段名与类型**。改一个字段名，冒烟仍然全绿，页面却白屏。
这个文件对用户主流程涉及的端点做结构化断言：

- ``/api/v1/agent/*``：对话、会话列表/消息、模型与技能
- ``/api/v1/investment-monitor/*``：情报列表、详情、正文按需抽取、看板、状态
- ``/api/v1/stocks/*``：自选列表、实时行情、公司 logo（含 404 降级契约）
- ``/api/v1/user-auth/*``：登录状态、登录失败错误码
- ``/api/v1/home-dashboard``：首页聚合（总览页数据源）
- ``/api/v1/system/*``：系统配置、配置 schema、调度状态、管理员鉴权边界

约定：断言的是**字段名 + 类型 + 必需字段**，不断言具体数值（数据是临时库/假数据）。
若某字段确实要改名，应同步改这里和前端，再改后端 —— 这正是本文件存在的意义。

离线保证：socket 层拦截外网；数据库与各下载目录都在 tmp。
"""

from __future__ import annotations

import inspect
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_smoke_policy as policy_mod  # noqa: E402  (需要在 sys.path 调整后导入)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _isolate_runtime_paths(monkeypatch, tmp):
    """把「import 时就固化 env」的服务目录常量改到 tmp。

    这些模块在 import 时就把 ``os.getenv(...)`` 读进模块常量（生产只 import 一次，
    所以没问题）。但 pytest 收集阶段会 import 一堆测试模块，其中有的会顺带 import
    这些服务，于是常量可能已经固化到本机数据目录；任何 500 都会变成跑测试顺序的
    函数。冒烟要可重复，这里统一改到 tmp（生产默认值现在由 ``DATABASE_PATH`` 推导，
    不再硬编码 ``/app/data``）。
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
    tmp = tmp_path_factory.mktemp("api-contract")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("ENV_FILE", str(tmp / "unused.env"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp / "contract.db"))
    monkeypatch.setenv("USER_ACCESS_ENABLED", "false")
    monkeypatch.setenv("ANNUAL_REPORT_DIR", str(tmp / "annual_reports"))
    monkeypatch.setenv("REPORT_DOWNLOAD_DIR", str(tmp / "downloads"))
    monkeypatch.setenv("REPORT_CACHE_DIR", str(tmp / "report_cache"))
    monkeypatch.setenv("ALPHASIFT_DATA_DIR", str(tmp / "alphasift"))

    from src.config import Config
    from src.storage import DatabaseManager

    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp / 'contract.db'}")

    _isolate_runtime_paths(monkeypatch, tmp)

    from api.app import create_app
    from api.v1.endpoints import stock_logo

    stock_logo._CACHE_DIR = tmp / "stock_logos"  # noqa: SLF001 - 测试隔离，不写仓库 data/
    stock_logo._misses.clear()  # noqa: SLF001

    # 种一条会话、一条新闻事件，让「列表/详情/看板」都有真实数据可断言。
    db.save_conversation_message("sess-contract", "user", "测试问题")
    db.save_conversation_message("sess-contract", "assistant", "测试回答")

    from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
    from src.services.investment_monitor_service import InvestmentMonitorService

    InvestmentMonitorService()  # 注册内置情报源（dashboard 需要 source 元数据）
    repo = InvestmentMonitorRepository(db)
    news_source = next(
        (row for row in repo.list_sources() if str(row.get("category") or "") == "news"),
        {"source_key": "test.news", "name": "测试快讯"},
    )
    repo.upsert_events(
        [
            {
                "source_key": news_source["source_key"],
                "source_name": news_source.get("name") or "测试快讯",
                "source_type": "news",
                "external_id": "contract-event-1",
                "event_type": "news",
                "perspective": "institution",
                "title": "契约测试快讯标题",
                "symbols": ["600519.SH"],
                "sentiment": "bullish",
                "importance_score": 85,
                "url": "https://example.invalid/contract-1",
                "summary": "契约测试快讯正文",
                "raw_payload": {},
            }
        ]
    )

    app = create_app(static_dir=tmp / "static")
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(client=client, app=app, tmp=tmp, db=db)
    finally:
        monkeypatch.undo()
        DatabaseManager.reset_instance()
        Config.reset_instance()


@pytest.fixture(scope="module", autouse=True)
def offline_network():
    """拦截所有外网连接（与冒烟测试同口径），保证离线可重复。"""
    import requests

    class OfflineNetworkBlocked(requests.exceptions.ConnectionError):
        """离线契约测试专用连接异常。"""

    def blocked(*_args, **_kwargs):
        raise OfflineNetworkBlocked("OFFLINE_CONTRACT_NETWORK_BLOCKED")

    saved = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.create_connection,
        socket.getaddrinfo,
    )
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _json(response, key):
    assert response.headers.get("content-type", "").startswith("application/json"), (
        f"{key} 响应不是 JSON：{response.headers.get('content-type')}"
    )
    return response.json()


def _assert_keys(payload, required, key):
    missing = [name for name in required if name not in payload]
    assert not missing, f"{key} 响应缺少字段 {missing}；实际字段 {sorted(payload)}"


# ---------------------------------------------------------------------------
# Agent / 对话
# ---------------------------------------------------------------------------


def test_agent_chat_sessions_contract(api):
    response = api.client.get("/api/v1/agent/chat/sessions")
    assert response.status_code == 200
    payload = _json(response, "agent.chat.sessions")
    _assert_keys(payload, ["sessions"], "agent.chat.sessions")
    assert isinstance(payload["sessions"], list)
    sessions = {item["session_id"]: item for item in payload["sessions"]}
    assert "sess-contract" in sessions, f"种下的会话没有出现在列表里：{sessions}"
    item = sessions["sess-contract"]
    _assert_keys(item, ["session_id", "title", "message_count", "created_at", "last_active"], "agent.chat.sessions[]")
    assert isinstance(item["message_count"], int)
    assert isinstance(item["title"], str)


def test_agent_chat_session_messages_contract(api):
    response = api.client.get("/api/v1/agent/chat/sessions/sess-contract")
    assert response.status_code == 200
    payload = _json(response, "agent.chat.session.messages")
    _assert_keys(payload, ["session_id", "messages"], "agent.chat.session.messages")
    assert payload["session_id"] == "sess-contract"
    assert len(payload["messages"]) == 2
    for message in payload["messages"]:
        _assert_keys(message, ["id", "role", "content", "created_at"], "agent.chat.session.messages[]")
        assert message["role"] in {"user", "assistant", "system", "tool"}
        assert isinstance(message["content"], str)

    # 未知会话：前端依赖「200 + 空消息」来渲染新会话，而不是 404。
    unknown = api.client.get("/api/v1/agent/chat/sessions/no-such-session")
    assert unknown.status_code == 200
    assert _json(unknown, "unknown")["messages"] == []


def test_agent_chat_response_contract(api, monkeypatch):
    executor = SimpleNamespace(
        chat=lambda **_kwargs: SimpleNamespace(success=True, content="你好，我是研究助手", error=None)
    )
    monkeypatch.setattr("api.v1.endpoints.agent.get_config", lambda: SimpleNamespace(is_agent_available=lambda: True))
    monkeypatch.setattr("api.v1.endpoints.agent._build_executor", lambda *_args, **_kwargs: executor)

    response = api.client.post(
        "/api/v1/agent/chat",
        json={"message": "你好", "session_id": "sess-contract"},
    )
    assert response.status_code == 200, response.text
    payload = _json(response, "agent.chat")
    _assert_keys(payload, ["success", "content", "session_id", "error"], "agent.chat")
    assert payload["success"] is True
    assert payload["content"] == "你好，我是研究助手"
    assert payload["session_id"] == "sess-contract"
    assert payload["error"] is None

    # 缺 message 必须 422（前端据此提示“请输入问题”），且错误体是 FastAPI 标准 detail 列表。
    invalid = api.client.post("/api/v1/agent/chat", json={})
    assert invalid.status_code == 422
    detail = _json(invalid, "agent.chat.invalid")["detail"]
    assert isinstance(detail, list) and detail
    assert any("message" in (entry.get("loc") or []) for entry in detail)


def test_agent_models_contract(api, monkeypatch):
    monkeypatch.setattr(
        "api.v1.endpoints.agent.list_agent_model_deployments",
        lambda _config: [
            {
                "deployment_id": "deploy-1",
                "model": "deepseek/deepseek-chat",
                "provider": "deepseek",
                "source": "env",
                "api_base": "https://api.example.invalid",
                "deployment_name": "primary",
                "is_primary": True,
                "is_fallback": False,
            }
        ],
    )
    response = api.client.get("/api/v1/agent/models")
    assert response.status_code == 200
    payload = _json(response, "agent.models")
    _assert_keys(payload, ["models"], "agent.models")
    item = payload["models"][0]
    _assert_keys(
        item,
        ["deployment_id", "model", "provider", "source", "api_base", "deployment_name", "is_primary", "is_fallback"],
        "agent.models[]",
    )
    assert item["is_primary"] is True


def test_agent_skills_contract(api):
    response = api.client.get("/api/v1/agent/skills")
    assert response.status_code == 200
    payload = _json(response, "agent.skills")
    _assert_keys(payload, ["skills", "default_skill_id"], "agent.skills")
    assert isinstance(payload["skills"], list)
    assert isinstance(payload["default_skill_id"], str)
    for skill in payload["skills"]:
        _assert_keys(skill, ["id", "name", "description"], "agent.skills[]")


# ---------------------------------------------------------------------------
# Investment monitor / 情报
# ---------------------------------------------------------------------------


def _find_contract_event(api):
    response = api.client.get("/api/v1/investment-monitor/events", params={"days": 7})
    assert response.status_code == 200
    payload = _json(response, "investment-monitor.events")
    for item in payload["items"]:
        if item.get("title") == "契约测试快讯标题":
            return item
    raise AssertionError(f"种下的情报事件没查到：{[item.get('title') for item in payload['items']]}")


def test_investment_monitor_events_contract(api):
    response = api.client.get("/api/v1/investment-monitor/events", params={"days": 7})
    assert response.status_code == 200
    payload = _json(response, "investment-monitor.events")
    _assert_keys(payload, ["items", "total", "page", "page_size"], "investment-monitor.events")
    assert isinstance(payload["items"], list)
    assert isinstance(payload["total"], int) and isinstance(payload["page"], int)

    item = _find_contract_event(api)
    _assert_keys(
        item,
        [
            "id",
            "source_key",
            "source_name",
            "source_type",
            "event_type",
            "perspective",
            "title",
            "summary",
            "url",
            "symbols",
            "sentiment",
            "importance_score",
            "event_at",
            "ingested_at",
        ],
        "investment-monitor.events[]",
    )
    assert isinstance(item["symbols"], list)
    assert isinstance(item["importance_score"], int)


def test_investment_monitor_event_detail_and_content_contract(api):
    event = _find_contract_event(api)
    event_id = event["id"]

    detail = api.client.get(f"/api/v1/investment-monitor/events/{event_id}")
    assert detail.status_code == 200
    detail_payload = _json(detail, "investment-monitor.event")
    _assert_keys(detail_payload, ["id", "title", "summary", "url"], "investment-monitor.event")
    assert detail_payload["id"] == event_id

    content = api.client.get(f"/api/v1/investment-monitor/events/{event_id}/content")
    assert content.status_code == 200
    payload = _json(content, "investment-monitor.event.content")
    _assert_keys(
        payload,
        ["eventId", "status", "content", "source", "url", "fetchedAt", "contentChars"],
        "investment-monitor.event.content",
    )
    assert payload["eventId"] == event_id
    assert payload["status"] == "ok"
    assert payload["source"] == "db"
    assert payload["content"] == event["summary"]
    assert payload["contentChars"] == len(event["summary"])
    assert payload["url"] == event["url"]

    missing = api.client.get("/api/v1/investment-monitor/events/999999/content")
    assert missing.status_code == 404
    missing_payload = _json(missing, "investment-monitor.event.content.missing")
    _assert_keys(missing_payload, ["error", "message"], "investment-monitor.event.content.missing")


def test_investment_monitor_dashboard_contract(api):
    response = api.client.get("/api/v1/investment-monitor/dashboard")
    assert response.status_code == 200
    payload = _json(response, "investment-monitor.dashboard")
    _assert_keys(
        payload,
        [
            "days",
            "generated_at",
            "watchlist",
            "summary",
            "perspectives",
            "event_types",
            "source_activity",
            "evidence_levels",
            "channels",
            "latest_events",
            "high_priority",
            "latest_news",
            "pinned_news",
            "sources",
        ],
        "investment-monitor.dashboard",
    )
    _assert_keys(
        payload["summary"],
        [
            "event_count",
            "watchlist_count",
            "high_priority_count",
            "bullish_count",
            "bearish_count",
            "active_source_count",
            "factual_count",
            "unverified_count",
            "original_link_count",
            "original_link_coverage",
        ],
        "investment-monitor.dashboard.summary",
    )
    assert isinstance(payload["watchlist"], list)

    news = payload["latest_news"] or payload["pinned_news"]
    assert news, "首页「最近快讯」依赖 latest_news/pinned_news，种下的新闻事件没有进去"
    _assert_keys(
        news[0],
        ["id", "title", "summary", "url", "source_key", "source_name", "event_at", "importance_score"],
        "investment-monitor.dashboard.latest_news[]",
    )


def test_investment_monitor_status_contract(api):
    response = api.client.get("/api/v1/investment-monitor/status")
    assert response.status_code == 200
    payload = _json(response, "investment-monitor.status")
    _assert_keys(payload, ["watchdog", "worker", "sources"], "investment-monitor.status")
    _assert_keys(
        payload["watchdog"],
        ["running", "interval_seconds", "stale_multiplier", "audit_count", "repair_count", "last_error"],
        "investment-monitor.status.watchdog",
    )
    _assert_keys(
        payload["worker"],
        [
            "running",
            "execution_mode",
            "externally_managed",
            "poll_seconds",
            "max_workers",
            "consecutive_failures",
            "last_error",
        ],
        "investment-monitor.status.worker",
    )
    _assert_keys(payload["sources"], ["items"], "investment-monitor.status.sources")
    if payload["sources"]["items"]:
        _assert_keys(
            payload["sources"]["items"][0],
            ["id", "source_key", "name", "category", "enabled"],
            "investment-monitor.status.sources.items[]",
        )


# ---------------------------------------------------------------------------
# Stocks / 行情与 logo
# ---------------------------------------------------------------------------


def test_stocks_watchlist_contract(api):
    response = api.client.get("/api/v1/stocks/watchlist")
    assert response.status_code == 200
    payload = _json(response, "stocks.watchlist")
    _assert_keys(payload, ["stock_codes", "message"], "stocks.watchlist")
    assert isinstance(payload["stock_codes"], list)
    assert all(isinstance(code, str) for code in payload["stock_codes"])
    assert isinstance(payload["message"], str)


def test_stocks_realtime_quote_contract(api, monkeypatch):
    from src.services.market_data_service import MarketDataService

    quote = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "current_price": 1800.0,
        "change": 15.0,
        "change_percent": 0.84,
        "open": 1785.0,
        "high": 1810.0,
        "low": 1780.0,
        "prev_close": 1785.0,
        "volume": 1000.0,
        "amount": 1_800_000.0,
        "second_volume": 0.0,
        "second_amount": 0.0,
        "update_time": "2026-11-14T10:00:00",
        "source": "local.test",
        "stale_seconds": 1.0,
        "is_stale": False,
    }
    monkeypatch.setattr(MarketDataService, "latest_quotes", lambda self, codes, refresh_missing=False: [dict(quote)])

    response = api.client.get("/api/v1/stocks/market-data/realtime", params={"symbols": "600519"})
    assert response.status_code == 200
    payload = _json(response, "stocks.realtime")
    assert isinstance(payload, list) and payload
    _assert_keys(
        payload[0],
        [
            "stock_code",
            "stock_name",
            "current_price",
            "change",
            "change_percent",
            "open",
            "high",
            "low",
            "prev_close",
            "volume",
            "amount",
            "update_time",
            "source",
            "stale_seconds",
            "is_stale",
        ],
        "stocks.realtime[]",
    )
    assert payload[0]["stock_code"] == "600519"


class _FakeResponse:
    """requests.Response 的最小替身（logo 抓取链路只用这几个属性）。"""

    def __init__(self, status_code=200, *, text="", content=b"", headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {}

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        return None


def test_stock_logo_contract_hit_and_fallback(api, monkeypatch):
    from api.v1.endpoints import stock_logo

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    page_html = '<html><img src="https://basic.10jqka.com.cn/ai_data/logo/company/abc123.png"></html>'
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        if "company.html" in url:
            return _FakeResponse(200, text=page_html, content=page_html.encode("utf-8"))
        return _FakeResponse(200, content=png, headers={"Content-Length": str(len(png))})

    monkeypatch.setattr(stock_logo.requests, "get", fake_get)
    monkeypatch.setattr(stock_logo, "_misses", {})  # 清空负缓存

    response = api.client.get("/api/v1/stocks/600519/logo")
    assert response.status_code == 200, response.text
    assert response.headers.get("content-type") == "image/png"
    assert response.content == png
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert "max-age" in response.headers.get("cache-control", "")
    assert len(calls) == 2  # 公司页 + logo 图片

    # 第二次走本地缓存，不应再回源（前端列表页会高频请求）。
    cached = api.client.get("/api/v1/stocks/600519/logo")
    assert cached.status_code == 200
    assert cached.content == png
    assert len(calls) == 2, f"缓存未命中，重复回源：{calls}"


def test_stock_logo_contract_returns_404_for_non_a_share_and_failure(api, monkeypatch):
    from api.v1.endpoints import stock_logo

    def failing_get(url, **_kwargs):
        raise stock_logo.requests.exceptions.ConnectionError("upstream down")

    monkeypatch.setattr(stock_logo.requests, "get", failing_get)
    monkeypatch.setattr(stock_logo, "_misses", {})

    # 非 A 股：直接 404，不访问上游（前端降级为首字色块）。
    for symbol in ("AAPL", "hk00700", "not-a-code"):
        response = api.client.get(f"/api/v1/stocks/{symbol}/logo")
        assert response.status_code == 404, f"{symbol} 应返回 404 而不是 {response.status_code}"
        payload = _json(response, "stocks.logo.404")
        _assert_keys(payload, ["error", "message"], "stocks.logo.404")
        assert payload["error"] == "logo_not_found"

    # A 股但上游失败：也必须 404（绝不能 500 或占位破图）。
    response = api.client.get("/api/v1/stocks/000001/logo")
    assert response.status_code == 404, response.text
    assert _json(response, "stocks.logo.failure")["error"] == "logo_not_found"


def test_http_exception_headers_are_preserved(api):
    """HTTPException(headers=...) 应原样出现在响应里。"""
    response = api.client.get("/api/v1/stocks/AAPL/logo")
    assert response.status_code == 404
    assert response.headers.get("cache-control") == "no-store"


# ---------------------------------------------------------------------------
# User auth / 登录
# ---------------------------------------------------------------------------


def test_user_auth_status_contract(api):
    response = api.client.get("/api/v1/user-auth/status")
    assert response.status_code == 200
    payload = _json(response, "user-auth.status")
    _assert_keys(
        payload,
        ["accessEnabled", "loggedIn", "authMethod", "registrationMode", "smsLoginEnabled", "captcha"],
        "user-auth.status",
    )
    assert isinstance(payload["accessEnabled"], bool)
    assert isinstance(payload["loggedIn"], bool)
    assert payload["authMethod"] in {"disabled", "session", "admin", "sms", "legacy"}
    assert payload["registrationMode"] in {"approval", "self_service"}
    _assert_keys(payload["captcha"], ["provider", "required", "siteKey"], "user-auth.status.captcha")


def test_user_auth_login_error_contract(api):
    response = api.client.post(
        "/api/v1/user-auth/login",
        json={"name": "不存在的用户", "password": "wrongpassword"},
    )
    assert response.status_code == 401, response.text
    payload = _json(response, "user-auth.login.error")
    _assert_keys(payload, ["error", "message"], "user-auth.login.error")
    assert payload["error"] == "invalid_credentials"
    assert isinstance(payload["message"], str) and payload["message"]

    invalid = api.client.post("/api/v1/user-auth/login", json={"name": ""})
    assert invalid.status_code == 422
    assert isinstance(_json(invalid, "user-auth.login.invalid")["detail"], list)


# ---------------------------------------------------------------------------
# Home dashboard / 总览页聚合
# ---------------------------------------------------------------------------


def test_home_dashboard_contract(api, monkeypatch):
    from src.services.home_dashboard_service import HomeDashboardService

    # 首页服务会调度后台刷新线程；契约测试只关心返回结构，关掉后台刷新保证确定性。
    monkeypatch.setattr(HomeDashboardService, "_schedule_refresh", lambda *args, **kwargs: None)
    # 服务有 5 分钟类级缓存，可能装着其它测试模块（空库）的 payload；清掉并强刷，
    # 否则断言「种下的新闻进了 latest_news」会因缓存命中而随机失败。
    HomeDashboardService._cache_payload = None  # noqa: SLF001 - 测试隔离
    HomeDashboardService._cache_at = 0.0  # noqa: SLF001

    response = api.client.get("/api/v1/home-dashboard")
    assert response.status_code == 200
    payload = _json(response, "home-dashboard")
    _assert_keys(
        payload,
        [
            "generated_at",
            "market_time",
            "trade_date",
            "cn_indices",
            "global_indices",
            "breadth",
            "sector_distribution",
            "northbound",
            "watchlist",
            "latest_events",
            "latest_news",
            "pinned_news",
            "intelligence_summary",
            "warnings",
            "cache",
        ],
        "home-dashboard",
    )
    assert isinstance(payload["cn_indices"], list) and payload["cn_indices"], "首页依赖指数列表，不能为空数组"
    _assert_keys(
        payload["cn_indices"][0],
        ["code", "name", "region", "trade_date", "close", "change_pct", "history"],
        "home-dashboard.cn_indices[]",
    )
    _assert_keys(
        payload["breadth"],
        ["available", "up", "down", "flat", "limit_up", "limit_down", "total", "distribution", "reason"],
        "home-dashboard.breadth",
    )
    _assert_keys(
        payload["intelligence_summary"],
        ["event_count", "high_priority_count", "active_source_count", "original_link_count"],
        "home-dashboard.intelligence_summary",
    )

    news = payload["latest_news"] or payload["pinned_news"]
    assert news, "首页「最近快讯」依赖 latest_news/pinned_news，种下的新闻事件没有进去"
    _assert_keys(
        news[0],
        ["id", "title", "summary", "url", "source_key", "source_name", "event_at"],
        "home-dashboard.latest_news[]",
    )


# ---------------------------------------------------------------------------
# System / 系统配置
# ---------------------------------------------------------------------------


def test_system_config_contract(api):
    response = api.client.get("/api/v1/system/config")
    assert response.status_code == 200
    payload = _json(response, "system.config")
    _assert_keys(payload, ["config_version", "mask_token", "items"], "system.config")
    assert isinstance(payload["items"], list) and payload["items"], "设置页依赖 items，不能为空"
    keys = {item["key"] for item in payload["items"]}
    assert "STOCK_LIST" in keys, f"设置页依赖 STOCK_LIST 配置项；实际 keys 前 20：{sorted(keys)[:20]}"
    for item in payload["items"]:
        _assert_keys(item, ["key", "value", "raw_value_exists", "is_masked", "schema"], "system.config.items[]")
        _assert_keys(
            item["schema"],
            ["key", "title", "category", "data_type", "ui_control", "is_sensitive", "is_editable"],
            "system.config.items[].schema",
        )
        if item["is_masked"]:
            assert item["value"] == payload["mask_token"], f"{item['key']} 敏感值应被掩码"


def test_system_config_schema_contract(api):
    response = api.client.get("/api/v1/system/config/schema")
    assert response.status_code == 200
    payload = _json(response, "system.config.schema")
    _assert_keys(payload, ["schema_version", "categories"], "system.config.schema")
    assert isinstance(payload["categories"], list) and payload["categories"]
    category = payload["categories"][0]
    _assert_keys(category, ["category", "title", "fields"], "system.config.schema.categories[]")
    assert category["fields"], "分类下必须有字段"
    _assert_keys(
        category["fields"][0],
        ["key", "title", "category", "data_type", "ui_control", "is_sensitive"],
        "system.config.schema.categories[].fields[]",
    )


def test_system_scheduler_status_contract(api):
    response = api.client.get("/api/v1/system/scheduler/status")
    assert response.status_code == 200
    payload = _json(response, "system.scheduler.status")
    _assert_keys(
        payload,
        [
            "enabled",
            "running",
            "schedule_times",
            "next_run_at",
            "last_run_at",
            "last_success_at",
            "last_error",
            "last_skipped_at",
            "last_skip_reason",
        ],
        "system.scheduler.status",
    )
    assert isinstance(payload["schedule_times"], list)


def test_admin_endpoints_require_admin_session(api, monkeypatch):
    """管理员接口的 401 契约：前端据此跳管理员登录页，字段名不能变。"""
    monkeypatch.setattr("api.middlewares.auth.is_auth_enabled", lambda: True)
    for path in ("/api/v1/system/config", "/api/v1/system/scheduler/status", "/api/v1/usage/summary"):
        response = api.client.get(path)
        assert response.status_code == 401, f"{path} 未带管理员会话时应 401，实际 {response.status_code}"
        payload = _json(response, path)
        _assert_keys(payload, ["error", "message"], path)
        assert payload["error"] == "unauthorized"


# ---------------------------------------------------------------------------
# 通用错误契约
# ---------------------------------------------------------------------------


def test_validation_error_contract(api):
    """422 结构是前端表单提示的依据：detail[].loc / msg / type。"""
    response = api.client.get("/api/v1/stocks/market-data/realtime")
    assert response.status_code == 422
    payload = _json(response, "validation-error")
    detail = payload["detail"]
    assert isinstance(detail, list) and detail
    entry = detail[0]
    _assert_keys(entry, ["loc", "msg", "type"], "validation-error.detail[]")
    assert entry["loc"][0] == "query"
    assert "symbols" in entry["loc"]


# ---------------------------------------------------------------------------
# 已修复缺陷的回归测试
#
# 这些用例原本是 xfail（tests/fixtures/api_endpoint_smoke_policy.json 的 known_issues
# 或本文件的 xfail 装饰器）。缺陷修好后转正，防止错误码/路径语义再次退化：
#   1. 报告服务默认目录硬编码 /app/data → 统一走运行时数据目录
#   2. 巨潮上游不可达 → 503 upstream_unavailable（不是 500）
#   3. TUSHARE_TOKEN 未配置 → 503 dependency_not_configured（不是 500）
#   4. 不支持的附件 kind → 400（不是 502，前端才不会误判上游故障）
#   5. HTTPException(headers=...) 的自定义头必须透传到响应
# ---------------------------------------------------------------------------


def test_report_service_default_dirs_are_portable():
    """两个报告服务的默认目录不能写死 Docker 的 /app/data，必须走统一解析函数。"""
    import src.services.annual_report_service as annual_report_service
    import src.services.report_pack_service as report_pack_service

    offenders = []
    for module in (annual_report_service, report_pack_service):
        source = inspect.getsource(module)
        if '"/app/data' in source or "'/app/data" in source:
            offenders.append(module.__name__)
        assert "runtime_data_directory" in source, f"{module.__name__} 没有复用统一的运行时数据目录解析"
    assert not offenders, f"默认目录仍硬编码 /app/data：{offenders}"


def test_runtime_data_directory_follows_database_path(monkeypatch, tmp_path):
    """数据目录 = DATABASE_PATH 的父目录；默认是 ./data，而不是 Docker 的 /app/data。"""
    from src.config import runtime_data_directory

    db_path = tmp_path / "runtime" / "stock_analysis.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    assert runtime_data_directory() == db_path.parent.resolve()

    monkeypatch.delenv("DATABASE_PATH", raising=False)
    default_dir = runtime_data_directory()
    assert default_dir.name == "data"
    assert str(default_dir) != "/app/data"


def test_report_services_instantiate_without_writable_app_dir(tmp_path):
    """非 Docker 部署（未配置 ANNUAL_REPORT_DIR / REPORT_*）实例化服务不应失败。

    目录常量在 import 时求值，所以用独立子进程验证「干净环境下的首次 import」，
    避免 pytest 进程里已被其它用例 patch 过的常量掩盖问题。
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ANNUAL_REPORT_DIR", "REPORT_DOWNLOAD_DIR", "REPORT_CACHE_DIR"}
    }
    env.update({
        "DATABASE_PATH": str(tmp_path / "data" / "stock_analysis.db"),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    script = (
        "import json\n"
        "from src.services.annual_report_service import AnnualReportService, DOWNLOAD_DIR as annual_dir\n"
        "from src.services.report_pack_service import ReportPackService, DOWNLOAD_DIR, CACHE_DIR\n"
        "AnnualReportService()\n"
        "ReportPackService()\n"
        "print(json.dumps({'annual': str(annual_dir), 'downloads': str(DOWNLOAD_DIR), 'cache': str(CACHE_DIR)}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, f"服务实例化失败（非 Docker 环境）：{completed.stderr[-2000:]}"
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    data_dir = (tmp_path / "data").resolve()
    assert payload["annual"] == str(data_dir / "annual_reports")
    assert payload["downloads"] == str(data_dir / "downloads")
    assert payload["cache"] == str(data_dir / "report_cache")


def test_annual_report_file_reads_from_configured_download_dir(api, monkeypatch, tmp_path):
    """年报文件下载必须落在服务实际使用的下载目录，而不是写死的 /app/data。"""
    import src.services.annual_report_service as annual_report_service

    target = tmp_path / "annual_reports"
    target.mkdir()
    (target / "600519-annual-2024-test.pdf").write_bytes(b"%PDF-1.4\n%contract-test\n")
    monkeypatch.setattr(annual_report_service, "DOWNLOAD_DIR", target)

    response = api.client.get("/api/v1/financial-data/annual-report/file/600519-annual-2024-test.pdf")
    assert response.status_code == 200, response.text
    assert response.headers.get("content-type", "").startswith("application/pdf")

    missing = api.client.get("/api/v1/financial-data/annual-report/file/no-such-file.pdf")
    assert missing.status_code == 404


def test_cninfo_unreachable_maps_to_503(api, monkeypatch):
    """上游不可达（连接/超时/DNS，没有 HTTP 状态码）→ 503 upstream_unavailable。"""
    from src.services.cninfo_announcement_service import CninfoAnnouncementError, CninfoAnnouncementService

    def unreachable(self, *_args, **_kwargs):
        raise CninfoAnnouncementError("巨潮公告查询失败：ConnectionError（请求 3 次）")

    monkeypatch.setattr(CninfoAnnouncementService, "_post_json", unreachable)
    cases = (
        ("/api/v1/financial-data/announcements", {"symbol": "600519"}),
        ("/api/v1/financial-data/annual-report", {"symbol": "600519", "download": "false"}),
    )
    for path, params in cases:
        response = api.client.get(path, params=params)
        assert response.status_code == 503, f"{path} 上游不可达应 503，实际 {response.status_code}：{response.text[:300]}"
        payload = _json(response, path)
        assert payload["error"] == "upstream_unavailable", f"{path} 错误码：{payload}"
        assert payload["message"], f"{path} 缺少可读的上游不可达说明"
        assert response.headers.get("retry-after") == "30"


def test_cninfo_upstream_http_error_maps_to_502(api, monkeypatch):
    """上游返回了 HTTP 错误（拿到了状态码）→ 502，与「不可达」区分开。"""
    from src.services.cninfo_announcement_service import CninfoAnnouncementError, CninfoAnnouncementService

    def upstream_http_error(self, *_args, **_kwargs):
        raise CninfoAnnouncementError("巨潮公告查询失败：HTTP 500（请求 1 次）", status_code=500)

    monkeypatch.setattr(CninfoAnnouncementService, "_post_json", upstream_http_error)
    response = api.client.get("/api/v1/financial-data/announcements", params={"symbol": "600519"})
    assert response.status_code == 502, response.text
    payload = _json(response, "announcements.upstream-http-error")
    assert payload["error"] == "upstream_error"


def test_dragon_tiger_missing_tushare_token_maps_to_503(api, monkeypatch):
    """依赖未配置（TUSHARE_TOKEN 缺失）→ 503 dependency_not_configured，而不是 500。"""
    from src.services.financial_data_service import FinancialDataValidationError
    from src.services.investment_monitor_service import InvestmentMonitorService

    def missing_token(self, **_kwargs):
        raise FinancialDataValidationError("TUSHARE_TOKEN is not configured")

    monkeypatch.setattr(InvestmentMonitorService, "dragon_tiger_daily", missing_token)
    response = api.client.get("/api/v1/investment-monitor/dragon-tiger/daily")
    assert response.status_code == 503, response.text
    payload = _json(response, "dragon-tiger.daily.503")
    assert payload["error"] == "dependency_not_configured"
    assert "TUSHARE_TOKEN" in payload["message"]


def test_unsupported_media_kind_is_client_error_not_upstream_error(api):
    """不支持的附件 kind 是客户端参数错误 → 400，不能伪装成 502 上游故障。"""
    response = api.client.get("/api/v1/financial-data/research-notes/topic-1/media/audio/asset-1")
    assert response.status_code == 400, response.text
    payload = _json(response, "research-note-media.400")
    assert payload["error"] == "validation_error"
    assert "audio" in payload["message"]


def test_endpoint_count_snapshot_is_serializable():
    """端点清单本身要能序列化（脚本/测试共用的基线格式）。"""
    baseline = policy_mod.load_baseline()
    assert json.dumps(baseline, ensure_ascii=False)
    assert baseline["totals"]["endpoints"] == len(baseline["endpoints"])
