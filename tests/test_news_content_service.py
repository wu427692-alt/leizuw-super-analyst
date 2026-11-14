# -*- coding: utf-8 -*-
"""按需正文抽取：缓存命中不触网、SSRF 被拒、失败降级为结构化结果。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import socket
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.v1.endpoints import investment_monitor as endpoints
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services import news_content_service as service_module
from src.services.news_content_service import (
    MAX_CONTENT_CHARS,
    RETRY_COOLDOWN_SECONDS,
    FetchFailure,
    NewsContentError,
    NewsContentService,
    decode_html,
    extract_article_text,
    fetch_public_html,
    resolve_public_addresses,
    strip_leading_title,
)
from src.storage import DatabaseManager, MonitoringEventContentRecord, MonitoringEventRecord

pytestmark = pytest.mark.unit

ARTICLE_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>宁德时代新品发布</title></head>
<body>
<nav><a href="/">首页</a><a href="/news">快讯</a></nav>
<section class="article">
<h1>宁德时代发布新一代储能电池</h1>
<p>宁德时代今日发布新一代储能电池产品，能量密度较上一代提升明显，公司计划在明年实现量产。</p>
<p>分析师认为该产品将推动行业格局变化，上游材料与设备供应商有望同步受益，产业链订单值得跟踪。</p>
</section>
<script>window.tracker = "should-not-leak";</script>
<footer>版权声明：本文为测试内容</footer>
</body></html>"""


# ---------------------------------------------------------------------------
# 抽取与解码
# ---------------------------------------------------------------------------
def test_extract_prefers_json_ld_article_body_and_drops_markup():
    body = "宁德时代披露新一代电池量产计划，预计明年一季度开始交付，客户结构将保持稳定。" * 2
    html = f"""<!doctype html><html lang="zh-CN"><head>
    <script type="application/ld+json">{json.dumps({"@context": "https://schema.org", "articleBody": body})}</script>
    </head><body><nav>首页 快讯</nav><div>侧栏推荐</div></body></html>"""
    text = extract_article_text(html, url="https://example.com/a")
    assert "宁德时代披露新一代电池量产计划" in text
    assert "侧栏推荐" not in text


def test_extract_readability_keeps_paragraphs_and_drops_scripts(monkeypatch):
    monkeypatch.setattr(service_module, "_extract_newspaper", lambda *args, **kwargs: "")
    text = extract_article_text(ARTICLE_HTML, url="https://example.com/a")
    assert "宁德时代发布新一代储能电池" in text
    assert "能量密度较上一代提升明显" in text
    assert "should-not-leak" not in text
    assert "首页" not in text


def test_extract_returns_empty_string_when_page_has_no_readable_text(monkeypatch):
    monkeypatch.setattr(service_module, "_extract_newspaper", lambda *args, **kwargs: "")
    html = "<html><body><nav><a href='/'>首页</a><a href='/b'>快讯</a></nav></body></html>"
    assert extract_article_text(html) == ""


def test_extract_caps_content_length():
    paragraph = "<p>" + ("宁德时代电池产能扩张与海外客户订单持续增长。" * 3) + "</p>"
    html = f"<html><body><article><h1>标题</h1>{paragraph * 400}</article></body></html>"
    text = extract_article_text(html)
    assert text
    assert len(text) <= MAX_CONTENT_CHARS


def test_extract_drops_app_promo_lines():
    html = """<html><body><article>
    <h1>宁德时代披露订单</h1>
    <p>打开APP</p>
    <p>芝麻AI为您解读全文APP内免费解锁</p>
    <p>登录新浪财经APP 搜索【信披】查看更多考评等级</p>
    <p>宁德时代今日公告称，公司与海外客户签署长期供货协议，金额超过百亿元。</p>
    </article></body></html>"""
    text = extract_article_text(html)
    assert "宁德时代今日公告称" in text
    assert "打开APP" not in text
    assert "免费解锁" not in text
    assert "考评等级" not in text


def test_newspaper_is_only_a_fallback(monkeypatch):
    """readability 已经能抽出正文时不能再去跑 newspaper（首次加载分词约 9s）。"""
    used: list = []

    def forbidden(*args, **kwargs):
        used.append(True)
        raise AssertionError("readability 成功时不应调用 newspaper")

    monkeypatch.setattr(service_module, "_extract_newspaper", forbidden)
    text = extract_article_text(ARTICLE_HTML)
    assert "宁德时代" in text
    assert used == []


def test_strip_leading_title_removes_duplicated_headline():
    text = "宁德时代发布新一代储能电池\n正文第一段。\n正文第二段。"
    stripped = strip_leading_title(text, "宁德时代发布新一代储能电池")
    assert stripped == "正文第一段。\n正文第二段。"
    # 首行不是标题时保持原样
    assert strip_leading_title("正文第一段。", "另一个标题") == "正文第一段。"


def test_decode_html_uses_meta_charset_for_gbk_page():
    markup = '<html><head><meta charset="gbk"><title>中文标题</title></head><body>宁德时代公告</body></html>'
    raw = markup.encode("gbk")
    assert "宁德时代公告" in decode_html(raw, "text/html")
    # 没有声明 charset 的 UTF-8 页面不能变成乱码
    assert "宁德时代公告" in decode_html(markup.encode("utf-8"), "text/html")


# ---------------------------------------------------------------------------
# SSRF 防护
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b"", location=None):
        self.status = status
        self.headers = dict(headers or {})
        if location:
            self.headers["Location"] = location
        self._body = body

    def read1(self, amt: int = 16384):
        chunk, self._body = self._body[:amt], self._body[amt:]
        return chunk

    def close(self):
        return None


class _FakePool:
    def __init__(self, response, requests, hop):
        self._response = response
        self._requests = requests
        self._hop = hop

    def request(self, method, target, **kwargs):
        self._requests.append({"hop": self._hop, "method": method, "target": target, **kwargs})
        return self._response

    def close(self):
        return None


def _patch_fetch(monkeypatch, *, resolver_map, responses):
    """用假的 DNS 与假的连接池驱动真实 fetch_public_html 的校验/重定向逻辑。

    ``connected`` 只记录「真的去连了哪个 IP」，因此校验被拒时必须为空。
    """
    connected: list = []
    requests: list = []

    def resolver(host, port):
        if host not in resolver_map:
            raise OSError(f"unexpected host {host}")
        return list(resolver_map[host])

    def pool_factory(scheme, ip, port, host, timeout):
        hop = len(connected)
        connected.append({"scheme": scheme, "ip": ip, "port": port, "host": host})
        return _FakePool(responses[min(hop, len(responses) - 1)], requests, hop)

    return resolver, pool_factory, connected


@pytest.mark.parametrize(
    "url,address",
    [
        ("https://evil.example.com/a", "127.0.0.1"),
        ("https://evil.example.com/a", "10.1.2.3"),
        ("https://evil.example.com/a", "172.20.0.9"),
        ("https://evil.example.com/a", "192.168.1.10"),
        ("http://evil.example.com/a", "169.254.169.254"),
        ("https://evil.example.com/a", "::1"),
        ("https://evil.example.com/a", "fd00::1"),
    ],
)
def test_fetch_rejects_private_and_reserved_answers(monkeypatch, url, address):
    resolver, pool_factory, connected = _patch_fetch(
        monkeypatch, resolver_map={"evil.example.com": [address]}, responses=[],
    )
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html(url, resolver=resolver, pool_factory=pool_factory)
    assert excinfo.value.reason == "ssrf_blocked"
    assert connected == []  # 校验失败时绝不发起连接


@pytest.mark.parametrize(
    "url,reason",
    [
        ("file:///etc/passwd", "unsupported_scheme"),
        ("ftp://example.com/a", "unsupported_scheme"),
        ("gopher://example.com/a", "unsupported_scheme"),
        ("https://user:secret@example.com/a", "invalid_url"),
        ("https://example.com:8080/a", "invalid_url"),
        ("http://localhost/a", "ssrf_blocked"),
        ("http://127.0.0.1/a", "ssrf_blocked"),
        ("http://[::1]/a", "ssrf_blocked"),
        ("/relative/path", "unsupported_scheme"),
    ],
)
def test_fetch_rejects_unsafe_url_shapes(url, reason):
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html(url)
    assert excinfo.value.reason == reason


def test_fetch_rejects_redirect_to_internal_address(monkeypatch):
    resolved = {
        "news.example.com": ["93.184.216.34"],
        "metadata.internal": ["169.254.169.254"],
    }
    _resolver, pool_factory, connected = _patch_fetch(
        monkeypatch,
        resolver_map=resolved,
        responses=[
            _FakeResponse(status=302, location="http://metadata.internal/latest/meta-data/"),
            _FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=b"<html><body>internal</body></html>"),
        ],
    )
    with pytest.raises(FetchFailure) as excinfo:
        # 公网可访问的 redirect 指向链路本地（云元数据）地址，必须在第二跳前被拒绝
        fetch_public_html(
            "https://news.example.com/redirect",
            resolver=lambda host, port: resolved[host],
            pool_factory=pool_factory,
        )
    assert excinfo.value.reason == "ssrf_blocked"
    assert [item["host"] for item in connected] == ["news.example.com"]


def test_fetch_follows_public_redirect_and_returns_final_url(monkeypatch):
    resolver = lambda host, port: ["93.184.216.34"]
    responses = [
        _FakeResponse(status=301, location="https://news.example.com/final"),
        _FakeResponse(
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=ARTICLE_HTML.encode("utf-8"),
        ),
    ]
    _resolver, pool_factory, connected = _patch_fetch(
        monkeypatch, resolver_map={"news.example.com": ["93.184.216.34"]}, responses=responses,
    )
    final_url, html = fetch_public_html(
        "https://news.example.com/start", resolver=resolver, pool_factory=pool_factory,
    )
    assert final_url == "https://news.example.com/final"
    assert "宁德时代" in html
    assert len(connected) == 2


def test_fetch_stops_after_redirect_limit(monkeypatch):
    _resolver, pool_factory, connected = _patch_fetch(
        monkeypatch,
        resolver_map={"loop.example.com": ["93.184.216.34"]},
        responses=[_FakeResponse(status=302, location="/again")],
    )
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html(
            "https://loop.example.com/a",
            resolver=lambda host, port: ["93.184.216.34"],
            pool_factory=pool_factory,
        )
    assert excinfo.value.reason == "redirect_limit"
    assert len(connected) == service_module.MAX_REDIRECTS + 1


def test_fetch_rejects_non_html_content_type(monkeypatch):
    _resolver, pool_factory, _connected = _patch_fetch(
        monkeypatch,
        resolver_map={"news.example.com": ["93.184.216.34"]},
        responses=[_FakeResponse(status=200, headers={"Content-Type": "application/pdf"}, body=b"%PDF-1.7")],
    )
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html(
            "https://news.example.com/a.pdf",
            resolver=lambda host, port: ["93.184.216.34"],
            pool_factory=pool_factory,
        )
    assert excinfo.value.reason == "not_html"


def test_fetch_rejects_oversized_body(monkeypatch):
    oversized = b"<html><body>" + b"x" * (service_module.MAX_WIRE_BYTES + 1024)
    _resolver, pool_factory, _connected = _patch_fetch(
        monkeypatch,
        resolver_map={"news.example.com": ["93.184.216.34"]},
        responses=[_FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=oversized)],
    )
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html(
            "https://news.example.com/big",
            resolver=lambda host, port: ["93.184.216.34"],
            pool_factory=pool_factory,
        )
    assert excinfo.value.reason == "too_large"


def test_fetch_reports_http_error_without_raising_raw_exception(monkeypatch):
    _resolver, pool_factory, _connected = _patch_fetch(
        monkeypatch,
        resolver_map={"news.example.com": ["93.184.216.34"]},
        responses=[_FakeResponse(status=404, headers={"Content-Type": "text/html"}, body=b"not found")],
    )
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html(
            "https://news.example.com/gone",
            resolver=lambda host, port: ["93.184.216.34"],
            pool_factory=pool_factory,
        )
    assert excinfo.value.reason == "network"
    assert "404" in str(excinfo.value)


def test_benchmark_range_is_blocked_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("NEWS_CONTENT_ALLOW_PROXY_DNS", raising=False)
    resolver, pool_factory, connected = _patch_fetch(
        monkeypatch, resolver_map={"news.example.com": ["198.18.1.218"]}, responses=[],
    )
    with pytest.raises(FetchFailure) as excinfo:
        fetch_public_html("https://news.example.com/a", resolver=resolver, pool_factory=pool_factory)
    assert excinfo.value.reason == "ssrf_blocked"
    assert connected == []


def test_benchmark_range_allowed_only_with_opt_in_and_real_private_still_blocked(monkeypatch):
    monkeypatch.setenv("NEWS_CONTENT_ALLOW_PROXY_DNS", "1")
    _resolver, pool_factory, connected = _patch_fetch(
        monkeypatch,
        resolver_map={"news.example.com": ["198.18.1.218"]},
        responses=[_FakeResponse(status=200, headers={"Content-Type": "text/html"}, body=ARTICLE_HTML.encode())],
    )
    final_url, html = fetch_public_html(
        "https://news.example.com/a",
        resolver=lambda host, port: ["198.18.1.218"],
        pool_factory=pool_factory,
    )
    assert final_url == "https://news.example.com/a"
    assert connected[0]["ip"] == "198.18.1.218"
    assert "宁德时代" in html
    # 打开选项也绝不放过真实内网地址
    for address in ("10.0.0.5", "192.168.1.10", "172.20.0.9", "169.254.169.254", "127.0.0.1"):
        with pytest.raises(FetchFailure) as excinfo:
            fetch_public_html(
                "https://lan.example.com/a",
                resolver=lambda host, port, _a=address: [_a],
                pool_factory=lambda *args, **kwargs: pytest.fail("不允许对内网地址发起连接"),
            )
        assert excinfo.value.reason == "ssrf_blocked"


def test_ssl_context_loads_trusted_roots():
    """部分 Python 发行版默认 CA 为空，必须回退到 certifi，否则 HTTPS 全部失败。"""
    context = service_module._ssl_context()
    assert context.cert_store_stats().get("x509", 0) > 0


def test_resolve_public_addresses_requires_every_answer_to_be_public():
    with pytest.raises(FetchFailure) as excinfo:
        resolve_public_addresses("mixed.example.com", 443, resolver=lambda host, port: ["93.184.216.34", "10.0.0.1"])
    assert excinfo.value.reason == "ssrf_blocked"
    assert resolve_public_addresses("ok.example.com", 443, resolver=lambda host, port: ["93.184.216.34"]) == [
        "93.184.216.34"
    ]


# ---------------------------------------------------------------------------
# 服务编排（临时数据库 + 注入抓取器，全程不触网）
# ---------------------------------------------------------------------------
@pytest.fixture()
def saved_events(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'news-content.db'}")
    for model in (MonitoringEventRecord, MonitoringEventContentRecord):
        model.__table__.create(engine)

    class TemporaryDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session

    repo = InvestmentMonitorRepository(db_manager=TemporaryDatabase())

    def add_event(
        *, summary=None, url="https://news.example.com/article", title="测试快讯",
        metrics=None, raw_payload=None,
    ) -> int:
        with Session(engine) as session:
            row = MonitoringEventRecord(
                source_key="feeds.intelligence", source_name="测试媒体", source_type="rss",
                external_id=f"ext-{title}-{summary is not None}-{url}",
                event_type="news", perspective="investor", title=title, summary=summary,
                url=url, metrics_json=json.dumps(metrics) if metrics is not None else None,
                raw_payload=json.dumps(raw_payload) if raw_payload is not None else None,
                symbol_codes="", event_at=datetime(2026, 11, 14, 1), ingested_at=datetime(2026, 11, 14, 1),
            )
            session.add(row)
            session.commit()
            return int(row.id)

    try:
        yield repo, add_event, engine
    finally:
        engine.dispose()


def _fetcher(html_text=ARTICLE_HTML, calls=None, final_url="https://news.example.com/article"):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        return final_url, html_text

    return fetch


def test_saved_summary_is_returned_without_any_network_call(saved_events, monkeypatch):
    repo, add_event, _engine = saved_events
    identifier = add_event(summary="数据库里已有的正文内容，足够长以便直接展示给用户。")

    def forbidden_fetch(_url):
        pytest.fail("已有 summary 时不允许发起网络请求")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("不允许做 DNS 解析"))
    monkeypatch.setattr(DatabaseManager, "get_instance", lambda: pytest.fail("不允许初始化应用数据库"))
    payload = NewsContentService(repository=repo, fetcher=forbidden_fetch).get_event_content(identifier)
    assert payload["status"] == "ok"
    assert payload["source"] == "db"
    assert payload["content"] == "数据库里已有的正文内容，足够长以便直接展示给用户。"
    assert payload["fetchedAt"] is None
    assert repo.get_event_content_cache(identifier) is None


def test_missing_summary_fetches_once_then_serves_from_cache(saved_events):
    repo, add_event, _engine = saved_events
    identifier = add_event()
    calls: list = []
    service = NewsContentService(repository=repo, fetcher=_fetcher(calls=calls))

    first = service.get_event_content(identifier)
    assert first["status"] == "ok"
    assert first["source"] == "fetched"
    assert "宁德时代" in first["content"]
    assert first["contentChars"] == len(first["content"])
    assert first["url"] == "https://news.example.com/article"
    assert first["fetchedAt"]

    second = service.get_event_content(identifier)
    assert second["status"] == "ok"
    assert second["source"] == "cache"
    assert second["content"] == first["content"]
    assert calls == ["https://news.example.com/article"]


def test_source_url_is_recovered_from_metrics_when_column_is_empty(saved_events):
    repo, add_event, _engine = saved_events
    # long_news 的真实形态：url 列为空，链接只在 metrics/raw_payload 里
    identifier = add_event(url=None, metrics={"url": "https://news.example.com/from-metrics"})
    calls: list = []
    payload = NewsContentService(
        repository=repo,
        fetcher=_fetcher(calls=calls, final_url="https://news.example.com/from-metrics"),
    ).get_event_content(identifier)
    assert payload["status"] == "ok"
    assert payload["url"] == "https://news.example.com/from-metrics"
    assert calls == ["https://news.example.com/from-metrics"]


def test_source_url_is_recovered_from_raw_payload_as_last_resort(saved_events):
    repo, add_event, _engine = saved_events
    identifier = add_event(url=None, raw_payload={"url": "https://news.example.com/from-payload"})
    calls: list = []
    payload = NewsContentService(
        repository=repo,
        fetcher=_fetcher(calls=calls, final_url="https://news.example.com/from-payload"),
    ).get_event_content(identifier)
    assert payload["status"] == "ok"
    assert payload["url"] == "https://news.example.com/from-payload"
    assert calls == ["https://news.example.com/from-payload"]


def test_event_without_url_returns_empty_without_network(saved_events):
    repo, add_event, _engine = saved_events
    identifier = add_event(url=None)
    calls: list = []
    payload = NewsContentService(repository=repo, fetcher=_fetcher(calls=calls)).get_event_content(identifier)
    assert payload["status"] == "empty"
    assert payload["reason"] == "no_url"
    assert payload["content"] is None
    assert calls == []


def test_fetch_failure_degrades_to_structured_failed_and_cools_down(saved_events):
    repo, add_event, _engine = saved_events
    identifier = add_event()
    calls: list = []

    def failing_fetch(url):
        calls.append(url)
        raise FetchFailure("network", "原文网络请求失败")

    service = NewsContentService(repository=repo, fetcher=failing_fetch)
    first = service.get_event_content(identifier)
    assert first["status"] == "failed"
    assert first["reason"] == "network"
    assert first["content"] is None
    assert first["message"] == "原文网络请求失败"
    assert first["retryAfterSeconds"] == RETRY_COOLDOWN_SECONDS

    # 冷却期内重复点开：不再抓取，仍返回结构化结果
    second = service.get_event_content(identifier)
    assert second["status"] == "failed"
    assert second["reason"] == "network"
    assert second["retryAfterSeconds"] > 0
    assert "稍后" in second["message"]
    assert calls == ["https://news.example.com/article"]


def test_cooldown_expires_then_retries_and_succeeds(saved_events):
    repo, add_event, _engine = saved_events
    identifier = add_event()
    state = {"now": datetime(2026, 11, 14, 3, 0, 0), "fail": True, "calls": 0}

    def fetch(_url):
        state["calls"] += 1
        if state["fail"]:
            raise FetchFailure("timeout", "原文抓取超时")
        return "https://news.example.com/article", ARTICLE_HTML

    service = NewsContentService(repository=repo, fetcher=fetch, clock=lambda: state["now"])
    assert service.get_event_content(identifier)["status"] == "failed"
    state["now"] += timedelta(seconds=RETRY_COOLDOWN_SECONDS + 1)
    state["fail"] = False
    recovered = service.get_event_content(identifier)
    assert recovered["status"] == "ok"
    assert recovered["source"] == "fetched"
    assert state["calls"] == 2


def test_page_without_readable_text_is_reported_as_empty(saved_events, monkeypatch):
    repo, add_event, _engine = saved_events
    identifier = add_event()
    monkeypatch.setattr(service_module, "_extract_newspaper", lambda *args, **kwargs: "")
    payload = NewsContentService(
        repository=repo,
        fetcher=_fetcher('<html><body><nav><a href="/">首页</a></nav></body></html>'),
    ).get_event_content(identifier)
    assert payload["status"] == "empty"
    assert payload["reason"] == "no_text"
    assert payload["retryAfterSeconds"] == RETRY_COOLDOWN_SECONDS


def test_unexpected_fetch_crash_never_escapes_as_exception(saved_events):
    repo, add_event, _engine = saved_events
    identifier = add_event()

    def broken_fetch(_url):
        raise RuntimeError("boom")

    payload = NewsContentService(repository=repo, fetcher=broken_fetch).get_event_content(identifier)
    assert payload["status"] == "failed"
    assert payload["reason"] == "fetch_failed"
    assert payload["message"] == "原文抓取失败"


def test_unknown_event_raises_news_content_error(saved_events):
    repo, add_event, _engine = saved_events
    add_event()
    with pytest.raises(NewsContentError):
        NewsContentService(repository=repo, fetcher=_fetcher()).get_event_content(999999)
    with pytest.raises(NewsContentError):
        NewsContentService(repository=repo, fetcher=_fetcher()).get_event_content("not-an-id")


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------
@pytest.fixture()
def http_client(saved_events, monkeypatch):
    repo, add_event, _engine = saved_events
    state = {"summary": None, "fetch_calls": 0}

    def build_service(**kwargs):
        def fetch(url):
            state["fetch_calls"] += 1
            if state["fetch_calls"] > 1:
                raise FetchFailure("network", "原文网络请求失败")
            return url, ARTICLE_HTML

        return NewsContentService(repository=repo, fetcher=fetch, **kwargs)

    monkeypatch.setattr(endpoints, "NewsContentService", build_service)
    app = FastAPI()
    app.include_router(endpoints.router, prefix="/api/v1/investment-monitor")
    with TestClient(app) as client:
        yield client, repo, add_event, state


def test_http_content_returns_documented_payload(http_client):
    client, _repo, add_event, _state = http_client
    identifier = add_event()
    response = client.get(f"/api/v1/investment-monitor/events/{identifier}/content")
    assert response.status_code == 200
    body = response.json()
    assert body["eventId"] == identifier
    assert body["status"] == "ok"
    assert body["source"] == "fetched"
    assert "宁德时代" in body["content"]
    assert body["reason"] is None
    assert body["retryAfterSeconds"] == 0
    assert set(body) == {
        "eventId", "title", "status", "content", "contentChars", "source",
        "url", "fetchedAt", "reason", "message", "retryAfterSeconds",
    }


def test_http_content_failure_stays_200_with_status_failed(http_client):
    client, _repo, add_event, state = http_client
    identifier = add_event()
    state["fetch_calls"] = 1  # 让注入的抓取器直接失败
    response = client.get(f"/api/v1/investment-monitor/events/{identifier}/content")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["reason"] == "network"
    assert body["content"] is None


def test_http_missing_event_keeps_404_contract(http_client):
    client, _repo, add_event, _state = http_client
    add_event()
    response = client.get("/api/v1/investment-monitor/events/999999/content")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "investment_monitor_error"


def test_http_non_integer_event_id_is_validated(http_client):
    client, _repo, _add_event, _state = http_client
    response = client.get("/api/v1/investment-monitor/events/not-an-id/content")
    assert response.status_code == 422
