# -*- coding: utf-8 -*-
"""Dedicated 20-related plus 5-single-stock expectation extraction tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import os

import pytest

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.essay_consensus_service import EssayConsensusAnalyzer, EssayConsensusService
from src.storage import DatabaseManager, ResearchNote


class FakeAnalyzer:
    model = "fake-consensus-model"

    def analyze(self, *, symbol, stock_name, notes):
        assert symbol == "603306.SH"
        assert stock_name == "华懋科技"
        assert len(notes) == 2
        return {
            "result": {
                "summary": "两篇材料中一篇提出净利润预期，一篇没有明确数字。",
                "has_explicit_expectations": True,
                "profit_outlook": "预计2027年净利润约10亿元。",
                "valuation_outlook": "信息不足",
                "estimates": [{
                    "topic_id": "topic-2", "subject": "华懋科技", "subject_relation": "target_stock",
                    "metric": "net_profit", "period": "2027年",
                    "value_text": "净利润约10亿元", "value_low": 10, "value_high": 10,
                    "unit": "亿元", "direction": "up", "evidence": "原文预计2027年净利润约10亿元",
                    "confidence": 0.82,
                }],
                "consensus_points": [], "conflicts": [],
                "time_observations": ["2026年8月提出，预测期为2027年"],
                "verification_conditions": [{
                    "condition": "2027年净利润达到10亿元", "window": "2027年报",
                    "impact": "达到则支持原预测", "expiry_at": "2028年4月",
                }],
                "caveats": ["未经公司公告确认"],
            },
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }


@pytest.fixture()
def consensus_service(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "consensus.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    now = datetime(2026, 8, 20, 8, 0)
    with db.get_session() as session:
        for index in (1, 2):
            content = f"华懋科技小作文 {index}" + ("，预计2027年净利润约10亿元" if index == 2 else "")
            session.add(ResearchNote(
                topic_id=f"topic-{index}", group_id="group-1", group_name="测试星球",
                title=f"华懋科技纪要 {index}", content=content, content_hash=hashlib.sha256(content.encode()).hexdigest(),
                created_at=now + timedelta(minutes=index), symbol_codes="603306.SH",
            ))
        session.commit()
    repo = InvestmentMonitorRepository(db)
    repo.ensure_sources([{
        "source_key": "zsxq.essays", "name": "知识星球", "adapter_type": "mcp",
        "provider": "zsxq", "category": "essay", "poll_interval_seconds": 30, "config": {},
    }])
    for index in (1, 2):
        repo.upsert_events([{
            "source_key": "zsxq.essays", "source_name": "知识星球", "source_type": "mcp",
            "external_id": f"topic-{index}", "event_type": "essay", "perspective": "investor",
            "title": f"华懋科技纪要 {index}", "summary": "", "symbols": ["603306.SH"],
            "sentiment": "neutral", "importance_score": 50, "confidence_score": 0.5,
            "tags": [], "actors": [], "metrics": {}, "raw_payload": {},
            "event_at": now + timedelta(minutes=index),
        }])
    service = EssayConsensusService(db=db, analyzer=FakeAnalyzer())
    try:
        yield service
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None: os.environ.pop("DATABASE_PATH", None)
        else: os.environ["DATABASE_PATH"] = previous


def test_latest_matched_essays_are_queued_processed_and_mapped_to_original_event(consensus_service):
    queued = consensus_service.enqueue("603306.SH", "华懋科技", limit=20)
    assert queued["status"] == "pending"
    assert queued["source_count"] == 2
    assert queued["related_source_count"] == 0
    assert queued["dedicated_source_count"] == 2
    assert consensus_service.process_next() is True
    assert consensus_service.process_next() is False

    result = consensus_service.snapshot("603306.SH", "华懋科技", limit=20)
    assert result["status"] == "completed"
    assert result["analyzed_count"] == 2
    assert result["metric_counts"] == {"net_profit": 1}
    assert result["estimates"][0]["event_id"] is not None
    assert result["estimates"][0]["subject"] == "华懋科技"
    assert result["estimates"][0]["evidence"] == "原文预计2027年净利润约10亿元"
    assert result["estimates"][0]["proposed_at"] == "2026-08-20T08:02:00Z"
    assert result["estimates"][0]["source_kind"] == "dedicated"
    assert result["time_observations"] == ["2026年8月提出，预测期为2027年"]
    assert result["verification_conditions"][0]["window"] == "2027年报"


def test_sampling_keeps_20_related_and_5_single_stock_notes_without_duplicates(consensus_service, monkeypatch):
    now = datetime(2026, 8, 20, 12, 0)
    events = [{
        "id": index + 1, "external_id": f"sample-{index}", "event_type": "essay",
        "event_at": (now - timedelta(minutes=index)).isoformat() + "Z",
    } for index in range(30)]
    notes = [{
        "topic_id": f"sample-{index}", "title": f"样本 {index}", "content": "原文",
        "created_at": (now - timedelta(minutes=index)).isoformat() + "Z",
        "author_name": "研究员", "group_name": "测试星球",
        "symbols": ["603306.SH"] if index < 7 else ["603306.SH", "300476.SZ"],
    } for index in range(30)]
    monkeypatch.setattr(consensus_service.monitor_repo, "recent_symbol_events", lambda **_kwargs: events)
    monkeypatch.setattr(consensus_service, "_notes_by_ids", lambda _topic_ids: notes)

    selected = consensus_service._select_notes("603306.SH", limit=20)

    assert len(selected) == 25
    assert len({item["topic_id"] for item in selected}) == 25
    assert sum(item["source_kind"] == "dedicated" for item in selected) == 5
    assert sum(item["source_kind"] == "related" for item in selected) == 20
    assert {item["topic_id"] for item in selected if item["source_kind"] == "dedicated"} == {
        "sample-0", "sample-1", "sample-2", "sample-3", "sample-4",
    }


def test_analyzer_normalization_rejects_estimates_without_a_selected_source():
    normalized = EssayConsensusAnalyzer._normalize({
        "summary": "测试",
        "estimates": [
            {"topic_id": "not-selected", "metric": "market_cap", "value_text": "100亿", "evidence": "传闻100亿"},
            {"topic_id": "topic-1", "metric": "market_cap", "value_text": "目标市值100亿", "evidence": "原文目标市值100亿", "confidence": 1.5},
        ],
    }, allowed_topic_ids={"topic-1"})
    assert len(normalized["estimates"]) == 1
    assert normalized["estimates"][0]["confidence"] == 1.0
