# -*- coding: utf-8 -*-
"""Durable queue, DeepSeek normalization and essay-radar API tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.v1.endpoints import essay_radar
from src.config import Config
from src.repositories.essay_analysis_repo import EssayAnalysisRepository, _select_in_batches
from src.services.essay_analysis_service import (
    DeepSeekEssayAnalyzer,
    EssayAnalysisService,
    EssayDailyReportService,
    _dashboard_rows_cache,
)
from src.services.essay_analysis_worker import EssayAnalysisWorker
from src.services.financial_data_service import ResearchNoteService
from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote


@pytest.fixture()
def essay_service(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "essay-radar.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    notes = ResearchNoteService()
    notes.import_topics([{
        "topic_id": "topic-1",
        "title": "机器人订单增长",
        "content": "公司称机器人订单增长，量产进度仍存在风险。",
        "create_time": "2026-08-19T14:26:11+0800",
        "group": {"group_id": "g1", "name": "调研纪要"},
    }])
    service = EssayAnalysisService()
    try:
        yield service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def test_analyzer_normalizes_json_output_and_stock_codes(monkeypatch) -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{"message": {"content": """{
          "items": [{
            "topic_id": "topic-1", "summary": "订单增长但量产有风险",
            "primary_category": "company_research", "sentiment": "mixed",
            "time_horizon": "medium", "importance_score": 83,
            "confidence_score": 0.88, "tags": ["订单增长", "风险提示"],
            "industries": ["机械设备"], "themes": ["机器人"],
            "stock_mentions": [{"ts_code": "688836.SS", "name": "宇树科技",
              "stance": "watching", "confidence": 0.8, "rationale": "等待量产"}],
            "key_points": ["订单增长"], "catalysts": ["量产"], "risks": ["交付延期"]
          }]
        }"""}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    session = MagicMock()
    session.post.return_value = response
    analyzer = DeepSeekEssayAnalyzer(api_key="test-key", session=session)

    result = analyzer.analyze_batch([{
        "topic_id": "topic-1",
        "title": "机器人订单增长",
        "content": "正文",
        "existing_symbols": [],
    }])

    assert result["items"][0]["stock_mentions"][0]["ts_code"] == "688836.SH"
    assert result["items"][0]["importance_score"] == 83
    assert result["usage"]["total_tokens"] == 150
    payload = session.post.call_args.kwargs["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}


def test_large_topic_id_queries_are_split_below_sqlite_parameter_limit() -> None:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []

    rows = _select_in_batches(
        session,
        ResearchNote,
        ResearchNote.topic_id,
        [f"topic-{index}" for index in range(1201)],
    )

    assert rows == []
    assert session.execute.call_count == 3


def test_queue_claim_save_and_dashboard_are_restart_safe(essay_service) -> None:
    progress = essay_service.progress(days=30)
    assert progress["pending"] == 1

    claimed = essay_service.repo.claim_batch(limit=10, max_attempts=4)
    assert [item["topic_id"] for item in claimed] == ["topic-1"]
    essay_service.repo.save_successes([{
        "topic_id": "topic-1",
        "summary": "机器人订单增长，关注量产风险。",
        "primary_category": "company_research",
        "sentiment": "mixed",
        "time_horizon": "medium",
        "importance_score": 80,
        "confidence_score": 0.9,
        "tags": ["订单增长", "风险提示"],
        "industries": ["机械设备"],
        "themes": ["机器人"],
        "stock_mentions": [
            {"ts_code": "600519.SH", "name": "贵州茅台", "stance": "watching", "confidence": 0.8, "rationale": "关注量产"},
            {"ts_code": "688836.SH", "name": "宇树科技", "stance": "watching", "confidence": 0.6, "rationale": "未上市公司"},
        ],
        "key_points": ["订单增长"],
        "catalysts": ["量产"],
        "risks": ["交付延期"],
    }], raw_response="{}", usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

    assert essay_service.progress(days=30)["coverage_percent"] == 100.0
    dashboard = essay_service.dashboard(days=30)
    assert dashboard["summary"]["analyzed_count"] == 1
    assert dashboard["top_tags"][0] == {"name": "订单增长", "count": 1}
    assert dashboard["top_stocks"][0]["ts_code"] == "600519.SH"
    insights = essay_service.insights(days=30, trend_days=7)
    assert len(insights["trend"]) == 7
    assert insights["coverage"]["analyzed_count"] == 1
    assert insights["source_quality"][0]["name"] == "unknown"
    assert [item["name"] for item in insights["watchlist"]] == ["华懋科技", "胜宏科技"]
    deep = essay_service.deep_insights(days=30, trend_days=7)
    assert len(deep["pulse"]) == 7
    assert deep["summary"]["theme_count"] == 1
    assert deep["layers"]["themes"][0]["label"] == "机器人"
    deep_stocks = {item["label"]: item["ts_code"] for item in deep["layers"]["stocks"]}
    assert deep_stocks["贵州茅台"] == "600519.SH"
    assert deep_stocks["宇树科技"] == ""
    assert deep["theme_heatmap"]["items"][0]["name"] == "机器人"
    assert deep["evidence_funnel"][2] == {"name": "含催化或风险", "count": 1}


def test_historical_backfill_queues_only_requested_unqueued_notes(essay_service) -> None:
    ResearchNoteService().import_topics([{
        "topic_id": "topic-old",
        "title": "较早的历史纪要",
        "content": "历史正文",
        "create_time": "2025-08-01T09:00:00+0800",
        "group": {"group_id": "g1", "name": "调研纪要"},
    }, {
        "topic_id": "topic-new",
        "title": "较新的历史纪要",
        "content": "历史正文",
        "create_time": "2025-08-02T09:00:00+0800",
        "group": {"group_id": "g1", "name": "调研纪要"},
    }], enqueue_analysis=False)

    before = essay_service.historical_backlog()
    assert before["total_notes"] == 3
    assert before["unqueued"] == 2

    queued = essay_service.enqueue_unqueued(count=1, order="newest")
    assert queued["requested"] == 1
    assert queued["selected"] == 1
    assert queued["created"] == 1
    with essay_service.repo.db.get_session() as session:
        topic_ids = set(session.execute(select(EssayAnalysisRecord.topic_id)).scalars().all())
    assert topic_ids == {"topic-1", "topic-new"}
    assert essay_service.historical_backlog()["unqueued"] == 1

    remaining = essay_service.enqueue_unqueued(count=5000, order="oldest")
    assert remaining["selected"] == 1
    assert remaining["created"] == 1
    assert essay_service.historical_backlog()["unqueued"] == 0


def test_worker_start_returns_immediately_when_already_running(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.essay_analysis_worker.DeepSeekEssayAnalyzer",
        lambda: MagicMock(configured=True),
    )
    worker = EssayAnalysisWorker()
    worker._thread = MagicMock()
    worker._thread.is_alive.return_value = True

    result = worker.start(bootstrap_recent=False)

    assert result["running"] is True


def test_essay_radar_read_api(essay_service, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.essay_analysis_worker.EssayAnalysisWorker.status",
        lambda self: {"running": False},
    )
    start_calls = []
    monkeypatch.setattr(
        "src.services.essay_analysis_worker.EssayAnalysisWorker.start",
        lambda self, **kwargs: start_calls.append(kwargs) or {"running": True},
    )
    app = FastAPI()
    app.include_router(essay_radar.router, prefix="/api/v1/essay-radar")
    client = TestClient(app)

    response = client.get("/api/v1/essay-radar/status")
    assert response.status_code == 200
    assert response.json()["progress"]["total_notes"] == 1

    response = client.get("/api/v1/essay-radar/analyses")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    assert client.get("/api/v1/essay-radar/word-cloud?period=day&kind=stocks").status_code == 200
    assert client.get("/api/v1/essay-radar/insights?days=30&trend_days=14").status_code == 200
    assert client.get("/api/v1/essay-radar/deep-insights?days=30&trend_days=14").status_code == 200
    backlog = client.get("/api/v1/essay-radar/historical-backlog")
    assert backlog.status_code == 200
    assert backlog.json()["unqueued"] == 0
    assert backlog.json()["total_notes"] == 1
    assert backlog.json()["group_count"] == 1
    assert backlog.json()["earliest_note_at"].startswith("2026-08-19")
    assert backlog.json()["latest_note_at"].startswith("2026-08-19")
    queued = client.post("/api/v1/essay-radar/backfill-count", json={"count": 100, "order": "newest"})
    assert queued.status_code == 200
    assert queued.json()["queue"]["selected"] == 0
    assert start_calls == [{"bootstrap_recent": False}]
    assert client.post("/api/v1/essay-radar/backfill-count", json={"count": 5001, "order": "newest"}).status_code == 422
    reports = client.get("/api/v1/essay-radar/daily-reports")
    assert reports.status_code == 200
    assert "models" in reports.json()


def test_feed_searches_raw_library_without_ai_analysis(essay_service) -> None:
    ResearchNoteService().import_topics([{
        "topic_id": "topic-raw-only",
        "title": "尚未分析的历史原文",
        "content": "这段原文包含全库独家关键词和未入队信息。",
        "create_time": "2025-08-01T09:00:00+0800",
        "group": {"group_id": "g1", "name": "调研纪要"},
        "owner": {"name": "原文作者"},
    }], enqueue_analysis=False)
    app = FastAPI()
    app.include_router(essay_radar.router, prefix="/api/v1/essay-radar")
    client = TestClient(app)

    feed = client.get("/api/v1/essay-radar/feed?days=0&query=全库独家关键词")
    assert feed.status_code == 200
    assert feed.json()["scope"] == "all_stored_notes"
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["topic_id"] == "topic-raw-only"
    assert feed.json()["items"][0]["status"] == "not_queued"
    assert "全库独家关键词" in feed.json()["items"][0]["note"]["content"]

    completed_only = client.get("/api/v1/essay-radar/feed?days=0&analysis_status=completed")
    assert completed_only.status_code == 200
    assert completed_only.json()["total"] == 0

    uncompleted = client.get("/api/v1/essay-radar/feed?days=0&analysis_status=uncompleted")
    assert uncompleted.status_code == 200
    assert uncompleted.json()["total"] == 2

    reused_total = client.get(
        "/api/v1/essay-radar/feed?days=0&query=全库独家关键词&page=2&known_total=123"
    )
    assert reused_total.status_code == 200
    assert reused_total.json()["total"] == 123


def test_word_cloud_and_daily_report_are_periodic_and_idempotent(essay_service, monkeypatch) -> None:
    claimed = essay_service.repo.claim_batch(limit=10, max_attempts=4)
    essay_service.repo.save_successes([{
        "topic_id": "topic-1", "summary": "机器人订单增长", "primary_category": "company_research",
        "sentiment": "bullish", "time_horizon": "medium", "importance_score": 88,
        "confidence_score": 0.9, "tags": ["订单增长"], "industries": ["机械设备"], "themes": ["机器人"],
        "stock_mentions": [{"ts_code": "688836.SH", "name": "宇树科技", "stance": "bullish", "confidence": 0.8, "rationale": "订单增长"}],
        "key_points": ["订单增长"], "catalysts": ["量产"], "risks": ["交付"],
        "evidence": [{"claim": "订单增长", "evidence": "公司称订单增长", "strength": "medium"}],
        "contradictions": [], "falsification_conditions": ["订单取消"], "monitoring_points": ["月度交付"],
        "earnings_impact": "收入可能增长", "valuation_impact": "估值取决于交付", "source_quality": "medium",
        "novelty_score": 70, "information_type": "management_guidance",
    }], raw_response="{}", usage={})

    cloud = essay_service.word_cloud(period="day", anchor_date="2026-08-19", kind="stocks")
    assert cloud["items"][0]["name"] == "宇树科技"
    assert cloud["items"][0]["count"] == 1

    def fake_call(self, model, target, rows):
        return {"executive_summary": "机器人订单是当日主线", "market_regime": "成长主题", "source_count": len(rows)}, {"total_tokens": 123}

    monkeypatch.setattr(EssayDailyReportService, "_call_model", fake_call)
    service = EssayDailyReportService(analysis_repo=essay_service.repo)
    first = service.generate(report_date="2026-08-19", models=["deepseek-v4-flash"])
    second = service.generate(report_date="2026-08-19", models=["deepseek-v4-flash"])
    assert first["models"][0]["status"] == "completed"
    assert second["models"][0]["status"] == "unchanged"
    saved = service.list(limit=5)["items"][0]
    assert saved["report"]["executive_summary"] == "机器人订单是当日主线"


def test_daily_context_uses_full_population_and_ranked_evidence() -> None:
    rows = [{
        "topic_id": f"topic-{index}",
        "summary": f"摘要 {index}",
        "primary_category": "rumor" if index == 0 else "company_research",
        "sentiment": "bullish", "importance_score": index % 100,
        "confidence_score": 0.3 if index == 0 else 0.8,
        "novelty_score": index % 90, "information_type": "market_rumor" if index == 0 else "institution_view",
        "source_quality": "low" if index == 0 else "medium", "tags": ["机器人"], "themes": ["AI"],
        "stock_mentions": [{"name": "宇树科技"}], "evidence": [{"claim": "c", "evidence": "e"}],
        "note": {"title": f"标题 {index}", "group_name": "调研纪要"},
    } for index in range(350)]
    context = EssayDailyReportService._daily_context(rows)
    assert context["coverage"]["total_records"] == 350
    assert context["coverage"]["representative_records"] == 240
    assert context["coverage"]["rumor_records"] == 1
    assert context["full_population_aggregates"]["top_stocks"][0] == ("宇树科技", 350)


def test_daily_context_keeps_low_frequency_watchlist_evidence(monkeypatch) -> None:
    monkeypatch.setenv("ESSAY_WATCHLIST", "603306.SH:华懋科技,300476.SZ:胜宏科技")
    rows = [{
        "topic_id": f"hot-{index}", "summary": "热门主题", "primary_category": "industry_chain",
        "sentiment": "bullish", "importance_score": 90, "confidence_score": 0.9,
        "novelty_score": 90, "information_type": "institution_view", "source_quality": "medium",
        "stock_mentions": [{"ts_code": "300308.SZ", "name": "中际旭创"}],
        "note": {"title": "热门算力", "group_name": "调研纪要"},
    } for index in range(300)]
    rows.append({
        "topic_id": "watch-huamao", "summary": "华懋科技跟踪", "primary_category": "company_research",
        "sentiment": "neutral", "importance_score": 10, "confidence_score": 0.6,
        "novelty_score": 5, "information_type": "fact", "source_quality": "high",
        "stock_mentions": [{"ts_code": "603306.SH", "name": "华懋科技"}],
        "note": {"title": "华懋科技更新", "group_name": "关注股星球"},
    })
    context = EssayDailyReportService._daily_context(rows)
    topic_ids = {item["topic_id"] for item in context["representative_evidence"]}
    assert "watch-huamao" in topic_ids
    assert context["coverage"]["representative_watchlist_records"] == 1
    assert context["coverage"]["selection_strategy"].startswith("watchlist_priority")


def test_cross_model_comparison_uses_structured_theme_and_stock_directions() -> None:
    rows = [
        {"model": "model-a", "report": {
            "key_themes": [{"name": "AI算力", "direction": "bullish"}],
            "stock_focus": [{"ts_code": "300476.SZ", "name": "胜宏科技", "stance": "bullish"}],
            "consensus": ["算力景气延续"], "divergences": [],
        }},
        {"model": "model-b", "report": {
            "key_themes": [{"name": "AI算力", "direction": "bullish"}],
            "stock_focus": [{"ts_code": "300476.SZ", "name": "胜宏科技", "stance": "bearish"}],
            "consensus": ["算力的景气仍在延续"], "divergences": [],
        }},
    ]
    comparison = EssayAnalysisService._compare_reports(rows)
    assert any("AI算力" in item["text"] and item["model_count"] == 2 for item in comparison["consensus"])
    assert any("胜宏科技" in item["text"] and item["model_count"] == 2 for item in comparison["divergences"])


def test_dashboard_and_insights_share_decoded_row_snapshot(essay_service, monkeypatch) -> None:
    calls = 0
    rows = [{"topic_id": "cached-row"}]

    def fake_rows(_repo, *, cutoff):
        nonlocal calls
        calls += 1
        return rows

    _dashboard_rows_cache.clear()
    monkeypatch.setattr(EssayAnalysisRepository, "completed_for_dashboard", fake_rows)
    assert essay_service._completed_dashboard_rows(30) is rows
    assert essay_service._completed_dashboard_rows(30) is rows
    assert calls == 1
    _dashboard_rows_cache.clear()
