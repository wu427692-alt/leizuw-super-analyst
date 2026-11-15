# -*- coding: utf-8 -*-
"""快讯去噪：停采股吧帖子、过滤纯英文标题、同标题多源去重。

用户明确不要的三类内容：
1. 东方财富股吧帖子（``eastmoney.guba_posts``）；
2. 港交所这类纯英文标题的英文公告；
3. 同一篇新闻的多源重复。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import (
    InvestmentMonitorRepository,
    news_dedupe_key,
    news_source_authority,
    normalized_title_key,
)
from src.services.investment_monitor_service import (
    BUILTIN_MONITORING_SOURCES,
    InvestmentMonitorService,
    _is_english_only_title,
)
from src.storage import (
    DatabaseManager,
    DatabaseSchemaMigration,
    MonitoringEventRecord,
    MonitoringSourceRecord,
    REJECTED_MONITOR_NOISE_MIGRATION,
    REJECTED_MONITOR_SOURCE_MIGRATION,
    utc_naive_now,
)

_HKEX_ENGLISH_TITLE = "Appointment of HKEX Risk Management Committee (Statutory) Member"
_SAME_ARTICLE = "国产全直驱数控机床龙头钶锐锶11月10日上市"


def _news_event(
    source_key: str,
    title: str,
    when: datetime,
    *,
    external_id: str = "",
    actors: list | None = None,
    event_type: str = "news",
) -> dict:
    return {
        "source_key": source_key,
        "source_name": source_key,
        "source_type": "api",
        "external_id": external_id or f"{source_key}-{abs(hash(title))}-{when.isoformat()}",
        "event_type": event_type,
        "perspective": "investor",
        "title": title,
        "summary": "正文摘要",
        "url": "https://example.com/news",
        "symbols": ["600519.SH"],
        "sentiment": "neutral",
        "importance_score": 60,
        "confidence_score": 0.8,
        "tags": [],
        "actors": actors or [],
        "metrics": {},
        "raw_payload": {},
        "event_at": when,
    }


@pytest.fixture()
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "denoise.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    repo = InvestmentMonitorRepository(DatabaseManager.get_instance())
    repo.ensure_sources(BUILTIN_MONITORING_SOURCES)
    try:
        yield repo
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


# --- 判据：纯英文标题 -------------------------------------------------------

@pytest.mark.parametrize("title", [
    _HKEX_ENGLISH_TITLE,
    "Hong Kong Exchanges and Clearing Limited announces board changes",
    "Fed officials signal one more rate hike before year end",
])
def test_english_only_titles_are_detected(title):
    assert _is_english_only_title(title) is True


@pytest.mark.parametrize("title", [
    "AI交易依然颠簸",
    "美元指数走高，美股三大指数收跌",
    _HKEX_ENGLISH_TITLE + "（港交所公告）",
    "AAPL",                     # 纯英文但太短：可能是代码标题，保留
    "HKEX Q1 results",          # 20 字符以内，保守保留
    "",
    None,
])
def test_mixed_short_or_empty_titles_are_kept(title):
    assert _is_english_only_title(title) is False


def test_exactly_twenty_ascii_characters_is_kept():
    # 判据是「长度 > 20」，正好 20 个 ASCII 字符仍算短标题
    assert _is_english_only_title("A" * 20) is False
    assert _is_english_only_title("A" * 21) is True


def test_drop_rejected_news_events_only_filters_news_types():
    kept = InvestmentMonitorService._drop_rejected_news_events([
        _news_event("feeds.intelligence", _HKEX_ENGLISH_TITLE, utc_naive_now()),
        _news_event("tushare.news.cls", "AI交易依然颠簸", utc_naive_now()),
        {
            **_news_event("feeds.intelligence", _HKEX_ENGLISH_TITLE, utc_naive_now()),
            "event_type": "company_announcement",
        },
        {**_news_event("feeds.intelligence", _HKEX_ENGLISH_TITLE, utc_naive_now()),
         "event_type": "long_news"},
    ])
    assert [event["event_type"] for event in kept] == [
        "news", "company_announcement",
    ]
    assert kept[0]["title"] == "AI交易依然颠簸"


def test_events_for_source_routes_every_news_path_through_the_filter(monkeypatch, repository):
    service = InvestmentMonitorService(
        repository=repository, initialize_sources=False,
    )
    monkeypatch.setattr(service, "_fetch_events_for_source", lambda source: [
        _news_event("feeds.intelligence", _HKEX_ENGLISH_TITLE, utc_naive_now()),
        _news_event("feeds.intelligence", "港交所公布风险管理委员会成员任命", utc_naive_now()),
    ])
    events = service._events_for_source({"source_key": "feeds.intelligence"})
    assert len(events) == 1
    assert events[0]["title"] == "港交所公布风险管理委员会成员任命"


# --- 停采股吧帖子 -----------------------------------------------------------

def test_guba_source_is_disabled_in_the_builtin_registry():
    guba = next(
        source for source in BUILTIN_MONITORING_SOURCES
        if source["source_key"] == "eastmoney.guba_posts"
    )
    assert guba["enabled"] is False


def test_guba_source_row_is_created_disabled(repository):
    source = repository.get_source("eastmoney.guba_posts")
    assert source is not None
    assert source["enabled"] is False


# --- 归一化与去重键 ---------------------------------------------------------

def test_normalized_title_key_reuses_announcement_normalization():
    # HTML 反转义 / 去标签 / NFKC / casefold / 去空白
    assert normalized_title_key("<b>贵州茅台</b>： 发布 公告") == "贵州茅台:发布公告"
    assert normalized_title_key("ＥＴＦ　份额创新高") == "etf份额创新高"
    # 剥离来源名前缀
    assert normalized_title_key("新华网:贵州茅台发布公告", ["新华网"]) == "贵州茅台发布公告"
    # 公告与新闻共用同一实现
    assert InvestmentMonitorRepository._normalized_announcement_title(
        "新华网:贵州茅台发布公告", ["新华网"],
    ) == "贵州茅台发布公告"


def test_news_dedupe_key_ignores_short_titles():
    assert news_dedupe_key("早盘") is None
    assert news_dedupe_key("涨停") is None
    assert news_dedupe_key(_SAME_ARTICLE) is not None
    assert news_dedupe_key(_SAME_ARTICLE) == news_dedupe_key(_SAME_ARTICLE + " ")


def test_news_source_authority_prefers_mainstream_media():
    assert news_source_authority("tushare.news.cls") > news_source_authority("tushare.long_news")
    assert news_source_authority("tushare.long_news") > news_source_authority("feeds.intelligence")
    assert news_source_authority("tushare.news.yicai") > news_source_authority("tushare.news.10jqka")
    # 未知来源落在默认档，不会被 RSS/NewsNow 反超
    assert news_source_authority("api.newsroom") > news_source_authority("feeds.intelligence")


# --- 写入路径去重 -----------------------------------------------------------

def test_cross_source_duplicate_news_is_not_stored_twice(repository):
    now = utc_naive_now()
    first = repository.upsert_events([
        _news_event("tushare.long_news", _SAME_ARTICLE, now, actors=["新浪财经"]),
    ])
    second = repository.upsert_events([
        _news_event("feeds.intelligence", _SAME_ARTICLE, now + timedelta(minutes=3)),
    ])
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1  # 只把新来源记进 provenance

    with repository.db.get_session() as session:
        rows = session.query(MonitoringEventRecord).all()
    assert len(rows) == 1
    assert rows[0].source_key == "tushare.long_news"
    assert rows[0].dedupe_key == news_dedupe_key(_SAME_ARTICLE)
    import json
    provenance = json.loads(rows[0].metrics_json)["_source_provenance"]
    assert {item["source_key"] for item in provenance} == {
        "tushare.long_news", "feeds.intelligence",
    }


def test_same_source_repeated_headline_is_deduplicated_too(repository):
    now = utc_naive_now()
    repository.upsert_events([
        _news_event("tushare.news.10jqka", "厉娜爆红芒果超媒市值大涨200亿", now, external_id="a1"),
    ])
    result = repository.upsert_events([
        _news_event("tushare.news.10jqka", "厉娜爆红芒果超媒市值大涨200亿", now + timedelta(minutes=1),
                    external_id="a2"),
    ])
    assert result["created"] == 0


def test_duplicate_outside_the_window_is_kept(repository):
    """同一标题在不同日期重新发布是真实的不同事件，不能合并。"""
    now = utc_naive_now()
    repository.upsert_events([
        _news_event("tushare.news.cls", "中际旭创成交额达100亿元", now - timedelta(hours=13)),
    ])
    result = repository.upsert_events([
        _news_event("tushare.news.cls", "中际旭创成交额达100亿元", now),
    ])
    assert result["created"] == 1


def test_short_titles_are_not_deduplicated(repository):
    now = utc_naive_now()
    repository.upsert_events([_news_event("tushare.news.cls", "涨停", now)])
    result = repository.upsert_events([
        _news_event("tushare.news.sina", "涨停", now + timedelta(minutes=1)),
    ])
    assert result["created"] == 1


def test_news_dedupe_does_not_change_announcement_cross_source_rules(repository):
    """公告的跨源合并逻辑归 `_cross_source_announcement_match`，新闻去重不介入。

    同一来源两条同标题公告保持现有语义（同源不合并成一条）。
    """
    now = utc_naive_now()
    metrics = {"disclosure_date": now.date().isoformat()}
    first = {
        **_news_event("cninfo.announcements", "贵州茅台发布公告", now),
        "event_type": "company_announcement",
        "metrics": metrics,
    }
    repository.upsert_events([first])
    second = repository.upsert_events([
        {**_news_event("cninfo.announcements", "贵州茅台发布公告", now),
         "event_type": "company_announcement",
         "external_id": "other-announcement",
         "metrics": metrics},
    ])
    assert second["created"] == 1
    with repository.db.get_session() as session:
        rows = session.query(MonitoringEventRecord).all()
    assert len(rows) == 2
    assert all(row.dedupe_key is None for row in rows)


# --- 存量清理 ---------------------------------------------------------------

def _insert_legacy_event(session, source_key: str, title: str, when: datetime,
                         event_type: str = "news", external_id: str = "") -> int:
    row = MonitoringEventRecord(
        source_key=source_key,
        source_name=source_key,
        source_type="api",
        external_id=external_id or f"{source_key}-{title}-{when.isoformat()}",
        event_type=event_type,
        perspective="investor",
        title=title,
        summary="正文",
        symbol_codes="600519.SH",
        sentiment="neutral",
        importance_score=60,
        confidence_score=0.8,
        tags_json="[]",
        actors_json="[]",
        metrics_json="{}",
        raw_payload="{}",
        event_at=when,
        ingested_at=when,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def test_collect_and_delete_rejected_noise(repository):
    now = utc_naive_now()
    with repository.db.get_session() as session:
        guba_ids = [
            _insert_legacy_event(session, "eastmoney.guba_posts", "今天割肉了，好难受",
                                 now, event_type="stock_forum_post"),
            _insert_legacy_event(session, "eastmoney.guba_posts", "章盟主都走了，不抱幻想咯",
                                 now, event_type="stock_forum_post"),
        ]
        english_ids = [
            _insert_legacy_event(session, "feeds.intelligence", _HKEX_ENGLISH_TITLE, now),
        ]
        # 同标题跨来源重复：feeds.intelligence 更早入库，tushare.news.cls 更权威
        duplicate_ids = [
            _insert_legacy_event(session, "feeds.intelligence", _SAME_ARTICLE,
                                 now - timedelta(hours=1)),
            _insert_legacy_event(session, "tushare.news.cls", _SAME_ARTICLE,
                                 now - timedelta(minutes=30)),
        ]
        kept_id = _insert_legacy_event(session, "tushare.news.yicai", "AI交易依然颠簸", now)
        session.commit()

    report = repository.collect_rejected_monitoring_noise()
    assert report["guba"] == 2
    assert report["english"] == 1
    assert report["duplicate"] == 1
    assert report["total"] == 4
    delete_ids = set(report["delete_ids"])
    assert set(guba_ids) <= delete_ids
    assert set(english_ids) <= delete_ids
    # 更权威的 tushare.news.cls 留下，先入库但权威度低的 feeds.intelligence 被删
    assert duplicate_ids[0] in delete_ids
    assert duplicate_ids[1] not in delete_ids
    assert kept_id not in delete_ids
    assert report["samples"]["delete"]
    assert report["samples"]["keep"]
    # 只读验证：还没删除任何数据
    with repository.db.get_session() as session:
        assert session.query(MonitoringEventRecord).count() == 6

    result = repository.delete_rejected_monitoring_noise()
    assert result["removed"] == 4
    with repository.db.get_session() as session:
        remaining = {int(row.id) for row in session.query(MonitoringEventRecord).all()}
    assert remaining == {duplicate_ids[1], kept_id}


def test_cleanup_ignores_titles_too_short_to_compare(repository):
    now = utc_naive_now()
    with repository.db.get_session() as session:
        _insert_legacy_event(session, "tushare.news.cls", "涨停", now)
        _insert_legacy_event(session, "tushare.news.sina", "涨停", now + timedelta(minutes=1))
        session.commit()
    report = repository.collect_rejected_monitoring_noise()
    assert report["duplicate"] == 0


# --- 启动迁移 ---------------------------------------------------------------

def test_storage_migration_disables_source_and_prunes_legacy_noise(tmp_path, monkeypatch):
    monkeypatch.setenv("PRUNE_REJECTED_MONITOR_NOISE", "1")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "legacy.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    now = utc_naive_now()
    with db.get_session() as session:
        session.add(MonitoringSourceRecord(
            source_key="eastmoney.guba_posts",
            name="东方财富股吧公开股评",
            adapter_type="html",
            provider="eastmoney_guba",
            category="comment",
            enabled=1,
            poll_interval_seconds=30,
            config_json="{}",
        ))
        _insert_legacy_event(session, "eastmoney.guba_posts", "破防了，1折起步",
                             now, event_type="stock_forum_post")
        _insert_legacy_event(session, "feeds.intelligence", _HKEX_ENGLISH_TITLE, now)
        kept_id = _insert_legacy_event(session, "tushare.news.yicai", "AI交易依然颠簸", now)
        # 让一次性迁移在下一次启动时重跑
        for version in (REJECTED_MONITOR_SOURCE_MIGRATION, REJECTED_MONITOR_NOISE_MIGRATION):
            session.query(DatabaseSchemaMigration).filter(
                DatabaseSchemaMigration.version == version,
            ).delete()
        session.commit()

    DatabaseManager.reset_instance()
    fresh = DatabaseManager.get_instance()
    with fresh.get_session() as session:
        rows = session.query(MonitoringEventRecord).all()
        assert [int(row.id) for row in rows] == [kept_id]
        source = session.query(MonitoringSourceRecord).filter(
            MonitoringSourceRecord.source_key == "eastmoney.guba_posts",
        ).one()
        assert source.enabled == 0
    DatabaseManager.reset_instance()
    Config.reset_instance()


def _prepare_legacy_noise_database(path) -> int:
    """建一个「存量脏数据」库：股吧源 enabled=1 + 三类噪音各一条，返回保留行 id。"""
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    now = utc_naive_now()
    with db.get_session() as session:
        session.add(MonitoringSourceRecord(
            source_key="eastmoney.guba_posts",
            name="东方财富股吧公开股评",
            adapter_type="html",
            provider="eastmoney_guba",
            category="comment",
            enabled=1,
            poll_interval_seconds=30,
            config_json="{}",
        ))
        _insert_legacy_event(session, "eastmoney.guba_posts", "破防了，1折起步",
                             now, event_type="stock_forum_post")
        _insert_legacy_event(session, "feeds.intelligence", _HKEX_ENGLISH_TITLE, now)
        kept_id = _insert_legacy_event(session, "tushare.news.yicai", "AI交易依然颠簸", now)
        for version in (REJECTED_MONITOR_SOURCE_MIGRATION, REJECTED_MONITOR_NOISE_MIGRATION):
            session.query(DatabaseSchemaMigration).filter(
                DatabaseSchemaMigration.version == version,
            ).delete()
        session.commit()
    DatabaseManager.reset_instance()
    return kept_id


def test_destructive_prune_is_opt_in(tmp_path, monkeypatch):
    """默认不跑：只有显式 PRUNE_REJECTED_MONITOR_NOISE=1 才允许删十万级存量。

    任何以默认 DATABASE_PATH 初始化 DatabaseManager 的测试或脚本都会触发启动迁移，
    所以这条破坏性迁移不能默认开启。
    """
    monkeypatch.delenv("PRUNE_REJECTED_MONITOR_NOISE", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "opt-in.db"))
    kept_id = _prepare_legacy_noise_database(tmp_path / "opt-in.db")

    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = {int(row.id) for row in session.query(MonitoringEventRecord).all()}
        # 非破坏性的「停采」仍然生效
        source = session.query(MonitoringSourceRecord).filter(
            MonitoringSourceRecord.source_key == "eastmoney.guba_posts",
        ).one()
        assert source.enabled == 0
    assert len(rows) == 3  # 噪音一条都没删
    assert kept_id in rows
    DatabaseManager.reset_instance()
    Config.reset_instance()
