from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.services.ai_news_service import AiNewsService, SOURCES, group_items, parse_feed, public_link

NOW = datetime(2026, 9, 30, 9, tzinfo=timezone.utc).timestamp()


def rss(title="GPT-6 model research release", url="https://example.com/news/6", date="Wed, 30 Sep 2026 08:00:00 GMT"):
    return f'<rss><channel><item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()


def test_parser_metadata_only_and_canonical_url():
    rows = parse_feed(rss(url="https://example.com/news/6?utm_source=rss&amp;keep=yes#x"), SOURCES[0], NOW)
    assert rows[0]["url"] == "https://example.com/news/6?keep=yes"
    assert rows[0]["topic"] == "models"
    assert rows[0]["published_at"] == "2026-09-30T08:00:00Z"
    assert rows[0]["summary"] is None
    assert "body" not in rows[0]


def test_atom_and_missing_date_are_not_fake_published_dates():
    xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Agent code tools</title><link href="https://example.com/agents" rel="alternate"/></entry></feed>'
    row = parse_feed(xml, SOURCES[0], NOW)[0]
    assert row["published_at"] is None
    assert row["time_basis"] == "discovered"
    assert row["topic"] == "agents"


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://127.0.0.1/x", "http://127.1/x", "http://0177.0.0.1/x", "http://0x7f.0.0.1/x", "http://169.254.169.254/", "https://localhost/x", "https://a.local/x", "https://user:pass@example.com/", "https://example.com:22/", "https://example.com\\@evil.com/", "https://example.com/\n"])
def test_reject_bad_links(value):
    assert public_link(value) is None


def test_reject_multicast_destinations(monkeypatch):
    from src.services import ai_news_service as module
    assert public_link("https://224.0.0.1/feed") is None
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("224.0.0.1", 443))])
    with pytest.raises(ValueError, match="non_public_dns"):
        module.resolve_public_addresses("example.com")


@pytest.mark.parametrize("xml", [b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>', '<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>'.encode('utf-16'), b'<html>not RSS</html>'])
def test_reject_unsafe_xml(xml):
    with pytest.raises(ValueError):
        parse_feed(xml, SOURCES[0], NOW)


def test_html_doctype_inside_cdata_is_not_an_xml_declaration():
    xml = rss().replace(b"</item>", b'<description><![CDATA[<!DOCTYPE html><p>Public short summary</p>]]></description></item>')
    row = parse_feed(b"\xef\xbb\xbf" + xml, SOURCES[0], NOW)[0]
    assert row["summary"] == "Public short summary"
    for extra in [b'<!DOCTYPE rss [<!ENTITY x "boom">]>', b'<!ENTITY x "boom">']:
        with pytest.raises(ValueError, match="unsafe_or_oversized_xml"):
            parse_feed(extra + xml, SOURCES[0], NOW)


def test_summaries_are_bounded_sanitized_and_ignore_full_article_body():
    xml = rss().replace(b"</item>", ('<description><![CDATA[<style>hidden</style><script>bad()</script><p>' + '公开摘要' * 100 + '</p>]]></description><content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">full article</content:encoded></item>').encode())
    row = parse_feed(xml, SOURCES[0], NOW)[0]
    assert len(row["summary"]) <= 161
    assert row["summary_origin"] == "publisher_feed"
    assert not any(value in row["summary"] for value in ["hidden", "bad", "full article", "<"])
    from src.services.ai_news_service import short_summary
    assert len(short_summary("word " * 100, "title").split()) == 25
    body_only = rss().replace(b"</item>", b'<content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">not a description</content:encoded></item>')
    assert parse_feed(body_only, SOURCES[0], NOW)[0]["summary"] is None


def test_general_technology_source_requires_explicit_ai_title():
    source = next(s for s in SOURCES if s[0] == "ithome")
    for title in ["苹果发布手机", "百度发布二季度财报", "松下发布USB充电器", "保时捷升级CarPlay", "detail of daily train timetable", "New battery charging technology"]:
        assert parse_feed(rss(title=title), source, NOW) == []
    for title in ["DeepSeek 新模型发布", "微软推出多模态 AI 研究系统", "百度文心一言升级", "具身智能机器人发布", "OpenAI发布新版", "Anthropic与SpaceX签署", "DeepSeek开源"]:
        assert len(parse_feed(rss(title=title), source, NOW)) == 1


def test_future_and_old_entries_are_excluded():
    assert not parse_feed(rss(date="Wed, 30 Sep 2027 08:00:00 GMT"), SOURCES[0], NOW)
    assert not parse_feed(rss(date="Wed, 30 Sep 2020 08:00:00 GMT"), SOURCES[0], NOW)


def test_grouping_distinct_publishers_and_version_guard():
    a = parse_feed(rss(), SOURCES[1], NOW)[0]
    b = parse_feed(rss(), SOURCES[2], NOW)[0]
    c = parse_feed(rss(), SOURCES[10], NOW)[0]
    grouped = group_items([a, b], NOW)
    assert grouped[0]["source_count"] == 1  # Google's two feeds are not two independent sources.
    assert not grouped[0]["multi_source_hot"]
    assert group_items([a, b, c], NOW)[0]["source_count"] == 2
    d = parse_feed(rss(title="Model 7 research release", url="https://example.com/news/7"), SOURCES[10], NOW)[0]
    assert len(group_items([a, d], NOW)) == 2


def test_older_multi_source_event_is_not_current_hot():
    rows = [parse_feed(rss(date="Sun, 27 Sep 2026 08:00:00 GMT"), s, NOW)[0] for s in [SOURCES[0], SOURCES[10]]]
    group = group_items(rows, NOW)[0]
    assert group["source_count"] == 2
    assert not group["multi_source_hot"]
    assert group["score"] == 0


def test_grouping_does_not_merge_opposing_claims():
    a = parse_feed(rss(title="OpenAI releases new model without safety testing", url="https://example.com/a"), SOURCES[0], NOW)[0]
    b = parse_feed(rss(title="OpenAI releases new model with safety testing", url="https://example.com/b"), SOURCES[10], NOW)[0]
    assert len(group_items([a, b], NOW)) == 2


def test_grouping_scales_to_cache_limit():
    import time
    prototype = parse_feed(rss(), SOURCES[0], NOW)[0]
    rows = [{**prototype, "id": str(n), "title": f"A unique headline about different AI project {n}", "url": f"https://example.com/{n}"} for n in range(5000)]
    start = time.monotonic()
    assert len(group_items(rows, NOW)) == 5000
    assert time.monotonic() - start < 1.0


def test_fetch_rejects_private_dns(monkeypatch):
    from src.services import ai_news_service as module
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="non_public_dns"):
        module.fetch_feed(SOURCES[0], {})


def test_fetch_conditional_headers_tls_and_read_deadline(monkeypatch):
    from src.services import ai_news_service as module
    monkeypatch.setattr(module, "resolve_public_addresses", lambda host: ["1.1.1.1"])
    ticks = iter([0, 26])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    response = Mock(status=200, headers={})
    response.read1.return_value = b"x"
    pool = Mock()
    pool.request.return_value = response
    factory = Mock(return_value=pool)
    monkeypatch.setattr(module.urllib3, "HTTPSConnectionPool", factory)
    with pytest.raises(TimeoutError, match="source_deadline"):
        module.fetch_feed(SOURCES[0], {"etag": "previous", "modified": "date"})
    assert factory.call_args.kwargs["assert_hostname"] == "openai.com"
    assert pool.request.call_args.kwargs["headers"]["If-None-Match"] == "previous"
    response.stream.assert_not_called()
    response.read1.assert_called_once_with(16384, decode_content=False)
    response.close.assert_called_once()
    pool.close.assert_called_once()


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_compressed_feed_is_decoded_with_both_size_bounds(monkeypatch, encoding):
    import gzip
    import zlib
    from src.services import ai_news_service as module
    monkeypatch.setattr(module, "resolve_public_addresses", lambda host: ["1.1.1.1"])
    compress = gzip.compress if encoding == "gzip" else zlib.compress
    response = Mock(status=200, headers={"Content-Encoding": encoding})
    response.read1.side_effect = [compress(rss()), b""]
    pool = Mock()
    pool.request.return_value = response
    monkeypatch.setattr(module.urllib3, "HTTPSConnectionPool", Mock(return_value=pool))
    assert module.fetch_feed(SOURCES[0], {})[0] == rss()
    monkeypatch.setattr(module, "MAX_FEED_BYTES", 1000)
    response.read1.side_effect = [compress(b"a" * 100_000), b""]
    with pytest.raises(ValueError, match="feed_too_large"):
        module.fetch_feed(SOURCES[0], {})
    response.read1.side_effect = [b"x" * 1001, b""]
    with pytest.raises(ValueError, match="feed_too_large"):
        module.fetch_feed(SOURCES[0], {})
    response.read1.side_effect = [compress(rss())[:-4], b""]
    with pytest.raises(ValueError, match="incomplete_content_encoding"):
        module.fetch_feed(SOURCES[0], {})
    response.read1.side_effect = [compress(rss()) + compress(rss()), b""]
    with pytest.raises(ValueError, match="invalid_content_encoding"):
        module.fetch_feed(SOURCES[0], {})


def test_cache_read_holds_lock_through_read_and_replace(tmp_path):
    service = AiNewsService(tmp_path / "cache.json")
    service.path.write_text(json.dumps({"version": 1, "items": [], "sources": {}, "updated_at": None}))
    real_path = service.path
    class InstrumentedPath:
        def stat(self):
            assert service._lock.locked()
            return real_path.stat()
        def read_text(self, **kwargs):
            assert service._lock.locked()
            return real_path.read_text(**kwargs)
    service.path = InstrumentedPath()
    service._read_cache()
    assert service._stamp == real_path.stat().st_mtime_ns


def test_cache_incremental_persist_filter_backoff_and_stale(tmp_path):
    calls = []
    clock = [NOW]
    def fetch(source, state):
        calls.append(source[0])
        return rss(), {"etag": "v1"}
    path = tmp_path / "news/cache.json"
    service = AiNewsService(path, fetcher=fetch, clock=lambda: clock[0])
    service.refresh_due()
    first = service.feed(view="latest")
    assert first["total"] == len(SOURCES)
    assert first["stats"]["healthy_sources"] == len(SOURCES)
    assert len(service.feed(view="hot")["items"]) == 1
    assert service.feed(source="openai")["total"] == 1
    assert service.feed(q="not-a-headline")["total"] == 0
    assert service.feed(view="latest", page_size=3, page=2)["page"] == 2
    service.refresh_due()
    assert len(calls) == len(SOURCES)  # Not each search/view/poll.
    restored = AiNewsService(path, clock=lambda: clock[0])
    assert restored.feed(view="latest")["total"] == first["total"]
    def fail(source, state):
        raise RuntimeError("private upstream response not for browser")
    service.fetcher = fail
    clock[0] += 3601
    service.refresh_due()
    assert service.feed(view="latest")["total"] == first["total"]
    assert "private upstream" not in json.dumps(service.feed())
    assert service.feed()["stats"]["healthy_sources"] == 0
    assert all(s["status"] == "error" for s in service.feed()["sources"])
    clock[0] += 11000
    assert service.feed()["stale"]


def test_discovery_time_survives_repeated_missing_date(tmp_path):
    clock = [NOW]
    service = AiNewsService(tmp_path / "cache.json", fetcher=lambda *_: (rss(date=""), {}), clock=lambda: clock[0])
    service.refresh_due()
    before = service.feed()["items"][0]["discovered_at"]
    clock[0] += 4000
    service.refresh_due()
    assert service.feed()["items"][0]["discovered_at"] == before


def test_fast_source_intervals_and_checks_are_not_new_content(tmp_path):
    from src.services.ai_news_service import PARSER_REVISION
    clock, calls = [NOW], []
    def fetch(source, state):
        calls.append(source[0])
        return (None if state.get("etag") else rss()), {"etag": "same"}
    service = AiNewsService(tmp_path / "cache.json", fetcher=fetch, clock=lambda: clock[0])
    service.refresh_due()
    first = service.feed()
    assert first["content_updated_at"] == first["last_checked_at"]
    assert first["latest_published_at"] == "2026-09-30T08:00:00Z"
    assert first["stats"]["items_12h"] == len(SOURCES)
    assert first["stats"]["items_24h"] == len(SOURCES)
    clock[0] += 301
    calls.clear()
    service.refresh_due()
    second = service.feed()
    assert set(calls) == {s[0] for s in SOURCES if s[3] == "media"}
    assert second["content_updated_at"] == first["content_updated_at"]
    assert second["last_checked_at"] != first["last_checked_at"]
    assert all(s["new_items"] == 0 for s in second["sources"] if s["kind"] == "media")
    assert all(v["parser_revision"] == PARSER_REVISION for v in service._state["sources"].values())
    clock[0] += 601
    calls.clear()
    service.refresh_due()
    assert set(calls) == {s[0] for s in SOURCES}


def test_old_cache_enriches_once_and_failure_backoff_is_bounded(tmp_path):
    from src.services.ai_news_service import PARSER_REVISION
    clock, seen = [NOW], []
    def fail(source, state):
        seen.append(state)
        raise ValueError("upstream_unavailable")
    service = AiNewsService(tmp_path / "cache.json", fetcher=fail, clock=lambda: clock[0])
    service._state["sources"] = {s[0]: {"etag": "old", "last_attempt": "2026-09-30T09:00:00Z", "next_attempt": NOW + 21600, "failures": 6} for s in SOURCES}
    service.refresh_due()
    assert all("etag" not in state for state in seen)
    assert all(state["next_attempt"] == NOW + 1800 for state in service._state["sources"].values())
    assert all(state["parser_revision"] == PARSER_REVISION for state in service._state["sources"].values())
    assert all("etag" not in state for state in service._state["sources"].values())
    seen.clear()
    service.refresh_due()
    assert not seen


def test_interval_configuration_is_bounded(monkeypatch):
    from src.services.ai_news_service import source_interval
    monkeypatch.setenv("AI_NEWS_MEDIA_INTERVAL_SECONDS", "0")
    assert source_interval(next(s for s in SOURCES if s[3] == "media")) == 300
    monkeypatch.setenv("AI_NEWS_OFFICIAL_INTERVAL_SECONDS", "90000")
    assert source_interval(SOURCES[0]) == 3600
    monkeypatch.setenv("AI_NEWS_OFFICIAL_INTERVAL_SECONDS", "invalid")
    assert source_interval(SOURCES[0]) == 900


def test_search_matches_original_chinese_or_english_alias_and_keeps_word_and(tmp_path):
    service = AiNewsService(tmp_path / "cache.json", clock=lambda: NOW)
    source = next(s for s in SOURCES if s[0] == "ithome")
    service._state["items"] = [
        parse_feed(rss(title=title, url=f"https://example.com/{n}"), source, NOW)[0]
        for n, title in enumerate(["微软发布AI研究系统", "Microsoft announces AI research", "微软AI产品更新", "谷歌发布AI研究系统", "Google releases AI research", "无关AI研究"])
    ]
    assert service.feed(view="latest", q="微软")["total"] == 3
    assert service.feed(view="latest", q="微软 系统")["total"] == 1
    assert service.feed(view="latest", q="微软 research")["total"] == 1
    assert service.feed(view="latest", q="谷歌")["total"] == 2
    assert service.feed(view="latest", q="微软 谷歌")["total"] == 0
    service._state["items"].append(parse_feed(rss(title="An AI system", url="https://example.com/google-source"), SOURCES[1], NOW)[0])
    assert service.feed(view="latest", q="谷歌", source="deepmind")["total"] == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.app import create_app
    from api.middlewares import auth
    from api.v1.endpoints import ai_news
    monkeypatch.setenv("USER_ACCESS_ENABLED", "true")
    monkeypatch.setenv("AI_NEWS_AUTO_START", "false")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_session", lambda token: token == "test-valid-admin")
    user_service = Mock()
    user_service.return_value.account_for_session.return_value = None
    monkeypatch.setattr(auth, "UserAccountService", user_service)
    monkeypatch.setattr(ai_news, "get_ai_news_service", lambda: AiNewsService(tmp_path / "cache.json", clock=lambda: NOW))
    static = tmp_path / "static"
    (static / "ai-news").mkdir(parents=True)
    (static / "assets/ai-news/assets").mkdir(parents=True)
    (static / "assets/ai-news/assets/main.js").write_text("// static AI page")
    (static / "index.html").write_text("main SPA")
    (static / "ai-news/ai-news.html").write_text("AI news standalone")
    return TestClient(create_app(static_dir=static), follow_redirects=False), auth.COOKIE_NAME


def test_auth_and_standalone_login_dispatch(client):
    session, cookie_name = client
    assert session.get("/api/v1/ai-news/feed").status_code == 401
    assert session.get("/ai-news").status_code == 307
    assert session.head("/ai-news/").status_code == 307
    assert session.get("/access?redirect=%2Fai-news").text == "AI news standalone"
    assert session.get("/assets/ai-news/assets/main.js").status_code == 200
    for redirect in ["https://evil.com/ai-news", "//evil.com/ai-news", "/ai-news-other", "/app", "/ai-news%00"]:
        assert session.get("/access", params={"redirect": redirect}).text == "main SPA"
    session.cookies.set(cookie_name, "test-valid-admin")
    assert session.get("/ai-news").text == "AI news standalone"
    assert session.get("/api/v1/ai-news/feed").status_code == 200
    assert session.get("/api/v1/ai-news/feed").headers["cache-control"] == "private, no-store"
    assert session.get("/api/v1/ai-news/feed?window=all").status_code == 422
    assert session.get("/api/v1/ai-news/feed?page=0").status_code == 422
