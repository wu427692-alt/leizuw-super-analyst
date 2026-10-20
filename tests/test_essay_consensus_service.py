# -*- coding: utf-8 -*-
"""Dedicated 20-related plus 5-single-stock expectation extraction tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os

import pytest
from sqlalchemy import event

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.essay_consensus_service import EssayConsensusAnalyzer, EssayConsensusService
from src.storage import DatabaseManager, EssayConsensusRecord, MonitoringEventRecord, ResearchNote


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
    assert queued["has_report"] is False
    assert queued["analysis_cutoff_at"] is None
    assert queued["completed_at"] is None
    assert queued["source_notes"] == []
    assert len(queued["candidate_source_notes"]) == 2
    assert consensus_service.process_next() is True
    assert consensus_service.process_next() is False

    result = consensus_service.snapshot("603306.SH", "华懋科技", limit=20)
    assert result["status"] == "completed"
    assert result["analyzed_count"] == 2
    assert result["has_report"] is True
    assert result["report_source_count"] == 2
    assert result["report_sources_complete"] is True
    assert result["analysis_cutoff_at"] == "2026-08-20T08:02:00Z"
    assert result["metric_counts"] == {"net_profit": 1}
    assert result["estimates"][0]["event_id"] is not None
    assert result["estimates"][0]["subject"] == "华懋科技"
    assert result["estimates"][0]["evidence"] == "原文预计2027年净利润约10亿元"
    assert result["estimates"][0]["proposed_at"] == "2026-08-20T08:02:00Z"
    assert result["estimates"][0]["source_kind"] == "dedicated"
    assert result["time_observations"] == ["2026年8月提出，预测期为2027年"]
    assert result["verification_conditions"][0]["window"] == "2027年报"


def _add_sampling_notes(service, samples):
    with service.db.get_session() as session:
        for topic_id, timestamp, symbols in samples:
            session.add(ResearchNote(
                topic_id=topic_id, title=topic_id, content="必须按需读取的原文" * 200,
                raw_payload="不应读取的附件载荷" * 1000,
                content_hash=topic_id, group_id="sampling", group_name="测试星球",
                created_at=timestamp, symbol_codes=symbols,
            ))
            session.add(MonitoringEventRecord(
                source_key="zsxq.essays", source_name="知识星球", source_type="mcp",
                external_id=topic_id, event_type="essay", perspective="investor",
                title=topic_id, symbol_codes="603306.SH", event_at=timestamp,
            ))
        session.commit()


def test_sampling_keeps_20_related_and_5_single_stock_notes_without_duplicates(consensus_service):
    now = datetime(2026, 8, 20, 12, 0)
    _add_sampling_notes(consensus_service, [
        (f"sample-{index}", now - timedelta(minutes=index),
         "603306.SH" if index < 7 else "603306.SH,300476.SZ")
        for index in range(30)
    ])

    selected = consensus_service._select_notes("603306.SH", limit=20)

    assert len(selected) == 25
    assert len({item["topic_id"] for item in selected}) == 25
    assert sum(item["source_kind"] == "dedicated" for item in selected) == 5
    assert sum(item["source_kind"] == "related" for item in selected) == 20
    assert {item["topic_id"] for item in selected if item["source_kind"] == "dedicated"} == {
        "sample-0", "sample-1", "sample-2", "sample-3", "sample-4",
    }
    assert all(item["content"].startswith("必须按需读取") for item in selected)


def test_snapshot_finds_sparse_dedicated_notes_across_pages_without_loading_bodies(consensus_service):
    now = datetime(2026, 9, 1, 12)
    _add_sampling_notes(consensus_service, [
        (f"many-related-{index}", now, "603306.SH,300476.SZ")
        for index in range(300)
    ] + [
        (f"old-dedicated-{index}", now - timedelta(days=1, minutes=index), "603306.SH")
        for index in range(5)
    ])
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    with consensus_service.db.get_session() as session:
        engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = consensus_service.snapshot("603306.SH", "华懋科技")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert result["source_count"] == 25
    sources = result["candidate_source_notes"]
    assert result["source_notes"] == []
    assert [item["topic_id"] for item in sources if item["source_kind"] == "related"] == [
        f"many-related-{index}" for index in range(299, 279, -1)
    ]
    assert [item["topic_id"] for item in sources if item["source_kind"] == "dedicated"] == [
        f"old-dedicated-{index}" for index in range(5)
    ]
    candidate_queries = [sql for sql in statements if "JOIN research_notes" in sql]
    assert len(candidate_queries) == 3
    assert all("LIMIT" in sql for sql in candidate_queries)
    assert not any("research_notes.content" in sql or "raw_payload" in sql for sql in statements)


def test_sampling_deduplicates_projection_and_skips_orphans_other_stocks_and_expired_notes(consensus_service, monkeypatch):
    now = datetime(2026, 9, 4, 12)
    monkeypatch.setattr("src.services.essay_consensus_service.utc_naive_now", lambda: now)
    _add_sampling_notes(consensus_service, [
        ("latest-related", now, "603306.SH,300476.SZ"),
        ("other-stock", now, "300476.SZ"),
        ("expired", now - timedelta(days=3651), "603306.SH"),
        ("non-essay", now, "603306.SH"),
    ])
    with consensus_service.db.get_session() as session:
        rows = session.query(MonitoringEventRecord).filter(
            MonitoringEventRecord.external_id.in_(("other-stock", "non-essay")),
        ).all()
        for row in rows:
            if row.external_id == "other-stock":
                row.symbol_codes = "300476.SZ"
            else:
                row.event_type = "news"
        duplicate = MonitoringEventRecord(
            source_key="ai.recording", source_name="AI录音纪要", source_type="internal",
            external_id="latest-related", event_type="essay", perspective="investor",
            title="重复投影", symbol_codes="603306.SH", event_at=now + timedelta(minutes=1),
        )
        session.add(duplicate)
        session.add(MonitoringEventRecord(
            source_key="zsxq.essays", source_name="知识星球", source_type="mcp",
            external_id="no-note", event_type="essay", perspective="investor",
            title="孤立投影", symbol_codes="603306.SH", event_at=now,
        ))
        session.commit()
        latest_event_id = duplicate.id

    selected = consensus_service._select_notes("603306.SH", limit=1)

    assert {item["topic_id"] for item in selected} == {"latest-related", "topic-1", "topic-2"}
    assert selected[0]["event_id"] == latest_event_id
    assert selected[0]["event_at"] == "2026-09-04T12:01:00Z"
    assert selected[0]["source_kind"] == "related"


def test_sampling_respects_requested_symbol(consensus_service):
    assert consensus_service._select_notes("300476.SZ", limit=20) == []


def _complete_report(service):
    service.enqueue("603306.SH", "华懋科技")
    assert service.process_next() is True
    return service.snapshot("603306.SH", "华懋科技")


def _replace_candidate_window(service):
    _add_sampling_notes(service, [
        (f"new-{index}", datetime(2026, 9, 1, 12) + timedelta(minutes=index), "603306.SH")
        for index in range(5)
    ])


def _strip_provenance(service):
    with service.db.get_session() as session:
        row = session.query(EssayConsensusRecord).one()
        result = json.loads(row.result_json)
        result.pop("_source_snapshot", None)
        row.result_json = json.dumps(result, ensure_ascii=False)
        session.commit()


def test_stale_report_keeps_its_actual_sources_cutoff_and_count_after_candidates_change(consensus_service):
    completed = _complete_report(consensus_service)
    _replace_candidate_window(consensus_service)

    result = consensus_service.snapshot("603306.SH", "华懋科技")

    assert result["status"] == "stale"
    assert result["source_count"] == 5
    assert result["pending_count"] == 5
    assert result["analyzed_count"] == result["report_source_count"] == 2
    assert result["report_sources_complete"] is True
    assert result["completed_at"] == completed["completed_at"]
    assert result["analysis_cutoff_at"] == "2026-08-20T08:02:00Z"
    assert result["candidate_cutoff_at"] == "2026-09-01T12:04:00Z"
    assert {item["topic_id"] for item in result["source_notes"]} == {"topic-1", "topic-2"}
    assert all(item["topic_id"].startswith("new-") for item in result["candidate_source_notes"])
    assert result["estimates"][0]["proposed_at"] == "2026-08-20T08:02:00Z"
    assert result["estimates"][0]["event_id"] == completed["estimates"][0]["event_id"]
    assert result["estimates"][0]["source_kind"] == "dedicated"
    assert result["estimates"][0]["unit"] == "亿元"


def test_report_metadata_is_frozen_and_uses_the_raw_note_timestamp_given_to_ai(consensus_service):
    with consensus_service.db.get_session() as session:
        projection = session.query(MonitoringEventRecord).filter_by(external_id="topic-2").one()
        projection.event_at = datetime(2026, 8, 30, 12)
        session.commit()
    completed = _complete_report(consensus_service)
    with consensus_service.db.get_session() as session:
        note = session.query(ResearchNote).filter_by(topic_id="topic-2").one()
        note.title = "之后修订的标题"
        note.created_at = datetime(2026, 9, 1, 12)
        note.symbol_codes = "603306.SH,300476.SZ"
        session.commit()

    result = consensus_service.snapshot("603306.SH", "华懋科技")

    assert completed["estimates"][0]["proposed_at"] == "2026-08-20T08:02:00Z"
    assert result["source_notes"] == completed["source_notes"]
    assert result["estimates"][0]["title"] == "华懋科技纪要 2"
    assert result["estimates"][0]["source_kind"] == "dedicated"
    assert result["analysis_cutoff_at"] == completed["analysis_cutoff_at"]
    with consensus_service.db.get_session() as session:
        stored = json.loads(session.query(EssayConsensusRecord).one().result_json)["_source_snapshot"]
    assert all("content" not in item and "raw_payload" not in item for item in stored["source_notes"])


def test_legacy_completed_report_recovers_out_of_window_sources_with_read_only_metadata(consensus_service):
    completed = _complete_report(consensus_service)
    _strip_provenance(consensus_service)
    _replace_candidate_window(consensus_service)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    with consensus_service.db.get_session() as session:
        engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = consensus_service.snapshot("603306.SH", "华懋科技")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert result["status"] == "stale"
    assert result["report_source_count"] == 2
    assert result["report_sources_complete"] is True
    assert result["source_notes"] == completed["source_notes"]
    assert result["completed_at"] == completed["completed_at"]
    assert result["analysis_cutoff_at"] == completed["analysis_cutoff_at"]
    assert result["estimates"][0]["proposed_at"] == "2026-08-20T08:02:00Z"
    assert not any("research_notes.content" in sql or "raw_payload" in sql for sql in statements)
    assert not any(sql.lstrip().upper().startswith(("UPDATE ", "INSERT ", "DELETE ")) for sql in statements)
    with consensus_service.db.get_session() as session:
        assert "_source_snapshot" not in json.loads(session.query(EssayConsensusRecord).one().result_json)


def test_retry_and_failure_preserve_legacy_report_not_the_new_job_sources_or_completion(consensus_service, monkeypatch):
    completed = _complete_report(consensus_service)
    _strip_provenance(consensus_service)
    _replace_candidate_window(consensus_service)
    queued = consensus_service.enqueue("603306.SH", "华懋科技")

    assert queued["status"] == "pending"
    assert queued["has_report"] is True
    assert queued["report_source_count"] == 2
    assert queued["analyzed_count"] == 2
    assert queued["source_count"] == 5
    assert queued["source_notes"] == completed["source_notes"]
    assert queued["completed_at"] == completed["completed_at"]
    assert queued["analysis_cutoff_at"] == completed["analysis_cutoff_at"]
    assert queued["summary"] == completed["summary"]
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        during = consensus_service.snapshot("603306.SH", "华懋科技")
        assert during["status"] == "processing"
        assert during["source_notes"] == completed["source_notes"]
        raise RuntimeError("测试上游失败")

    monkeypatch.setattr(consensus_service.analyzer, "analyze", fail)
    assert consensus_service.process_next() is True
    assert consensus_service.process_next() is False
    failed = consensus_service.snapshot("603306.SH", "华懋科技")
    assert failed["status"] == "failed"
    assert failed["summary"] == completed["summary"]
    assert failed["source_notes"] == completed["source_notes"]
    assert failed["completed_at"] == completed["completed_at"]
    assert failed["analysis_cutoff_at"] == completed["analysis_cutoff_at"]
    assert failed["analyzed_count"] == 2
    assert len(calls) == 1
    assert len(calls[0]["notes"]) == 5


@pytest.mark.parametrize("status", ["pending", "processing", "failed"])
def test_legacy_retry_with_lost_input_ids_never_claims_candidate_count_or_cutoff_as_report(consensus_service, status):
    _complete_report(consensus_service)
    _strip_provenance(consensus_service)
    _replace_candidate_window(consensus_service)
    with consensus_service.db.get_session() as session:
        row = session.query(EssayConsensusRecord).one()
        row.status = status
        row.source_topic_ids_json = json.dumps([f"new-{index}" for index in range(5)])
        row.source_count = 5
        row.completed_at = None
        session.commit()

    result = consensus_service.snapshot("603306.SH", "华懋科技")

    assert result["status"] == status
    assert result["has_report"] is True
    assert result["report_sources_complete"] is False
    assert result["report_source_count"] is None
    assert result["analyzed_count"] == 0
    assert result["analysis_cutoff_at"] is None
    assert result["completed_at"] is None
    assert result["candidate_cutoff_at"] == "2026-09-01T12:04:00Z"
    assert [item["topic_id"] for item in result["source_notes"]] == ["topic-2"]
    assert result["source_notes"][0]["event_at"] == "2026-08-20T08:02:00Z"
    assert result["estimates"][0]["proposed_at"] == "2026-08-20T08:02:00Z"


def test_report_count_reflects_actual_inputs_when_a_queued_note_is_unavailable(consensus_service, monkeypatch):
    consensus_service.enqueue("603306.SH", "华懋科技")
    with consensus_service.db.get_session() as session:
        session.delete(session.query(ResearchNote).filter_by(topic_id="topic-1").one())
        session.commit()
    captured = []

    def analyze(**kwargs):
        captured.extend(kwargs["notes"])
        return {"result": {"summary": "剩余一篇无数字预期", "estimates": []}, "usage": {}}

    monkeypatch.setattr(consensus_service.analyzer, "analyze", analyze)
    assert consensus_service.process_next() is True
    result = consensus_service.snapshot("603306.SH", "华懋科技")
    assert len(captured) == 1
    assert result["report_source_count"] == result["analyzed_count"] == 1
    assert result["report_sources_complete"] is True
    assert [item["topic_id"] for item in result["source_notes"]] == ["topic-2"]


def test_completed_report_freshness_uses_actual_inputs_even_if_queue_selection_changes(consensus_service, monkeypatch):
    consensus_service.enqueue("603306.SH", "华懋科技")
    original_analyze = consensus_service.analyzer.analyze
    calls = []

    def analyze(**kwargs):
        calls.append(kwargs)
        _replace_candidate_window(consensus_service)
        consensus_service.enqueue("603306.SH", "华懋科技")
        return original_analyze(**kwargs)

    monkeypatch.setattr(consensus_service.analyzer, "analyze", analyze)
    assert consensus_service.process_next() is True
    result = consensus_service.snapshot("603306.SH", "华懋科技")

    assert len(calls) == 1
    assert result["status"] == "stale"
    assert result["report_source_count"] == 2
    assert result["source_count"] == 5
    assert result["analysis_cutoff_at"] == "2026-08-20T08:02:00Z"
    assert result["candidate_cutoff_at"] == "2026-09-01T12:04:00Z"


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
