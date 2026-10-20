# -*- coding: utf-8 -*-
"""Unified investment monitor normalization, deduplication and scorecard tests."""

from __future__ import annotations

import os
import hashlib
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sqlalchemy_event, literal, select, update

from src.config import Config
from src.repositories.investment_monitor_repo import (
    InvestmentMonitorRepository, _financial_calendar_date_expr, _financial_statement_scope_expr,
)
from src.repositories.market_data_repo import MarketDataRepository
from src.services.investment_monitor_service import (
    BUILTIN_MONITORING_SOURCES,
    InvestmentMonitorService,
    MonitoringSourceNotConfigured,
)
from src.services.investment_monitor_worker import InvestmentMonitorWorker
from src.storage import DatabaseManager, EssayAnalysisRecord, MonitoringEventRecord, ResearchNote, utc_naive_now
from src.utils.financial_statement_selection import financial_calendar_date, financial_statement_scope_rank


class FakeTushare:
    def query(self, api_name, params=None, fields=None):
        del fields
        if api_name == "stock_basic":
            return {"rows": [{"ts_code": params["ts_code"], "name": "贵州茅台"}]}
        if api_name == "news":
            return {"rows": [{
                "datetime": "2026-08-19 10:01:00",
                "title": "贵州茅台发布公告",
                "content": "公司拟回购股份，机构上调盈利预测。",
            }]}
        if api_name == "trade_cal":
            return {"rows": [{"cal_date": "20260819", "is_open": 1}]}
        fixtures = {
            "fina_indicator": [{"end_date": "20260630", "ann_date": "20260720", "q_netprofit_yoy": 25, "roe": 12, "grossprofit_margin": 60}],
            "income": [{"end_date": "20260630", "ann_date": "20260720", "revenue": 100, "n_income": 20}],
            "cashflow": [{"end_date": "20260630", "n_cashflow_act": 18}],
            "forecast": [{"end_date": "20260630", "ann_date": "20260715", "type": "预增"}],
            "cyq_perf": [{"trade_date": "20260818", "weight_avg": 1200, "winner_rate": 0.8}],
            "cyq_chips": [{"trade_date": "20260818", "price": 1200, "percent": 1}],
            "margin_detail": [{"trade_date": "20260818", "rzye": 1000, "rzmre": 100}],
            "margin": [{"trade_date": "20260818", "exchange_id": "SSE", "rzye": 5000, "rqye": 50, "rzrqye": 5050}],
            "moneyflow": [{"trade_date": "20260818", "net_mf_amount": 88}],
            "hk_hold": [{"trade_date": "20260630", "ratio": 3.2}],
            "stk_factor": [{"trade_date": "20260819", "close": 1300, "rsi_6": 55, "macd": 2, "kdj_k": 60, "kdj_d": 50}],
            "stk_nineturn": [{"trade_date": "20260819", "up_count": 9, "down_count": 0, "nine_up_turn": 1}],
            "pledge_stat": [{"end_date": "20260814", "pledge_ratio": 2.5}],
            "share_float": [], "stk_holdertrade": [], "repurchase": [],
            "research_report": [{"trade_date": "20260818", "title": "白酒行业：贵州茅台保持领先", "abstr": "贵州茅台渠道稳定", "report_type": "行业", "inst_csname": "测试证券", "author": "研究员", "url": "https://example.com/report.pdf"}],
            "major_news": [{"pub_time": "2026-08-19 08:00:00", "title": "贵州茅台渠道更新", "content": "贵州茅台动销改善"}],
            "cctv_news": [],
            "top_list": [{"trade_date": "20260819", "ts_code": "600519.SH", "reason": "日涨幅偏离值达7%", "net_amount": 30, "l_buy": 80, "l_sell": 50}],
            "top_inst": [{"trade_date": "20260819", "ts_code": "600519.SH", "exalter": "机构专用", "side": "买方", "buy": 50, "sell": 10, "net_buy": 40}],
            "limit_list_d": [], "block_trade": [], "suspend_d": [],
            "broker_recommend": [{"month": "202608", "ts_code": "600519.SH", "name": "贵州茅台", "broker": "测试证券"}],
            "stock_company": [{"ts_code": "600519.SH", "com_name": "贵州茅台酒股份有限公司", "main_business": "白酒", "setup_date": "19991120"}],
            "stk_managers": [{"ts_code": "600519.SH", "ann_date": "20260801", "name": "张三", "title": "董事长", "begin_date": "20250101", "end_date": ""}],
            "stk_holdernumber": [{"ts_code": "600519.SH", "ann_date": "20260801", "end_date": "20260630", "holder_num": 100000}],
            "top10_holders": [{"ts_code": "600519.SH", "ann_date": "20260801", "end_date": "20260630", "holder_name": "中国贵州茅台酒厂"}],
            "top10_floatholders": [], "dividend": [], "stk_rewards": [],
            "fina_audit": [{"ts_code": "600519.SH", "ann_date": "20260401", "end_date": "20251231", "audit_result": "标准无保留意见", "audit_agency": "测试会计师事务所"}],
            "daily": [{"trade_date": "20260819", "ts_code": "600519.SH", "close": 1300}],
            "moneyflow_cnt_ths": [
                {"trade_date": "20260819", "name": "白酒概念", "net_amount": 120},
                {"trade_date": "20260819", "name": "食品消费", "net_amount": -30},
            ],
            "moneyflow_ind_ths": [
                {"trade_date": "20260819", "name": "饮料制造", "net_amount": 80},
                {"trade_date": "20260819", "name": "零售", "net_amount": -20},
            ],
            "limit_list_ths": {
                "涨停池": [{"trade_date": "20260819", "ts_code": "600519.SH", "name": "贵州茅台", "tag": "消费"}],
                "炸板池": [{"trade_date": "20260819", "ts_code": "000001.SZ", "name": "平安银行", "tag": "金融"}],
                "跌停池": [{"trade_date": "20260819", "ts_code": "600000.SH", "name": "浦发银行", "tag": "金融"}],
            },
        }
        if api_name in fixtures:
            if api_name == "limit_list_ths":
                return {"rows": fixtures[api_name].get((params or {}).get("limit_type"), [])}
            return {"rows": fixtures[api_name]}
        return {"rows": []}


class FakeStockService:
    def get_realtime_quote(self, code):
        return {"stock_name": "贵州茅台", "current_price": 1300, "change_percent": 2.5,
                "amount": 100, "high": 1310, "low": 1270, "update_time": "2026-08-19T10:02:00"}


class FakeGuba:
    def fetch_latest(self, symbols, *, limit_per_symbol=20):
        assert list(symbols) == ["600519.SH"]
        assert limit_per_symbol == 20
        return [{
            "post_id": "1762000001", "code": "600519", "author": "公开用户",
            "content": "$贵州茅台(SH600519)$ 渠道反馈不错，但仍需核验。",
            "published_at": datetime(2026, 8, 19, 2, 30), "time_text": "今天 10:30",
            "views": 321, "reply_count": 7, "like_count": 2,
            "image_urls": ["https://example.com/guba.jpg"],
            "url": "https://mguba.eastmoney.com/mguba/article/0/1762000001",
        }]


def test_tianyancha_missing_authorization_is_treated_as_not_configured(monitor, monkeypatch):
    monkeypatch.setattr("src.services.investment_monitor_service.shutil.which", lambda _name: "/usr/local/bin/tyc")
    monkeypatch.setattr(
        "src.services.investment_monitor_service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="未配置 Authorization。请先运行：tyc init --authorization YOUR_API_KEY",
        ),
    )

    with pytest.raises(MonitoringSourceNotConfigured):
        monitor._tianyancha_events({"source_key": "tianyancha.enterprise"})


@pytest.fixture()
def monitor(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "monitor.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    repository = InvestmentMonitorRepository(DatabaseManager.get_instance())
    service = InvestmentMonitorService(
        repository=repository, tushare=FakeTushare(), stock_service=FakeStockService(), guba=FakeGuba(),
    )
    service.watchlist = lambda: ["600519.SH"]
    try:
        yield service
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None: os.environ.pop("DATABASE_PATH", None)
        else: os.environ["DATABASE_PATH"] = previous


def test_tushare_news_associates_watchlist_and_company_perspective(monitor):
    result = monitor.sync_source("tushare.news.cls")
    assert result["created"] == 1
    event = monitor.list_events(days=3650)["items"][0]
    detail = monitor.event_detail(event["id"])
    assert event["symbols"] == ["600519.SH"]
    assert detail["id"] == event["id"]
    assert detail["summary"] == event["summary"]
    assert event["perspective"] == "company"
    assert event["sentiment"] == "bullish"


def test_home_news_stream_excludes_non_news_and_pins_important_watchlist_news(monitor):
    monitor.create_external_source({
        "source_key": "api.newsroom", "name": "测试财经媒体", "category": "news",
    })
    monitor.create_external_source({
        "source_key": "api.comments", "name": "测试股评", "category": "comment",
    })
    monitor.ingest_external_events("api.newsroom", [{
        "external_id": "important-news", "event_type": "news",
        "title": "贵州茅台发布重要经营更新", "summary": "公司披露最新经营进展。",
        "symbols": ["600519"], "importance_score": 88,
        "url": "https://example.com/important-news",
    }])
    monitor.ingest_external_events("api.newsroom", [{
        "external_id": "ordinary-news", "event_type": "news",
        "title": "行业日常新闻", "importance_score": 55,
    }])
    monitor.ingest_external_events("api.newsroom", [{
        "external_id": "calibrated-important-news", "event_type": "news",
        "title": "行业重大政策发布", "importance_score": 71,
    }])
    monitor.ingest_external_events("api.comments", [{
        "external_id": "comment-1", "event_type": "stock_forum_post",
        "title": "论坛用户观点不应进入首页新闻", "importance_score": 99,
    }])

    result = monitor.dashboard(days=7)

    assert [item["title"] for item in result["pinned_news"]] == [
        "贵州茅台发布重要经营更新", "行业重大政策发布",
    ]
    assert [item["title"] for item in result["latest_news"]] == ["行业日常新闻"]
    assert all("论坛用户观点" not in item["title"] for item in result["latest_news"])


def test_public_guba_posts_are_real_attributed_unverified_events(monitor):
    first = monitor.sync_source("eastmoney.guba_posts")
    second = monitor.sync_source("eastmoney.guba_posts")
    assert first["received"] == 1
    assert first["created"] == 1
    assert second["created"] == 0

    event = monitor.list_events(days=3650, event_type="stock_forum_post")["items"][0]
    assert event["symbols"] == ["600519.SH"]
    assert event["actors"] == ["公开用户", "东方财富股吧"]
    assert event["metrics"]["reply_count"] == 7
    assert event["metrics"]["_evidence"]["evidence_level"] == "unverified"
    assert event["url"].endswith("/1762000001")
    compact = monitor.compact_event(event)
    assert compact["metrics"]["author"] == "公开用户"
    assert compact["metrics"]["views"] == 321
    assert compact["metrics"]["reply_count"] == 7


@pytest.mark.parametrize("time_fields", [
    {"time_precision": "date", "disclosure_date": "2025-09-08"},
    {"time_precision": "second", "disclosure_date": "2025-09-08"},
    {"disclosure_date": "2025-09-08"},
    {},
])
def test_compact_announcement_preserves_source_time_precision_without_inventing_history(time_fields):
    monitor = InvestmentMonitorService.__new__(InvestmentMonitorService)
    original = {
        "id": 17, "event_type": "company_announcement", "source_key": "sse.announcements",
        "title": "历史公告", "summary": "公告摘要", "event_at": "2025-09-07T16:00:00Z",
        "metrics": {**time_fields, "size_kb": 123, "provider_payload": {"not": "needed"}},
    }
    compact = monitor.compact_event(original)
    assert compact["metrics"] == {
        "_evidence": {"evidence_level": "official", "channel": "other"}, **time_fields,
    }
    assert compact["event_at"] == "2025-09-07T16:00:00Z"
    assert compact["summary"] == "公告摘要"
    assert original["metrics"]["size_kb"] == 123
    # Identically named fields in other channels are not announcement metadata.
    assert set(monitor.compact_event({**original, "event_type": "news"})["metrics"]) == {"_evidence"}


def test_super_watchlist_stock_comments_only_exposes_genuine_forum_posts(monitor):
    monitor.sync_source("eastmoney.guba_posts")
    indicator_source = monitor.repo.get_source("akshare.stock_comments")
    monitor.repo.upsert_events([monitor._base_event(
        indicator_source, external_id="legacy-xq", event_type="stock_discussion_heat",
        perspective="investor", title="历史讨论热度", summary="历史遗留指标",
        symbols=["600519.SH"], sentiment="neutral", importance=30,
        event_at=datetime(2026, 8, 19, 2, 0), tags=["历史指标"],
    )])

    comments = monitor.super_watchlist(days=3650)["stocks"][0]["stock_comments"]

    assert comments["count"] == 1
    assert [item["event_type"] for item in comments["items"]] == ["stock_forum_post"]
    assert "雪球" not in comments["source_note"]


def test_external_api_ingestion_is_idempotent_and_updates_scorecard(monitor):
    monitor.create_external_source({"source_key": "api.partner", "name": "合作资讯 API"})
    payload = [{"external_id": "evt-1", "title": "贵州茅台获机构增持", "symbols": ["600519"],
                "perspective": "institution", "sentiment": "bullish", "importance_score": 88}]
    first = monitor.ingest_external_events("api.partner", payload)
    second = monitor.ingest_external_events("api.partner", payload)
    assert first["created"] == 1
    assert second["created"] == 0
    card = monitor.dashboard(days=7)["watchlist"][0]
    assert card["event_count"] == 1
    assert card["perspectives"]["institution"] == 1
    assert card["opportunity_score"] > 50


def test_dashboard_uses_aggregate_symbol_stats_without_loading_full_documents(monitor, monkeypatch):
    monitor.create_external_source({"source_key": "api.aggregate", "name": "聚合测试"})
    monitor.ingest_external_events("api.aggregate", [{
        "external_id": "aggregate-1", "title": "贵州茅台盈利预测上调",
        "symbols": ["600519"], "perspective": "institution",
        "sentiment": "bullish", "importance_score": 88,
    }])
    monkeypatch.setattr(
        monitor.repo,
        "all_symbol_events",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("dashboard hydrated full documents")),
    )

    card = monitor.dashboard(days=7)["watchlist"][0]

    assert card["event_count"] == 1
    assert card["perspectives"]["institution"] == 1
    assert card["opportunity_score"] > 50


def test_source_status_separates_sync_success_from_actual_data_freshness(monitor, monkeypatch):
    monkeypatch.setattr(
        "src.services.investment_monitor_service.utc_naive_now",
        lambda: __import__("datetime").datetime(2026, 8, 19, 2, 2),
    )
    monitor.create_external_source({"source_key": "api.empty", "name": "空返回来源"})
    monitor.sync_source("tushare.news.cls")
    sources = monitor.list_sources()
    by_key = {item["source_key"]: item for item in sources["items"]}
    assert by_key["tushare.news.cls"]["freshness_status"] == "fresh"
    assert by_key["tushare.news.cls"]["stored_event_count"] == 1
    assert by_key["tushare.news.cls"]["monitoring_status"] == "live"
    assert by_key["tushare.news.cls"]["last_check_age_seconds"] == 0
    assert by_key["api.empty"]["freshness_status"] == "empty"
    assert sources["with_data"] >= 1
    assert sources["empty"] >= 1


def test_source_status_records_upstream_received_and_deduplicated_counts(monitor):
    first = monitor.sync_source("tushare.news.cls")
    second = monitor.sync_source("tushare.news.cls")
    source = {item["source_key"]: item for item in monitor.list_sources()["items"]}["tushare.news.cls"]

    assert first["received"] == 1
    assert second["received"] == 1
    assert source["last_received_count"] == 1
    assert source["last_created_count"] == 0
    assert source["last_duration_ms"] >= 0


def test_source_bi_exposes_inventory_activity_and_direct_use_contract(monitor):
    monitor.sync_source("tushare.news.cls")
    result = monitor.source_bi(days=30)
    source = next(item for item in result["sources"] if item["source_key"] == "tushare.news.cls")

    assert result["summary"]["stored_event_count"] >= 1
    assert len(result["daily_trend"]) == 30
    assert source["period_event_count"] == 1
    assert source["direct_use"]["local_store"] == "SQLite.monitoring_events"
    assert "source_key=tushare.news.cls" in source["direct_use"]["events_api"]


def test_realtime_quote_is_reduced_to_one_daily_open_snapshot(monitor, monkeypatch):
    monkeypatch.setattr("src.services.investment_monitor_service.utc_naive_now", lambda: __import__("datetime").datetime(2026, 8, 19, 2, 2))
    result = monitor.sync_source("realtime.quotes")
    assert result["created"] == 1
    second = monitor.sync_source("realtime.quotes")
    assert second["created"] == 0
    event = monitor.list_events(days=3650, event_type="market_open_snapshot")["items"][0]
    assert event["sentiment"] == "bullish"
    assert event["metrics"]["open"] == 1300
    assert event["event_at"].startswith("2026-08-19T01:30:00")
    assert monitor.list_events(days=3650)["total"] == 1


def test_legacy_realtime_quotes_are_hidden_from_default_intelligence_feed(monitor):
    monitor.create_external_source({"source_key": "api.quotes", "name": "历史实时行情"})
    monitor.ingest_external_events("api.quotes", [{"external_id": "tick", "event_type": "realtime_quote", "title": "盘中行情"}])
    assert monitor.list_events(days=3650)["total"] == 0
    assert monitor.list_events(days=3650, event_type="realtime_quote")["total"] == 1


def test_open_snapshot_source_does_not_fetch_quotes_outside_open_window(monitor, monkeypatch):
    monkeypatch.setattr("src.services.investment_monitor_service.utc_naive_now", lambda: __import__("datetime").datetime(2026, 8, 19, 6, 0))
    monitor.stock_service.get_realtime_quote = lambda _code: (_ for _ in ()).throw(AssertionError("must not fetch"))
    result = monitor.sync_source("realtime.quotes")
    assert result["received"] == 0


def test_high_value_tushare_domains_are_materialized_as_events(monitor):
    expected = {
        "tushare.fundamentals": "fundamental_snapshot",
        "tushare.capital": "capital_chip_snapshot",
        "tushare.technical": "technical_factor",
        "tushare.ownership": "ownership_snapshot",
    }
    for source_key, event_type in expected.items():
        result = monitor.sync_source(source_key)
        assert result["created"] == 1
        item = monitor.list_events(days=3650, source_key=source_key)["items"][0]
        assert item["event_type"] == event_type
        assert item["symbols"] == ["600519.SH"]
        if source_key == "tushare.capital":
            assert item["metrics"]["market_margin"]["margin_balance"] == 5050
        if source_key == "tushare.technical":
            assert item["metrics"]["nine_turn"]["nine_up_turn"] == 1
            assert "stk_nineturn" in item["metrics"]["_evidence"]["origin_apis"]


def test_research_pdf_and_long_news_channels_match_watchlist(monitor):
    report = monitor.sync_source("tushare.research_pdf")
    news = monitor.sync_source("tushare.long_news")
    assert report["created"] == 1
    assert news["created"] == 1
    report_event = monitor.list_events(days=3650, source_key="tushare.research_pdf")["items"][0]
    assert report_event["url"] == "https://example.com/report.pdf"
    assert report_event["symbols"] == ["600519.SH"]


def test_historical_news_paginates_and_keeps_only_requested_watchlist_symbol(monitor):
    original_query = monitor.tushare.query
    offsets = []

    def query(api_name, params=None, fields=None):
        if api_name != "news":
            return original_query(api_name, params=params, fields=fields)
        offset = int((params or {}).get("offset") or 0)
        offsets.append(offset)
        if offset == 0:
            return {"rows": [{
                "datetime": "2026-08-18 09:00:00", "title": "000001 银行消息", "content": "平安银行事项",
            }] * 300}
        return {"rows": [{
            "datetime": "2026-08-17 09:00:00", "title": "贵州茅台渠道更新", "content": "公司经营稳定",
        }]}

    monitor.tushare.query = query
    monitor._backfill_days = 30
    try:
        events = monitor._tushare_news_events(monitor.repo.get_source("tushare.news.cls"))
    finally:
        monitor._backfill_days = None

    assert offsets == [0, 300]
    assert len(events) == 1
    assert events[0]["symbols"] == ["600519.SH"]


def test_exchange_activity_and_company_facts_are_traceable(monitor):
    activity = monitor.sync_source("tushare.market_activity")
    profile = monitor.sync_source("tushare.company_profile")
    assert activity["created"] == 3
    assert profile["created"] == 5
    events = monitor.list_events(days=3650, evidence_level="factual")["items"]
    assert {item["event_type"] for item in events} >= {
        "dragon_tiger", "institution_seat", "broker_recommendation",
        "company_profile", "executive_roster", "holder_number", "top_shareholders", "audit_opinion",
    }
    assert all(item["metrics"]["_evidence"]["origin_apis"] for item in events)


def test_dragon_tiger_daily_preserves_reasons_and_seat_details(monitor):
    original_query = monitor.tushare.query

    def query(api_name, params=None, fields=None):
        if api_name == "top_list":
            return {"rows": [
                {"trade_date": "20260819", "ts_code": "600519.SH", "name": "贵州茅台", "reason": "日涨幅偏离值达7%", "net_amount": 30, "l_buy": 80, "l_sell": 50},
                {"trade_date": "20260819", "ts_code": "600519.SH", "name": "贵州茅台", "reason": "连续三日涨幅偏离值累计20%", "net_amount": 20, "l_buy": 60, "l_sell": 40},
                {"trade_date": "20260819", "ts_code": "000001.SZ", "name": "平安银行", "reason": "日换手率达20%", "net_amount": -10, "l_buy": 20, "l_sell": 30},
            ]}
        return original_query(api_name, params=params, fields=fields)

    monitor.tushare.query = query
    payload = monitor.dragon_tiger_daily(trade_date="2026-08-19", refresh=True)
    assert payload["summary"]["row_count"] == 3
    assert payload["summary"]["symbol_count"] == 2
    assert payload["summary"]["seat_count"] == 1
    assert len([item for item in payload["items"] if item["ts_code"] == "600519.SH"]) == 2
    assert payload["items"][0]["seats"][0]["exalter"] == "机构专用"

    history = monitor.dragon_tiger_history(start_date="20260819", end_date="20260819")
    assert history["total"] == 3
    assert history["cached_trade_days"] == 1
    assert history["trend"][0]["symbols"] == 2


def test_market_theme_flow_and_limit_pool_are_factual_market_snapshots(monitor):
    result = monitor.sync_source("tushare.market_themes")
    assert result["created"] == 2
    events = monitor.list_events(days=3650, source_key="tushare.market_themes")["items"]
    by_type = {item["event_type"]: item for item in events}
    assert by_type["market_theme_flow"]["metrics"]["concept"]["inflow_top10"][0]["name"] == "白酒概念"
    assert by_type["limit_pool"]["metrics"]["counts"] == {"涨停池": 1, "炸板池": 1, "跌停池": 1}
    assert by_type["limit_pool"]["sentiment"] == "neutral"
    assert by_type["market_theme_flow"]["metrics"]["_evidence"]["origin_apis"] == [
        "moneyflow_cnt_ths", "moneyflow_ind_ths",
    ]


def test_unverified_essay_is_excluded_from_factual_filter(monitor):
    monitor.create_external_source({"source_key": "api.unverified", "name": "待核验外部消息"})
    monitor.ingest_external_events("api.unverified", [{"external_id": "claim", "title": "市场传闻"}])
    assert monitor.list_events(days=3650, evidence_level="unverified")["total"] == 1
    assert monitor.list_events(days=3650, evidence_level="factual")["total"] == 0


def test_intelligence_dashboard_builds_decision_aggregates(monitor):
    monitor.sync_source("realtime.quotes")
    monitor.sync_source("tushare.market_activity")
    # This checks aggregation within a rolling window, not the fixture's
    # fixed market date. Keep the sample in that window as the calendar moves.
    with monitor.repo.db.get_session() as session:
        session.execute(update(MonitoringEventRecord).values(event_at=utc_naive_now()))
        session.commit()
    result = monitor.intelligence_dashboard(days=14)
    assert len(result["daily_trend"]) == 14
    assert result["summary"]["factual_count"] >= 1
    assert result["summary"]["watchlist_hits"] >= 1
    assert result["watchlist"][0]["symbol"] == "600519.SH"
    assert all("change_pct" in row for row in result["channels"])


def test_super_watchlist_uses_structured_data_and_compact_evidence(monitor):
    for source_key in ("realtime.quotes", "tushare.market", "tushare.fundamentals", "tushare.capital", "tushare.technical"):
        monitor.sync_source(source_key)
    result = monitor.super_watchlist(days=3650)
    stock = result["stocks"][0]
    assert result["version"] == "5.1-shared-store"
    assert stock["symbol"] == "600519.SH"
    assert stock["market"]["price"] == 1300
    assert stock["fundamentals"]["net_profit_yoy"] == 25
    assert stock["capital"]["winner_rate"] == 0.8
    assert stock["technical"]["rsi_6"] == 55
    assert {row["name"] for row in stock["coverage"]} >= {"market", "fundamental", "capital"}
    assert all(set(event["metrics"]) <= {"_evidence"} for event in stock["timeline"])


@pytest.fixture()
def financial_source_rows(monitor, monkeypatch):
    """Exercise the real financial collector with only bounded in-memory rows."""
    datasets = {}
    calls = []

    def query(api_name, *, params):
        assert api_name in {"fina_indicator", "income", "balancesheet", "cashflow", "forecast"}
        assert params["ts_code"] == "600519.SH"
        calls.append(api_name)
        return {"rows": datasets.get(api_name, [])}

    monkeypatch.setattr(monitor.tushare, "query", query)
    monkeypatch.setattr(monitor, "_stock_name", lambda code: "测试公司")
    source = monitor.repo.get_source("tushare.fundamentals")

    def collect(rows):
        datasets.clear()
        datasets.update(rows)
        calls.clear()
        result = monitor._tushare_fundamental_events(source)
        assert calls == ["fina_indicator", "income", "balancesheet", "cashflow", "forecast"]
        return result

    return collect


@pytest.mark.parametrize(("dataset", "metric", "field"), [
    ("fina_indicator", "indicator", "roe"), ("income", "income", "revenue"),
    ("balancesheet", "balance_sheet", "total_assets"), ("cashflow", "cashflow", "n_cashflow_act"),
    ("forecast", "forecast", "p_change_min"),
])
def test_financial_source_rows_choose_latest_period_then_revision_independent_of_order(
    financial_source_rows, dataset, metric, field,
):
    rows = [
        {"end_date": "20260331", "ann_date": "20260901", field: 900},
        {"end_date": "20260630", "ann_date": "20260801", "update_flag": "0", field: 100},
        {"end_date": "20260630", "ann_date": "20260801", "f_ann_date": "20260820", "update_flag": "1", field: 0},
    ]
    original = json.loads(json.dumps(rows))
    forward = financial_source_rows({dataset: rows})[0]
    reverse = financial_source_rows({dataset: list(reversed(rows))})[0]
    assert forward == reverse
    assert forward["metrics"][metric] == rows[2]
    assert forward["metrics"][metric][field] == 0
    assert forward["event_at"] == datetime(2026, 8, 19, 16)
    assert forward["external_id"] == "600519.SH:20260630"
    assert rows == original


def test_financial_source_rows_resolve_same_date_revisions_and_content_ties(financial_source_rows):
    base = {"end_date": "20260630", "ann_date": "20260820"}
    rows = [
        {**base, "update_flag": "0", "report_type": "4", "revenue": 900},
        {**base, "update_flag": "1", "report_type": "1", "revenue": 800},
        {**base, "update_flag": "1", "report_type": "4", "revenue": 100},
        {**base, "update_flag": "1", "report_type": "4", "revenue": 100, "n_income_attr_p": 0},
        {**base, "update_flag": "1", "report_type": "4", "revenue": 200, "n_income_attr_p": 0},
    ]
    forward = financial_source_rows({"income": rows})
    reverse = financial_source_rows({"income": list(reversed(rows))})
    assert forward == reverse
    assert len(forward) == 4
    # All equally marked type 1/4 rows remain as source candidates; field
    # completeness and lexical value do not establish a newer revision.
    assert {json.dumps(event["metrics"]["income"], sort_keys=True) for event in forward} == {
        json.dumps(row, sort_keys=True) for row in rows[1:]
    }


@pytest.mark.parametrize("invalid_date", [
    None, "", True, "20260230", "20261331", "later 20260630", "2026-06-30T00:00:00",
    "２０２６0630", "٢٠٢٦0630", "２０２６-06-30", "٢٠٢٦-06-30",
])
def test_financial_source_rows_reject_missing_or_invalid_report_period(financial_source_rows, invalid_date):
    invalid = {"end_date": invalid_date, "ann_date": "20260901", "revenue": 999}
    valid = {"end_date": "2026-06-30", "ann_date": "20260820", "revenue": 0}
    result = financial_source_rows({"income": [invalid, valid]})
    assert result[0]["metrics"]["income"] == valid
    assert result[0]["external_id"] == "600519.SH:20260630"
    assert financial_source_rows({"income": [invalid]}) == []


@pytest.mark.parametrize("invalid_date", [None, "", True, "20260230", "20991331", "date 20990101"])
def test_financial_source_rows_invalid_announcement_cannot_outrank_disclosure(financial_source_rows, invalid_date):
    invalid = {"end_date": "20260630", "ann_date": invalid_date, "f_ann_date": invalid_date,
               "update_flag": "1", "report_type": "4", "revenue": 999}
    valid = {"end_date": "20260630", "ann_date": "2026-08-20", "update_flag": "1", "report_type": "1", "revenue": 0}
    result = financial_source_rows({"income": [invalid, valid]})[0]
    assert result["metrics"]["income"] == valid
    assert result["event_at"] == datetime(2026, 8, 19, 16)


def test_financial_source_rows_keep_undated_disclosure_at_real_period_not_today(financial_source_rows):
    row = {"end_date": "20240229", "n_cashflow_act": 0}
    result = financial_source_rows({"cashflow": [row]})[0]
    assert result["metrics"]["cashflow"] == row
    assert result["event_at"] == datetime(2024, 2, 28, 16)
    assert result["external_id"] == "600519.SH:20240229"
    assert financial_source_rows({}) == []


def test_financial_source_rows_summary_preserves_zero_and_independent_periods(financial_source_rows):
    result = financial_source_rows({
        "fina_indicator": [{"end_date": "20260630", "q_sales_yoy": 0, "revenue_yoy": 99,
                            "q_netprofit_yoy": 0, "netprofit_yoy": 99, "roe": 0, "roe_waa": 99,
                            "grossprofit_margin": 0}],
        "cashflow": [{"end_date": "20260331", "n_cashflow_act": 0}],
    })[0]
    assert "财务指标期 20260630" in result["summary"]
    assert "现金流量表期 20260331" in result["summary"]
    for expected in ("营收同比 0%", "归母净利同比 0%", "ROE 0%", "毛利率 0%", "经营现金流 0"):
        assert expected in result["summary"]
    assert result["sentiment"] == "neutral"


@pytest.mark.parametrize("report_type", ["2", "3", "6", "7", "8", "9", "10", "11", "12", "13", "unknown", True, False, 1.0])
def test_financial_source_rows_exclude_incompatible_statement_types(financial_source_rows, report_type):
    for dataset, metric in (("income", "income"), ("cashflow", "cashflow"), ("balancesheet", "balance_sheet")):
        valid = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "revenue": 200}
        invalid = {"end_date": "20260930", "ann_date": "20261020", "report_type": report_type, "revenue": 999}
        assert financial_source_rows({dataset: [invalid, valid]})[0]["metrics"][metric] == valid
        assert financial_source_rows({dataset: [valid, invalid]})[0]["metrics"][metric] == valid
        assert financial_source_rows({dataset: [invalid]}) == []


@pytest.mark.parametrize("preferred_type", ["1", "4"])
def test_financial_source_rows_same_period_consolidated_outranks_late_original_or_unknown(
    financial_source_rows, preferred_type,
):
    for dataset, metric in (("income", "income"), ("cashflow", "cashflow"), ("balancesheet", "balance_sheet")):
        preferred = {"end_date": "20260630", "ann_date": "20260820", "report_type": preferred_type, "revenue": 0}
        before_adjustment = {"end_date": "20260630", "ann_date": "20260901", "report_type": "5", "revenue": 999}
        unknown = {"end_date": "20260630", "ann_date": "20260902", "revenue": 888}
        rows = [unknown, before_adjustment, preferred]
        assert financial_source_rows({dataset: rows})[0]["metrics"][metric] == preferred
        assert financial_source_rows({dataset: list(reversed(rows))})[0]["metrics"][metric] == preferred
        assert financial_source_rows({dataset: [unknown, before_adjustment]})[0]["metrics"][metric] == before_adjustment
        later_unknown = {**unknown, "end_date": "20260930", "ann_date": "20261020"}
        assert financial_source_rows({dataset: [preferred, later_unknown]})[0]["metrics"][metric] == later_unknown


def test_financial_source_rows_keep_independent_periods_through_both_workspaces(
    financial_source_rows, monitor, monkeypatch,
):
    rows = {
        "fina_indicator": [{"end_date": "20260630", "ann_date": "20260820", "q_sales_yoy": 0}],
        "income": [{"end_date": "20260331", "ann_date": "20260420", "revenue": 0, "n_income_attr_p": 0}],
        "cashflow": [{"end_date": "20251231", "ann_date": "20260401", "n_cashflow_act": 0}],
    }
    financial_source_rows(rows)
    # Use the real sync/upsert/read path in the fixture's disposable database.
    assert monitor.sync_source("tushare.fundamentals")["created"] == 1
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("workspace must stay local"))
    for view in ("overview", "full"):
        stock = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)["stock"]
        financial = stock["fundamentals"]
        assert financial["indicator_period"] == "2026-06-30"
        assert financial["income_period"] == "2026-03-31"
        assert financial["cashflow_period"] == "2025-12-31"
        assert financial["revenue"] == financial["net_profit"] == financial["operating_cashflow"] == 0
        assert financial["revenue_yoy"] == 0
        assert isinstance(financial["source_event_id"], int)


@pytest.mark.parametrize("reverse_sync", [False, True])
def test_financial_workspace_prefers_later_report_period_across_syncs(
    financial_source_rows, monitor, monkeypatch, reverse_sync,
):
    q1 = {"end_date": "20260331", "ann_date": "20260901", "revenue": 100, "n_income_attr_p": 10}
    half = {"end_date": "20260630", "ann_date": "20260820", "revenue": 200, "n_income_attr_p": 20}
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))

    def sync(rows):
        financial_source_rows({"income": rows})
        return monitor.sync_source("tushare.fundamentals")

    if reverse_sync:
        assert sync([half])["created"] == 1
        assert sync([q1])["created"] == 1
    else:
        assert sync([q1])["created"] == 1
        assert sync([q1, half])["created"] == 1
    records = monitor.list_events(days=3650, event_type="fundamental_snapshot")["items"]
    expected_id = next(row["id"] for row in records if row["metrics"]["income"]["end_date"] == "20260630")
    # Original chronology is unchanged: a later Q1 correction remains first
    # in the evidence timeline, but must not become the current H1 financials.
    assert records[0]["metrics"]["income"]["end_date"] == "20260331"

    def assert_views(expected_revenue, expected_event_at):
        for view in ("overview", "full"):
            payload = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)
            financial = payload["stock"]["fundamentals"]
            assert financial["income_period"] == "2026-06-30"
            assert financial["revenue"] == expected_revenue
            assert financial["source_event_id"] == expected_id
            assert monitor.event_detail(expected_id)["event_at"] == expected_event_at
            if view == "full":
                assert payload["agent_context"]["fundamental_context"]["fundamentals"] == financial
                assert payload["stock"]["timeline"][0]["id"] != expected_id

    assert_views(200, "2026-08-19T16:00:00Z")
    revised = {**half, "f_ann_date": "20260825", "update_flag": "1", "revenue": 250}
    original_id = expected_id
    original_detail = monitor.event_detail(original_id)
    assert sync([half, q1, revised])["created"] == 1
    records = monitor.list_events(days=3650, event_type="fundamental_snapshot")["items"]
    expected_id = next(row["id"] for row in records if row["metrics"]["income"].get("revenue") == 250)
    assert expected_id != original_id
    assert_views(250, "2026-08-24T16:00:00Z")
    assert sync([revised, q1, half])["updated"] == 0
    assert_views(250, "2026-08-24T16:00:00Z")
    assert monitor.event_detail(original_id) == original_detail


@pytest.fixture()
def financial_snapshot_projection(monitor, monkeypatch):
    """Read stored legacy snapshots without changing their disclosed dates."""
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))
    source = monitor.repo.get_source("tushare.fundamentals")

    def read(rows):
        events = [monitor._base_event(
            source, external_id=f"legacy-fixture-{index}", event_type="fundamental_snapshot",
            perspective="company", title="财务测试记录", summary="", symbols=["600519.SH"],
            sentiment="neutral", importance=50, event_at=event_at, tags=[], metrics=metrics, raw=metrics,
        ) for index, (event_at, metrics) in enumerate(rows)]
        # Persist actual pre-version legacy rows without invoking the new
        # immutable writer, so this fixture still exercises backward reads.
        with monitor.repo.db.get_session() as session:
            session.add_all(MonitoringEventRecord(**monitor.repo._event_fields(event)) for event in events)
            session.commit()
        return {
            view: monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)["stock"]["fundamentals"]
            for view in ("overview", "full")
        }

    return read


def test_financial_versions_keep_per_table_sources_and_whole_null_rows_in_both_views(
    financial_source_rows, monitor, monkeypatch,
):
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))
    old_rows = {
        "income": [{"end_date": "20260331", "ann_date": "20260420", "revenue": 100, "n_income_attr_p": 10}],
        "cashflow": [{"end_date": "20260630", "ann_date": "20260820", "n_cashflow_act": 0}],
    }
    financial_source_rows(old_rows)
    assert monitor.sync_source("tushare.fundamentals")["created"] == 1
    original_id = monitor.list_events(days=3650, event_type="fundamental_snapshot")["items"][0]["id"]
    original_detail = monitor.event_detail(original_id)
    financial_source_rows({
        "fina_indicator": [{"end_date": "20260630", "ann_date": "20260825", "roe": 20}],
        "income": [{"end_date": "20260630", "ann_date": "20260825", "revenue": None, "n_income_attr_p": 20}],
    })
    assert monitor.sync_source("tushare.fundamentals")["created"] == 1
    for view in ("overview", "full"):
        payload = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)
        financial = payload["stock"]["fundamentals"]
        assert financial["revenue"] is None
        assert financial["net_profit"] == 20 and financial["operating_cashflow"] == 0
        assert financial["roe"] == 20
        assert financial["source_event_id"] is None
        assert financial["sources"]["cashflow"]["event_id"] == original_id
        assert financial["sources"]["income"]["event_id"] == financial["sources"]["indicator"]["event_id"] != original_id
        for key in ("income", "cashflow", "indicator"):
            source = monitor.event_detail(financial["sources"][key]["event_id"])
            assert source["metrics"][key]["end_date"] == "20260630"
        if view == "full":
            assert payload["agent_context"]["fundamental_context"]["fundamentals"] == financial
            context = json.loads(payload["agent_context"]["analysis_context_pack_summary"].rsplit("\n", 1)[-1])
            assert context["fundamentals"] == financial
    financial_source_rows(old_rows)
    assert monitor.sync_source("tushare.fundamentals")["created"] == 0
    assert monitor.event_detail(original_id) == original_detail


@pytest.mark.parametrize("reverse", [False, True])
def test_financial_collector_retains_same_response_conflicts_through_source_and_context(
    financial_source_rows, monitor, monkeypatch, reverse,
):
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))
    income = [{"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "revenue": value}
              for value in (100, 200)]
    events = financial_source_rows({"income": list(reversed(income)) if reverse else income})
    assert len(events) == 2
    assert monitor.sync_source("tushare.fundamentals")["created"] == 2
    for view in ("overview", "full"):
        payload = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)
        financial = payload["stock"]["fundamentals"]
        source = financial["sources"]["income"]
        assert source["selection_status"] == "conflict"
        assert source["period"] == "2026-06-30" and source["candidate_count"] == 2
        assert source["event_id"] is financial["source_event_id"] is financial["revenue"] is None
        assert {monitor.event_detail(identifier)["metrics"]["income"]["revenue"]
                for identifier in source["candidate_event_ids"]} == {100, 200}
        if view == "full":
            assert payload["agent_context"]["fundamental_context"]["fundamentals"] == financial


@pytest.mark.parametrize(("dataset", "field", "output"), [
    ("income", "revenue", "revenue"), ("cashflow", "n_cashflow_act", "operating_cashflow"),
])
@pytest.mark.parametrize("arrival", ["same_response", "forward_syncs", "reverse_syncs"])
@pytest.mark.parametrize("case", ["latest_flag_before_date", "actual_date_before_ann_date"])
def test_financial_revision_policy_is_consistent_from_collector_to_both_views(
    financial_source_rows, monitor, monkeypatch, dataset, field, output, arrival, case,
):
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))
    base = {"end_date": "20260630", "report_type": "1"}
    if case == "latest_flag_before_date":
        expected = {**base, "ann_date": "20260820", "f_ann_date": "20260820", "update_flag": "1", field: 0}
        other = {**base, "ann_date": "20260830", "f_ann_date": "20260830", "update_flag": "0", field: 900}
        expected_date = "2026-08-20"
    else:
        expected = {**base, "ann_date": "20260825", "f_ann_date": "20260825", "update_flag": "1", field: 0}
        other = {**base, "ann_date": "20260830", "f_ann_date": "20260820", "update_flag": "1", field: 900}
        expected_date = "2026-08-25"
    batches = [[expected, other]] if arrival == "same_response" else [[expected], [other]]
    if arrival == "reverse_syncs":
        batches.reverse()
    for batch in batches:
        collected = financial_source_rows({dataset: batch})
        if arrival == "same_response":
            assert len(collected) == 1 and collected[0]["metrics"][dataset] == expected
        monitor.sync_source("tushare.fundamentals")
    for view in ("overview", "full"):
        payload = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)
        financial = payload["stock"]["fundamentals"]
        assert financial[output] == 0
        source = financial["sources"][dataset]
        assert source["selection_status"] == "selected"
        assert source["announced_at"] == expected_date
        original = monitor.event_detail(source["event_id"])
        assert original["metrics"][dataset] == expected
        expected_event_at = datetime.fromisoformat(expected_date) - timedelta(hours=8)
        assert original["event_at"] == expected_event_at.isoformat() + "Z"
        if view == "full":
            assert payload["agent_context"]["fundamental_context"]["fundamentals"] == financial


@pytest.mark.parametrize(("dataset", "field", "output"), [
    ("income", "revenue", "revenue"), ("cashflow", "n_cashflow_act", "operating_cashflow"),
])
@pytest.mark.parametrize("arrival", ["same_response", "forward_syncs", "reverse_syncs"])
def test_consolidated_type_one_and_four_tie_remains_a_source_conflict(
    financial_source_rows, monitor, monkeypatch, dataset, field, output, arrival,
):
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}))
    base = {"end_date": "20260630", "ann_date": "20260820", "f_ann_date": "20260820", "update_flag": "1"}
    rows = [{**base, "report_type": "1", field: 100}, {**base, "report_type": "4", field: 200}]
    batches = [rows] if arrival == "same_response" else [[rows[0]], [rows[1]]]
    if arrival == "reverse_syncs":
        batches.reverse()
    for batch in batches:
        collected = financial_source_rows({dataset: batch})
        assert len(collected) == len(batch)
        monitor.sync_source("tushare.fundamentals")
    for view in ("overview", "full"):
        payload = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)
        financial = payload["stock"]["fundamentals"]
        assert financial[output] is financial["source_event_id"] is None
        source = financial["sources"][dataset]
        assert source["selection_status"] == "conflict" and source["candidate_count"] == 2
        assert {monitor.event_detail(identifier)["metrics"][dataset]["report_type"]
                for identifier in source["candidate_event_ids"]} == {"1", "4"}
        if view == "full":
            assert payload["agent_context"]["fundamental_context"]["fundamentals"] == financial


@pytest.mark.parametrize("invalid_period", [None, "", True, "20260230", "2026-02-30", "99991331", "bad20260630"])
def test_financial_snapshot_projection_rejects_invalid_period_and_forecast_anchor(
    financial_snapshot_projection, invalid_period,
):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"income": {"end_date": "20260630", "revenue": 200}}),
        (datetime(2026, 9, 1), {"period": "20261231", "indicator": {"end_date": invalid_period},
                               "income": {"end_date": "20260331", "revenue": 100},
                               "forecast": {"end_date": "20271231"}}),
    ])
    for financial in views.values():
        assert financial["income_period"] == "2026-06-30"
        assert financial["revenue"] == 200


def test_financial_snapshot_projection_uses_revision_date_of_its_highest_period(financial_snapshot_projection):
    views = financial_snapshot_projection([
        (datetime(2026, 9, 1), {"indicator": {"end_date": "20260630", "ann_date": "20260820"},
                               "income": {"end_date": "20260331", "ann_date": "20260901", "revenue": 100}}),
        (datetime(2026, 8, 25), {"indicator": {"end_date": "20260630", "f_ann_date": "20260825"},
                                "income": {"end_date": "20260331", "revenue": 200}}),
    ])
    for financial in views.values():
        assert financial["indicator_period"] == "2026-06-30"
        assert financial["income_period"] == "2026-03-31"
        assert financial["revenue"] == 100
        assert financial["sources"]["indicator"]["event_id"] != financial["sources"]["income"]["event_id"]


def test_financial_snapshot_projection_legacy_unknown_period_has_no_selected_financials(financial_snapshot_projection):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"income": {"revenue": 100}}),
        (datetime(2026, 9, 1), {"income": {"revenue": 200}}),
    ])
    for financial in views.values():
        assert financial["income_period"] is None
        assert financial["revenue"] is None
        assert financial["source_event_id"] is None


def test_financial_snapshot_projection_retains_independent_cashflow_period(financial_snapshot_projection):
    views = financial_snapshot_projection([
        (datetime(2026, 9, 1), {"indicator": {"end_date": "20260331"},
                               "income": {"end_date": "20260331", "revenue": 100}}),
        (datetime(2026, 8, 25), {"indicator": {"end_date": "20260331"},
                                "income": {"end_date": "20260331", "revenue": 200},
                                "cashflow": {"end_date": "20260630", "n_cashflow_act": 0}}),
    ])
    for financial in views.values():
        assert financial["indicator_period"] == "2026-03-31"
        assert financial["income_period"] == "2026-03-31"
        assert financial["cashflow_period"] == "2026-06-30"
        assert financial["operating_cashflow"] == 0
        assert financial["revenue"] is None
        assert financial["sources"]["income"]["selection_status"] == "conflict"


def test_financial_snapshot_projection_same_period_prefers_saved_revision_flag(financial_snapshot_projection):
    views = financial_snapshot_projection([
        (datetime(2026, 9, 1), {"income": {"end_date": "20260630", "ann_date": "20260820",
                                          "update_flag": "0", "revenue": 100}}),
        (datetime(2026, 8, 25), {"income": {"end_date": "20260630", "ann_date": "20260820",
                                           "update_flag": "1", "revenue": 200}}),
    ])
    assert all(financial["revenue"] == 200 for financial in views.values())


def test_financial_snapshot_projection_tracks_indicator_revision_alongside_typed_income(financial_snapshot_projection):
    income = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "revenue": 200}
    views = financial_snapshot_projection([
        (datetime(2026, 8, 30), {"income": income,
                                "indicator": {"end_date": "20260630", "ann_date": "20260830", "roe": 20}}),
        (datetime(2026, 9, 1), {"income": income,
                               "indicator": {"end_date": "20260630", "ann_date": "20260820", "roe": 10},
                               "cashflow": {"end_date": "20260331", "ann_date": "20260901", "report_type": "1"}}),
    ])
    for financial in views.values():
        assert financial["indicator_period"] == "2026-06-30"
        assert financial["roe"] == 20
        assert financial["revenue"] == 200
        assert financial["cashflow_period"] == "2026-03-31"


@pytest.mark.parametrize("preferred_type", ["1", "4"])
def test_financial_snapshot_projection_prefers_consolidated_scope_before_same_period_announcement(
    financial_snapshot_projection, preferred_type,
):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"income": {"end_date": "20260630", "ann_date": "20260820",
                                           "report_type": preferred_type, "revenue": 0}}),
        (datetime(2026, 9, 1), {"income": {"end_date": "20260630", "ann_date": "20260901",
                                          "report_type": "5", "revenue": 100}}),
        (datetime(2026, 9, 2), {"income": {"end_date": "20260630", "ann_date": "20260902", "revenue": 200}}),
    ])
    assert all(financial["revenue"] == 0 for financial in views.values())


@pytest.mark.parametrize("report_type", ["2", "3", "6", "12", "unknown", True, 1.0])
def test_financial_snapshot_projection_bad_scope_future_period_does_not_mask_consolidated(
    financial_snapshot_projection, report_type,
):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"income": {"end_date": "20260630", "report_type": "1", "revenue": 200}}),
        (datetime(2026, 9, 1), {"income": {"end_date": "20260930", "report_type": report_type, "revenue": 999},
                               "cashflow": {"end_date": "20260930", "report_type": report_type, "n_cashflow_act": 999},
                               "balance_sheet": {"end_date": "20260930", "report_type": report_type}}),
    ])
    assert all(financial["income_period"] == "2026-06-30" and financial["revenue"] == 200 for financial in views.values())


def test_financial_snapshot_projection_new_period_unknown_scope_remains_legacy_compatible(financial_snapshot_projection):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"income": {"end_date": "20260331", "report_type": "1", "revenue": 100}}),
        (datetime(2026, 9, 1), {"income": {"end_date": "20260630", "revenue": 200}}),
    ])
    for financial in views.values():
        assert financial["income_period"] == "2026-06-30"
        assert financial["revenue"] == 200
        assert "report_type" not in financial


def test_financial_snapshot_projection_hides_incompatible_amounts_but_keeps_source_and_context(
    financial_snapshot_projection, monitor,
):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"indicator": {"end_date": "20260630", "roe": 12},
                                "income": {"end_date": "20260630", "report_type": "2", "revenue": 50, "n_income_attr_p": 5},
                                "cashflow": {"end_date": "20260630", "report_type": "6", "n_cashflow_act": 10}}),
    ])
    for financial in views.values():
        assert financial["indicator_period"] == "2026-06-30"
        assert financial["revenue"] is financial["net_profit"] is financial["operating_cashflow"] is None
        assert financial["income_period"] is financial["cashflow_period"] is None
        original = monitor.event_detail(financial["source_event_id"])
        assert original["metrics"]["income"]["revenue"] == 50
        assert original["metrics"]["cashflow"]["n_cashflow_act"] == 10
    full = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view="full")
    assert full["agent_context"]["fundamental_context"]["fundamentals"] == views["full"]
    context = json.loads(full["agent_context"]["analysis_context_pack_summary"].rsplit("\n", 1)[-1])
    assert context["fundamentals"] == views["full"]


@pytest.mark.parametrize("bad_section", ["not-a-financial-row", [1, 2], [["revenue", 999]], True, 123])
def test_financial_snapshot_projection_non_object_sections_are_unknown_not_page_errors(
    financial_snapshot_projection, monitor, bad_section,
):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"indicator": {"end_date": "20260630", "roe": 12},
                                "income": bad_section, "cashflow": bad_section, "balance_sheet": bad_section}),
    ])
    for financial in views.values():
        assert financial["indicator_period"] == "2026-06-30"
        assert financial["revenue"] is financial["net_profit"] is financial["operating_cashflow"] is None
        assert financial["income_period"] is financial["cashflow_period"] is None
        assert monitor.event_detail(financial["source_event_id"])["metrics"]["income"] == bad_section
    full = monitor.stock_workspace("600519.SH", days=3650, refresh=True, view="full")
    context = json.loads(full["agent_context"]["analysis_context_pack_summary"].rsplit("\n", 1)[-1])
    assert context["fundamentals"] == views["full"]
    assert full["agent_context"]["fundamental_context"]["fundamentals"] == views["full"]


def test_financial_snapshot_projection_non_object_indicator_does_not_hide_valid_income(financial_snapshot_projection):
    views = financial_snapshot_projection([
        (datetime(2026, 8, 20), {"indicator": "bad", "income": {"end_date": "20260630", "report_type": "1", "revenue": 200}}),
    ])
    for financial in views.values():
        assert financial["indicator_period"] is None
        assert financial["income_period"] == "2026-06-30"
        assert financial["revenue"] == 200


def test_financial_snapshot_projection_statement_scope_matches_collector(monitor):
    values = [None, "", " ", "\t1\n", "\u30004\u3000", "\xa05\u202f", "1", "4", "5", "2", "3", "6", "7", "8", "9", "10", "11", "12",
              "13", "unknown", True, False, 1, 4, 5, 1.0, [], {}]
    with monitor.repo.db.get_session() as session:
        for value in values:
            row = {"report_type": value}
            actual = session.execute(select(_financial_statement_scope_expr(literal(json.dumps(row))))).scalar_one()
            expected = financial_statement_scope_rank(row)
            assert actual == (-1 if expected is None else expected), repr(value)


def test_financial_snapshot_projection_date_validation_matches_collector(monitor):
    values = [None, "", True, False, 20260630, 20260630.0, "20260630", "2026-06-30", " 2026-06-30 ",
              "\t20260630\n", "\u30002026-06-30\u3000", "\xa020260630\u202f",
              "20240229", "20260229", "2026-02-30", "00000101", "00010101", "99991231", "20261331",
              "2026011", "bad20260630", "2026--0630", "2026-1-030", "2026-06-30T00:00:00"]
    with monitor.repo.db.get_session() as session:
        for value in values:
            actual = session.execute(select(_financial_calendar_date_expr(literal(value)))).scalar_one()
            assert actual == (financial_calendar_date(value) or ""), repr(value)


def test_financial_snapshot_projection_ignores_malformed_legacy_json_and_obeys_scope(monitor):
    now = utc_naive_now()
    with monitor.repo.db.get_session() as session:
        for identifier, kind, symbol, age, metrics in (
            (101, "fundamental_snapshot", "600519.SH", 1, '{broken'),
            (102, "fundamental_snapshot", "600519.SH", 2, '{"income":{"end_date":"20260630"}}'),
            (103, "fundamental_snapshot", "600519.SZ", 0, '{"income":{"end_date":"20261231"}}'),
            (104, "fundamental_snapshot", "600519.SH", 31, '{"income":{"end_date":"20260930"}}'),
            (201, "market_snapshot", "600519.SH", 1, '{"income":{"end_date":"20200101"},"close":2}'),
            (202, "market_snapshot", "600519.SH", 2, '{"income":{"end_date":"20291231"},"close":1}'),
        ):
            session.add(MonitoringEventRecord(
                id=identifier, source_key="test.snapshot", source_name="测试来源", source_type="test",
                external_id=str(identifier), event_type=kind, perspective="company", title="测试快照",
                symbol_codes=symbol, event_at=now - timedelta(days=age), metrics_json=metrics,
            ))
        session.commit()
    for overview in (True, False):
        bundle = monitor.repo.symbol_workspace_bundle(symbol="600519.SH", days=30, overview=overview)
        assert {row["event_type"]: row["id"] for row in bundle["snapshots"]} == {
            "fundamental_snapshot": 102, "market_snapshot": 201,
        }


@pytest.fixture()
def financial_workspaces(monitor, monkeypatch):
    """Exercise assembler guards, including non-JSON NaN, in both read views.

    Repository selection and provenance are covered by real temporary-DB tests;
    this fixture injects the same selected source rows into every representation.
    """
    monitor.sync_source("tushare.fundamentals")
    read_bundle = monitor.repo.symbol_workspace_bundle
    financial_metrics = {}

    def bundle_with_financial_metrics(**kwargs):
        bundle = read_bundle(**kwargs)
        source_event = None
        for rows in bundle.values():
            if isinstance(rows, list):
                for row in rows:
                    if (isinstance(row, dict) and row.get("event_type") == "fundamental_snapshot"
                            and isinstance(row.get("id"), int)):
                        row["metrics"] = financial_metrics
                        source_event = row
        bundle["financial_sections"] = {
            key: {"row": row, "event": source_event, "selection_status": "selected"}
            for key, row in financial_metrics.items() if isinstance(row, dict) and row
        }
        return bundle

    monkeypatch.setattr(monitor.repo, "symbol_workspace_bundle", bundle_with_financial_metrics)
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(
        "src.services.investment_monitor_service.EssayConsensusService",
        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}),
    )
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("financial reads must stay local"))

    def read(indicator, *, income=None, cashflow=None):
        financial_metrics.update(indicator=indicator, income=income or {}, cashflow=cashflow or {})
        return {
            view: monitor.stock_workspace("600519", days=3650, refresh=True, view=view)["stock"]
            for view in ("overview", "full")
        }

    return read


@pytest.mark.parametrize(("indicator", "expected_value", "expected_field", "expected_label"), [
    ({"q_sales_yoy": 0, "or_yoy": 18, "tr_yoy": 20}, 0, "q_sales_yoy", "单季收入同比"),
    ({"q_sales_yoy": None, "or_yoy": 0, "tr_yoy": 20}, 0, "or_yoy", "累计收入同比"),
    ({"q_sales_yoy": float("nan"), "or_yoy": None, "tr_yoy": 12.5}, 12.5, "tr_yoy", "累计总收入同比"),
    ({"q_sales_yoy": -2, "or_yoy": 18, "tr_yoy": 20}, -2, "q_sales_yoy", "单季收入同比"),
    ({"q_sales_yoy": None, "or_yoy": float("nan"), "tr_yoy": None}, None, None, None),
])
def test_financial_revenue_growth_retains_field_scope_and_valid_zero(
    financial_workspaces, indicator, expected_value, expected_field, expected_label,
):
    views = financial_workspaces({"end_date": "20260630", **indicator})
    assert views["overview"]["fundamentals"] == views["full"]["fundamentals"]
    fundamentals = views["full"]["fundamentals"]
    assert fundamentals["revenue_yoy"] == expected_value
    assert fundamentals["revenue_yoy_field"] == expected_field
    signals = [row for row in views["full"]["signals"] if "收入同比" in row["title"]]
    if expected_label:
        assert len(signals) == 1
        assert signals[0]["title"] == f"{expected_label} {expected_value:.2f}%"
    else:
        assert signals == []
    assert views["overview"]["signals"] == []


@pytest.mark.parametrize(("indicator", "expected_value", "expected_field", "expected_label"), [
    ({"netprofit_yoy": 0, "q_netprofit_yoy": 25}, 0, "netprofit_yoy", "累计净利润同比"),
    ({"netprofit_yoy": None, "q_netprofit_yoy": 0}, 0, "q_netprofit_yoy", "单季净利润同比"),
    ({"netprofit_yoy": float("nan"), "q_netprofit_yoy": 25}, 25, "q_netprofit_yoy", "单季净利润同比"),
    ({"netprofit_yoy": None, "q_netprofit_yoy": None}, None, None, None),
    ({"netprofit_yoy": float("nan"), "q_netprofit_yoy": float("nan")}, None, None, None),
])
def test_financial_profit_growth_retains_cumulative_or_quarterly_scope(
    financial_workspaces, indicator, expected_value, expected_field, expected_label,
):
    views = financial_workspaces({"end_date": "20260630", **indicator})
    assert views["overview"]["fundamentals"] == views["full"]["fundamentals"]
    fundamentals = views["full"]["fundamentals"]
    assert fundamentals["net_profit_yoy"] == expected_value
    assert fundamentals["net_profit_yoy_field"] == expected_field
    signals = [row for row in views["full"]["signals"] if "净利润同比" in row["title"]]
    if expected_label:
        assert len(signals) == 1
        assert signals[0]["title"] == f"{expected_label} {expected_value:.2f}%"
    else:
        assert signals == []


@pytest.mark.parametrize(("income_date", "cashflow_date", "expected_income", "expected_cashflow"), [
    ("20260331", "20260630", "2026-03-31", "2026-06-30"),
    (None, "20260331", None, "2026-03-31"),
    ("20260331", None, "2026-03-31", None),
    (None, None, None, None),
    ("20260230", "20260231", None, None),
])
def test_income_and_cashflow_periods_are_independent_and_never_borrow_indicator_date(
    financial_workspaces, income_date, cashflow_date, expected_income, expected_cashflow,
):
    views = financial_workspaces(
        {"end_date": "20260630", "q_sales_yoy": 0, "q_netprofit_yoy": 0},
        income={"end_date": income_date, "revenue": 100, "n_income_attr_p": 20},
        cashflow={"end_date": cashflow_date, "n_cashflow_act": 18},
    )
    assert views["overview"]["fundamentals"] == views["full"]["fundamentals"]
    fundamentals = views["full"]["fundamentals"]
    assert fundamentals["period"] == "20260630"
    assert fundamentals["as_of"] == "2026-06-30"
    assert fundamentals["income_period"] == expected_income
    assert fundamentals["cashflow_period"] == expected_cashflow
    assert fundamentals["revenue"] == 100
    assert fundamentals["net_profit"] == 20
    assert fundamentals["operating_cashflow"] == 18
    assert views["overview"]["overview"]["data_as_of"]["fundamentals"] == "2026-06-30"


@pytest.mark.parametrize(("indicator_date", "expected"), [
    ("20260630", "2026-06-30"), ("2026-06-30", "2026-06-30"),
    (None, None), ("", None), ("20260230", None),
])
def test_growth_indicator_period_never_borrows_income_date(financial_workspaces, indicator_date, expected):
    indicator = {"q_sales_yoy": 0, "netprofit_yoy": 5}
    if indicator_date is not None:
        indicator["end_date"] = indicator_date
    views = financial_workspaces(indicator, income={"end_date": "20260331", "revenue": 100})
    for view in ("overview", "full"):
        fundamentals = views[view]["fundamentals"]
        assert fundamentals["indicator_period"] == expected
        assert fundamentals["income_period"] == "2026-03-31"
        assert fundamentals["period"] == (indicator_date or "20260331")
        assert fundamentals["revenue_yoy"] == 0


@pytest.fixture()
def capital_workspaces(monitor, monkeypatch):
    """Read both real workspace assemblers using independent, local source dates."""
    monitor.sync_source("tushare.capital")
    monitor.sync_source("tushare.technical")
    read_bundle = monitor.repo.symbol_workspace_bundle
    capital_metrics = {}

    def bundle_with_capital_metrics(**kwargs):
        bundle = read_bundle(**kwargs)
        for rows in bundle.values():
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("event_type") == "capital_chip_snapshot":
                        row["metrics"] = capital_metrics
                    elif isinstance(row, dict) and row.get("event_type") == "technical_factor":
                        row["metrics"] = {"trade_date": "20260911", "rsi_6": 55}
        return bundle

    monkeypatch.setattr(monitor.repo, "symbol_workspace_bundle", bundle_with_capital_metrics)
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(
        "src.services.investment_monitor_service.EssayConsensusService",
        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}),
    )
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("capital reads must stay local"))

    def read(chip, *, moneyflow=None, margin=None, northbound=None):
        capital_metrics.update(
            chip_performance=chip, moneyflow=moneyflow or {}, margin=margin or {}, northbound=northbound or {},
        )
        return {
            view: monitor.stock_workspace("600519", days=3650, refresh=True, view=view)
            for view in ("overview", "full")
        }

    return read


def test_capital_source_dates_survive_overview_full_and_agent_context(capital_workspaces):
    views = capital_workspaces(
        {"trade_date": "20260828", "winner_rate": 83.43, "weight_avg": 73.44},
        moneyflow={"trade_date": "20260910", "net_mf_amount": 88},
        margin={"trade_date": "20260909", "rzye": 0},
        northbound={"trade_date": "20240816", "ratio": 0},
    )
    expected = {
        "chip_trade_date": "2026-08-28", "moneyflow_trade_date": "2026-09-10",
        "margin_trade_date": "2026-09-09", "northbound_trade_date": "2024-08-16",
    }
    overview = views["overview"]["stock"]
    full = views["full"]["stock"]
    for stock in (overview, full):
        assert {key: stock["capital"][key] for key in expected} == expected
        assert stock["capital"]["source"] == "筹码、资金与北向持股"
        assert stock["capital"]["weighted_cost"] == 73.44
        assert stock["technical"]["trade_date"] == "20260911"
    assert overview["overview"]["data_as_of"]["capital"] == "2026-08-28"
    context_text = views["full"]["agent_context"]["analysis_context_pack_summary"]
    context = json.loads(context_text.splitlines()[-1])
    assert {key: context["capital"][key] for key in expected} == expected
    assert context["capital"]["source"] == full["capital"]["source"]
    assert "日期为空表示未知，不得借用行情、技术指标或其他资金数据的日期" in context_text
    assert "net_mf_amount 与各买卖 *_amount 的原始单位为万元" in context_text
    assert "不得用大小单买卖额加总替代" in context_text
    assert "rzye、rzmre、rzche 的原始单位为元" in context_text
    assert "百分数表示的筹码模型估算获利比例，不是预测胜率" in context_text
    assert context["capital"]["winner_rate"] == 83.43
    assert context["capital"]["moneyflow"]["net_mf_amount"] == 88
    assert context["capital"]["margin"]["rzye"] == 0
    assert views["full"]["agent_context"]["chip_distribution"] == full["capital"]


@pytest.mark.parametrize("missing_date", [None, "", 0, "20260230", "2026-02-30", "not-a-date", True, False])
def test_undated_chip_values_do_not_borrow_funds_or_technical_dates(capital_workspaces, missing_date):
    views = capital_workspaces(
        {"trade_date": missing_date, "winner_rate": 0, "weight_avg": 0, "cost_50pct": 0, "cost_85pct": 0},
        moneyflow={"trade_date": "20260910", "net_mf_amount": 0},
        margin={"rzye": 0}, northbound={"ratio": 0},
    )
    for payload in views.values():
        capital = payload["stock"]["capital"]
        assert capital["chip_trade_date"] is None
        assert capital["margin_trade_date"] is None
        assert capital["northbound_trade_date"] is None
        assert capital["moneyflow_trade_date"] == "2026-09-10"
        assert capital["winner_rate"] == capital["weighted_cost"] == capital["cost_50pct"] == capital["cost_85pct"] == 0
        assert capital["moneyflow"]["net_mf_amount"] == capital["margin"]["rzye"] == capital["northbound"]["ratio"] == 0
    assert views["overview"]["stock"]["overview"]["data_as_of"]["capital"] is None
    context = json.loads(views["full"]["agent_context"]["analysis_context_pack_summary"].splitlines()[-1])
    assert context["capital"]["chip_trade_date"] is None
    assert context["capital"]["weighted_cost"] == 0
    chip_signal = next(row for row in views["full"]["stock"]["signals"] if row["title"].startswith("筹码获利比例"))
    assert "加权平均成本 0" in chip_signal["detail"]


@pytest.mark.parametrize("valid_date", ["20240229", "2024-02-29", 20240229])
def test_capital_dates_accept_valid_compact_or_iso_calendar_dates(capital_workspaces, valid_date):
    views = capital_workspaces(
        {"trade_date": valid_date, "winner_rate": 0},
        moneyflow={"trade_date": valid_date}, margin={"trade_date": valid_date}, northbound={"trade_date": valid_date},
    )
    for payload in views.values():
        capital = payload["stock"]["capital"]
        assert all(capital[key] == "2024-02-29" for key in (
            "chip_trade_date", "moneyflow_trade_date", "margin_trade_date", "northbound_trade_date",
        ))


@pytest.mark.parametrize("invalid_date", ["2026-02-30", "not-a-date", True, False])
def test_all_capital_date_segments_reject_invalid_source_dates(capital_workspaces, invalid_date):
    views = capital_workspaces(
        {"trade_date": "20260910", "winner_rate": 0},
        moneyflow={"trade_date": invalid_date, "net_mf_amount": 0},
        margin={"trade_date": invalid_date, "rzye": 0}, northbound={"trade_date": invalid_date, "ratio": 0},
    )
    for payload in views.values():
        capital = payload["stock"]["capital"]
        assert capital["chip_trade_date"] == "2026-09-10"
        assert all(capital[key] is None for key in (
            "moneyflow_trade_date", "margin_trade_date", "northbound_trade_date",
        ))
    context_text = views["full"]["agent_context"]["analysis_context_pack_summary"]
    context = json.loads(context_text.splitlines()[-1])
    assert all(context["capital"][key] is None for key in (
        "moneyflow_trade_date", "margin_trade_date", "northbound_trade_date",
    ))
    assert "也不能用嵌套原始 trade_date 替代" in context_text


def test_super_watchlist_aggregates_beyond_feed_page_limit(monitor):
    monitor.sync_source("tushare.fundamentals")
    monitor.create_external_source({"source_key": "api.bulk", "name": "批量事实"})
    monitor.ingest_external_events("api.bulk", [{
        "external_id": f"bulk-{index}", "title": f"贵州茅台事实 {index}",
        "symbols": ["600519.SH"], "event_at": "2026-08-19T10:00:00",
    } for index in range(230)])

    stock = monitor.super_watchlist(days=3650)["stocks"][0]

    assert stock["evidence"]["raw_event_count"] == 231
    assert stock["coverage"][1]["name"] == "fundamental"
    assert stock["coverage"][1]["available"] is True
    assert stock["fundamentals"]["net_profit_yoy"] == 25


def test_super_watchlist_reads_latest_price_from_shared_market_cache(monitor):
    monitor.sync_source("tushare.market")
    MarketDataRepository(DatabaseManager.get_instance()).upsert_ticks([{
        "code": "600519", "timestamp": datetime.now(), "price": 1318.5,
        "open": 1290, "high": 1320, "low": 1288, "pre_close": 1300,
        "change": 18.5, "change_percent": 1.4231, "volume": 10, "amount": 2_000_000,
    }], source="test.shared.quote")

    stock = monitor.super_watchlist(days=3650)["stocks"][0]

    assert stock["market"]["price"] == 1318.5
    assert stock["market"]["source"] == "test.shared.quote"
    assert stock["market"]["amount"] == 2_000_000


@pytest.mark.parametrize(("raw", "expected"), [
    ("920748", "920748.BJ"), ("920748.BJ", "920748.BJ"),
    ("430047", "430047.BJ"), ("830799", "830799.BJ"),
    ("600519", "600519.SH"), ("000001", "000001.SZ"),
    ("510300", "510300.SH"), ("000001.SH", "000001.SH"),
])
def test_monitor_uses_shared_exchange_inference_without_rewriting_explicit_markets(raw, expected):
    assert InvestmentMonitorService._canonical_ts_code(raw) == expected


@pytest.mark.parametrize("requested", ["920748", "920748.BJ"])
def test_stock_workspace_accepts_bse_920_quote_from_bare_local_code(monitor, requested):
    MarketDataRepository(monitor.repo.db).upsert_ticks([{
        "code": "920748", "timestamp": datetime(2026, 9, 11, 15), "price": 10.5,
        "open": 10, "high": 10.6, "low": 9.9, "pre_close": 10,
        "change": 0.5, "change_percent": 5, "volume": 100, "amount": 1050,
    }], source="test.bse.snapshot")
    stock = monitor.stock_workspace(requested, days=183, refresh=True, view="overview")["stock"]
    assert stock["symbol"] == "920748.BJ"
    assert stock["market"]["price"] == 10.5
    assert stock["market"]["change_pct"] == 5
    assert stock["market"]["prev_close"] == 10
    assert stock["market"]["updated_at"] == "2026-09-11T15:00:00"
    assert stock["market"]["source"] == "test.bse.snapshot"


def test_super_watchlist_preserves_each_snapshot_type_under_frequent_market_updates(monitor):
    now = utc_naive_now()
    with monitor.repo.db.get_session() as session:
        for index in range(220):
            session.add(MonitoringEventRecord(
                source_key="tushare.market", source_name="行情", source_type="tushare",
                external_id=f"frequent-market-{index}", event_type="market_snapshot",
                perspective="investor", title="行情", symbol_codes="600519.SH",
                event_at=now - timedelta(hours=index), metrics_json='{"close":1300}',
            ))
        for index, symbol, age, profit in (
            (1, "600519.SH", 31, 10),
            (2, "600519.SH", 30, 20),
            (3, "600519.SH", 30, 25),
            (4, "603306.SH", 1, 999),
        ):
            session.add(MonitoringEventRecord(
                source_key="tushare.fundamentals", source_name="财务", source_type="tushare",
                external_id=f"financial-{index}", event_type="fundamental_snapshot",
                perspective="company", title="财务", symbol_codes=symbol,
                event_at=now - timedelta(days=age),
                metrics_json=json.dumps({"indicator": {
                    "end_date": "20260630", "ann_date": f"202608{20 + index:02d}", "netprofit_yoy": profit,
                }}),
            ))
        session.commit()

    bundle = monitor.repo.symbol_workspace_bundle(symbol="600519.SH", days=365)

    assert len(bundle["snapshots"]) == 2
    fundamental = next(row for row in bundle["snapshots"] if row["event_type"] == "fundamental_snapshot")
    assert fundamental["external_id"] == "financial-3"
    assert bundle["type_counts"]["fundamental_snapshot"] == 1
    stock = monitor.super_watchlist(days=365)["stocks"][0]
    assert stock["fundamentals"]["net_profit_yoy"] == 25


@pytest.mark.parametrize("view", ("overview", "full"))
@pytest.mark.parametrize("missing", (None, "fundamental_snapshot", "market_snapshot", "both"))
def test_workspace_financial_source_ids_follow_actual_selected_snapshot(monitor, monkeypatch, view, missing):
    now = utc_naive_now()
    with monitor.repo.db.get_session() as session:
        for identifier, kind, symbol, age, metrics in (
            (100, "fundamental_snapshot", "600519.SH", 3, {"income": {"end_date": "20260331", "revenue": 100}}),
            (101, "fundamental_snapshot", "600519.SH", 2, {"income": {"end_date": "20260630", "revenue": 200}}),
            (200, "market_snapshot", "600519.SH", 3, {"valuation_pe_ttm": 10}),
            (201, "market_snapshot", "600519.SH", 2, {"valuation_pe_ttm": 20}),
            (300, "fundamental_snapshot", "603306.SH", 1, {"income": {"revenue": 999}}),
            (301, "market_snapshot", "603306.SH", 1, {"valuation_pe_ttm": 999}),
            (900, "market_open_snapshot", "600519.SH", 0, {"open": 1300}),
            (999, "news", "600519.SH", 0, {}),
        ):
            if symbol == "600519.SH" and kind in {"fundamental_snapshot", "market_snapshot"} and missing in {kind, "both"}:
                continue
            session.add(MonitoringEventRecord(
                id=identifier, source_key="test.snapshot", source_name=f"来源-{identifier}", source_type="test",
                external_id=f"snapshot-{identifier}", event_type=kind, perspective="company",
                title="测试快照", symbol_codes=symbol, event_at=now - timedelta(days=age),
                metrics_json=json.dumps(metrics), raw_payload='{"private_fixture":"not_for_workspace"}',
            ))
        session.commit()
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda _: {
        "current_price": 1500, "update_time": datetime.now().isoformat(), "source": "test.live_quote",
    })
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService",
                        lambda **_: SimpleNamespace(snapshot=lambda *a, **k: {}))
    payload = monitor.stock_workspace("600519.SH", days=183, refresh=True, view=view)
    stock = payload["stock"]
    expected_fundamental = None if missing in {"fundamental_snapshot", "both"} else 101
    expected_market = None if missing in {"market_snapshot", "both"} else 201
    assert stock["fundamentals"]["source_event_id"] == expected_fundamental
    assert stock["valuation"]["source_event_id"] == expected_market
    assert stock["fundamentals"]["revenue"] == (200 if expected_fundamental else None)
    assert stock["valuation"]["pe_ttm"] == (20 if expected_market else None)
    assert stock["market"]["price"] == 1500
    for field, identifier in (("fundamentals", expected_fundamental), ("valuation", expected_market)):
        assert stock[field]["source"] == (f"来源-{identifier}" if identifier else None)
        assert stock[field]["source_event_id"] is None or type(stock[field]["source_event_id"]) is int
    assert "raw_payload" not in json.dumps(stock) and "private_fixture" not in json.dumps(stock)
    if view == "full":
        assert payload["agent_context"]["fundamental_context"]["fundamentals"]["source_event_id"] == expected_fundamental
        assert payload["agent_context"]["fundamental_context"]["valuation"]["source_event_id"] == expected_market
    monkeypatch.setattr(monitor, "_super_stock", lambda *a, **k: pytest.fail("fresh cache must not reread snapshots"))
    cached = monitor.stock_workspace("600519.SH", days=183, view=view)
    assert cached["cache"]["hit"] is True
    assert cached["stock"]["fundamentals"]["source_event_id"] == expected_fundamental
    assert cached["stock"]["valuation"]["source_event_id"] == expected_market


def test_stock_workspace_exposes_one_shared_context_for_legacy_modules(monitor):
    monitor.sync_source("tushare.news.cls")
    monitor.sync_source("eastmoney.guba_posts")

    result = monitor.stock_workspace("600519", days=3650, refresh=True)

    assert result["version"] == "5.2-unified-decision-context"
    assert result["stock"]["symbol"] == "600519.SH"
    assert result["data_policy"]["upstream_fetch_on_read"] is False
    assert [item["version"] for item in result["iterations"]] == ["V1", "V2", "V3", "V4", "V5"]
    agent_context = result["agent_context"]
    assert "全渠道统一个股事实底稿" in agent_context["analysis_context_pack_summary"]
    assert agent_context["source_count"] >= 1
    assert "贵州茅台发布公告" in agent_context["news_context"]


def test_stock_workspace_short_cache_reuses_assembled_payload(monitor, monkeypatch):
    first = monitor.stock_workspace("600519", days=3650, refresh=True)
    monkeypatch.setattr(monitor, "_super_stock", lambda *args, **kwargs: pytest.fail("must use workspace cache"))

    second = monitor.stock_workspace("600519.SH", days=3650)

    assert first["generated_at"] == second["generated_at"]
    assert second["cache"]["hit"] is True
    assert second["cache"]["state"] == "fresh"


def test_stock_overview_is_bounded_read_only_and_skips_research_work(monitor, monkeypatch):
    monitor.sync_source("tushare.fundamentals")
    now = utc_naive_now()
    with monitor.repo.db.get_session() as session:
        for event_type in ("company_announcement", "research_report_pdf", "essay", "news", "stock_forum_post"):
            for index in range(8):
                session.add(MonitoringEventRecord(
                    source_key=f"test.{event_type}", source_name=event_type, source_type="test",
                    external_id=f"{event_type}-{index}", event_type=event_type,
                    perspective="company", title=f"{event_type} {index}", symbol_codes="600519.SH",
                    event_at=now - timedelta(minutes=index), summary="原文摘要",
                ))
        session.commit()
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(monitor.repo, "list_sources", lambda: pytest.fail("overview must not read source status"))
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("overview must not fetch upstream"))
    monkeypatch.setattr("src.services.investment_monitor_service.get_index_stock_name", lambda code: None)
    monitor._name_cache.clear()
    monkeypatch.setattr("src.services.investment_monitor_service.EssayConsensusService", lambda *args, **kwargs: pytest.fail("overview must not sample consensus"))
    monkeypatch.setattr(monitor, "_agent_context_from_stock", lambda *args: pytest.fail("overview must not assemble LLM context"))
    statements = []
    with monitor.repo.db.get_session() as session:
        engine = session.get_bind()
    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe)
    try:
        result = monitor.stock_workspace("600519", days=3650, refresh=True, view="overview")
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe)
    stock = result["stock"]
    assert result["view"] == "overview"
    assert "agent_context" not in result
    assert stock["name"] == "600519.SH"
    assert stock["history"] == []
    assert stock["coverage"] == []
    assert stock["signals"] == []
    assert stock["stock_comments"]["items"] == []
    assert stock["consensus"]["essay_analysis"]["status"] == "not_loaded"
    assert stock["fundamentals"]["net_profit_yoy"] == 25
    assert stock["overview"]["data_as_of"]["fundamentals"] == "2026-06-30"
    assert stock["overview"]["evidence_counts_complete"] is False
    assert len(stock["overview"]["recent_evidence"]) == 12
    assert {section["key"] for section in stock["overview"]["sections"]} == {"announcements", "research", "opinions", "news"}
    assert all(len(section["items"]) == 3 for section in stock["overview"]["sections"])
    assert not any("stock_daily" in sql or "research_notes" in sql for sql in statements)
    # Only per-table top-tier distinct-version window counts are needed for
    # honest conflict status; the overview still avoids a full evidence census.
    assert "count(*) over (partition by workspace_distinct_sections.section_key)" in statements[0]
    assert not any("count(" in sql for sql in statements[1:])
    assert "group by monitoring_events.source_key" not in statements[0]
    assert not any(sql.lstrip().startswith(("insert", "update", "delete")) for sql in statements)
    # Latest snapshots + direct-research probe + related fallback + three other
    # sections. Original report fields are selected in bulk, never lazy N+1.
    assert len(statements) == 6


def test_stock_workspace_messages_keep_news_under_newer_opinion_and_enterprise_pressure(monitor, monkeypatch):
    now = utc_naive_now()
    monkeypatch.setattr("src.repositories.investment_monitor_repo.utc_naive_now", lambda: now)
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("message reads must stay local"))
    monkeypatch.setattr("src.services.investment_monitor_service.get_index_stock_name", lambda code: None)
    monkeypatch.setattr(
        "src.services.investment_monitor_service.EssayConsensusService",
        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}),
    )
    enterprise_types = (
        "enterprise_registration", "enterprise_risk", "enterprise_credit",
        "enterprise_ipr", "enterprise_history",
    )
    with monitor.repo.db.get_session() as session:
        for types, count, age in (
            (("news", "long_news"), 25, timedelta(days=2)),
            (("essay",), 45, timedelta(hours=1)),
            (enterprise_types, 15, timedelta(days=1)),
        ):
            for index in range(count):
                event_type = types[index % len(types)]
                session.add(MonitoringEventRecord(
                    source_key=f"test.{event_type}", source_name=event_type, source_type="test",
                    external_id=f"scoped-{index}", event_type=event_type,
                    perspective="company", title=f"{event_type} {index}", symbol_codes="600519.SH",
                    event_at=now - age - timedelta(minutes=index), summary="已保存资料",
                ))
        # All three budget queries must apply the same exact symbol/day scope.
        for event_type in ("news", "long_news", "essay", *enterprise_types):
            for label, symbol, at in (
                ("old", "600519.SH", now - timedelta(days=31)),
                ("other-market", "600519.SZ", now),
                ("substring", "1600519.SH", now),
            ):
                session.add(MonitoringEventRecord(
                    source_key=f"test.{event_type}", source_name=event_type, source_type="test",
                    external_id=f"excluded-{label}", event_type=event_type,
                    perspective="company", title=f"excluded {label}", symbol_codes=symbol,
                    event_at=at, summary="不能进入当前工作区",
                ))
        session.commit()
        engine = session.get_bind()

    statements = []
    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe)
    try:
        views = {
            view: monitor.stock_workspace("600519.SH", days=30, refresh=True, view=view)["stock"]
            for view in ("overview", "full")
        }
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe)

    overview, full = views["overview"], views["full"]
    preview = next(section["items"] for section in overview["overview"]["sections"] if section["key"] == "news")
    messages = full["messages"]["items"]
    news = [row for row in messages if row["event_type"] in {"news", "long_news"}]
    opinions = [row for row in messages if row["event_type"] == "essay"]
    enterprise = [row for row in messages if row["event_type"] in enterprise_types]
    assert len(preview) == 3
    assert {row["id"] for row in preview} <= {row["id"] for row in news}
    assert (len(news), len(opinions), len(enterprise)) == (20, 10, 10)
    assert {row["event_type"] for row in news} == {"news", "long_news"}
    assert {row["title"] for row in news} == {f"{('news', 'long_news')[index % 2]} {index}" for index in range(20)}
    assert {row["title"] for row in opinions} == {f"essay {index}" for index in range(10)}
    assert {row["title"] for row in enterprise} == {f"{enterprise_types[index % 5]} {index}" for index in range(10)}
    assert len(messages) == len({row["id"] for row in messages}) == 40
    assert messages == sorted(messages, key=lambda row: (row["event_at"], row["id"]), reverse=True)
    assert not any(row["title"].startswith("excluded") for row in messages)
    assert full["messages"]["count"] == 85
    assert full["evidence"]["raw_event_count"] == 85
    assert full["evidence"]["counts_complete"] is True
    assert overview["evidence"]["counts_complete"] is False
    assert not any(sql.lstrip().startswith(("insert", "update", "delete", "replace")) for sql in statements)


@pytest.mark.parametrize(("counts", "expected"), [
    ((25, 45, 15), (20, 10, 10)),
    ((1, 45, 0), (1, 10, 0)),
    ((25, 0, 0), (20, 0, 0)),
    ((0, 0, 15), (0, 0, 10)),
    ((0, 0, 0), (0, 0, 0)),
])
def test_stock_workspace_message_budgets_do_not_borrow_and_use_id_for_equal_times(monitor, counts, expected):
    at = utc_naive_now() - timedelta(hours=1)
    selected_ids = []
    with monitor.repo.db.get_session() as session:
        for event_type, count, budget in zip(("long_news", "essay", "enterprise_risk"), counts, (20, 10, 10)):
            rows = []
            for index in range(count):
                row = MonitoringEventRecord(
                    source_key=f"test.{event_type}", source_name=event_type, source_type="test",
                    external_id=f"tie-{index}", event_type=event_type,
                    perspective="company", title=f"{event_type} {index}", symbol_codes="600519.SH",
                    event_at=at, summary="同一发布时间",
                )
                session.add(row)
                rows.append(row)
            session.flush()
            selected_ids.extend([row.id for row in rows][-budget:])
        session.commit()

    bundle = monitor.repo.symbol_workspace_bundle(symbol="600519.SH", days=30)
    messages = bundle["messages"]
    assert tuple(sum(row["event_type"] == event_type for row in messages)
                 for event_type in ("long_news", "essay", "enterprise_risk")) == expected
    assert [row["id"] for row in messages] == sorted(set(selected_ids), reverse=True)
    assert len(messages) == sum(expected)
    assert bundle["raw_event_count"] == sum(counts)


@pytest.mark.parametrize("view", ["overview", "full"])
def test_stock_workspace_cache_hit_overlays_fresh_quote_without_mutating_cached_facts(monitor, monkeypatch, view):
    quote = {
        "stock_code": "600519", "current_price": 1300, "change": 0, "change_percent": 0,
        "prev_close": 1300, "update_time": "2026-09-11T10:00:00", "source": "test.snapshot",
    }
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: dict(quote))
    first = monitor.stock_workspace("600519", days=183, refresh=True, view=view)
    quote.update(current_price=1200, change=-100, change_percent=-7.69, update_time="2026-09-11T10:01:00")
    monkeypatch.setattr(monitor, "_super_stock", lambda *args, **kwargs: pytest.fail("cached evidence must not be rebuilt"))
    second = monitor.stock_workspace("600519", days=183, view=view)
    assert second["cache"]["hit"] is True
    assert first["stock"]["market"]["price"] == 1300
    assert second["stock"]["market"]["price"] == 1200
    assert second["stock"]["market"]["change_pct"] == -7.69
    assert second["stock"]["market"]["prev_close"] == 1300
    assert second["stock"]["market"]["updated_at"] == "2026-09-11T10:01:00"
    assert not any("收盘涨跌" in row["title"] for row in second["stock"]["signals"])
    if view == "overview":
        assert second["stock"]["overview"]["data_as_of"]["market"] == "2026-09-11T10:01:00"
    else:
        assert second["agent_context"]["realtime_quote"]["price"] == 1200


def test_stock_workspace_does_not_replace_newer_market_data_with_old_quote(monitor):
    newer = {"price": 1500, "updated_at": "2026-09-11T07:00:00Z", "quote_kind": "daily_close"}
    older = {"current_price": 1400, "update_time": "2026-09-11T14:00:00", "source": "old.snapshot"}
    assert monitor._market_with_quote(newer, older) == newer
    assert monitor._market_with_quote(newer, {**older, "update_time": None}) == newer


def test_overview_daily_close_and_valuation_keep_independent_actual_dates(monitor, monkeypatch):
    monitor.sync_source("tushare.market")
    with monitor.repo.db.get_session() as session:
        session.execute(update(MonitoringEventRecord).where(
            MonitoringEventRecord.event_type == "market_snapshot",
        ).values(metrics_json='{"close":1300,"trade_date":"20260819","valuation_trade_date":"20260818","valuation_pe_ttm":30}'))
        session.commit()
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    stock = monitor.stock_workspace("600519", days=3650, view="overview", refresh=True)["stock"]
    assert stock["market"]["updated_at"] == "2026-08-19T15:00:00+08:00"
    assert stock["market"]["quote_kind"] == "daily_close"
    assert stock["valuation"]["as_of"] == "2026-08-18"
    assert stock["overview"]["data_as_of"]["valuation"] == "2026-08-18"


def test_overview_does_not_assign_quote_date_to_undated_valuation(monitor, monkeypatch):
    monitor.sync_source("tushare.market")
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    stock = monitor.stock_workspace("600519", days=3650, view="overview", refresh=True)["stock"]
    assert stock["valuation"]["as_of"] is None


def test_stock_workspace_cache_isolates_view_and_database(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    first = monitor.stock_workspace("600519", days=183, refresh=True, view="overview")
    full = monitor.stock_workspace("600519", days=183)
    assert first["view"] == "overview"
    assert full["view"] == "full"
    assert full["cache"]["hit"] is False
    monkeypatch.setattr(monitor.repo.db, "_db_url", "sqlite:///another-user-environment.db")
    other_database = monitor.stock_workspace("600519", days=183, view="overview")
    assert other_database["cache"]["hit"] is False


@pytest.fixture()
def coherent_workspace_cache(monitor, monkeypatch):
    from src.services import investment_monitor_service as module

    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("workspace reads must stay local"))
    monkeypatch.setattr(module, "get_index_stock_name", lambda code: None)
    monkeypatch.setattr(module, "EssayConsensusService", lambda **kwargs: SimpleNamespace(snapshot=lambda *a, **k: {}))
    pending = []
    monkeypatch.setattr(module, "_stock_workspace_refresh_executor", SimpleNamespace(submit=lambda task: pending.append(task)))
    calls = []
    original = monitor._super_stock

    def build(symbol, **kwargs):
        calls.append((symbol, kwargs["days"], kwargs["overview"]))
        return original(symbol, **kwargs)

    monkeypatch.setattr(monitor, "_super_stock", build)

    def add_news(title="新消息", summary="新闻摘录"):
        with monitor.repo.db.get_session() as session:
            row = MonitoringEventRecord(
                source_key="test.news", source_name="新闻来源", source_type="test",
                external_id=title, event_type="news", perspective="company", title=title,
                symbol_codes="600519.SH", event_at=utc_naive_now(), summary=summary,
            )
            session.add(row)
            session.commit()
            return row.id

    def key(view, symbol="600519.SH", days=183):
        return f"{monitor.repo.db._db_url}:{view}:{symbol}:{days}"

    def expire_overview():
        entry = module._stock_workspace_cache[key("overview")]
        module._stock_workspace_cache[key("overview")] = (time.monotonic() - 31, *entry[1:])

    def read(view="full", refresh=False, symbol="600519.SH", days=183):
        return monitor.stock_workspace(symbol, days=days, view=view, refresh=refresh)

    return SimpleNamespace(
        monitor=monitor, module=module, read=read, add_news=add_news, calls=calls,
        pending=pending, key=key, expire_overview=expire_overview, build=build,
    )


def test_overview_explicit_refresh_invalidates_only_matching_full_after_success(coherent_workspace_cache):
    case = coherent_workspace_cache
    case.add_news("原新闻")
    case.read()
    case.read(days=90)
    case.read(symbol="300308.SZ")
    before = len(case.calls)
    case.read("overview", refresh=True)
    assert len(case.calls) == before + 1  # Refreshing the preview does not build full eagerly.
    assert case.read(days=90)["cache"]["hit"] is True
    assert case.read(symbol="300308.SZ")["cache"]["hit"] is True
    assert case.read()["cache"]["hit"] is False
    assert case.read()["cache"]["hit"] is True


def test_failed_overview_refresh_keeps_successful_full_cache(coherent_workspace_cache, monkeypatch):
    case = coherent_workspace_cache
    case.read()
    monkeypatch.setattr(case.monitor, "_super_stock", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read failed")))
    with pytest.raises(RuntimeError, match="read failed"):
        case.read("overview", refresh=True)
    assert case.read()["cache"]["hit"] is True


def test_unchanged_background_overview_rebuild_keeps_full_without_heavy_reads(coherent_workspace_cache):
    case = coherent_workspace_cache
    # Full carries 600 characters but the overview carries only 240 Unicode
    # code points. That projection difference is not a source revision.
    case.add_news("原新闻", "😀" * 260 + "完整摘录尾部")
    case.read("overview", refresh=True)
    full = case.read()
    case.expire_overview()
    assert case.read("overview")["cache"]["state"] == "stale-refreshing"
    case.pending.pop()()
    assert case.read()["cache"]["hit"] is True
    assert case.read()["generated_at"] == full["generated_at"]
    assert sum(not overview for _, _, overview in case.calls) == 1


@pytest.mark.parametrize("change", ["new", "title", "summary", "evidence"])
def test_background_overview_new_or_revised_news_invalidates_full_once(coherent_workspace_cache, change):
    case = coherent_workspace_cache
    event_id = case.add_news("原新闻")
    case.read("overview", refresh=True)
    case.read()
    if change == "new":
        event_id = case.add_news("新出现的新闻")
    else:
        with case.monitor.repo.db.get_session() as session:
            values = {"title": "修订后的标题"} if change == "title" else {"summary": "修订后的摘录"}
            if change == "evidence":
                values = {"metrics_json": json.dumps({"_evidence": {"evidence_level": "unverified"}})}
            session.execute(update(MonitoringEventRecord).where(MonitoringEventRecord.id == event_id).values(**values))
            session.commit()
    case.expire_overview()
    case.read("overview")
    case.pending.pop()()
    overview = case.read("overview")
    assert sum(not overview for _, _, overview in case.calls) == 1
    full = case.read()
    assert full["cache"]["hit"] is False
    news = {row["id"]: row for row in full["stock"]["messages"]["items"]}
    for row in overview["stock"]["messages"]["items"]:
        assert row["id"] in news
        assert row["title"] == news[row["id"]]["title"]
        assert row["summary"] == news[row["id"]]["summary"][:240]
        assert row["metrics"]["_evidence"] == news[row["id"]]["metrics"]["_evidence"]
    assert case.read()["cache"]["hit"] is True


def test_newer_full_news_revision_is_not_rejected_by_unchanged_older_overview(coherent_workspace_cache):
    case = coherent_workspace_cache
    event_id = case.add_news("原新闻")
    case.read("overview", refresh=True)
    with case.monitor.repo.db.get_session() as session:
        session.execute(update(MonitoringEventRecord).where(MonitoringEventRecord.id == event_id).values(title="后来修订"))
        session.commit()
    full = case.read()
    assert full["cache"]["state"] == "rebuilt"
    assert full["stock"]["messages"]["items"][0]["title"] == "后来修订"
    assert case.read()["cache"]["hit"] is True
    case.expire_overview()
    case.read("overview")
    case.pending.pop()()
    # The preview catches up to a full snapshot which already has this revision.
    assert case.read()["cache"]["hit"] is True
    assert sum(not overview for _, _, overview in case.calls) == 1


def test_unchanged_overview_rebuilt_during_full_read_does_not_discard_full(coherent_workspace_cache, monkeypatch):
    case = coherent_workspace_cache
    case.add_news("原新闻")
    case.read("overview", refresh=True)

    def interleaved_full(symbol, **kwargs):
        stock = case.build(symbol, **kwargs)
        if not kwargs["overview"]:
            case.expire_overview()
            case.read("overview")
            case.pending.pop()()
        return stock

    monkeypatch.setattr(case.monitor, "_super_stock", interleaved_full)
    assert case.read()["cache"]["state"] == "rebuilt"
    assert case.read()["cache"]["hit"] is True
    assert sum(not overview for _, _, overview in case.calls) == 1


@pytest.mark.parametrize("change_news", [False, True])
def test_full_inflight_before_explicit_overview_refresh_does_not_restore_old_cache(
    coherent_workspace_cache, monkeypatch, change_news,
):
    case = coherent_workspace_cache
    case.add_news("原新闻")
    case.read("overview", refresh=True)

    def delayed_full(symbol, **kwargs):
        stock = case.build(symbol, **kwargs)
        if not kwargs["overview"]:
            if change_news:
                case.add_news("中途出现的新闻")
            # Deterministically interleave a finished overview before the
            # earlier full assembler publishes; no thread or server is needed.
            case.read("overview", refresh=True)
        return stock

    monkeypatch.setattr(case.monitor, "_super_stock", delayed_full)
    response = case.read()
    assert response["cache"]["state"] == "superseded"
    assert case.key("full") not in case.module._stock_workspace_cache
    assert sum(not overview for _, _, overview in case.calls) == 1
    monkeypatch.setattr(case.monitor, "_super_stock", case.build)
    refreshed = case.read()
    assert refreshed["cache"]["hit"] is False
    if change_news:
        assert "中途出现的新闻" in {row["title"] for row in refreshed["stock"]["messages"]["items"]}
    assert case.read()["cache"]["hit"] is True


def test_workspace_read_service_does_not_initialize_sources_or_artifacts(monitor, monkeypatch):
    monkeypatch.setattr(monitor.repo, "ensure_sources", lambda *args: pytest.fail("reading must not seed sources"))
    monkeypatch.setattr("src.services.investment_monitor_service.AnnouncementArtifactService", lambda: pytest.fail("reading must not create download folders"))
    service = InvestmentMonitorService(
        repository=monitor.repo, tushare=FakeTushare(), stock_service=FakeStockService(),
        initialize_sources=False,
    )
    assert service._announcement_artifacts is None


def test_stock_workspace_endpoint_validates_and_passes_view(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.v1.endpoints import investment_monitor as endpoint
    calls = []
    class ReadService:
        def __init__(self, *, initialize_sources):
            assert initialize_sources is False
        def stock_workspace(self, symbol, **kwargs):
            calls.append((symbol, kwargs))
            return {"symbol": symbol, **kwargs}
    monkeypatch.setattr(endpoint, "InvestmentMonitorService", ReadService)
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/monitor")
    client = TestClient(app)
    overview = client.get("/monitor/stock-workspace/600519?view=overview&days=183")
    assert overview.status_code == 200
    assert calls[-1] == ("600519", {"days": 183, "refresh": False, "view": "overview"})
    assert client.get("/monitor/stock-workspace/600519").json()["view"] == "full"
    assert client.get("/monitor/stock-workspace/600519?view=unknown").status_code == 422


def test_watchlist_backfill_job_is_durable_and_idempotent_while_active(monitor):
    first = monitor.request_backfill("600519", days=183)
    second = monitor.request_backfill("600519.SH", days=183)
    assert first["id"] == second["id"]
    assert first["symbol"] == "600519.SH"
    assert first["status"] == "pending"
    dashboard = monitor.super_watchlist(days=183)
    assert dashboard["backfill_jobs"][0]["progress"] == 0


@pytest.mark.parametrize("original_status", ["pending", "running"])
def test_watchlist_two_year_backfill_does_not_reuse_narrower_task(monitor, original_status):
    first = monitor.request_backfill("600519", days=183)
    monitor.repo.update_backfill_job(first["id"], status=original_status)
    wide = monitor.request_backfill("600519.SH", days=730)
    repeated = monitor.request_backfill("600519", days=730)
    assert wide["id"] != first["id"]
    assert repeated["id"] == wide["id"]
    assert wide["days"] == 730 and wide["status"] == "pending"
    original = next(job for job in monitor.repo.pending_backfill_jobs() if job["id"] == first["id"])
    assert original["days"] == 183 and original["status"] == original_status
    assert monitor.repo.list_backfill_jobs(["600519.SH"])[0]["days"] == 730
    # Public stock facts are shared, but another symbol must not inherit this task.
    assert monitor.request_backfill("000001.SZ", days=730)["id"] != wide["id"]


def test_watchlist_two_year_range_reaches_runner_without_clamping(monitor, monkeypatch):
    job = monitor.request_backfill("600519", days=730)
    seen = []
    monkeypatch.setattr(monitor, "_save_backfill_daily", lambda symbol, days: seen.append((symbol, days)))
    monkeypatch.setattr(monitor.repo, "list_sources", lambda: [])
    result = monitor.run_backfill_job(job["id"], job["symbol"], days=job["days"])
    assert seen == [("600519.SH", 730)]
    assert result["days"] == 730 and result["progress"] == 100
    assert monitor._backfill_days is None


def test_watchlist_backfill_endpoint_validates_requested_history(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.v1.endpoints import investment_monitor as endpoint
    calls = []
    worker = SimpleNamespace(enqueue=lambda symbol, days: calls.append((symbol, days)) or {"days": days})
    monkeypatch.setattr(endpoint.WatchlistBackfillWorker, "get_instance", lambda: worker)
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/monitor")
    client = TestClient(app)
    url = "/monitor/super-watchlist/600519.SH/backfill"
    assert client.post(url + "?days=730").json()["days"] == 730
    assert client.post(url).json()["days"] == 183
    assert client.post(url + "?days=731").status_code == 422
    assert client.post(url + "?days=29").status_code == 422
    assert calls == [("600519.SH", 730), ("600519.SH", 183)]


def test_watchlist_keyword_aliases_match_short_company_names(monitor):
    names = {"603306.SH": "华懋科技", "300476.SZ": "胜宏科技"}

    assert monitor._symbols_for_text("华懋产能释放超预期", names) == ["603306.SH"]
    assert monitor._symbols_for_text("胜宏订单与300476景气度更新", names) == ["300476.SZ"]
    assert monitor._symbols_for_text("无关行业观点", names) == []


def test_zsxq_projection_only_hydrates_incrementally_changed_topics(monitor):
    now = utc_naive_now()
    source = monitor.repo.get_source("zsxq.essays")
    source["last_success_at"] = now.isoformat() + "Z"
    with monitor.repo.db.get_session() as session:
        session.add_all([
            ResearchNote(
                topic_id="recent-note", group_id="g1", group_name="调研纪要",
                title="贵州茅台最新渠道", content="新增原文", content_hash="h1",
                created_at=now, synced_at=now,
            ),
            ResearchNote(
                topic_id="recent-analysis", group_id="g1", group_name="调研纪要",
                title="贵州茅台分析更新", content="旧原文，新分析", content_hash="h2",
                created_at=now - timedelta(days=2), synced_at=now - timedelta(days=2),
            ),
            ResearchNote(
                topic_id="stale-topic", group_id="g1", group_name="调研纪要",
                title="历史原文", content="没有变化", content_hash="h3",
                created_at=now - timedelta(days=3), synced_at=now - timedelta(days=3),
            ),
        ])
        session.flush()
        session.add(EssayAnalysisRecord(
            topic_id="recent-analysis", status="completed", model="test",
            prompt_version="v1", input_hash="a1", summary="分析刚更新",
            updated_at=now,
        ))
        session.commit()

    events = monitor._zsxq_events(source)

    assert {event["external_id"] for event in events} == {"recent-note", "recent-analysis"}


def test_watchlist_keyword_reindex_updates_existing_local_essay(monitor, monkeypatch):
    source = monitor.repo.get_source("zsxq.essays")
    monitor.repo.upsert_events([monitor._base_event(
        source, external_id="topic-hm", event_type="essay", perspective="investor",
        title="华懋订单跟踪", summary="简称口径的小作文", symbols=[], sentiment="neutral",
        importance=50, event_at=datetime.now(), tags=[], url="https://wx.zsxq.com/topic/hm",
    )])
    monitor.watchlist = lambda: ["603306.SH"]
    monkeypatch.setattr(monitor, "_stock_name", lambda _symbol: "华懋科技")

    result = monitor.reindex_watchlist_keywords(days=30)
    events = monitor.repo.all_symbol_events(symbol="603306.SH", days=30)

    assert result["updated"] == 1
    assert len(events) == 1
    assert events[0]["url"] == "https://wx.zsxq.com/topic/hm"


def test_consensus_keeps_broker_numbers_separate_from_ai_essay_expectations(monitor):
    research = [{
        "id": 1, "event_type": "institution_forecast", "event_at": "2026-08-20T01:00:00Z",
        "metrics": {"rating": "买入", "target_price_min": 80, "target_price_max": 100,
                    "forecasts": [{"quarter": "2026", "eps": 2, "np": 300}]},
    }, {
        "id": 2, "event_type": "institution_forecast", "event_at": "2026-08-20T02:00:00Z",
        "metrics": {"rating": "增持", "target_price_min": 90, "target_price_max": 110,
                    "forecasts": [{"quarter": "2026", "eps": 4, "np": 500}]},
    }]
    essays = [{"id": 3, "title": "渠道调研", "event_at": "2026-08-20T03:00:00Z", "metrics": {}}]
    essay_snapshot = {
        "status": "completed", "source_count": 20, "analyzed_count": 20,
        "estimates": [{
            "event_id": 3, "topic_id": "3", "title": "渠道调研", "event_at": "2026-08-20T03:00:00Z",
            "metric": "net_profit", "period": "2026H2", "value_text": "下半年利润约5亿元",
            "evidence": "原文预计下半年利润约5亿元", "confidence": 0.7,
        }],
    }

    result = monitor._consensus_payload(research, essays, essay_snapshot)

    assert result["target_price"]["median"] == 95
    assert result["forecasts"][0]["eps_median"] == 3
    assert result["forecasts"][0]["np_median"] == 400
    assert result["essay_expectation_count"] == 1
    assert result["essay_analysis"]["analyzed_count"] == 20
    assert result["essay_expectations"][0]["metric"] == "net_profit"
    assert "不混算" in result["method"]


def _broker_event(event_id, *, symbol="600519.SH", org_name=None, **metrics):
    return {
        "id": event_id, "external_id": f"report-{event_id}", "source_key": "tushare.institution",
        "event_type": "institution_forecast", "event_at": f"2026-08-{event_id:02d}T08:00:00Z",
        "title": f"报告 {event_id}", "symbols": [symbol],
        "metrics": {"org_name": org_name, **metrics},
    }


def test_same_broker_report_for_two_stocks_has_distinct_idempotent_records(monitor, monkeypatch):
    symbols = ["600519.SH", "603306.SH"]
    monkeypatch.setattr(monitor, "equity_watchlist", lambda: symbols)

    def query(api_name, params=None, fields=None):
        if api_name != "report_rc":
            return {"rows": []}
        return {"rows": [{
            "ts_code": params["ts_code"], "report_date": "20260820", "report_title": "同一行业深度报告",
            "org_name": "测试证券", "author_name": "研究员", "quarter": "2027",
            "eps": 10 if params["ts_code"] == symbols[0] else 2,
        }]}

    monkeypatch.setattr(monitor.tushare, "query", query)
    source = monitor.repo.get_source("tushare.institution")
    first = monitor._tushare_institution_events(source)
    assert len({item["external_id"] for item in first}) == 2
    assert len({item["metrics"]["report_identity"] for item in first}) == 1
    assert monitor.repo.upsert_events(first)["created"] == 2
    assert monitor.repo.upsert_events(monitor._tushare_institution_events(source))["created"] == 0
    for symbol, eps in zip(symbols, (10, 2)):
        stored = monitor.repo.recent_symbol_events(symbol=symbol, days=3650, event_types=("institution_forecast",))
        assert len(stored) == 1
        assert stored[0]["symbols"] == [symbol]
        assert stored[0]["metrics"]["forecasts"][0]["eps"] == eps
        assert stored[0]["metrics"]["org_name"] == "测试证券"


def test_old_and_new_broker_ids_are_deduplicated_per_stock_without_deleting_legacy_records(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "equity_watchlist", lambda: ["600519.SH", "603306.SH"])
    raw = {"report_date": "20260820", "report_title": "行业深度", "org_name": "测试证券", "author_name": "研究员"}
    legacy_key = "|".join(raw[field] for field in ("report_date", "report_title", "org_name", "author_name"))
    legacy_id = hashlib.sha256(legacy_key.encode()).hexdigest()[:32]
    source = monitor.repo.get_source("tushare.institution")
    monitor.repo.upsert_events([monitor._base_event(
        source, external_id=legacy_id, event_type="institution_forecast", perspective="institution",
        title="行业深度", summary="旧版尚存记录", symbols=["600519.SH"], sentiment="neutral", importance=50,
        event_at=datetime(2026, 8, 20), metrics={"forecasts": [{"quarter": "2027", "eps": 999}]}, tags=[],
    )])

    def query(api_name, params=None, fields=None):
        return {"rows": [{**raw, "ts_code": params["ts_code"], "quarter": "2027", "eps": 2}]} if api_name == "report_rc" else {"rows": []}

    monkeypatch.setattr(monitor.tushare, "query", query)
    fresh = monitor._tushare_institution_events(source)
    monitor.repo.upsert_events(fresh)
    monitor.repo.upsert_events(monitor._tushare_institution_events(source))
    all_rows = []
    for symbol in ("600519.SH", "603306.SH"):
        rows = monitor.repo.recent_symbol_events(symbol=symbol, days=3650, event_types=("institution_forecast",))
        all_rows.extend(rows)
        result = monitor._consensus_payload(rows, [], broker_total_record_count=len(rows))
        assert result["broker_sample"]["report_count"] == 1
        assert result["forecasts"][0]["eps_median"] == 2
        assert result["forecasts"][0]["metric_sample_counts"]["eps"] == 1
    assert len(all_rows) == 3  # No migration/deletion: two new stock keys plus the old row.
    assert len(monitor._broker_report_rows(all_rows)) == 2
    assert any(item["external_id"] == legacy_id for item in all_rows)


def test_broker_sample_separates_coverage_reports_metrics_and_known_institutions(monitor):
    first = _broker_event(1, org_name="测试证券", forecasts=[
        {"quarter": "2026", "eps": 1, "np": 10000},
        {"quarter": "2026", "eps": 1, "np": 10000},
        {"quarter": "2027", "eps": 9},
    ])
    second = _broker_event(2, org_name="测试证券", forecasts=[{"quarter": "2026", "eps": 3, "roe": 0}])
    third = _broker_event(3, forecasts=[{"quarter": "2026", "np": 30000}])
    third["actors"] = ["只有分析师姓名，不能冒充机构"]
    result = monitor._consensus_payload([first, second, third], [], broker_total_record_count=40, broker_sample_limit=3)

    sample = result["broker_sample"]
    assert result["broker_report_count"] == sample["total_record_count"] == 40
    assert sample["input_record_count"] == sample["report_count"] == sample["valid_report_count"] == 3
    assert sample["truncated"] is True
    assert sample["aggregation"] == "report_weighted"
    assert sample["institution_count"] is None
    assert sample["known_institution_count"] == sample["unknown_institution_report_count"] == 1
    assert [item["event_id"] for item in sample["source_notes"]] == [3, 2, 1]
    assert sample["source_notes"][0]["org_name"] is None
    current, following = result["forecasts"]
    assert current["period"] == "2026"
    assert current["report_count"] == 3
    assert current["sample_count"] == 2  # Historical max-field count, not document/institution count.
    assert current["metric_sample_counts"] == {"eps": 2, "np": 2, "pe": 0, "roe": 1, "op_rt": 0}
    assert current["eps_median"] == 2
    assert current["np_median"] == 20000  # report_rc unit remains 万元; API does not silently convert.
    assert current["roe_median"] == 0
    assert set(current["metric_source_event_ids"]["eps"]) == {1, 2}
    assert set(current["metric_source_event_ids"]["np"]) == {1, 3}
    assert following["period"] == "2027"
    assert following["eps_median"] == 9
    assert following["metric_sample_counts"]["np"] == 0


def test_target_prices_distinguish_endpoint_median_from_document_weighted_midpoints(monitor):
    rows = [
        _broker_event(1, org_name="甲证券", target_price_min=100, target_price_max=200),
        _broker_event(2, org_name="甲证券", target_price_min=110, target_price_max=120),
        _broker_event(3, org_name="乙证券", target_price_min=130, target_price_max=140),
    ]
    result = monitor._consensus_payload(rows, [])
    target = result["target_price"]
    assert target["sample_count"] == target["endpoint_count"] == 6
    assert target["document_count"] == 3
    assert target["institution_count"] == 2
    assert target["median"] == 125  # Legacy endpoint statistic retained explicitly.
    assert (target["document_min"], target["document_median"], target["document_max"]) == (115, 135, 150)
    assert "不是机构等权" in target["document_method"]
    same = monitor._consensus_payload([_broker_event(1, target_price_min=88, target_price_max=88)], [])["target_price"]
    assert same["document_count"] == 1
    assert same["document_median"] == 88
    one_sided = monitor._consensus_payload([_broker_event(1, target_price_min=None, target_price_max=90)], [])["target_price"]
    assert one_sided["document_count"] == one_sided["endpoint_count"] == 1
    assert one_sided["document_median"] == 90


def test_conflicting_same_report_period_metric_is_not_averaged_or_counted_twice(monitor):
    result = monitor._consensus_payload([_broker_event(1, forecasts=[
        {"quarter": "2027", "eps": 1, "np": 0},
        {"quarter": "2027", "eps": 3, "np": 0},
    ])], [])
    row = result["forecasts"][0]
    assert row["metric_sample_counts"]["eps"] == 0
    assert "eps_median" not in row
    assert row["conflicting_metric_counts"] == {"eps": 1}
    assert row["np_median"] == 0
    assert row["metric_sample_counts"]["np"] == 1


def test_unknown_forecast_periods_are_not_combined_into_an_invented_comparable_horizon(monitor):
    result = monitor._consensus_payload([
        _broker_event(1, forecasts=[{"eps": 99}, {"quarter": "2026", "eps": 1}]),
        _broker_event(2, forecasts=[{"quarter": None, "eps": 200}]),
        _broker_event(3, forecasts=[{"quarter": "2027", "eps": 3}]),
    ], [])
    assert [(row["period"], row["eps_median"]) for row in result["forecasts"]] == [("2026", 1), ("2027", 3)]
    assert result["broker_sample"]["missing_period_report_count"] == 2
    assert result["broker_sample"]["valid_report_count"] == 2
    assert result["broker_sample"]["report_count"] == 3


def test_broker_zero_valid_samples_and_unloaded_coverage_do_not_invent_values(monitor):
    result = monitor._consensus_payload([_broker_event(
        1, target_price_min=0, target_price_max=float("inf"), forecasts=[
            {"quarter": "2026", "eps": float("nan"), "np": None, "pe": float("inf"), "roe": True},
        ],
    )], [], broker_total_record_count=1)
    assert result["broker_sample"]["valid_report_count"] == 0
    assert result["broker_sample"]["report_count"] == 1
    assert result["broker_sample"]["truncated"] is False
    assert result["forecasts"] == []
    assert result["target_price"]["document_count"] == 0
    assert result["target_price"]["document_median"] is None
    assert result["target_price"]["median"] is None
    json.dumps(result, allow_nan=False)
    empty = monitor._consensus_payload([], [], broker_loaded=False, broker_sample_limit=200)
    assert empty["broker_sample"]["loaded"] is False
    assert empty["broker_sample"]["total_record_count"] is None
    assert empty["broker_sample"]["truncated"] is None


def test_full_workspace_forecast_sample_is_not_crowded_out_by_pdf_previews(monitor, monkeypatch):
    now = utc_naive_now()
    with monitor.repo.db.get_session() as session:
        for index in range(201):
            session.add(MonitoringEventRecord(
                source_key="test.pdf", source_name="PDF", source_type="test", external_id=f"pdf-{index}",
                event_type="research_report_pdf", perspective="institution", title="较新研报",
                symbol_codes="600519.SH", event_at=now, summary="预览",
            ))
        for index in range(205):
            session.add(MonitoringEventRecord(
                source_key="tushare.institution", source_name="Tushare", source_type="test", external_id=f"forecast-{index}",
                event_type="institution_forecast", perspective="institution", title="盈利预测",
                symbol_codes="600519.SH", event_at=now - timedelta(days=1, minutes=index),
                metrics_json=json.dumps({"forecasts": [{"quarter": "2027", "eps": index}], "org_name": "测试证券"}),
                raw_payload="不应读取的原始附件" * 1000,
            ))
        session.commit()
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("workspace must stay local"))
    statements = []
    with monitor.repo.db.get_session() as session:
        engine = session.get_bind()
    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe)
    try:
        result = monitor.stock_workspace("600519.SH", days=3650, refresh=True)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe)
    sample = result["stock"]["consensus"]["broker_sample"]
    assert sample["total_record_count"] == 205
    assert sample["input_record_count"] == sample["report_count"] == sample["sample_limit"] == 200
    assert sample["truncated"] is True
    assert sample["valid_report_count"] == 200
    assert sample["institution_count"] == 1
    assert result["stock"]["consensus"]["forecasts"][0]["metric_sample_counts"]["eps"] == 200
    # Attribution now reads original subject fields for the bounded research
    # slice. It must not issue one lazy raw-payload query for each report or
    # return those originals to the client. Legacy unidentifiable rows remain
    # explicitly counted as unknown rather than disappearing from consensus.
    raw_reads = [statement for statement in statements if "raw_payload" in statement]
    assert len(raw_reads) <= 4  # Includes the separate three-direct-report guard.
    assert all("limit" in statement or " in (" in statement for statement in raw_reads)
    assert sample["unknown_relation_count"] == 200
    assert "raw_payload" not in json.dumps(result, ensure_ascii=False)


def test_equity_watchlist_excludes_bonds_and_funds_from_company_apis(monitor):
    monitor.watchlist = lambda: ["603306.SH", "113677.SH", "510300.SH"]

    assert monitor.equity_watchlist() == ["603306.SH"]


def test_due_source_fetches_are_parallel_but_results_persist_normally(monitor, monkeypatch):
    sources = []
    for index in range(4):
        sources.append(monitor.create_external_source({
            "source_key": f"api.parallel{index}",
            "name": f"并发来源 {index}",
            "poll_interval_seconds": 10,
        }))

    def slow_empty(_source):
        time.sleep(0.1)
        return []

    monkeypatch.setenv("INVESTMENT_MONITOR_MAX_WORKERS", "4")
    monkeypatch.setattr(monitor, "_events_for_source", slow_empty)
    started = time.perf_counter()
    result = monitor._sync_sources(sources)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.3
    assert result["totals"]["sources"] == 4
    assert result["totals"]["success"] == 4
    assert result["totals"]["max_workers"] == 4


def test_fast_source_is_persisted_before_slow_peer_finishes(monitor, monkeypatch):
    fast = monitor.create_external_source({
        "source_key": "api.fastlive", "name": "快源", "poll_interval_seconds": 10,
    })
    slow = monitor.create_external_source({
        "source_key": "api.slowlive", "name": "慢源", "poll_interval_seconds": 10,
    })
    slow_finished = {"value": False}
    fast_persisted_while_slow_running = {"value": False}
    original_upsert = monitor.repo.upsert_events

    def fetch(source):
        if source["source_key"] == "api.slowlive":
            time.sleep(0.15)
            slow_finished["value"] = True
            return []
        time.sleep(0.01)
        return []

    def observe_upsert(events):
        if not slow_finished["value"]:
            fast_persisted_while_slow_running["value"] = True
        return original_upsert(events)

    monkeypatch.setenv("INVESTMENT_MONITOR_MAX_WORKERS", "2")
    monkeypatch.setattr(monitor, "_events_for_source", fetch)
    monkeypatch.setattr(monitor.repo, "upsert_events", observe_upsert)

    result = monitor._sync_sources([slow, fast])

    assert result["totals"]["success"] == 2
    assert fast_persisted_while_slow_running["value"] is True


def test_builtin_sources_use_continuous_monitoring_cadences():
    by_key = {item["source_key"]: item for item in BUILTIN_MONITORING_SOURCES}
    assert by_key["tushare.news.cls"]["poll_interval_seconds"] == 15
    assert by_key["cninfo.announcements"]["poll_interval_seconds"] == 60
    assert by_key["eastmoney.guba_posts"]["poll_interval_seconds"] == 30
    assert by_key["akshare.stock_comments"]["enabled"] is False
    assert max(item["poll_interval_seconds"] for item in BUILTIN_MONITORING_SOURCES) <= 600


def test_news_scheduler_cursor_converts_utc_to_shanghai_clock():
    converted = InvestmentMonitorService._utc_iso_to_market_naive("2026-08-21T12:21:44Z")

    assert converted == datetime(2026, 8, 21, 20, 21, 44)


def test_zsxq_mirror_cursor_keeps_utc_database_clock():
    converted = InvestmentMonitorService._utc_iso_to_utc_naive("2026-08-21T12:21:44Z")

    assert converted == datetime(2026, 8, 21, 12, 21, 44)


def test_external_investment_monitor_worker_cannot_start_inside_web_process(monkeypatch):
    monkeypatch.setenv("INVESTMENT_MONITOR_RUN_MODE", "external")
    worker = InvestmentMonitorWorker()

    started = worker.start()
    triggered = worker.trigger()

    assert started["running"] is False
    assert started["externally_managed"] is True
    assert triggered["running"] is False
    assert worker._thread is None
