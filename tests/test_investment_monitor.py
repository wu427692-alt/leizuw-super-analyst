# -*- coding: utf-8 -*-
"""Unified investment monitor normalization, deduplication and scorecard tests."""

from __future__ import annotations

import os

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.investment_monitor_service import InvestmentMonitorService
from src.storage import DatabaseManager


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


@pytest.fixture()
def monitor(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "monitor.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    repository = InvestmentMonitorRepository(DatabaseManager.get_instance())
    service = InvestmentMonitorService(repository=repository, tushare=FakeTushare(), stock_service=FakeStockService())
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


def test_source_status_separates_sync_success_from_actual_data_freshness(monitor):
    monitor.create_external_source({"source_key": "api.empty", "name": "空返回来源"})
    monitor.sync_source("tushare.news.cls")
    sources = monitor.list_sources()
    by_key = {item["source_key"]: item for item in sources["items"]}
    assert by_key["tushare.news.cls"]["freshness_status"] == "fresh"
    assert by_key["tushare.news.cls"]["stored_event_count"] == 1
    assert by_key["api.empty"]["freshness_status"] == "empty"
    assert sources["with_data"] >= 1
    assert sources["empty"] >= 1


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
    assert result["version"] == "5.0"
    assert stock["symbol"] == "600519.SH"
    assert stock["market"]["price"] == 1300
    assert stock["fundamentals"]["net_profit_yoy"] == 25
    assert stock["capital"]["winner_rate"] == 0.8
    assert stock["technical"]["rsi_6"] == 55
    assert {row["name"] for row in stock["coverage"]} >= {"market", "fundamental", "capital"}
    assert all(set(event["metrics"]) <= {"_evidence"} for event in stock["timeline"])


def test_watchlist_backfill_job_is_durable_and_idempotent_while_active(monitor):
    first = monitor.request_backfill("600519", days=183)
    second = monitor.request_backfill("600519.SH", days=183)
    assert first["id"] == second["id"]
    assert first["symbol"] == "600519.SH"
    assert first["status"] == "pending"
    dashboard = monitor.super_watchlist(days=183)
    assert dashboard["backfill_jobs"][0]["progress"] == 0
