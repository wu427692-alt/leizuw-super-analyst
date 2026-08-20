# -*- coding: utf-8 -*-
"""Durable queue, DeepSeek normalization and essay-radar API tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import essay_radar
from src.config import Config
from src.repositories.essay_analysis_repo import EssayAnalysisRepository
from src.services.essay_analysis_service import (
    DeepSeekEssayAnalyzer,
    EssayAnalysisService,
    EssayDailyReportService,
    _dashboard_rows_cache,
)
from src.services.financial_data_service import ResearchNoteService
from src.storage import DatabaseManager


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
        "stock_mentions": [{"ts_code": "688836.SH", "name": "宇树科技", "stance": "watching", "confidence": 0.8, "rationale": "关注量产"}],
        "key_points": ["订单增长"],
        "catalysts": ["量产"],
        "risks": ["交付延期"],
    }], raw_response="{}", usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

    assert essay_service.progress(days=30)["coverage_percent"] == 100.0
    dashboard = essay_service.dashboard(days=30)
    assert dashboard["summary"]["analyzed_count"] == 1
    assert dashboard["top_tags"][0] == {"name": "订单增长", "count": 1}
    assert dashboard["top_stocks"][0]["ts_code"] == "688836.SH"
    insights = essay_service.insights(days=30, trend_days=7)
    assert len(insights["trend"]) == 7
    assert insights["coverage"]["analyzed_count"] == 1
    assert insights["source_quality"][0]["name"] == "unknown"
    assert [item["name"] for item in insights["watchlist"]] == ["华懋科技", "胜宏科技"]


def test_essay_radar_read_api(essay_service, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.essay_analysis_worker.EssayAnalysisWorker.status",
        lambda self: {"running": False},
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
    reports = client.get("/api/v1/essay-radar/daily-reports")
    assert reports.status_code == 200
    assert "models" in reports.json()


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
