# -*- coding: utf-8 -*-
"""Unified investment monitor normalization, deduplication and scorecard tests."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.repositories.market_data_repo import MarketDataRepository
from src.services.investment_monitor_service import (
    BUILTIN_MONITORING_SOURCES,
    InvestmentMonitorService,
    MonitoringSourceNotConfigured,
)
from src.services.investment_monitor_worker import InvestmentMonitorWorker
from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote, utc_naive_now


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


def test_watchlist_backfill_job_is_durable_and_idempotent_while_active(monitor):
    first = monitor.request_backfill("600519", days=183)
    second = monitor.request_backfill("600519.SH", days=183)
    assert first["id"] == second["id"]
    assert first["symbol"] == "600519.SH"
    assert first["status"] == "pending"
    dashboard = monitor.super_watchlist(days=183)
    assert dashboard["backfill_jobs"][0]["progress"] == 0


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
