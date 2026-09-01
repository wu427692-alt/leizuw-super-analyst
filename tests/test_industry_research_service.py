from datetime import date, timedelta
import json
from threading import Event, Thread, enumerate as enumerate_threads
import time
from unittest.mock import patch

import pytest
from sqlalchemy import select

import src.services.industry_research_service as industry_research_service
from src.services.essay_analysis_service import DeepSeekEssayAnalyzer
from src.services.industry_research_service import (
    IndustryResearchService,
    IndustryResearchTaskManager,
    _IndustryResearchLeaseHeartbeat,
)
from src.services.industry_research_sources import IndustryResearchSourceCollector
from src.services.research_report_library_service import ResearchReportLibraryService
from src.storage import (
    DatabaseManager,
    IndustryResearchProjectRecord,
    MonitoringEventRecord,
    ResearchNote,
    ResearchReportRecord,
    utc_naive_now,
)


def _db(tmp_path) -> DatabaseManager:
    IndustryResearchTaskManager.reset_instance()
    DatabaseManager.reset_instance()
    ResearchReportLibraryService.reset_instance()
    return DatabaseManager(db_url=f"sqlite:///{tmp_path / 'industry-research.db'}")


def teardown_function() -> None:
    IndustryResearchTaskManager.reset_instance()
    ResearchReportLibraryService.reset_instance()
    DatabaseManager.reset_instance()


def test_blueprint_deduplicates_zsxq_event_mirror_and_keeps_evidence_links(tmp_path):
    db = _db(tmp_path)
    now = utc_naive_now()
    with db.get_session() as session:
        session.add(ResearchReportRecord(
            report_key="report-1", trade_date=utc_naive_now().date(), title="光模块产业链深度研究",
            abstract="800G 光模块需求、硅光路线与应用场景", report_type="行业研报",
            broker="测试证券", company_name="中际旭创", ts_code="300308.SZ",
            industry="通信设备", pdf_url="https://example.com/report.pdf",
        ))
        session.add(ResearchNote(
            topic_id="note-1", group_id="g1", group_name="调研纪要", title="800G 光模块专家交流",
            content="讨论 CPO、LPO 与上游光芯片瓶颈。", content_hash="hash-1", created_at=now,
        ))
        session.add_all([
            MonitoringEventRecord(
                source_key="news.cls", source_name="财联社新闻", source_type="news", external_id="news-1",
                event_type="news", perspective="investor", title="CPO 产业链应用进展", summary="数据中心场景",
                event_at=now, symbol_codes="300308.SZ", url="https://example.com/news",
            ),
            MonitoringEventRecord(
                source_key="zsxq.essays", source_name="知识星球", source_type="mcp", external_id="note-1",
                event_type="essay", perspective="institution", title="800G 光模块专家交流", summary="镜像事件",
                event_at=now,
            ),
        ])
        session.commit()

    result = IndustryResearchService(db).blueprint("光模块", lookback_days=730)
    snapshot = result["snapshot"]

    assert snapshot["totals"] | {"direct_sources": 0, "audio_candidates": 0, "images": 0} == snapshot["totals"]
    assert {key: snapshot["totals"][key] for key in ("evidence", "reports", "notes", "events", "media_files")} == {
        "evidence": 3, "reports": 1, "notes": 1, "events": 1, "media_files": 0,
    }
    assert {item["evidence_id"] for item in snapshot["evidence"]} == {"report:1", "note:note-1", "event:1"}
    assert next(item for item in snapshot["coverage"] if item["key"] == "news_comments")["count"] == 1
    assert snapshot["companies"][0]["name"] == "中际旭创"
    assert snapshot["evidence"][0]["original_available"] is True


def test_audio_candidates_deduplicate_physical_file_and_prefer_original_topic(tmp_path):
    db = _db(tmp_path)
    now = utc_naive_now()
    duplicate_file = {
        "name": "华懋科技20260826.mp3",
        "file_id": "814885484555442",
        "asset_kind": "audio",
    }
    with db.get_session() as session:
        session.add_all([
            ResearchNote(
                topic_id="55521151455141244", group_id="g1", group_name="调研纪要",
                # The real parent post is about another company; only the
                # attachment name identifies the relevant recording.
                title="华勤技术20260827.mp3", content="华勤技术原始会议录音",
                topic_type="talk", files_json=json.dumps([duplicate_file], ensure_ascii=False),
                images_json="[]", content_hash="original-audio", created_at=now,
            ),
            ResearchNote(
                topic_id="audio-memo-generation-1", group_id="ai-audio-memo", group_name="AI录音纪要",
                title="华懋科技2026年半年报业绩交流会录音纪要", content="AI 转写衍生帖",
                author_id="deepseek-audio-memo", topic_type="audio_memo",
                files_json=json.dumps([{**duplicate_file, "source_topic_id": "55521151455141244"}], ensure_ascii=False),
                images_json="[]", content_hash="derived-audio-memo", created_at=now,
            ),
            ResearchNote(
                topic_id="audio-memo-generation-2", group_id="ai-audio-memo", group_name="AI录音纪要",
                title="华懋科技二次录音纪要", content="二次转写污染链",
                author_id="deepseek-audio-memo", topic_type="audio_memo",
                files_json=json.dumps([{
                    **duplicate_file, "source_topic_id": "audio-memo-generation-1",
                }], ensure_ascii=False),
                images_json="[]", content_hash="second-generation-memo", created_at=now,
            ),
            ResearchNote(
                topic_id="audio-memo-derived-only", group_id="ai-audio-memo", group_name="AI录音纪要",
                title="华懋科技历史录音纪要", content="原帖已不在本地库",
                author_id="deepseek-audio-memo", topic_type="audio_memo",
                files_json=json.dumps([{
                    "name": "华懋科技历史录音.mp3", "file_id": "derived-only-file", "asset_kind": "audio",
                    "source_topic_id": "missing-original-topic",
                }], ensure_ascii=False),
                images_json="[]", content_hash="derived-only-audio-memo", created_at=now,
            ),
        ])
        session.commit()

    service = IndustryResearchService(db)
    subject = {
        "research_type": "company", "name": "华懋科技", "symbol": "603306.SH", "resolved": True,
    }
    with patch.object(service, "_concept_context", return_value={"status": "missing", "items": []}), patch.object(
        ResearchReportLibraryService, "status", return_value={"status": "ready", "total": 0, "pdf_count": 0},
    ):
        snapshot = service.collect_evidence(
            "华懋科技",
            terms=["华懋科技", "603306.SH", "华懋"],
            lookback_days=730,
            research_type="company",
            subject=subject,
            direct_sources={"subject": subject, "evidence": [], "source_status": []},
        )

    assert len(snapshot["audio_candidates"]) == 1
    selected = snapshot["audio_candidates"][0]
    assert {
        key: selected[key] for key in ("topic_id", "file_id", "filename", "note_title")
    } == {
        "topic_id": "55521151455141244",
        "file_id": "814885484555442",
        "filename": "华懋科技20260826.mp3",
        "note_title": "华勤技术20260827.mp3",
    }
    assert snapshot["totals"]["audio_candidates"] == 1
    assert snapshot["audio_pipeline"]["candidate_count"] == 1
    audio_coverage = next(item for item in snapshot["coverage"] if item["key"] == "audio_transcripts")
    assert audio_coverage["candidates"] == 1
    evidence_ids = {item["evidence_id"] for item in snapshot["evidence"]}
    assert "note:audio-memo-generation-1" in evidence_ids
    assert "note:audio-memo-generation-2" in evidence_ids
    assert "note:audio-memo-derived-only" in evidence_ids


def test_audio_candidate_physical_dedupe_prefers_original_when_memo_is_seen_first():
    result = IndustryResearchService._dedupe_audio_candidates([
        {
            "topic_id": "audio-memo-generation-1", "file_id": "same-file",
            "filename": "华懋科技.mp3", "note_title": "AI 录音纪要",
        },
        {
            "topic_id": "55521151455141244", "file_id": "same-file",
            "filename": "华懋科技.mp3", "note_title": "原始帖子",
        },
    ])

    assert len(result) == 1
    assert result[0]["topic_id"] == "55521151455141244"


def test_project_listing_and_detail_are_owner_scoped(tmp_path):
    db = _db(tmp_path)
    with db.get_session() as session:
        session.add_all([
            IndustryResearchProjectRecord(
                project_id="mine", owner_id="user:1", topic="光模块", objective="理解产业链",
                query_json='{"terms":["光模块"]}', evidence_snapshot_json='{"totals":{"evidence":7}}',
                report_json='{"one_sentence":"摘要","long_form_char_count":21000,"chapters":[{"body_markdown":"很长的正文"}]}',
            ),
            IndustryResearchProjectRecord(
                project_id="other", owner_id="user:2", topic="机器人", objective="理解产业链",
            ),
        ])
        session.commit()

    service = IndustryResearchService(db)
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:1"):
        listed = service.list_projects()
        mine = service.get_project("mine")
        hidden = service.get_project("other")

    assert listed["total"] == 1
    assert listed["items"][0]["project_id"] == "mine"
    assert listed["items"][0]["report"] == {"one_sentence": "摘要", "long_form_char_count": 21000}
    assert mine is not None and mine["snapshot"]["totals"]["evidence"] == 7
    assert mine["report"]["chapters"][0]["body_markdown"] == "很长的正文"
    assert hidden is None


def test_evidence_only_report_is_explicit_about_unknowns(tmp_path):
    db = _db(tmp_path)
    report = IndustryResearchService(db)._evidence_only_report(
        "光模块", {"query_terms": ["光模块"], "totals": {"evidence": 12}}, "AI 暂不可用",
    )
    assert "12" in report["executive_summary"]
    assert report["leaders"] == []
    assert report["industry_boundary"]["definition"] == "待验证"


def test_unconfigured_ai_returns_limited_evidence_workbench(tmp_path):
    db = _db(tmp_path)
    analyzer = type("UnavailableAnalyzer", (), {
        "configured": False, "model": "openai/kimi-for-coding",
        "provider": "kimi", "channel": "kimi_code",
    })()
    snapshot = {
        "query_terms": ["光模块"], "totals": {"evidence": 3},
        "evidence": [], "data_quality": {"warnings": ["研报正文待补"]},
    }
    with patch.object(IndustryResearchService, "_research_analyzer", return_value=analyzer):
        report = IndustryResearchService(db).analyze_snapshot("光模块", "深度调研", snapshot)

    assert report["quality_assurance"]["status"] == "limited"
    assert report["generation"]["status"] == "limited"
    assert report["generation"]["actual_chars"] == 0


def test_long_form_assembly_counts_narrative_and_keeps_evidence_appendix(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    chapters = [{
        "chapter_id": "scope", "title": "研究边界", "body_markdown": "事实与推断 [event:7]",
        "summary": "摘要", "evidence_ids": ["event:7"], "open_questions": [], "char_count": 16,
    }]
    markdown = service._assemble_long_form_report("光模块", {"one_sentence": "一句话"}, chapters)
    appendix = service._build_evidence_appendix({"evidence": [{
        "evidence_id": "event:7", "title": "公告事实", "source": "巨潮公告", "date": "2026-08-25",
        "evidence_level": "factual", "summary": "可回到原文核验",
    }]})

    assert "第1章 研究边界" in markdown
    assert service._count_report_chars(markdown) > 10
    assert "[event:7]" in appendix
    assert "收录不等于认可其结论" in appendix


def test_evidence_appendix_never_reintroduces_raw_audio_or_note_claims(tmp_path):
    _db(tmp_path)
    appendix = IndustryResearchService._build_evidence_appendix({"evidence": [{
        "evidence_id": "audio:SAFE", "kind": "audio_transcript",
        "title": "RAW_TITLE 客户独供 58%", "model_title": "录音核验索引",
        "source": "录音转写", "date": "2026-08-31", "evidence_level": "ai_transcript",
        "summary": "RAW_POISON 归母净利润同比110.32%，拟购58%股权。",
        "model_summary": "【录音假设投影】具体断言均未获一级证据确认，仅保留核验问题。",
    }]})

    assert "录音核验索引" in appendix
    assert "仅保留核验问题" in appendix
    for leaked in ("RAW_TITLE", "RAW_POISON", "110.32%", "58%", "客户独供"):
        assert leaked not in appendix


def test_evidence_appendix_never_falls_back_to_raw_unverified_text(tmp_path):
    _db(tmp_path)
    appendix = IndustryResearchService._build_evidence_appendix({"evidence": [{
        "evidence_id": "audio:NO-PROJECTION", "kind": "audio_transcript",
        "title": "客户独供58%", "source": "录音转写", "date": "2026-08-31",
        "evidence_level": "ai_transcript",
        "summary": "RAW_POISON归母净利润同比110.32%，拟购58%股权。",
    }]})

    assert "安全投影尚未生成" in appendix
    for leaked in ("RAW_POISON", "110.32%", "58%", "客户独供"):
        assert leaked not in appendix


def test_report_visuals_are_unit_consistent_and_portable_in_markdown(tmp_path):
    _db(tmp_path)
    snapshot = {
        "collected_at": "2026-08-31T08:00:00Z",
        "coverage": [
            {"name": "券商研报", "count": 12},
            {"name": "机构段子", "count": 30},
        ],
        "timeline": [{"month": "2026-07", "count": 10}, {"month": "2026-08", "count": 32}],
        "evidence": [
            {"evidence_id": "report:1", "evidence_level": "reported", "date": "2026-08-30"},
            {"evidence_id": "event:2", "evidence_level": "factual", "date": "2025-06-01"},
        ],
        "data_quality": {"dimensions": {"completeness": 88, "timeliness": 72}},
        "companies": [
            {"name": "公司甲", "evidence_count": 9},
            {"name": "公司乙", "evidence_count": 6},
        ],
        "concept_context": {
            "items": [
                {"canonical_name": "光通信", "heat_score": 90, "constituent_count": 45},
                {"canonical_name": "CPO", "heat_score": 72, "constituent_count": 18},
            ],
            "constituents": [
                {"name": "公司甲", "weight_score": 95, "beta": 1.2, "alpha_annualized": 8.0},
                {"name": "公司乙", "weight_score": 82, "beta": .9, "alpha_annualized": 3.0},
                {"name": "公司丙", "weight_score": 70, "beta": 1.4, "alpha_annualized": -2.0},
                {"name": "公司丁", "weight_score": 66, "beta": .7, "alpha_annualized": 1.0},
            ],
        },
        "financial_series": [
            {
                "period": f"202{year}1231", "revenue": 100 + year * 20,
                "net_profit": 10 + year * 2, "operating_cashflow": 9 + year * 2,
                "revenue_yoy": 10 + year, "net_profit_yoy": 12 + year,
                "roe": 8 + year, "gross_margin": 25 + year,
            }
            for year in range(3, -1, -1)
        ],
        "market_series": [
            {"date": f"2026-08-{day:02d}", "close": 100 + day, "volume": 1000 + day * 20}
            for day in range(1, 13)
        ],
        "valuation_series": [
            {
                "date": f"202608{day:02d}", "pe_ttm": 20 + day / 10, "pb": 3 + day / 100,
                "ps_ttm": 5 + day / 100, "chip_cost": 98 + day / 10,
                "winner_rate": 45 + day, "turnover_rate": 2 + day / 10,
            }
            for day in range(12, 0, -1)
        ],
        "industry_peer_matrix": {
            "status": "covered", "company_count": 3, "common_period": "20260630",
            "companies": [
                {
                    "name": f"同业{index}", "symbol": f"60000{index}.SH",
                    "common_period_fact": {
                        "period": "20260630", "revenue": 1000 + index * 100,
                        "net_profit": 100 + index * 10, "operating_cashflow": 90 + index * 8,
                        "roe": 10 + index, "gross_margin": 25 + index,
                    },
                }
                for index in range(3)
            ],
        },
    }

    figures = IndustryResearchService._visualizations(snapshot)
    by_id = {item["id"]: item for item in figures}

    assert "concept_structure" not in by_id
    assert by_id["concept_heat"]["y_keys"] == ["heat"]
    assert by_id["concept_constituent_scale"]["y_keys"] == ["constituents"]
    assert by_id["industry_beta_alpha"]["type"] == "scatter"
    assert by_id["industry_peer_revenue"]["y_keys"] == ["revenue"]
    assert by_id["industry_peer_profit_cash"]["unit"] == "元"
    assert by_id["industry_peer_profitability"]["unit"] == "%"
    assert by_id["valuation_pe"]["y_keys"] == ["pe_ttm"]
    assert by_id["chip_cost"]["y_keys"] == ["chip_cost"]
    assert all(item.get("analytical_question") and item.get("insight") and item.get("unit") for item in figures)

    from src.services.industry_research_visualization_service import IndustryResearchVisualizationService

    appendix = IndustryResearchVisualizationService.markdown_appendix(figures)
    assert "图表数据、口径与阅读说明" in appendix
    assert "分析问题" in appendix
    assert "题材 Beta 与个股 Alpha 归因" in appendix
    assert "完整数据保存在证据 JSON" not in appendix or "数据抽样" in appendix


def test_queued_project_does_not_expose_empty_snapshot_object(tmp_path):
    _db(tmp_path)
    row = IndustryResearchProjectRecord(project_id="queued", topic="光模块", objective="长篇研究")

    serialized = IndustryResearchService._serialize_project(row, include_snapshot=True)

    assert serialized["snapshot"] is None


def test_task_manager_does_not_requeue_fresh_inflight_project_on_start(tmp_path):
    db = _db(tmp_path)
    now = utc_naive_now()
    with db.get_session() as session:
        session.add(IndustryResearchProjectRecord(
            project_id="inflight", topic="光模块", objective="深度研究",
            status="analyzing", progress=70, stage="validation",
            message="Kimi 正在撰写", started_at=now, updated_at=now,
        ))
        session.commit()

    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    manager.start()
    try:
        with db.get_session() as session:
            row = session.execute(
                select(IndustryResearchProjectRecord).where(IndustryResearchProjectRecord.project_id == "inflight")
            ).scalar_one()
            assert row.status == "analyzing"
            assert row.progress == 70
    finally:
        manager.stop()


def test_lease_heartbeat_keeps_long_collection_out_of_stale_recovery(tmp_path):
    db = _db(tmp_path)
    entered = Event()
    release = Event()
    lease_clock = {"now": utc_naive_now()}
    project_id = "long-collection"
    with db.get_session() as session:
        session.add(IndustryResearchProjectRecord(
            project_id=project_id,
            topic="华懋科技",
            objective="长步骤租约测试",
            status="queued",
            query_json=json.dumps({"terms": ["华懋科技"]}, ensure_ascii=False),
        ))
        session.commit()

    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    recovery_manager = IndustryResearchTaskManager(db=db, worker_count=1)
    direct = {
        "subject": {"research_type": "company", "name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [], "financial_series": [], "market_series": [], "media_gallery": [], "source_status": [],
    }
    snapshot = {"source_hash": "lease-test", "evidence": [], "coverage": [], "data_quality": {}}

    def slow_collect(*_args, **_kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return direct

    worker = Thread(target=manager._execute, args=(project_id,), name="lease-test-worker")
    try:
        with (
            patch(
                "src.services.industry_research_service.utc_naive_now",
                new=lambda: lease_clock["now"],
            ),
            patch.object(manager, "_lease_heartbeat_interval_seconds", return_value=0.02),
            patch.object(recovery_manager, "_lease_seconds", return_value=0.08),
            patch.object(ResearchReportLibraryService, "ensure_background_sync", return_value={"status": "ready"}),
            patch("src.services.industry_research_service.ZsxqMcpSyncWorker.get_instance") as sync_worker,
            patch.object(IndustryResearchSourceCollector, "collect", side_effect=slow_collect),
            patch.object(IndustryResearchService, "collect_evidence", return_value=snapshot),
            patch.object(IndustryResearchService, "transcribe_relevant_audio", return_value=snapshot),
            patch.object(IndustryResearchService, "analyze_snapshot", return_value={
                "one_sentence": "租约测试完成",
                "quality_assurance": {"status": "ready", "score": 100},
                "generation": {"status": "completed"},
            }),
        ):
            sync_worker.return_value.sync_now.return_value = {"totals": {}}
            worker.start()
            assert entered.wait(timeout=2)

            # Advance the lease clock beyond the synthetic expiry and wait for
            # the real heartbeat thread to publish that logical time.  Keeping
            # the clock fixed makes the recovery assertion independent of a
            # stop-the-world GC pause or a temporarily starved CI thread.
            lease_clock["now"] += timedelta(seconds=1)
            heartbeat_deadline = time.monotonic() + 2
            heartbeat_updated_at = None
            while time.monotonic() < heartbeat_deadline:
                with db.get_session() as session:
                    active = session.execute(select(IndustryResearchProjectRecord).where(
                        IndustryResearchProjectRecord.project_id == project_id,
                    )).scalar_one()
                    if active.updated_at == lease_clock["now"]:
                        heartbeat_updated_at = active.updated_at
                        break
                time.sleep(0.01)
            assert heartbeat_updated_at == lease_clock["now"]

            with db.get_session() as session:
                active = session.execute(select(IndustryResearchProjectRecord).where(
                    IndustryResearchProjectRecord.project_id == project_id,
                )).scalar_one()
                assert active.status == "collecting"
                assert active.progress == 10
                assert active.message == "正在唤醒知识星球增量同步与两年研报库"
                assert (lease_clock["now"] - active.started_at).total_seconds() > 0.08

            recovery_manager._last_stale_recovery = 0.0
            recovery_manager._recover_stale_projects()

            with db.get_session() as session:
                active = session.execute(select(IndustryResearchProjectRecord).where(
                    IndustryResearchProjectRecord.project_id == project_id,
                )).scalar_one()
                assert active.status == "collecting"
                assert active.progress == 10
                assert active.message == "正在唤醒知识星球增量同步与两年研报库"
                assert active.updated_at >= heartbeat_updated_at
            assert recovery_manager._queue.empty()
            release.set()
            worker.join(timeout=5)
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not any(thread.name == f"industry-research-lease-{project_id[:12]}" for thread in enumerate_threads())
    with db.get_session() as session:
        completed = session.execute(select(IndustryResearchProjectRecord).where(
            IndustryResearchProjectRecord.project_id == project_id,
        )).scalar_one()
        assert completed.status == "completed"


def test_old_claim_heartbeat_cannot_touch_new_claim(tmp_path):
    db = _db(tmp_path)
    project_id = "reclaimed-project"
    old_started_at = utc_naive_now() - timedelta(minutes=20)
    new_started_at = utc_naive_now()
    new_updated_at = new_started_at + timedelta(seconds=1)
    with db.get_session() as session:
        session.add(IndustryResearchProjectRecord(
            project_id=project_id,
            topic="华懋科技",
            objective="重领任务测试",
            status="collecting",
            progress=37,
            stage="chain",
            message="新执行正在解析公告",
            started_at=new_started_at,
            updated_at=new_updated_at,
        ))
        session.commit()

    old_heartbeat = _IndustryResearchLeaseHeartbeat(
        db,
        project_id,
        old_started_at,
        interval_seconds=60,
    )

    assert old_heartbeat._touch_once() is False
    with db.get_session() as session:
        current = session.execute(select(IndustryResearchProjectRecord).where(
            IndustryResearchProjectRecord.project_id == project_id,
        )).scalar_one()
        assert current.started_at == new_started_at
        assert current.updated_at == new_updated_at
        assert current.progress == 37
        assert current.stage == "chain"
        assert current.message == "新执行正在解析公告"


def test_model_evidence_selection_reserves_each_source_kind(tmp_path):
    _db(tmp_path)
    noisy_feed = [
        {"evidence_id": f"event:{index}", "kind": "news_comments", "title": f"评论 {index}"}
        for index in range(80)
    ]
    filings = [
        {"evidence_id": "filing:A1", "kind": "filing_text", "title": "年度报告正文"},
        {"evidence_id": "financial:1", "kind": "financial_statement", "title": "财务事实"},
        {"evidence_id": "audio:T1", "kind": "audio_transcript", "title": "录音转写"},
    ]

    selected = IndustryResearchService._select_model_evidence([*noisy_feed, *filings], limit=20)
    selected_ids = {item["evidence_id"] for item in selected}

    assert len(selected) == 20
    assert {"filing:A1", "financial:1", "audio:T1"}.issubset(selected_ids)


def test_long_research_uses_dedicated_timeout_and_retry_budget(monkeypatch, tmp_path):
    _db(tmp_path)
    monkeypatch.delenv("INDUSTRY_RESEARCH_LLM_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("INDUSTRY_RESEARCH_LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("ESSAY_ANALYSIS_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("ESSAY_ANALYSIS_MAX_RETRIES", raising=False)
    monkeypatch.setenv("INDUSTRY_RESEARCH_REQUIRE_KIMI", "false")

    ordinary_analyzer = DeepSeekEssayAnalyzer(call_type="essay_analysis")
    analyzer = IndustryResearchService._research_analyzer("industry_research_synthesis")

    assert ordinary_analyzer.timeout_seconds == 120
    assert ordinary_analyzer.max_retries == 3
    assert analyzer.timeout_seconds == 300
    assert analyzer.max_retries == 2

    monkeypatch.setenv("INDUSTRY_RESEARCH_LLM_TIMEOUT_SEC", "420")
    monkeypatch.setenv("INDUSTRY_RESEARCH_LLM_MAX_RETRIES", "4")
    configured = IndustryResearchService._research_analyzer("industry_research_chapter")

    assert configured.timeout_seconds == 420
    assert configured.max_retries == 4


def test_model_transport_context_is_bounded_deterministic_and_keeps_core_documents(tmp_path):
    _db(tmp_path)
    evidence = [
        {
            "evidence_id": f"event:{index}", "kind": "news_comments",
            "title": f"高频信息 {index}", "summary": "消息" * 2000,
        }
        for index in range(180)
    ] + [
        {"evidence_id": "filing:A1", "kind": "filing_text", "summary": "年报" * 4000},
        {"evidence_id": "broker:R1", "kind": "broker_report_text", "summary": "研报" * 4000},
        {"evidence_id": "audio:T1", "kind": "audio_transcript", "summary": "录音" * 4000},
        {"evidence_id": "web:W1", "kind": "web_fulltext", "summary": "网页" * 4000},
    ]

    first = IndustryResearchService._compact_model_evidence(evidence)
    second = IndustryResearchService._compact_model_evidence(evidence)
    selected_ids = {item["evidence_id"] for item in first}

    assert first == second
    assert len(first) == 96
    assert {"filing:A1", "broker:R1", "audio:T1", "web:W1"}.issubset(selected_ids)
    assert len(next(item for item in first if item["evidence_id"] == "filing:A1")["summary"]) == 3200
    assert len(next(item for item in first if item["evidence_id"] == "web:W1")["summary"]) == 2200

    rows = [{"date": index, "value": index} for index in range(520)]
    sampled = IndustryResearchService._downsample_model_rows(rows, 72)
    assert len(sampled) == 72
    assert sampled[0] == rows[0]
    assert sampled[-1] == rows[-1]
    assert sampled == IndustryResearchService._downsample_model_rows(rows, 72)


def test_chapter_context_limits_series_and_reserves_each_core_source(tmp_path):
    _db(tmp_path)
    snapshot = {
        "financial_series": [{"period": index} for index in range(30)],
        "market_series": [{"date": index} for index in range(520)],
        "valuation_series": [{"date": index} for index in range(520)],
        "fact_ledger": [{"fact_id": index} for index in range(400)],
        "ownership_governance": [{"id": index} for index in range(50)],
        "capital_market_activity": [{"id": index} for index in range(80)],
    }
    context = IndustryResearchService._bounded_structured_model_context(snapshot, phase="chapter")
    evidence = [
        {"evidence_id": f"event:{index}", "kind": "news_comments", "title": "财务消息"}
        for index in range(80)
    ] + [
        {"evidence_id": "filing:A1", "kind": "filing_text", "title": "年报正文"},
        {"evidence_id": "broker:R1", "kind": "broker_report_text", "title": "研报正文"},
        {"evidence_id": "audio:T1", "kind": "audio_transcript", "title": "录音逐字稿"},
        {"evidence_id": "web:W1", "kind": "web_fulltext", "title": "官网正文"},
    ]
    selected = IndustryResearchService._select_chapter_evidence(
        evidence, {"keywords": ["财务"]}, limit=12,
    )

    assert len(context["financial_series"]) == 16
    assert len(context["market_series"]) == 48
    assert len(context["valuation_series"]) == 36
    assert len(context["fact_ledger"]) == 64
    assert len(context["ownership_governance"]) == 16
    assert len(context["capital_market_activity"]) == 20
    assert {"filing:A1", "broker:R1", "audio:T1", "web:W1"}.issubset(
        {item["evidence_id"] for item in selected}
    )


def test_synthesis_request_uses_bounded_context_and_reports_honest_wait(monkeypatch, tmp_path):
    db = _db(tmp_path)

    class CapturingAnalyzer:
        configured = True
        model = "kimi-for-coding"
        provider = "kimi"
        channel = "kimi_code"

        def __init__(self):
            self.request = None

        def _post_with_retry(self, request):
            self.request = request
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    analyzer = CapturingAnalyzer()
    monkeypatch.delenv("INDUSTRY_RESEARCH_SYNTHESIS_MAX_TOKENS", raising=False)
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "totals": {}, "evidence": [
            {"evidence_id": f"event:{index}", "kind": "news_comments", "summary": "消息" * 1000}
            for index in range(150)
        ],
        "market_series": [{"date": index, "close": index} for index in range(520)],
        "valuation_series": [{"date": index, "pe_ttm": index} for index in range(520)],
        "financial_series": [{"period": index} for index in range(16)],
        "fact_ledger": [{"fact_id": index} for index in range(300)],
        "ownership_governance": [], "capital_market_activity": [],
        "data_quality": {}, "coverage": [], "source_status": [], "source_plan": [],
    }
    progress = []
    service = IndustryResearchService(db)
    with (
        patch.object(IndustryResearchService, "_research_analyzer", return_value=analyzer),
        patch.object(service, "_generate_long_form_chapters", return_value=([], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})),
        patch.object(service, "_run_editorial_review", return_value=({"status": "completed", "release_recommendation": "limited"}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})),
        patch.object(service, "_verify_report_quality", return_value={"status": "limited", "score": 0}),
    ):
        service.analyze_snapshot(
            "华懋科技", "公司深度研究", snapshot,
            progress_callback=lambda value, message: progress.append((value, message)),
        )

    assert analyzer.request["max_tokens"] == 6000
    payload = json.loads(analyzer.request["messages"][1]["content"])
    assert len(payload["evidence"]) == 96
    assert len(payload["market_series"]) == 72
    assert len(payload["valuation_series"]) == 48
    assert len(payload["fact_ledger"]) == 96
    assert any(value == 62 and "可能需要数分钟" in message for value, message in progress)


def test_evidence_hash_is_reproducible_and_detects_content_revision(tmp_path):
    _db(tmp_path)
    original = [{
        "evidence_id": "filing:A1", "kind": "filing_text", "source": "巨潮",
        "date": "2026-08-20", "title": "半年报", "summary": "收入增长 20%",
    }]
    copied = [dict(original[0])]
    revised = [{**original[0], "summary": "收入增长 25%（修订）"}]

    assert IndustryResearchService._evidence_hash(original) == IndustryResearchService._evidence_hash(copied)
    assert IndustryResearchService._evidence_hash(original) != IndustryResearchService._evidence_hash(revised)


def test_snapshot_hash_includes_structured_model_inputs(tmp_path):
    _db(tmp_path)
    base = {
        "evidence": [{"evidence_id": "event:1", "summary": "same"}],
        "financial_series": [{"period": "20260630", "revenue": 100}],
        "market_series": [{"date": "2026-08-20", "close": 10}],
        "valuation_series": [{"date": "2026-08-20", "pe_ttm": 20}],
        "ownership_governance": [], "capital_market_activity": [],
        "concept_context": {}, "filing_documents": [], "subject": {},
        "query_terms": ["光模块"], "cutoff": "2024-08-31",
    }
    revised = {**base, "valuation_series": [{"date": "2026-08-20", "pe_ttm": 25}]}

    assert IndustryResearchService._snapshot_hash(base) != IndustryResearchService._snapshot_hash(revised)


def test_snapshot_hash_includes_industry_peer_matrix(tmp_path):
    _db(tmp_path)
    base = {
        "evidence": [], "financial_series": [], "market_series": [], "valuation_series": [],
        "ownership_governance": [], "capital_market_activity": [], "concept_context": {},
        "fact_ledger": [], "filing_documents": [], "broker_report_documents": [], "web_documents": [],
        "subject": {}, "query_terms": ["光模块"], "research_contract": {}, "source_plan": [],
        "coverage": [], "source_status": [], "audio_pipeline": {}, "cutoff": "2026-08-31",
        "industry_peer_matrix": {
            "company_count": 3,
            "companies": [{"symbol": "600000.SH", "periods": [{"period": "20260630", "revenue": 100}]}],
        },
    }
    revised = {
        **base,
        "industry_peer_matrix": {
            "company_count": 3,
            "companies": [{"symbol": "600000.SH", "periods": [{"period": "20260630", "revenue": 120}]}],
        },
    }

    assert IndustryResearchService._snapshot_hash(base) != IndustryResearchService._snapshot_hash(revised)


def test_snapshot_hash_includes_primary_precedence_ledger(tmp_path):
    _db(tmp_path)
    base = {
        "evidence": [], "financial_series": [], "market_series": [], "valuation_series": [],
        "ownership_governance": [], "capital_market_activity": [], "concept_context": {},
        "industry_peer_matrix": {}, "fact_ledger": [], "filing_documents": [],
        "broker_report_documents": [], "web_documents": [], "subject": {}, "query_terms": [],
        "research_contract": {}, "source_plan": [], "coverage": [], "source_status": [],
        "audio_pipeline": {}, "primary_precedence": {
            "status": "conflicts_quarantined", "conflict_count": 1,
        }, "cutoff": "2024-08-31",
    }
    revised = {
        **base,
        "primary_precedence": {
            "status": "conflicts_quarantined", "conflict_count": 2,
        },
    }

    assert IndustryResearchService._snapshot_hash(base) != IndustryResearchService._snapshot_hash(revised)


def test_fact_ledger_keeps_metric_period_unit_and_evidence_id(tmp_path):
    _db(tmp_path)
    snapshot = {
        "topic": "中际旭创", "subject": {"name": "中际旭创", "symbol": "300308.SZ"},
        "financial_series": [{"period": "20260630", "revenue": 12_000_000, "roe": 18.2}],
        "market_series": [{"date": "2026-08-28", "close": 858.35}],
        "valuation_series": [{"date": "20260828", "pe_ttm": 70.5}],
        "evidence": [
            {
                "evidence_id": "financial:300308.SZ:20260630",
                "kind": "financial_statement", "symbol": "300308.SZ",
            },
            {
                "evidence_id": "valuation:300308.SZ:20260828",
                "kind": "valuation_fact", "symbol": "300308.SZ",
            },
        ],
    }

    facts = IndustryResearchService._build_fact_ledger(snapshot)
    revenue = next(item for item in facts if item["metric"] == "营业收入")
    valuation = next(item for item in facts if item["metric"] == "PE(TTM)")

    assert revenue["period"] == "20260630"
    assert revenue["unit"] == "元"
    assert revenue["evidence_ids"] == ["financial:300308.SZ:20260630"]
    assert valuation["evidence_ids"] == ["valuation:300308.SZ:20260828"]


def test_primary_evidence_precedence_quarantines_conflicting_audio_numbers(tmp_path):
    _db(tmp_path)
    snapshot = {
        "topic": "示例公司", "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:PRIMARY-1", "kind": "filing_text",
                "symbol": "600001.SH", "evidence_level": "factual",
                "summary": (
                    "标的公司甲2026年上半年归母净利润同比增长103.23%；"
                    "本次交易完成后上市公司持有标的公司甲57.84%的股权。"
                ),
            },
            {
                "evidence_id": "audio:LOW-1", "kind": "audio_transcript",
                "symbol": "600001.SH", "evidence_level": "ai_transcript",
                "summary": (
                    "标的公司甲2026H1净利润2.18亿元，同比增长110.32%；"
                    "交易后收购标的公司甲剩余股权58%。"
                    "管理层同时讨论了产能建设节奏。"
                ),
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "110.32" in audio["summary"]  # raw evidence remains auditable
    assert "110.32" not in audio["model_summary"]
    assert "2.18" not in audio["model_summary"]
    assert "58%" not in audio["model_summary"]
    assert "管理层同时讨论了产能建设节奏" in audio["model_summary"]
    assert "未确认内容不得进入执行摘要" in audio["model_summary"]
    assert audio["hypothesis_projection"]["suppressed_count"] == 3
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert snapshot["primary_precedence"]["audio_projection_count"] == 1
    assert snapshot["primary_precedence"]["audio_suppressed_count"] == 3

    compact = IndustryResearchService._compact_model_evidence(snapshot["evidence"])
    audio_for_model = next(item for item in compact if item["evidence_id"] == "audio:LOW-1")
    assert "110.32" not in audio_for_model["summary"]
    assert "58%" not in audio_for_model["summary"]


def test_primary_evidence_precedence_preserves_agreeing_secondary_claim(tmp_path):
    _db(tmp_path)
    snapshot = {
        "evidence": [
            {
                "evidence_id": "financial:600001.SH:20260630", "kind": "financial_statement",
                "symbol": "600001.SH", "evidence_level": "factual",
                "summary": "示例公司2026H1归母净利润同比增长103.23%。",
            },
            {
                "evidence_id": "report:AGREE", "kind": "broker_report_text",
                "symbol": "600001.SH", "evidence_level": "reported",
                "summary": "示例公司2026年上半年净利润同比增长103.23%。",
            },
        ],
        "fact_ledger": [],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    report = snapshot["evidence"][1]
    assert "model_summary" not in report
    assert snapshot["primary_precedence"]["status"] == "no_detected_conflict"


def test_statutory_fact_ledger_overrides_same_symbol_audio_claim(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "evidence": [
            {
                "evidence_id": "financial:600001.SH:20260630", "kind": "financial_statement",
                "symbol": "600001.SH", "evidence_level": "factual",
                "summary": "示例公司2026H1法定财务快照。",
            },
            {
                "evidence_id": "audio:LOW-STATUTORY", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "ai_transcript",
                "summary": "示例公司2026年上半年归母净利润同比增长110.32%。",
            },
        ],
        "fact_ledger": [{
            "entity": "600001.SH", "metric": "累计归母净利润同比",
            "value": 103.23, "unit": "%", "period": "20260630",
            "evidence_ids": ["financial:600001.SH:20260630"],
        }],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "110.32" not in audio["model_summary"]
    assert audio["hypothesis_projection"]["suppressed_count"] == 1
    assert audio["hypothesis_projection"]["confirmed_count"] == 0


def test_statutory_subject_fact_does_not_overwrite_named_acquisition_target(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "evidence": [
            {
                "evidence_id": "financial:600001.SH:20260630", "kind": "financial_statement",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "factual",
                "summary": "示例公司2026H1法定财务快照。",
            },
            {
                "evidence_id": "audio:TARGET", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "ai_transcript",
                "summary": "收购标的甲2026H1归母净利润同比增长110.32%。",
            },
        ],
        "fact_ledger": [{
            "entity": "600001.SH", "metric": "累计归母净利润同比",
            "value": -12.5, "unit": "%", "period": "20260630",
            "evidence_ids": ["financial:600001.SH:20260630"],
        }],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    assert "110.32" not in snapshot["evidence"][1]["model_summary"]
    assert snapshot["evidence"][1]["hypothesis_projection"]["suppressed_count"] == 1
    assert snapshot["primary_precedence"]["status"] == "no_detected_conflict"


def test_precedence_requires_exact_entity_not_shared_text_or_symbol(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "上市公司甲", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:ENTITY-A", "kind": "filing_text",
                "symbol": "600001.SH", "company": "上市公司甲", "evidence_level": "factual",
                "summary": "子公司蓝海科技2026H1归母净利润同比增长103.23%。",
            },
            {
                "evidence_id": "audio:ENTITY-B", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "上市公司甲", "evidence_level": "ai_transcript",
                "summary": "子公司蓝海材料2026H1归母净利润同比增长110.32%。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    assert "110.32" not in snapshot["evidence"][1]["model_summary"]
    assert snapshot["evidence"][1]["hypothesis_projection"]["suppressed_count"] == 1
    assert snapshot["primary_precedence"]["conflict_count"] == 0


def test_precedence_requires_period_on_both_sides_for_financial_metric(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:DATED", "kind": "filing_text",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "factual",
                "summary": "示例公司2025年度归母净利润3.5319亿元。",
            },
            {
                "evidence_id": "audio:UNDATED", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "ai_transcript",
                "summary": "示例公司剔除股份支付后归母净利润1.26亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    assert "1.26" not in snapshot["evidence"][1]["model_summary"]
    assert snapshot["evidence"][1]["hypothesis_projection"]["suppressed_count"] == 1
    assert snapshot["primary_precedence"]["conflict_count"] == 0


def test_unverified_institution_note_projects_only_qualitative_hypotheses(tmp_path):
    _db(tmp_path)
    raw = (
        "主题为光模块国产替代；预计2027年利润20亿元以上；"
        "Cohr/Lite客户绑定且独供；NPO/CPO收入10亿元；关注产品验证节奏。"
    )
    snapshot = {
        "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [{
            "evidence_id": "note:UNVERIFIED", "kind": "institution_note",
            "source": "机构语料", "title": "光模块20亿元+Cohr独供",
            "symbol": "600001.SH", "company": "示例公司",
            "evidence_level": "unverified", "summary": raw,
        }],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    note = snapshot["evidence"][0]
    assert note["summary"] == raw
    assert "光模块国产替代" in note["model_summary"]
    assert "关注产品验证节奏" in note["model_summary"]
    assert "20亿元" not in note["model_summary"]
    assert "10亿元" not in note["model_summary"]
    assert "客户绑定" not in note["model_summary"]
    assert "独供" not in note["model_summary"]
    assert "20亿元" not in note["model_title"]
    assert "独供" not in note["model_title"]
    assert note["hypothesis_projection"]["suppressed_count"] == 3
    assert note["hypothesis_projection"]["allowed_use"] == "verification_questions_only"
    assert snapshot["primary_precedence"]["conflict_count"] == 0
    compact = IndustryResearchService._compact_model_evidence(snapshot["evidence"])
    assert "20亿元" not in compact[0]["summary"]
    assert "独供" not in compact[0]["title"]


def test_adjusted_profit_same_period_is_not_statutory_profit_conflict(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:STATUTORY", "kind": "filing_text",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "factual",
                "summary": "示例公司2026H1法定归母净利润1.20亿元。",
            },
            {
                "evidence_id": "audio:ADJUSTED", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "示例公司", "evidence_level": "ai_transcript",
                "summary": "示例公司2026H1剔除股份支付后归母净利润1.26亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    assert "1.26" not in snapshot["evidence"][1]["model_summary"]
    assert snapshot["evidence"][1]["hypothesis_projection"]["suppressed_count"] == 1
    assert snapshot["primary_precedence"]["conflict_count"] == 0


def test_audio_keeps_number_when_same_entity_period_metric_and_primary_agree(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "标的科技", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:AGREE", "kind": "filing_text",
                "symbol": "600001.SH", "company": "标的科技", "evidence_level": "factual",
                "summary": "标的科技2026H1归母净利润同比增长103.23%。",
            },
            {
                "evidence_id": "audio:AGREE", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "标的科技", "evidence_level": "ai_transcript",
                "summary": "标的科技2026年上半年归母净利润同比增长103.23%。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "103.23" in audio["model_summary"]
    assert audio["hypothesis_projection"]["confirmed_count"] == 1
    assert audio["hypothesis_projection"]["suppressed_count"] == 0
    assert snapshot["primary_precedence"]["conflict_count"] == 0


def test_audio_projection_only_transports_primary_confirmed_facts(tmp_path):
    _db(tmp_path)
    raw = (
        "富创优越2026H1剔除股份支付后归母净利润约1.26亿元；"
        "富创优越2026H1股份支付费用1.20亿元，占净利润42.16%；"
        "上市公司拟购富创优越57.84%股权；"
        "富创优越2026H1归母净利润同比增长110.32%；"
        "上市公司拟购富创优越58%股权；"
        "富创优越订单三倍；"
        "Cohr/Lite客户绑定且独供；"
        "预计NPO/CPO于2027年量产并贡献收入20亿元；"
        "光通信产业主题仍待核验。"
    )
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:FU-2026H1", "kind": "filing_text",
                "company": "华懋科技", "evidence_level": "factual",
                "summary": (
                    "富创优越2026H1剔除股份支付后归母净利润1.258979亿元；"
                    "富创优越2026H1股份支付费用1.20亿元，占净利润42.16%；"
                    "上市公司拟购富创优越57.84%股权；"
                    "富创优越2026H1归母净利润同比增长103.23%。"
                ),
            },
            {
                "evidence_id": "audio:FU-2026H1", "kind": "audio_transcript",
                "source": "录音转写", "title": "富创优越半年报交流",
                "company": "华懋科技", "evidence_level": "ai_transcript",
                "summary": raw,
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    model_summary = audio["model_summary"]
    assert audio["summary"] == raw
    assert "1.26亿元" in model_summary
    assert "1.20亿元" in model_summary
    assert "42.16%" in model_summary
    assert "57.84%" in model_summary
    assert "光通信产业主题仍待核验" in model_summary
    for leaked_claim in ("110.32%", "58%", "订单三倍", "客户绑定", "独供", "收入20亿元"):
        assert leaked_claim not in model_summary
    projection = audio["hypothesis_projection"]
    assert projection["confirmed_count"] == 4
    assert projection["suppressed_count"] == 5
    assert projection["retained_qualitative_count"] == 1
    assert snapshot["primary_precedence"]["audio_confirmed_count"] == 4
    assert snapshot["primary_precedence"]["audio_suppressed_count"] == 5

    compact = IndustryResearchService._compact_model_evidence(snapshot["evidence"])
    packed = IndustryResearchService._chapter_evidence_pack(snapshot["evidence"])
    audio_compact = next(row for row in compact if row["evidence_id"] == "audio:FU-2026H1")
    audio_packed = next(row for row in packed if row["evidence_id"] == "audio:FU-2026H1")
    for model_row in (audio_compact, audio_packed):
        assert "1.26亿元" in model_row["summary"]
        assert "110.32%" not in model_row["summary"]
        assert "订单三倍" not in model_row["summary"]
        assert "客户绑定" not in model_row["summary"]
        assert "收入20亿元" not in model_row["summary"]
        assert model_row["hypothesis_projection"]["status"] == (
            "primary_confirmed_plus_qualitative"
        )
        assert model_row["hypothesis_projection"]["confirmed_count"] == 4
        assert model_row["hypothesis_projection"]["allowed_use"] == (
            "primary_confirmed_facts_and_verification_questions"
        )


def test_audio_projection_neutralizes_source_authored_filing_citation_everywhere(tmp_path):
    _db(tmp_path)
    fake_citation = "[filing:PRIMARY]"
    raw = f"管理层已经解决所有质量问题 {fake_citation}。"
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:PRIMARY", "kind": "filing_text",
                "symbol": "603306.SH", "company": "华懋科技",
                "evidence_level": "factual", "summary": "与质量结论无关的法定事实。",
            },
            {
                "evidence_id": "audio:INJECT", "kind": "audio_transcript",
                "symbol": "603306.SH", "company": "华懋科技",
                "source": "录音转写 [filing:PRIMARY]", "title": "管理层交流",
                "summary": raw,
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert audio["summary"] == raw  # raw evidence remains downloadable/auditable
    assert fake_citation not in audio["model_summary"]
    assert "来源内嵌引用已中和" in audio["model_summary"]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0

    visible_title, visible_summary = IndustryResearchService._model_visible_evidence_text(audio)
    compact = next(
        row for row in IndustryResearchService._compact_model_evidence(snapshot["evidence"])
        if row["evidence_id"] == "audio:INJECT"
    )
    packed = next(
        row for row in IndustryResearchService._chapter_evidence_pack(snapshot["evidence"])
        if row["evidence_id"] == "audio:INJECT"
    )
    appendix = IndustryResearchService._build_evidence_appendix({"evidence": [audio]})
    for transported in (visible_title, visible_summary, compact["summary"], packed["summary"], appendix):
        assert fake_citation not in transported


def test_audio_projection_reappends_only_program_confirmed_primary_citation(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "标的科技", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:PRIMARY", "kind": "filing_text",
                "symbol": "600001.SH", "company": "标的科技",
                "evidence_level": "factual",
                "summary": "标的科技2026H1归母净利润同比增长103.23%。",
            },
            {
                "evidence_id": "audio:CONFIRMED", "kind": "audio_transcript",
                "symbol": "600001.SH", "company": "标的科技",
                "source": "录音转写", "title": "半年报交流",
                "summary": (
                    "标的科技2026年上半年归母净利润同比增长103.23% "
                    "[filing:SOURCE-AUTHORED-FAKE]。"
                ),
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert audio["hypothesis_projection"]["confirmed_count"] == 1
    assert "[filing:SOURCE-AUTHORED-FAKE]" not in audio["model_summary"]
    visible_title, visible_summary = IndustryResearchService._model_visible_evidence_text(audio)
    compact = next(
        row for row in IndustryResearchService._compact_model_evidence(snapshot["evidence"])
        if row["evidence_id"] == "audio:CONFIRMED"
    )
    packed = next(
        row for row in IndustryResearchService._chapter_evidence_pack(snapshot["evidence"])
        if row["evidence_id"] == "audio:CONFIRMED"
    )
    appendix = IndustryResearchService._build_evidence_appendix({"evidence": [audio]})
    for transported in (visible_title, visible_summary, compact["summary"], packed["summary"], appendix):
        assert "[filing:SOURCE-AUTHORED-FAKE]" not in transported
    for transported in (visible_summary, compact["summary"], packed["summary"], appendix):
        assert "[filing:PRIMARY]" in transported
        assert "程序核验一级依据" in transported


@pytest.mark.parametrize(
    ("primary_company", "primary_symbol"),
    [
        ("平安银行", "000001.SZ"),
        ("华懋科技", "000001.SZ"),
        ("平安银行", "603306.SH"),
    ],
)
def test_audio_does_not_inherit_subject_from_wrong_issuer_filing_row(
    tmp_path, primary_company, primary_symbol,
):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:OTHER", "kind": "filing_text",
                "symbol": primary_symbol, "company": primary_company,
                "evidence_level": "factual", "report_period": "2026H1",
                # A statutory table row commonly omits the issuer.  A row from
                # another issuer must not silently inherit snapshot.subject.
                "document_text": (
                    "2026年半年度报告；单位：元；主要会计数据；"
                    "归母净利润 100,000,000.00"
                ),
                "summary": "2026年半年度报告正文已入库。",
            },
            {
                "evidence_id": "audio:HMT", "kind": "audio_transcript",
                "symbol": "603306.SH", "company": "华懋科技",
                "source": "录音转写", "title": "半年报交流",
                "summary": "华懋科技2026H1归母净利润1.00亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert audio["hypothesis_projection"]["suppressed_count"] == 1
    for transported in (
        audio["model_summary"],
        IndustryResearchService._compact_model_evidence(snapshot["evidence"])[1]["summary"],
        IndustryResearchService._chapter_evidence_pack(snapshot["evidence"])[1]["summary"],
        IndustryResearchService._build_evidence_appendix({"evidence": [audio]}),
    ):
        assert "[filing:OTHER]" not in transported


def test_audio_identity_mismatch_cannot_confirm_or_rebuild_subject_citation(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:HMT", "kind": "filing_text",
                "symbol": "603306.SH", "company": "华懋科技",
                "evidence_level": "factual", "report_period": "2026H1",
                "summary": "华懋科技2026H1归母净利润1.00亿元。",
            },
            {
                "evidence_id": "audio:OTHER", "kind": "audio_transcript",
                "symbol": "000001.SZ", "company": "平安银行",
                "source": "录音转写", "title": "错误归档的录音",
                "summary": "华懋科技2026H1归母净利润1.00亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert audio["hypothesis_projection"]["suppressed_count"] == 1
    assert "[filing:HMT]" not in audio["model_summary"]


def test_matching_issuer_filing_table_row_may_confirm_audio_without_row_entity(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:HMT-TABLE", "kind": "filing_text",
                "symbol": "603306.SH", "company": "华懋科技",
                "evidence_level": "factual", "report_period": "2026H1",
                "document_text": (
                    "2026年半年度报告；单位：元；主要会计数据；"
                    "归母净利润 100,000,000.00"
                ),
                "summary": "2026年半年度报告正文已入库。",
            },
            {
                "evidence_id": "audio:HMT-TABLE", "kind": "audio_transcript",
                "symbol": "603306.SH", "company": "华懋科技",
                "source": "录音转写", "title": "半年报交流",
                "summary": "华懋科技2026H1归母净利润1.00亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert audio["hypothesis_projection"]["confirmed_count"] == 1
    assert audio["hypothesis_projection"]["suppressed_count"] == 0
    assert "[filing:HMT-TABLE]" in audio["model_summary"]
    _, visible_summary = IndustryResearchService._model_visible_evidence_text(audio)
    assert "[filing:HMT-TABLE]" in visible_summary
    assert "程序核验一级依据" in visible_summary


def test_matching_issuer_legal_name_alias_does_not_kill_table_confirmation(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:HMT-LEGAL-NAME", "kind": "filing_text",
                "symbol": "603306.SH",
                "company": "华懋（厦门）新材料科技股份有限公司",
                "evidence_level": "factual", "report_period": "2026H1",
                "document_text": (
                    "2026年半年度报告；单位：元；主要会计数据；"
                    "归母净利润 100,000,000.00"
                ),
                "summary": "2026年半年度报告正文已入库。",
            },
            {
                "evidence_id": "audio:HMT-LEGAL-NAME", "kind": "audio_transcript",
                "symbol": "603306.SH", "company": "华懋科技",
                "summary": "华懋科技2026H1归母净利润1.00亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert audio["hypothesis_projection"]["confirmed_count"] == 1
    assert "[filing:HMT-LEGAL-NAME]" in audio["model_summary"]


def test_huamao_production_shape_inherits_context_and_uses_filing_fulltext(tmp_path):
    _db(tmp_path)
    half_year_text = (
        "华懋（厦门）新材料科技股份有限公司2026年半年度报告。"
        "报告期内，公司实现营业收入10.91亿元，同比下降1.53%；"
        "归属于上市公司股东的净利润0.23亿元，同比下降82.95%。"
        "因实施员工持股计划产生的股份支付费用1.20亿元，较去年同期增加1.12亿元。"
        "剔除股份支付的影响，归属于母公司的净利润1.26亿元，同比下降12.15%。"
        "单位：元 币种：人民币 主要会计数据 本报告期（1－6月） 上年同期 本期比上年同期增减(%) "
        "扣除股份支付影响后的净利润 125,897,911.25 143,317,500.70 -12.15。"
        "富创优越2026年上半年实现营业收入16.81亿元，同比增长83.36%，"
        "净利润2.18亿元，同比增长103.23%。"
        "目前公司尚未收购完成富创优越全部股权，仍为公司参股公司，未实现并表。"
    )
    transaction_text = (
        "本次交易前，上市公司通过全资子公司华懋东阳持有富创优越42.16%的股权，"
        "本次交易上市公司拟通过直接和间接的方式购买富创优越剩余57.84%的股权。"
        "本次交易完成后，富创优越将成为上市公司的全资子公司并纳入合并报表。"
    )
    audio_raw = (
        "本次录音纪要覆盖华懋科技2026年8月26日业绩交流；"
        "华懋科技上半年营收10.9亿元，归母净利润0.23亿元；"
        "今年上半年的股份支付费用1.2亿元，比去年同期增加1.12亿元；"
        "剔除股份支付影响后归母净利润1.26亿元，同比下降12.1%；"
        "公司正推进收购一家叫富创优越的剩余全部股权；"
        "富创优越上半年营收16.8亿元，净利润2.18亿元，同比增长110.32%；"
        "我们现在持有它大概42.16%的股权；"
        "收购他剩余58%股权；"
        "富创优越现在已经是全资子公司并已并表。"
    )
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:1225505930", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "report_period": "20260630", "date": "2026-08-26",
                "evidence_level": "factual", "summary": half_year_text,
                "document_text": half_year_text,
            },
            {
                "evidence_id": "filing:1225532560", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "date": "2026-08-26", "evidence_level": "factual",
                "summary": transaction_text, "document_text": transaction_text,
            },
            {
                "evidence_id": "audio:PRODUCTION-SHAPE", "kind": "audio_transcript",
                "source": "阿里云语音转写 + 录音纪要", "title": "华懋科技 · 深度研究录音纪要",
                "company": "华懋科技", "symbol": "603306.SH",
                "date": "2026-08-27T18:06:19Z", "evidence_level": "ai_transcript",
                "summary": audio_raw,
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][2]
    projection = audio["hypothesis_projection"]
    model_summary = audio["model_summary"]
    for confirmed_value in ("10.9亿元", "0.23亿元", "1.2亿元", "1.26亿元", "16.8亿元", "2.18亿元", "42.16%"):
        assert confirmed_value in model_summary
    for quarantined_value in ("1.12亿元", "12.1%", "110.32%", "58%"):
        assert quarantined_value not in model_summary
    assert "现在已经是全资子公司" not in model_summary
    assert "并已并表" not in model_summary
    assert projection["confirmed_count"] >= 7
    assert projection["suppressed_count"] >= 5
    confirmed_periods = {
        period for item in projection["confirmed"] for period in item.get("periods") or []
    }
    assert "2026H1" in confirmed_periods
    assert snapshot["primary_precedence"]["audio_confirmed_count"] >= 7


def test_huamao_governing_statutory_facts_are_atomic_and_period_locked(tmp_path):
    _db(tmp_path)
    h1_text = (
        "华懋科技2026年半年度营业收入1,091,459,912.33元；"
        "华懋科技2026年半年度归属于上市公司股东的净利润23,285,735.42元；"
        "华懋科技2026年半年度股份支付费用1.20亿元；"
        "华懋科技2026年半年度扣除股份支付影响后的归母净利润"
        "125,897,911.25元同比-12.15%。"
        "华懋科技2026年6月30日总资产6,171,145,144.82元；"
        "华懋科技2026年6月30日归属于上市公司股东的净资产"
        "3,817,464,934.50元。"
        "截至2026年6月30日，公司尚未收购完成富创优越全部股权；"
        "截至2026年6月30日，富创优越未实现并表。"
    )
    transaction_text = (
        "本次交易前，上市公司持有富创优越42.16%的股权；"
        "上市公司拟购买富创优越剩余57.84%的股权。"
        "本次交易完成后，富创优越将成为上市公司的全资子公司并纳入合并报表。"
    )
    q1_text = (
        "华懋科技2026年第一季度总资产6,008,970,568.47元；"
        "华懋科技2026年第一季度归属于上市公司股东的净资产"
        "3,475,323,616.35元。"
    )
    q3_text = (
        "华懋科技2025年第三季度报告：归属于上市公司股东的净资产"
        "3,363,507,381.94元。"
    )
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "financial_series": [{
            "period": "20260331", "period_basis": "YTD_Q1",
            "total_assets": 6_009_000_000.0, "net_profit": 26_000_000.0,
        }],
        "evidence": [
            {
                "evidence_id": "financial:603306.SH:20260331", "kind": "financial_statement",
                "symbol": "603306.SH", "date": "2026-03-31", "summary": "一季度结构化财务",
            },
            {
                "evidence_id": "filing:1224752345", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "date": "2025-10-30", "summary": q3_text,
            },
            {
                "evidence_id": "filing:1225224760", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "date": "2026-04-28", "summary": q1_text,
            },
            {
                "evidence_id": "filing:1225505930", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "date": "2026-08-26", "summary": "半年报摘要",
                "report_period": "20260630",
            },
            {
                "evidence_id": "filing:1225532560", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "date": "2026-08-26", "summary": "交易报告摘要",
            },
            {
                "evidence_id": "audio:RAW", "kind": "audio_transcript",
                "symbol": "603306.SH", "title": "录音", "summary": "当前持股58%，已经并表。",
            },
        ],
        "filing_documents": [
            {
                "announcement_id": "1225505930", "company": "华懋科技",
                "symbol": "603306.SH", "document_text": h1_text,
            },
            {
                "announcement_id": "1225532560", "company": "华懋科技",
                "symbol": "603306.SH", "document_text": transaction_text,
            },
        ],
        "valuation_series": [], "market_series": [], "industry_peer_matrix": {},
        "research_contract": {"subject_name": "华懋科技", "symbol": "603306.SH"},
        "data_quality": {},
    }

    governing = IndustryResearchService._build_governing_statutory_facts(snapshot)
    snapshot["governing_statutory_facts"] = governing
    snapshot["fact_ledger"] = IndustryResearchService._build_fact_ledger(snapshot)

    current = {
        (item["metric"], item["metric_basis"]): item
        for item in governing if item.get("usage_scope") == "current_governing"
    }
    assert current[("营业收入", "statutory_gaap")]["value"] == 1_091_459_912.33
    assert current[("归属于上市公司股东的净利润", "statutory_gaap_attributable")]["value"] == 23_285_735.42
    adjusted = current[("扣除股份支付影响后的归母净利润", "non_gaap_excluding_share_based_payment")]
    assert adjusted["value"] == 125_897_911.25
    adjusted_yoy = current[(
        "扣除股份支付影响后的归母净利润同比",
        "non_gaap_excluding_share_based_payment_yoy",
    )]
    assert adjusted_yoy["value"] == -12.15
    assert adjusted_yoy["display_value"] == "-12.15%"
    assert "不能据此归因于任何单一业务板块" in adjusted_yoy["required_sentence"]
    share_payment = current[("股份支付费用", "share_based_payment_expense")]
    assert share_payment["display_value"] == "1.20亿元"
    assert share_payment["paired_display_value"] == "1.26亿元"
    assert share_payment["required_sentence"] == (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润"
        "1.26亿元 [filing:1225505930]"
    )
    assert adjusted["display_value"] == "1.26亿元"
    assert adjusted["paired_display_value"] == "1.20亿元"
    assert adjusted["metric_basis"] != current[("归属于上市公司股东的净利润", "statutory_gaap_attributable")]["metric_basis"]
    h1_assets = current[("总资产", "statutory_balance_sheet")]
    assert h1_assets["value"] == 6_171_145_144.82
    assert h1_assets["display_value"] == "61.71亿元"
    assert h1_assets["required_sentence"] == (
        "华懋科技2026H1总资产61.71亿元 [filing:1225505930]"
    )
    assert current[("归属于上市公司股东的净资产", "statutory_attributable_equity")]["value"] == 3_817_464_934.50
    assert all(item["supporting_evidence_ids"] == item["evidence_ids"] for item in governing)

    ownership = {item["metric"]: item for item in governing if item.get("entity") == "富创优越"}
    assert ownership["当前持股比例"]["value"] == 42.16
    assert ownership["当前持股比例"]["metric_basis"] == "current_ownership"
    assert ownership["拟收购股权比例"]["value"] == 57.84
    assert ownership["拟收购股权比例"]["usage_scope"] == "proposed_only"
    assert ownership["交易完成状态"]["value"] == "尚未完成"
    assert ownership["交易完成状态"]["period"] == "2026-06-30"
    assert ownership["交易完成状态"]["period_basis"] == "BALANCE_SHEET_DATE"
    assert ownership["交易完成状态"]["condition"] == "仅代表2026年6月30日报告期末状态"
    assert ownership["当前合并范围状态"]["value"] == "未并表"
    assert ownership["当前合并范围状态"]["period"] == "2026-06-30"
    assert ownership["当前合并范围状态"]["period_basis"] == "BALANCE_SHEET_DATE"
    assert ownership["交易完成后股权状态"]["condition"] == "仅在本次交易完成后成立"
    assert ownership["交易完成后合并范围状态"]["period_basis"] == "CONDITIONAL_FUTURE"

    historical = [item for item in governing if item.get("usage_scope") == "historical_only"]
    assert {(item["period"], item["metric"], item["value"]) for item in historical} == {
        ("2026Q1", "总资产", 6_008_970_568.47),
        ("2026Q1", "归属于上市公司股东的净资产", 3_475_323_616.35),
        ("2025Q3", "归属于上市公司股东的净资产", 3_363_507_381.94),
    }
    q1_facts = [item for item in historical if item["period"] == "2026Q1"]
    q3_facts = [item for item in historical if item["period"] == "2025Q3"]
    assert all(item["prohibited_periods"] == ["2026H1", "20260630"] for item in q1_facts)
    assert q3_facts[0]["prohibited_periods"] == [
        "2025FY", "20251231", "2026H1", "20260630",
    ]
    q1_structured = [
        item for item in snapshot["fact_ledger"]
        if item.get("period") == "20260331"
    ]
    assert q1_structured
    assert all(item.get("usage_scope") == "historical_only" for item in q1_structured)


def _huamao_comparative_balance_table_snapshot(text):
    return {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [{
            "evidence_id": "filing:1225505930", "kind": "filing_text",
            "company": "华懋科技", "symbol": "603306.SH",
            "title": "华懋科技2026年半年度报告 · 正文重点章节",
            "report_period": "20260630", "document_text": text,
        }],
    }


def test_governing_balance_facts_bind_cross_line_headers_to_one_metric_row(tmp_path):
    _db(tmp_path)
    snapshot = _huamao_comparative_balance_table_snapshot(
        "单位：元 币种：人民币\n"
        "主要会计数据\n"
        "本报告期末 上年度末\n"
        "本报告期末比上\n"
        "年度末增减(%)\n"
        "归属于上市公司股东的净资产 "
        "3,817,464,934.50 3,429,966,675.77 11.30\n"
        "总资产 6,171,145,144.82 5,993,670,009.88 2.96\n"
    )

    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    by_period_metric = {
        (item.get("period"), item.get("metric")): item for item in facts
    }
    h1_equity = by_period_metric[(
        "2026H1", "归属于上市公司股东的净资产",
    )]
    year_end_assets = by_period_metric[("2025FY", "总资产")]

    assert h1_equity["value"] == 3_817_464_934.50
    assert h1_equity["display_value"] == "38.17亿元"
    assert h1_equity["required_sentence"] == (
        "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]"
    )
    assert year_end_assets["value"] == 5_993_670_009.88
    assert year_end_assets["display_value"] == "59.94亿元"
    assert year_end_assets["required_sentence"] == (
        "华懋科技2025年末总资产59.94亿元 [filing:1225505930]"
    )
    assert year_end_assets["usage_scope"] == "historical_only"


def test_governing_h1_facts_bind_real_pdf_prose_and_cross_line_tables(tmp_path):
    _db(tmp_path)
    snapshot = _huamao_comparative_balance_table_snapshot(
        "华懋（厦门）新材料科技股份有限公司2026年半年度报告\n"
        "因实施员工持股计划产生的股份支付费用1.20亿元，较去年同期增加1.12亿元。\n"
        "单位：元 币种：人民币\n"
        "主要会计数据 本报告期（1－6月） 上年同期 本期比上年同期增减(%)\n"
        "扣除股份支付影响后的净利润 "
        "125,897,911.25 143,317,500.70 -12.15\n"
        "单位：元 币种：人民币\n"
        "本报告期末 上年度末 本报告期末比上年度末增减(%)\n"
        "归属于上市公司股东的净资产 "
        "3,817,464,934.50 3,429,966,675.77 11.30\n"
        "总资产 6,171,145,144.82 5,993,670,009.88 2.96\n"
    )

    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    current = {
        (item.get("metric"), item.get("metric_basis")): item
        for item in facts if item.get("usage_scope") == "current_governing"
    }

    assert current[("股份支付费用", "share_based_payment_expense")]["display_value"] == "1.20亿元"
    assert current[(
        "扣除股份支付影响后的归母净利润",
        "non_gaap_excluding_share_based_payment",
    )]["display_value"] == "1.26亿元"
    assert current[(
        "扣除股份支付影响后的归母净利润同比",
        "non_gaap_excluding_share_based_payment_yoy",
    )]["display_value"] == "-12.15%"
    assert current[("总资产", "statutory_balance_sheet")]["required_sentence"] == (
        "华懋科技2026H1总资产61.71亿元 [filing:1225505930]"
    )


@pytest.mark.parametrize(
    "text,missing_metric",
    [
        (
            "单位：万元 币种：人民币\n"
            "本报告期（1－6月） 上年同期 本期比上年同期增减(%)\n"
            "扣除股份支付影响后的净利润 "
            "125,897,911.25 143,317,500.70 -12.15",
            "扣除股份支付影响后的归母净利润",
        ),
        (
            "单位：元 币种：人民币\n"
            "本报告期（1－6月） 上年同期 本期比上年同期增减(%)\n"
            "扣除股份支付影响后的净利润 "
            "143,317,500.70 125,897,911.25 -12.15",
            "扣除股份支付影响后的归母净利润",
        ),
        (
            "华懋科技2026年半年度股份支付费用1.20万元。",
            "股份支付费用",
        ),
    ],
    ids=["wrong-table-unit", "swapped-profit-columns", "wrong-share-unit"],
)
def test_governing_h1_profit_atoms_fail_closed_on_wrong_unit_or_column_order(
    tmp_path, text, missing_metric,
):
    _db(tmp_path)
    facts = IndustryResearchService._build_governing_statutory_facts(
        _huamao_comparative_balance_table_snapshot(text)
    )
    assert all(item.get("metric") != missing_metric for item in facts)


def _huamao_q1_flow_table_snapshot(
    text, *, title="华懋科技2026年第一季度报告", period="20260331",
    company="华懋科技", symbol="603306.SH",
):
    return {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [{
            "evidence_id": "filing:1225224760", "kind": "filing_text",
            "company": company, "symbol": symbol, "title": title,
            "report_period": period, "document_text": text,
        }],
    }


def _real_huamao_q1_flow_table_text():
    return (
        "华懋（厦门）新材料科技股份有限公司2026年第一季度报告\n"
        "单位：元 币种：人民币\n"
        "主要会计数据 本报告期 上年同期 本报告期比上年同期增减(%)\n"
        "归属于上市公司股东的净利润\n"
        "11,696,307.92 86,421,910.38 -86.47\n"
        "经营活动产生的现金流量净额\n"
        "116,738,968.21 19,006,538.58 514.20\n"
    )


def test_governing_q1_flow_facts_bind_real_cross_line_current_prior_yoy_table(tmp_path):
    _db(tmp_path)
    facts = IndustryResearchService._build_governing_statutory_facts(
        _huamao_q1_flow_table_snapshot(_real_huamao_q1_flow_table_text())
    )
    by_basis = {item.get("metric_basis"): item for item in facts}

    assert by_basis["statutory_gaap_attributable_q1"]["value"] == 11_696_307.92
    assert by_basis["statutory_gaap_attributable_q1_prior"]["value"] == 86_421_910.38
    assert by_basis["statutory_gaap_attributable_q1_prior"]["period"] == "2025Q1"
    assert by_basis["statutory_gaap_attributable_q1_yoy"]["value"] == -86.47
    assert by_basis["statutory_operating_cash_flow_q1"]["value"] == 116_738_968.21
    assert by_basis["statutory_operating_cash_flow_q1_prior"]["value"] == 19_006_538.58
    assert by_basis["statutory_operating_cash_flow_q1_yoy"]["value"] == 514.20
    assert by_basis["statutory_gaap_attributable_q1"]["required_sentence"] == (
        "华懋科技2026Q1归母净利润11,696,307.92元，"
        "上年同期86,421,910.38元，同比下降86.47% "
        "[filing:1225224760]"
    )
    assert by_basis["statutory_operating_cash_flow_q1_yoy"]["required_sentence"] == (
        "华懋科技2026Q1经营活动产生的现金流量净额"
        "116,738,968.21元，上年同期19,006,538.58元，"
        "同比增长514.20% [filing:1225224760]"
    )
    assert all(
        item.get("usage_scope") == "historical_only"
        for key, item in by_basis.items() if key.startswith("statutory_")
    )


@pytest.mark.parametrize(
    ("mutation", "overrides"),
    [
        ("wrong_unit", {}),
        ("swapped_profit_columns", {}),
        ("swapped_cash_columns", {}),
        ("foreign_profit_subject", {}),
        ("cross_metric_rows", {}),
        ("wrong_period", {"title": "华懋科技2026年半年度报告", "period": "20260630"}),
        ("wrong_filing_subject", {"company": "胜宏科技", "symbol": "300476.SZ"}),
    ],
)
def test_governing_q1_flow_table_fails_closed_on_wrong_binding(
    tmp_path, mutation, overrides,
):
    _db(tmp_path)
    text = _real_huamao_q1_flow_table_text()
    if mutation == "wrong_unit":
        text = text.replace("单位：元", "单位：万元")
    elif mutation == "swapped_profit_columns":
        text = text.replace(
            "11,696,307.92 86,421,910.38 -86.47",
            "86,421,910.38 11,696,307.92 -86.47",
        )
    elif mutation == "swapped_cash_columns":
        text = text.replace(
            "116,738,968.21 19,006,538.58 514.20",
            "19,006,538.58 116,738,968.21 514.20",
        )
    elif mutation == "foreign_profit_subject":
        text = text.replace(
            "归属于上市公司股东的净利润",
            "富创优越归属于上市公司股东的净利润",
        )
    elif mutation == "cross_metric_rows":
        text = text.replace(
            "归属于上市公司股东的净利润\n"
            "11,696,307.92 86,421,910.38 -86.47",
            "归属于上市公司股东的净利润 11,696,307.92\n"
            "营业收入 86,421,910.38 -86.47",
        )
    snapshot = _huamao_q1_flow_table_snapshot(text, **overrides)
    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    profit_bases = {
        "statutory_gaap_attributable_q1",
        "statutory_gaap_attributable_q1_prior",
        "statutory_gaap_attributable_q1_yoy",
    }
    cash_bases = {
        "statutory_operating_cash_flow_q1",
        "statutory_operating_cash_flow_q1_prior",
        "statutory_operating_cash_flow_q1_yoy",
    }
    forbidden_bases = (
        cash_bases if mutation == "swapped_cash_columns"
        else profit_bases if mutation in {
            "swapped_profit_columns", "foreign_profit_subject", "cross_metric_rows",
        }
        else profit_bases | cash_bases
    )
    assert not any(item.get("metric_basis") in forbidden_bases for item in facts)


@pytest.mark.parametrize(
    "text",
    [
        (
            "单位：万元 币种：人民币\n本报告期末 上年度末\n"
            "本报告期末比上年度末增减(%)\n"
            "归属于上市公司股东的净资产 "
            "3,817,464,934.50 3,429,966,675.77 11.30\n"
            "总资产 6,171,145,144.82 5,993,670,009.88 2.96"
        ),
        (
            "单位：元 币种：人民币\n本报告期末 上年度末\n"
            "本报告期末比上年度末增减(%)\n"
            "归属于上市公司股东的净资产 "
            "3,429,966,675.77 3,817,464,934.50 11.30\n"
            "总资产 5,993,670,009.88 6,171,145,144.82 2.96"
        ),
        (
            "单位：元 币种：人民币\n本报告期末 上年度末\n"
            "本报告期末比上年度末增减(%)\n"
            "归属于上市公司股东的净资产 3,817,464,934.50\n"
            "其他指标 3,429,966,675.77\n"
            "总资产 6,171,145,144.82\n其他指标 5,993,670,009.88"
        ),
        (
            "单位：元 币种：人民币\n本报告期末 上年度末\n"
            "本报告期末比上年度末增减(%)\n"
            "富创优越总资产 6,171,145,144.82 5,993,670,009.88 2.96"
        ),
    ],
    ids=["wrong-unit", "swapped-columns", "values-cross-rows", "foreign-entity"],
)
def test_governing_balance_table_fails_closed_without_exact_row_binding(tmp_path, text):
    _db(tmp_path)
    facts = IndustryResearchService._build_governing_statutory_facts(
        _huamao_comparative_balance_table_snapshot(text)
    )

    assert all(
        not (
            item.get("period") == "2026H1"
            and item.get("metric") == "归属于上市公司股东的净资产"
        )
        for item in facts
    )
    assert all(
        not (item.get("period") == "2025FY" and item.get("metric") == "总资产")
        for item in facts
    )


def test_huamao_governing_facts_require_exact_resolved_issuer_and_filing_symbol(tmp_path):
    _db(tmp_path)
    h1_text = "华懋科技2026年半年度报告，营业收入1,091,459,912.33元。"
    filing = {
        "evidence_id": "filing:1225505930", "kind": "filing_text",
        "company": "华懋科技", "symbol": "603306.SH", "summary": h1_text,
    }

    wrong_subject = {
        "topic": "华懋科技供应链样本",
        "subject": {
            "name": "华懋科技供应链样本", "symbol": "000001.SZ", "resolved": True,
        },
        "evidence": [filing],
    }
    mismatched_filing = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [{**filing, "symbol": "000001.SZ"}],
    }
    mismatched_company = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [{**filing, "company": "胜宏科技"}],
    }
    unresolved_subject = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": False},
        "evidence": [filing],
    }

    assert IndustryResearchService._build_governing_statutory_facts(wrong_subject) == []
    assert IndustryResearchService._build_governing_statutory_facts(mismatched_filing) == []
    assert IndustryResearchService._build_governing_statutory_facts(mismatched_company) == []
    assert IndustryResearchService._build_governing_statutory_facts(unresolved_subject) == []


def _huamao_governing_revenue_snapshot(
    text, *, source_mode="document", document_overrides=None,
):
    evidence = {
        "evidence_id": "filing:1225505930", "kind": "filing_text",
        "company": "华懋科技", "symbol": "603306.SH",
        "report_period": "20260630", "date": "2026-08-26",
        "summary": "华懋科技2026年半年度报告正文见附件。",
    }
    documents = []
    if source_mode == "filing_text":
        evidence["document_text"] = text
    else:
        document = {
            "announcement_id": "1225505930", "company": "华懋科技",
            "symbol": "603306.SH", "report_period": "20260630",
            "document_text": text,
        }
        document.update(document_overrides or {})
        documents.append(document)
    return {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [evidence], "filing_documents": documents,
    }


@pytest.mark.parametrize("source_mode", ["filing_text", "document"])
def test_governing_fact_accepts_issuer_bound_atomic_filing_source(tmp_path, source_mode):
    _db(tmp_path)
    snapshot = _huamao_governing_revenue_snapshot(
        "华懋科技2026年半年度营业收入1,091,459,912.33元。",
        source_mode=source_mode,
    )
    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    revenue = [item for item in facts if item.get("metric") == "营业收入"]
    assert len(revenue) == 1
    assert revenue[0]["value"] == 1_091_459_912.33


def test_governing_fact_allows_entity_neutral_row_from_bound_filing(tmp_path):
    _db(tmp_path)
    snapshot = _huamao_governing_revenue_snapshot(
        "2026年半年度营业收入1,091,459,912.33元。",
    )
    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    revenue = [item for item in facts if item.get("metric") == "营业收入"]
    assert len(revenue) == 1
    assert revenue[0]["entity"] == "华懋科技"


@pytest.mark.parametrize(
    ("text", "document_overrides"),
    [
        (
            "华懋科技2026年半年度营业收入1,091,459,912.33元。",
            {"symbol": "300476.SZ"},
        ),
        (
            "华懋科技2026年半年度营业收入1,091,459,912.33元。",
            {"company": "胜宏科技"},
        ),
        (
            "华懋科技2026年半年度营业收入1,091,459,912.33元。",
            {"symbol": "603306.SH", "ts_code": "300476.SZ"},
        ),
        (
            "华懋科技2026年半年度营业收入1,091,459,912.33元。",
            {"company": "华懋科技", "issuer_name": "胜宏科技"},
        ),
        (
            "华懋科技2026年半年度营业收入91091459912.330元。",
            {},
        ),
        (
            "华懋科技2026年半年度营业收入1091459912.33%。",
            {},
        ),
        (
            "华懋科技2025年半年度营业收入1,091,459,912.33元。",
            {},
        ),
        (
            "华懋科技2026年半年度总资产1,091,459,912.33元。",
            {},
        ),
        (
            "华懋科技2026年半年度营业收入待披露、总资产1,091,459,912.33元。",
            {},
        ),
        (
            "华懋科技2026年半年度报告。营业收入1,091,459,912.33元。",
            {},
        ),
        (
            "华懋科技2026年半年度营业收入。2026年半年度数值1,091,459,912.33元。",
            {},
        ),
    ],
    ids=[
        "wrong-document-symbol", "wrong-document-company",
        "conflicting-document-symbols", "conflicting-document-companies",
        "value-inside-larger-number", "wrong-value-unit", "wrong-period",
        "wrong-metric", "value-owned-by-adjacent-metric",
        "period-in-adjacent-atom", "metric-in-adjacent-atom",
    ],
)
def test_governing_document_fails_closed_without_one_atomic_issuer_fact(
    tmp_path, text, document_overrides,
):
    _db(tmp_path)
    snapshot = _huamao_governing_revenue_snapshot(
        text, document_overrides=document_overrides,
    )
    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    assert all(item.get("metric") != "营业收入" for item in facts)


@pytest.mark.parametrize("foreign_entity", ["子公司甲", "其他公司", "富创优越"])
def test_governing_financial_atoms_reject_explicit_foreign_entity(
    tmp_path, foreign_entity,
):
    _db(tmp_path)
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [
            {
                "evidence_id": "filing:1225505930", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "document_text": (
                    f"{foreign_entity}2026年半年度营业收入1,091,459,912.33元；"
                    f"{foreign_entity}2026年6月30日总资产6,171,145,144.82元。"
                ),
            },
            {
                "evidence_id": "filing:1225224760", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "document_text": (
                    f"{foreign_entity}2026年第一季度总资产6,008,970,568.47元。"
                ),
            },
            {
                "evidence_id": "filing:1224752345", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "document_text": (
                    f"{foreign_entity}2025年第三季度归属于上市公司股东的净资产"
                    "3,363,507,381.94元。"
                ),
            },
        ],
    }

    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    assert facts == []


@pytest.mark.parametrize(
    "target_prefix",
    ["", "富创优越为本次交易标的。", "富创优越为本次交易标的，"],
)
def test_governing_legal_facts_require_fuchuang_in_the_same_atom(
    tmp_path, target_prefix,
):
    _db(tmp_path)
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": [
            {
                "evidence_id": "filing:1225505930", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "document_text": (
                    target_prefix
                    + "截至2026年6月30日，公司尚未收购完成甲公司全部股权；"
                    "截至2026年6月30日，甲公司未实现并表。"
                ),
            },
            {
                "evidence_id": "filing:1225532560", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH", "date": "2026-08-26",
                "document_text": (
                    target_prefix
                    + "本次交易前，上市公司持有甲公司42.16%的股权；"
                    "上市公司拟购买甲公司剩余57.84%的股权；"
                    "本次交易完成后，甲公司将成为上市公司的全资子公司并纳入合并报表。"
                ),
            },
        ],
    }

    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    assert all(item.get("entity") != "富创优越" for item in facts)


def test_financial_chapter_deterministically_inserts_governing_share_payment_sentence(tmp_path):
    _db(tmp_path)
    required = (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润"
        "1.26亿元 [filing:1225505930]"
    )
    governing = [{
        "metric": "股份支付费用",
        "display_value": "1.20亿元",
        "paired_display_value": "1.26亿元",
        "required_sentence": required,
        "supporting_evidence_ids": ["filing:1225505930"],
    }]

    body = IndustryResearchService._enforce_required_governing_sentence(
        "本章先分析法定利润口径。 [filing:1225505930]",
        {"filing:1225505930"},
        governing,
        "financials",
    )

    assert required in body
    assert IndustryResearchService._production_accounting_policy_failures(body) == []


def test_financial_chapter_canonicalizes_governing_balance_facts_with_subject(tmp_path):
    _db(tmp_path)
    h1_sentence = (
        "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]"
    )
    year_end_sentence = (
        "华懋科技2025年末总资产59.94亿元 [filing:1225505930]"
    )
    h1_assets_sentence = (
        "华懋科技2026H1总资产61.71亿元 [filing:1225505930]"
    )
    governing = [
        {
            "metric": "归属于上市公司股东的净资产",
            "period": "2026H1", "display_value": "38.17亿元",
            "required_sentence": h1_sentence,
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "总资产", "period": "2025FY",
            "display_value": "59.94亿元",
            "required_sentence": year_end_sentence,
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "总资产", "period": "2026H1",
            "display_value": "61.71亿元",
            "required_sentence": h1_assets_sentence,
            "supporting_evidence_ids": ["filing:1225505930"],
        },
    ]

    body = IndustryResearchService._enforce_required_governing_sentence(
        (
            "2026H1末归母净资产为38.17亿元 [filing:1225505930]。\n\n"
            "2025年末总资产约59.94亿元 "
            "[financial:603306.SH:20251231]。\n\n"
            "2026年6月30日总资产6,171,145,144.82元 "
            "[filing:1225505930]。"
        ),
        {"filing:1225505930", "financial:603306.SH:20251231"},
        governing,
        "financials",
    )

    assert body.count(h1_sentence) == 1
    assert body.count(year_end_sentence) == 1
    assert body.count(h1_assets_sentence) == 1
    assert "6,171,145,144.82元" not in body
    assert "2025年末总资产约59.94亿元" not in body
    assert "financial:603306.SH:20251231" not in body


def _production_h1_balance_governing_facts():
    return [
        {
            "metric": "总资产", "period": "2026H1",
            "display_value": "61.71亿元",
            "required_sentence": (
                "华懋科技2026H1总资产61.71亿元 [filing:1225505930]"
            ),
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "归属于上市公司股东的净资产", "period": "2026H1",
            "display_value": "38.17亿元",
            "required_sentence": (
                "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]"
            ),
            "supporting_evidence_ids": ["filing:1225505930"],
        },
    ]


def test_final_storage_formats_and_validates_production_h1_shared_filing_sentence(tmp_path):
    _db(tmp_path)
    filler = (
        "本章其余内容只说明研究方法、证据边界与后续核验原则，"
        "不新增任何外部数字或公司事实判断。" * 70
    )
    stored = IndustryResearchService._sanitize_chapter_for_storage(
        {
            "chapter_id": "industry_position", "title": "产业位置",
            "summary": "本章按法定报告列示资产负债表事实。",
            "body_markdown": (
                "截至2026-06-30，总资产61.71亿元、归母净资产38.17亿元 "
                "[filing:1225505930]。\n\n" + filler
            ),
            "allowed_evidence_ids": ["filing:1225505930"],
            "allowed_figure_ids": [], "validation_failures": [],
            "citation_validation": {},
        },
        governing_facts=_production_h1_balance_governing_facts(),
    )

    body = stored["body_markdown"]
    assert "华懋科技2026H1总资产61.71亿元 [filing:1225505930]" in body
    assert "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]" in body
    assert "总资产61.71亿元、归母净资产38.17亿元" not in body
    assert stored["validation_failures"] == []
    assert stored["citation_validation"]["storage_formatter_applied"] is True
    assert stored["citation_validation"]["storage_validation_acceptable"] is True


@pytest.mark.parametrize(
    ("prose", "expected_failure"),
    [
        (
            "截至2026-06-30，总资产61.70亿元、归母净资产38.17亿元 "
            "[filing:1225505930]。",
            "2026H1总资产金额必须与61.71亿元一级证据一致",
        ),
        (
            "截至2025-06-30，总资产61.71亿元、归母净资产38.17亿元 "
            "[filing:1225505930]。",
            "只能绑定2026H1法定期间",
        ),
        (
            "截至2026-06-30，总资产61.71亿元、归母净资产38.17亿元 "
            "[filing:wrong]。",
            "原候选包含非白名单引用",
        ),
    ],
)
def test_final_h1_shared_filing_formatter_fails_closed(
    tmp_path, prose, expected_failure,
):
    _db(tmp_path)
    filler = "本段只陈述研究边界，不新增外部事实。" * 120
    validation = IndustryResearchService._validate_chapter_candidate(
        prose + "\n\n" + filler,
        {"filing:1225505930"},
        set(),
        governing_facts=_production_h1_balance_governing_facts(),
        chapter_id="industry_position",
    )

    assert validation["acceptable"] is False
    assert any(
        expected_failure in item
        for item in validation["validation_failures"]
    )


@pytest.mark.parametrize(
    "prose,expected_failure",
    [
        (
            "华懋科技2026H1归母净资产34.75亿元 [filing:1225224760]。",
            "2026H1归母净资产金额必须与38.17亿元一级证据一致",
        ),
        (
            "华懋科技2026H1归母净资产38.17亿元 [filing:1225224760]。",
            "2026H1归母净资产38.17亿元必须在同一事实原子引用对应H1一级证据",
        ),
        (
            "华懋科技2026H1归母净资产38.17亿元。",
            "2026H1归母净资产38.17亿元必须在同一事实原子引用对应H1一级证据",
        ),
        (
            "华懋科技2025年末总资产60.09亿元 [financial:603306.SH:20260331]。",
            "2025年末总资产金额必须与59.94亿元一级证据一致",
        ),
        (
            "华懋科技2025年末总资产59.94亿元 [financial:603306.SH:20260331]。",
            "2025年末总资产59.94亿元必须在同一事实原子引用对应年末一级证据",
        ),
        (
            "华懋科技2025年末总资产59.94亿元。",
            "2025年末总资产59.94亿元必须在同一事实原子引用对应年末一级证据",
        ),
        (
            "华懋科技2026H1总资产60.09亿元 [filing:1225224760]。",
            "2026H1总资产金额必须与61.71亿元一级证据一致",
        ),
        (
            "华懋科技2026H1总资产61.71亿元。",
            "2026H1总资产61.71亿元必须在同一事实原子引用对应H1一级证据",
        ),
    ],
)
def test_production_policy_blocks_wrong_or_uncited_governing_balance_facts(
    tmp_path, prose, expected_failure,
):
    _db(tmp_path)
    failures = IndustryResearchService._production_accounting_policy_failures(prose)
    assert expected_failure in failures


@pytest.mark.parametrize("prose", [
    "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]。",
    (
        "华懋科技2025年末总资产59.94亿元 "
        "[financial:603306.SH:20251231]。"
    ),
    "华懋科技2025年末总资产59.94亿元 [filing:1225505930]。",
    "华懋科技2026H1总资产61.71亿元 [filing:1225505930]。",
])
def test_production_policy_accepts_exact_governing_balance_fact_binding(
    tmp_path, prose,
):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


@pytest.mark.parametrize("prose", [
    (
        "华懋科技2026H1归母净资产并非38.17亿元；"
        "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]。"
    ),
    (
        "华懋科技2025年末总资产不是59.94亿元；"
        "华懋科技2025年末总资产59.94亿元 [filing:1225505930]。"
    ),
    (
        "华懋科技2026H1归母净资产待核验34.75亿元；"
        "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]。"
    ),
])
def test_production_policy_blocks_negated_or_pending_balance_counterclaims(
    tmp_path, prose,
):
    _db(tmp_path)
    assert any(
        "反向或待核验数字" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


def test_chapter_validation_does_not_silently_rewrite_bad_governing_balance_fact(
    tmp_path,
):
    _db(tmp_path)
    governing = [
        {
            "metric": "归属于上市公司股东的净资产",
            "period": "2026H1", "display_value": "38.17亿元",
            "required_sentence": (
                "华懋科技2026H1归母净资产38.17亿元 "
                "[filing:1225505930]"
            ),
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "总资产", "period": "2025FY",
            "display_value": "59.94亿元",
            "required_sentence": (
                "华懋科技2025年末总资产59.94亿元 "
                "[filing:1225505930]"
            ),
            "supporting_evidence_ids": ["filing:1225505930"],
        },
    ]
    malformed = (
        "华懋科技2026H1归母净资产34.75亿元 [filing:1225224760]。\n\n"
        "华懋科技2025年末总资产60.09亿元 [financial:603306.SH:20260331]。\n\n"
        + "本段用于维持章节长度，不改变上述法定事实口径。" * 95
    )
    result = IndustryResearchService._validate_chapter_candidate(
        malformed,
        {
            "filing:1225505930", "filing:1225224760",
            "financial:603306.SH:20260331",
        },
        set(), governing_facts=governing, chapter_id="financials",
    )
    assert result["acceptable"] is False, result
    assert any(
        "2026H1归母净资产金额" in item
        for item in result["validation_failures"]
    )
    assert any(
        "2025年末总资产金额" in item
        for item in result["validation_failures"]
    )
    assert "34.75亿元" in result["body_markdown"]
    assert "60.09亿元" in result["body_markdown"]


def test_company_chapter_guard_normalizes_all_production_accounting_boundaries(tmp_path):
    _db(tmp_path)
    governing = [
        {
            "metric": "股份支付费用",
            "display_value": "1.20亿元",
            "paired_display_value": "1.26亿元",
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "扣除股份支付影响后的归母净利润同比",
            "value": -12.15,
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "交易完成后股权状态",
            "condition": "仅在本次交易完成后成立",
            "supporting_evidence_ids": ["filing:1225532560"],
        },
        {
            "metric": "交易完成后合并范围状态",
            "condition": "仅在本次交易完成后成立",
            "supporting_evidence_ids": ["filing:1225532560"],
        },
    ]
    prose = (
        "8月20日PE(TTM)为168.34倍 [market:a]，8月31日为246.92倍 [market:b]。\n\n"
        "2026H1股份支付费用约1.2亿元、调整后归母净利润1.26亿元，"
        "说明汽车主业承压 "
        "[filing:1225505930]。\n\n"
        "26.13亿元乘57.84%得到新增商誉15.11亿元。\n\n"
        "富创优越已经成为全资子公司并纳入合并报表。"
    )

    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {
            "market:a", "market:b", "filing:1225505930",
            "filing:1225532560",
        },
        governing,
    )

    assert "差异待核验" in guarded
    assert "股份支付费用1.20亿元" in guarded
    assert "扣除股份支付影响后的归母净利润1.26亿元" in guarded
    assert "说明汽车主业承压" not in guarded
    assert "不能由交易对价或持股比例直接计算" in guarded
    assert "可辨认净资产公允价值" in guarded
    assert "完成PPA后确认" in guarded
    assert "新增商誉15.11亿元" not in guarded
    assert "若本次交易完成，富创优越将成为全资子公司并纳入合并报表" in guarded
    assert "[filing:1225532560]" in guarded
    assert IndustryResearchService._production_accounting_policy_failures(guarded) == []


def test_company_chapter_guard_rejects_uncited_h1_attribution_and_rewrites_unbound_valuation(tmp_path):
    _db(tmp_path)
    governing = [
        {
            "metric": "股份支付费用",
            "display_value": "1.20亿元",
            "paired_display_value": "1.26亿元",
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "扣除股份支付影响后的归母净利润同比",
            "value": -12.15,
            "supporting_evidence_ids": ["filing:1225505930"],
        },
    ]
    prose = (
        "华懋科技当前价值创造仍依赖汽车零部件主业，2026年上半年合并口径盈利显著承压，"
        "股份支付费用是表观利润下滑的主要解释，但调整后利润仍同比负增长。\n\n"
        "富创优越收益法评估值26.13亿元对应的敏感性区间较宽。"
    )

    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        prose,
        {"filing:1225505930", "filing:1225532560"},
        governing,
    )

    assert "当前价值创造仍依赖汽车零部件主业" in guarded
    assert "股份支付费用1.20亿元" not in guarded
    assert any(
        "必须有逐句一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(guarded)
    )
    assert "评估值26.13亿元" not in guarded
    assert "具体参数须回到交易报告和同日行情逐项核对" in guarded


def test_company_chapter_guard_canonicalizes_cited_statutory_atoms_and_removes_forbidden_attribution(tmp_path):
    _db(tmp_path)
    governing = [
        {
            "metric": "股份支付费用",
            "display_value": "1.20亿元",
            "paired_display_value": "1.26亿元",
            "supporting_evidence_ids": ["filing:1225505930"],
        },
        {
            "metric": "扣除股份支付影响后的归母净利润同比",
            "value": -12.15,
            "supporting_evidence_ids": ["filing:1225505930"],
        },
    ]
    prose = (
        "2026H1股份支付费用1.2亿元，扣除股份支付影响后的归母净利润"
        "125,897,911.25元，同比下降12.15%，说明汽车主业内生增长乏力 "
        "[filing:1225505930]。"
    )

    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        prose, {"filing:1225505930"}, governing,
    )

    assert "股份支付费用1.20亿元" in guarded
    assert "扣除股份支付影响后的归母净利润1.26亿元" in guarded
    assert "同比下降12.15%" in guarded
    assert "不能据此归因于任何单一业务板块" in guarded
    assert "汽车主业内生增长乏力" not in guarded
    assert IndustryResearchService._production_accounting_policy_failures(guarded) == []


def test_company_chapter_guard_canonicalizes_correct_split_pair(tmp_path):
    _db(tmp_path)
    governing = [{
        "metric": "股份支付费用",
        "display_value": "1.20亿元",
        "paired_display_value": "1.26亿元",
        "supporting_evidence_ids": ["filing:1225505930"],
    }]
    prose = (
        "2026H1，股份支付费用1.20亿元 [filing:1225505930]。"
        "2026H1，扣除股份支付影响后的归母净利润1.26亿元 "
        "[filing:1225505930]。"
    )
    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        prose, {"filing:1225505930"}, governing,
    )

    assert "2026H1，股份支付费用1.20亿元" in guarded
    assert "扣除股份支付影响后的归母净利润1.26亿元" in guarded


@pytest.mark.parametrize("prose,expected_failure", [
    (
        "股份支付费用1.20万元，扣除股份支付影响后的归母净利润1.26亿元 "
        "[filing:1225505930]。",
        "不得改写为错误单位",
    ),
    (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润1.26亿元。",
        "必须逐句引用filing:1225505930",
    ),
])
def test_company_chapter_guard_does_not_repair_wrong_unit_or_uncited_atoms(
    tmp_path, prose, expected_failure,
):
    _db(tmp_path)
    governing = [{
        "metric": "股份支付费用",
        "display_value": "1.20亿元",
        "paired_display_value": "1.26亿元",
        "supporting_evidence_ids": ["filing:1225505930"],
    }]
    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        prose, {"filing:1225505930"}, governing,
    )
    assert guarded == prose
    assert any(
        expected_failure in item
        for item in IndustryResearchService._production_accounting_policy_failures(guarded)
    )


@pytest.mark.parametrize("question_list", [
    (
        "现有证据尚不足以判断：（1）光通信业务2026年能否形成收入贡献？"
        "（2）越南产线2026年产能利用率需核验；"
        "（3）2026年上半年利润总额降幅原因如何拆分？"
    ),
    (
        "- 富创优越2024-2025年度经审计收入与利润口径是否一致？\n"
        "- 2026年预测数据与实际订单之间的偏差如何验证？"
    ),
    (
        "1. 若本次交易完成，合并范围何时变化？\n"
        "2. 越南产线2026年爬坡进度如何核验？\n"
        "3. 2026年上半年股份支付费用的会计处理细节需确认？"
    ),
])
def test_research_question_ledgers_do_not_inflate_numeric_citation_denominator(
    tmp_path, question_list,
):
    _db(tmp_path)
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(
        question_list
    ) is True
    assert IndustryResearchService._is_auditable_numeric_fact(question_list) is False


def test_completed_fact_inside_question_style_list_remains_auditable(tmp_path):
    _db(tmp_path)
    prose = (
        "仍需核验的问题：（1）公司2026年已实现营业收入20亿元；"
        "（2）后续增长原因如何验证？"
    )

    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is False
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is True


def test_chapter_guard_does_not_authorize_filing_replacement_without_governing_ledger(tmp_path):
    _db(tmp_path)
    original = "富创优越已经成为全资子公司并纳入合并报表。"

    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        original,
        {"filing:1225532560"},
        [],
    )

    assert guarded == original
    assert IndustryResearchService._production_accounting_policy_failures(guarded)


def test_chapter_summary_neutralizes_single_pe_endpoint_for_cross_chapter_comparison(tmp_path):
    _db(tmp_path)
    summary, audit = IndustryResearchService._citation_safe_chapter_summary(
        "2026年8月31日PE(TTM)为246.92倍 [market:latest]。",
        "2026年8月31日PE(TTM)为246.92倍 [market:latest]。",
        {"market:latest"},
    )

    assert "差异待核验" in summary
    assert "不得据此推导业务原因或估值趋势" in summary
    assert audit["numeric_cited"] is True


def test_production_policy_requires_cross_date_pe_caveat_in_same_paragraph(tmp_path):
    _db(tmp_path)
    unsafe = "8月20日PE(TTM)为168.34倍，8月31日为246.92倍。"
    safe = unsafe + " 各日期PE(TTM)仅作中性列示，差异待核验。"

    assert any(
        "同一段明确标注差异待核验" in item
        for item in IndustryResearchService._production_accounting_policy_failures(unsafe)
    )
    assert IndustryResearchService._production_accounting_policy_failures(safe) == []


def test_pe_observation_extractor_never_treats_date_as_valuation_multiple(tmp_path):
    _db(tmp_path)
    prose = "PE(TTM)，2026年8月20日为168.34倍；2026年8月31日为246.92倍。"

    values = IndustryResearchService._pe_observation_values(prose)

    assert values == {"168.34", "246.92"}
    assert "2026" not in values


def test_production_policy_requires_share_payment_pair_in_one_fact_atom(tmp_path):
    _db(tmp_path)
    split = (
        "股份支付费用1.20亿元 [filing:1225505930]。"
        "扣除股份支付影响后的归母净利润1.26亿元 [filing:1225505930]。"
    )
    paired = (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润1.26亿元 "
        "[filing:1225505930]。"
    )

    assert any(
        "同一事实原子成对列示" in item
        for item in IndustryResearchService._production_accounting_policy_failures(split)
    )
    assert IndustryResearchService._production_accounting_policy_failures(paired) == []


def test_chapter_summary_replaces_uncited_share_payment_number_with_cited_governing_sentence(tmp_path):
    _db(tmp_path)
    required = (
        "2026H1，股份支付费用1.20亿元，扣除股份支付影响后的归母净利润"
        "1.26亿元 [filing:1225505930]。"
    )

    summary, audit = IndustryResearchService._citation_safe_chapter_summary(
        "股份支付费用约1.2亿元，调整后利润约1.26亿元。",
        required,
        {"filing:1225505930"},
    )

    assert "股份支付费用1.20亿元" in summary
    assert "扣除股份支付影响后的归母净利润1.26亿元" in summary
    assert "[filing:1225505930]" in summary
    assert audit["numeric_cited"] is True
    assert audit["derived_from_body"] is True


def test_chapter_summary_rechecks_policy_after_fallback_excerpt_truncation(tmp_path):
    _db(tmp_path)
    body = (
        "本章先说明研究范围与证据口径。" * 35
        + "2026H1，股份支付费用1.20亿元，"
        + "本句中间包含较长的口径解释。" * 12
        + "扣除股份支付影响后的归母净利润1.26亿元 "
        + "[filing:1225505930]。"
    )
    summary, audit = IndustryResearchService._citation_safe_chapter_summary(
        "股份支付费用约1.2亿元，调整后利润约1.26亿元。",
        body,
        {"filing:1225505930"},
    )
    assert IndustryResearchService._production_accounting_policy_failures(summary) == []
    assert not ("1.20亿元" in summary and "1.26亿元" not in summary)
    assert audit["numeric_removed"] is True


def test_executive_summary_rechecks_pe_and_share_payment_after_240_char_excerpt(tmp_path):
    _db(tmp_path)
    pe_body = (
        "2026年8月20日PE(TTM)为168.34倍，2026年8月31日为246.92倍；"
        + "本段继续说明估值快照只用于日期观察。" * 18
        + "各日期PE(TTM)仅作中性列示，差异待核验；"
        "不得据此推导业务原因或估值趋势 [market:pe]。"
    )
    share_body = (
        "本章先说明法定披露、非GAAP口径和使用限制。" * 14
        + "2026H1，股份支付费用1.20亿元，"
        "扣除股份支付影响后的归母净利润1.26亿元 "
        "[filing:1225505930]。"
    )
    summary = IndustryResearchService._validated_executive_summary([
        {
            "title": "估值", "body_markdown": pe_body,
            "allowed_evidence_ids": ["market:pe"],
        },
        {
            "title": "财务", "body_markdown": share_body,
            "allowed_evidence_ids": ["filing:1225505930"],
        },
    ])
    assert IndustryResearchService._production_accounting_policy_failures(summary) == []
    if "168.34倍" in summary or "246.92倍" in summary:
        assert "差异待核验" in summary
    assert not ("1.20亿元" in summary and "1.26亿元" not in summary)


@pytest.mark.parametrize("prose,expected", [
    ("PE(TTM)从246.92倍变化，主因是高基数退出 [valuation:1]。", "高基数退出"),
    ("越南产线爬坡贡献收入1.20亿元 [event:1]。", "越南爬坡"),
    ("26.13亿元乘57.84%得到新增商誉15.11亿元 [filing:1]。", "新增商誉"),
    ("2025年末归母净资产33.64亿元 [financial:1]。", "2025Q3"),
    ("2025年末总资产5.99亿元 [financial:1]。", "总资产"),
    ("未并表，所以对法定财务影响为零 [filing:1]。", "法定财务影响为零"),
    ("调整后净利润1.26亿元证明主营业务增长乏力 [filing:1225505930]。", "汽车主业"),
])
def test_production_accounting_policy_rejects_known_huamao_false_inferences(tmp_path, prose, expected):
    _db(tmp_path)
    failures = IndustryResearchService._production_accounting_policy_failures(prose)
    assert failures
    assert any(expected in item for item in failures)


@pytest.mark.parametrize("prose", [
    "PE(TTM)上升由TTM盈利分母收缩驱动。",
    "TTM盈利分母收缩导致PE(TTM)上升。",
    "PE主要反映TTM分母端变化。",
    "不能忽视价格波动，但PE上升由分母收缩驱动。",
])
def test_production_accounting_policy_rejects_pe_denominator_causality_in_both_orders(
    tmp_path, prose,
):
    _db(tmp_path)
    assert any(
        "分母变化方向" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "不能将PE变化归因于分母收缩。",
    "PE差异待核验，不得解释为高基数退出。",
    "未并表并不等于法定财务影响为零。",
    "1.26亿元不足以证明汽车主业内生增长乏力 [filing:1225505930]。",
])
def test_production_accounting_policy_preserves_explicit_prohibitions(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_production_policy_allows_explicit_goodwill_nonconstruction(tmp_path):
    _db(tmp_path)
    prose = (
        "交易对价26.13亿元与拟收购股权比例57.84%的乘积不构成商誉估算；"
        "商誉金额须待购买价分摊和法定披露确认。"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_production_policy_allows_its_own_two_sentence_goodwill_boundary(tmp_path):
    _db(tmp_path)
    prose = (
        "交易对价26.13亿元与拟收购股权比例57.84%的乘法不等于新增商誉，"
        "也不得被称为商誉估算。"
        "商誉必须等待购买价分摊、可辨认净资产公允价值和交易完成后的法定披露。"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_production_policy_allows_directly_equal_goodwill_boundary_from_real_report(
    tmp_path,
):
    _db(tmp_path)
    prose = (
        "交易对价26.13亿元与拟收购股权比例57.84%的乘积不得直接等同于新增商誉；"
        "商誉金额须待购买价分摊、可辨认净资产公允价值确定及交易完成后法定披露方可确认 "
        "[filing:1225532560]。"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


@pytest.mark.parametrize("prose", [
    (
        "本次交易不构成商誉。"
        "商誉必须等待购买价分摊和法定披露。"
    ),
    (
        "交易对价26.13亿元与拟收购股权比例57.84%的乘法得到新增商誉15.11亿元。"
        "商誉必须等待购买价分摊和法定披露。"
    ),
])
def test_goodwill_adjacent_ppa_sentence_does_not_sanitize_unsafe_claim(
    tmp_path, prose,
):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose)


def test_production_policy_does_not_accept_unrelated_negation_before_goodwill_formula(tmp_path):
    _db(tmp_path)
    prose = "该事项不构成业绩承诺，但26.13亿元乘57.84%得到新增商誉15.11亿元。"
    assert any(
        "新增商誉" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "PE(TTM)由168倍跃升至233倍以上；差异待核验。",
    "PE(TTM)飙升至246.92倍，可能为数据口径差异。",
    "不能否认PE(TTM)由168倍跃升至233倍。",
    "PE(TTM)由168倍激增至233倍，差异待核验。",
])
def test_production_policy_rejects_pe_direction_words_even_with_caveat(tmp_path, prose):
    _db(tmp_path)
    assert any(
        "方向词" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "8月20日PE(TTM)为168倍，8月31日跃升至233倍；差异待核验。",
    "市盈率方面，8月31日升至233倍。",
    "8月20日PE为168倍，8月31日，估值升至233倍。",
    "不能说PE(TTM)没有跃升至233倍。",
    "不能认为PE(TTM)没有跃升至233倍。",
    "不能排除PE(TTM)跃升至233倍。",
    "PE(TTM)从168倍涨到233倍。",
    "PE(TTM)从168倍上升到233倍。",
    "PE(TTM)提高到233倍。",
    "PE(TTM)从168倍扩大至233倍，差异待核验。",
])
def test_production_policy_rejects_pe_direction_after_context_only_atom(
    tmp_path, prose,
):
    _db(tmp_path)
    assert any(
        "方向词" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "8月20日PE(TTM)为168倍，8月31日为246.92倍，差异待核验。",
    "不得把两个交易日的PE(TTM)差异写成跃升。",
    "PE(TTM)为168倍，营业收入显著上升。",
])
def test_production_policy_allows_neutral_or_explicitly_prohibited_pe_wording(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_production_policy_requires_q3_equity_to_cite_q3_evidence(tmp_path):
    _db(tmp_path)
    wrong = (
        "2025年第三季度末归属于上市公司股东的净资产为33.64亿元 "
        "[filing:1225505930]。"
    )
    correct = (
        "2025年第三季度末归属于上市公司股东的净资产为33.64亿元 "
        "[filing:1224752345]。"
    )
    assert any(
        "Q3一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(wrong)
    )
    assert IndustryResearchService._production_accounting_policy_failures(correct) == []


def test_year_end_total_asset_comparison_does_not_relabel_following_h1_equity(
    tmp_path,
):
    _db(tmp_path)
    prose = (
        "截至2026年6月30日，华懋科技总资产61.71亿元 "
        "[filing:1225505930]，较2025年末59.94亿元 "
        "[financial:603306.SH:20251231]增长约2.95%；归属于上市公司股东的"
        "所有者权益38.17亿元 [filing:1225505930]。"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_conditional_consolidation_requires_same_sentence_condition_and_filing(tmp_path):
    _db(tmp_path)
    unsafe = (
        "在此情景下，2026H2起富创优越纳入合并报表，上市公司营业收入与"
        "资产规模将扩张。"
    )
    failures = IndustryResearchService._production_accounting_policy_failures(unsafe)
    assert any("条件必须在同一句明示" in item for item in failures)
    assert any("逐句引用filing:1225532560" in item for item in failures)


def test_conditional_consolidation_accepts_same_sentence_condition_and_filing(tmp_path):
    _db(tmp_path)
    safe = (
        "若本次交易完成，富创优越将成为全资子公司并纳入合并报表 "
        "[filing:1225532560]。"
    )
    assert IndustryResearchService._production_accounting_policy_failures(safe) == []


@pytest.mark.parametrize("prose", [
    "不得忽视：富创优越已经成为全资子公司并纳入合并报表。",
    (
        "如果交易不完成，富创优越将成为全资子公司并纳入合并报表 "
        "[filing:1225532560]。"
    ),
    (
        "若交易完成传闻被证伪，富创优越将成为全资子公司并纳入合并报表 "
        "[filing:1225532560]。"
    ),
])
def test_conditional_consolidation_rejects_negation_and_false_condition_bypasses(
    tmp_path, prose,
):
    _db(tmp_path)
    assert any(
        "条件必须在同一句明示" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "截至当前，富创优越尚未纳入合并报表 [filing:1225505930]。",
    "仍需核验富创优越是否已纳入合并报表。",
    "不得断言富创优越已经成为全资子公司并纳入合并报表。",
])
def test_conditional_consolidation_allows_true_negative_question_or_nonassertion(
    tmp_path, prose,
):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


@pytest.mark.parametrize("prose", [
    "标的公司已成为全资子公司并纳入合并报表。",
    "该公司已完成并表。",
    "富创公司已成为全资子公司并纳入合并报表。",
    (
        "富创优越（以下简称标的公司）尚在交易中。"
        "标的公司已经纳入合并报表。"
    ),
])
def test_conditional_consolidation_rejects_fuchuang_coreference_bypasses(
    tmp_path, prose,
):
    _db(tmp_path)
    failures = IndustryResearchService._production_accounting_policy_failures(prose)
    assert any("条件必须在同一句明示" in item for item in failures)
    assert any("逐句引用filing:1225532560" in item for item in failures)


@pytest.mark.parametrize("prose", [
    (
        "若本次交易完成，标的公司将成为全资子公司并纳入合并报表 "
        "[filing:1225532560]。"
    ),
    "截至当前，该公司尚未纳入合并报表。",
    "标的公司是否会纳入合并报表？",
    "不得断言富创公司已经成为全资子公司并纳入合并报表。",
])
def test_conditional_consolidation_coreferences_keep_safe_boundaries(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


@pytest.mark.parametrize("prose", [
    "录音转写因安全投影尚未生成，仅保留来源索引 [audio:task-1]。",
    "录音与机构段子均处于安全投影未生成状态 [audio:task-1]。",
])
def test_production_policy_rejects_false_audio_projection_unavailable_claim(
    tmp_path, prose,
):
    _db(tmp_path)
    assert any(
        "录音在进入报告前已生成安全投影" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("period", [
    "截至2025年9月30日", "截至2025年9月底", "截至2025年三季末", "截至2025三季度末",
])
def test_q3_equity_period_persists_across_intermediate_atoms_and_aliases(
    tmp_path, period,
):
    _db(tmp_path)
    wrong = (
        f"{period}，公司财务状况如下，总资产61亿元，"
        "归属于母公司所有者权益33.64亿元 [filing:1225505930]。"
    )
    correct = (
        f"{period}，公司财务状况如下，总资产61亿元，"
        "归属于母公司所有者权益33.64亿元 [filing:1224752345]。"
    )
    assert any(
        "Q3一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(wrong)
    )
    assert IndustryResearchService._production_accounting_policy_failures(correct) == []


def test_q3_equity_markdown_row_binds_header_unit_and_row_citation(tmp_path):
    _db(tmp_path)
    wrong = (
        "| 期间 | 指标 | 数值（亿元） | 证据 |\n"
        "| --- | --- | ---: | --- |\n"
        "| 2025Q3 | 归母净资产 | 33.64 | [filing:1225505930] |"
    )
    correct = wrong.replace("1225505930", "1224752345")
    assert any(
        "Q3一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(wrong)
    )
    assert IndustryResearchService._production_accounting_policy_failures(correct) == []


def test_q3_equity_cannot_borrow_correct_citation_from_other_metric_same_sentence(tmp_path):
    _db(tmp_path)
    prose = (
        "2025年第三季度营业收入10亿元 [filing:1224752345]及"
        "归母净资产33.64亿元 [filing:other]。"
    )
    assert any(
        "同一事实原子" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("connector", ["与", "和", "、"])
def test_q3_equity_rejects_any_foreign_citation_even_with_unsplit_connector(
    tmp_path, connector,
):
    _db(tmp_path)
    prose = (
        f"2025年第三季度营业收入10亿元 [filing:1224752345]{connector}"
        "归母净资产33.64亿元 [filing:other]。"
    )
    assert any(
        "同一事实原子" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


def test_q3_equity_cannot_borrow_the_only_correct_citation_from_revenue(tmp_path):
    _db(tmp_path)
    prose = (
        "2025年第三季度营业收入10亿元 [filing:1224752345]与"
        "归母净资产33.64亿元。"
    )
    table = (
        "| 期间 | 营业收入 | 归母净资产 |\n"
        "| --- | ---: | ---: |\n"
        "| 2025Q3 | 10亿元 [filing:1224752345] | 33.64亿元 |"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose)
    assert IndustryResearchService._production_accounting_policy_failures(table)


@pytest.mark.parametrize("value", ["33.6亿元", "33亿6400万元"])
def test_q3_equity_rounded_or_compound_value_still_requires_bound_q3_citation(
    tmp_path, value,
):
    _db(tmp_path)
    wrong = f"2025Q3归母净资产{value} [filing:other]。"
    correct = f"2025Q3归母净资产{value} [filing:1224752345]。"
    assert IndustryResearchService._production_accounting_policy_failures(wrong)
    assert IndustryResearchService._production_accounting_policy_failures(correct) == []


def test_q3_equity_value_cannot_borrow_canonical_amount_from_other_metric(tmp_path):
    _db(tmp_path)
    prose = (
        "2025Q3归母净资产35亿元与营业收入33.64亿元 "
        "[filing:1224752345]。"
    )
    table = (
        "| 期间 | 归母净资产 | 营业收入 | 权益证据 |\n"
        "| --- | ---: | ---: | --- |\n"
        "| 2025Q3 | 35亿元 | 33.64亿元 | [filing:1224752345] |"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose)
    assert IndustryResearchService._production_accounting_policy_failures(table)


def test_q3_equity_wide_period_table_binds_the_q3_value_column(tmp_path):
    _db(tmp_path)
    wrong = (
        "| 指标 | 2025Q3 | 2024年末 | 证据 |\n"
        "| --- | ---: | ---: | --- |\n"
        "| 归母净资产（亿元） | 35.00 | 33.64 | [filing:1224752345] |"
    )
    correct = wrong.replace("35.00 | 33.64", "33.64 | 35.00")
    assert IndustryResearchService._production_accounting_policy_failures(wrong)
    assert IndustryResearchService._production_accounting_policy_failures(correct) == []


@pytest.mark.parametrize(
    "unit,wrong_value,correct_value",
    [("亿元", "35.00", "33.64"), ("万元", "350000", "336400")],
)
def test_q3_equity_binds_immediately_preceding_external_table_unit(
    tmp_path, unit, wrong_value, correct_value,
):
    _db(tmp_path)
    template = (
        f"单位：{unit}\n\n"
        "| 期间 | 归母净资产 | 证据 |\n"
        "| --- | ---: | --- |\n"
        "| 2025Q3 | {value} | [filing:1224752345] |"
    )
    wrong = template.format(value=wrong_value)
    correct = template.format(value=correct_value)
    assert any(
        "33.64亿元一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(wrong)
    )
    assert IndustryResearchService._production_accounting_policy_failures(correct) == []


def test_external_table_unit_does_not_cross_intervening_prose(tmp_path):
    _db(tmp_path)
    prose = (
        "单位：亿元\n\n"
        "下文表格另有独立口径说明。\n\n"
        "| 期间 | 归母净资产 | 证据 |\n"
        "| --- | ---: | --- |\n"
        "| 2025Q3 | 35.00 | [filing:1224752345] |"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_year_end_growth_binds_immediately_preceding_external_percent_unit(tmp_path):
    _db(tmp_path)
    table = (
        "单位：%\n\n"
        "| 期间 | 归母净资产增长 |\n"
        "| --- | ---: |\n"
        "| 2025年末 | 11.28 |"
    )
    assert any(
        "11.28%增长" in item
        for item in IndustryResearchService._production_accounting_policy_failures(table)
    )


@pytest.mark.parametrize("period", [
    "2025财年第三季度", "2025年9月末", "2025年前三季度末",
])
def test_q3_equity_period_aliases_are_normalized(tmp_path, period):
    _db(tmp_path)
    prose = f"{period}母公司所有者权益33.64亿元 [filing:other]。"
    assert any(
        "Q3一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    (
        "2026H1归母净资产38.17亿元 [filing:1224752345]，"
        "2025年第三季度末归母净资产33.64亿元 [filing:1225505930]。"
    ),
    (
        "截至2025-09-30，归属于上市公司股东的所有者权益为336400万元 "
        "[filing:1225505930]。"
    ),
])
def test_q3_equity_cannot_borrow_citation_or_bypass_with_unit_conversion(tmp_path, prose):
    _db(tmp_path)
    assert any(
        "同一事实原子" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose,expected", [
    (
        "2025年末归母净资产34.30亿元 [financial:603306.SH:20251231]。",
        "34.30亿元",
    ),
    (
        "归母净资产较2025年末增长约11.28%。",
        "11.28%增长",
    ),
    (
        "截至2025-12-31，归属于上市公司股东的所有者权益为343000万元。",
        "34.30亿元",
    ),
    (
        "较2025年末，归母净资产增长11.28%。",
        "11.28%增长",
    ),
])
def test_production_policy_rejects_unsupported_year_end_equity_and_derivative(
    tmp_path, prose, expected,
):
    _db(tmp_path)
    assert any(
        expected in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("period", ["2025年末", "2025年年底", "2025财年末"])
def test_year_end_equity_requires_filing_atom_and_derivative_stays_blocked(
    tmp_path, period,
):
    _db(tmp_path)
    amount = (
        f"截至{period}，公司资产结构如下，总资产59.94亿元，"
        "归属于母公司所有者权益34.30亿元。"
    )
    growth = f"较{period}，公司资产结构有所变化，归母权益增长约11.3%。"
    assert any(
        "2025年末归母净资产34.30亿元必须在同一事实原子引用对应一级证据" in item
        for item in IndustryResearchService._production_accounting_policy_failures(amount)
    )
    assert any(
        "百分比增长" in item
        for item in IndustryResearchService._production_accounting_policy_failures(growth)
    )


@pytest.mark.parametrize("period", ["2025会计年度末", "2025年12月底"])
def test_year_end_equity_period_alias_and_header_unit_are_blocked(tmp_path, period):
    _db(tmp_path)
    prose = f"截至{period}，母公司所有者权益（亿元）34.30。"
    table = (
        "| 期间 | 指标 | 数值（亿元） |\n"
        "| --- | --- | ---: |\n"
        f"| {period} | 母公司所有者权益 | 34.30 |"
    )
    assert IndustryResearchService._production_accounting_policy_failures(prose)
    assert IndustryResearchService._production_accounting_policy_failures(table)


def test_year_end_equity_growth_markdown_header_percent_is_blocked(tmp_path):
    _db(tmp_path)
    table = (
        "| 期间 | 归母净资产增长（%） |\n"
        "| --- | ---: |\n"
        "| 2025年末 | 11.28 |"
    )
    assert any(
        "11.28%增长" in item
        for item in IndustryResearchService._production_accounting_policy_failures(table)
    )


@pytest.mark.parametrize("prose", [
    "当前一级证据不支持截至2025-12-31归母净资产34.30亿元，该值已删除。",
    "不得采用较2025年末归母净资产增长11.28%的说法。",
    (
        "26.13亿元与57.84%的乘积不足以确认商誉估算；"
        "商誉金额须待购买价分摊和法定披露确认。"
    ),
])
def test_production_policy_preserves_explicit_rejection_language(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_goodwill_double_negation_never_counts_as_safe_boundary(tmp_path):
    _db(tmp_path)
    prose = "不能否认26.13亿元乘57.84%并非不构成新增商誉15.11亿元。"
    assert any(
        "新增商誉" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "26.13亿元乘57.84%未必不构成新增商誉15.11亿元。",
    "26.13亿元乘57.84%不能说不构成商誉。",
    "26.13亿元乘57.84%不能认为不构成商誉。",
    "26.13亿元乘57.84%不一定不构成商誉。",
    "26.13亿元乘57.84%难言不构成商誉。",
    "26.13亿元乘57.84%无法断定不构成商誉。",
    "26.13亿元乘57.84%不能断言不构成商誉。",
])
def test_goodwill_ambiguous_double_negation_never_counts_as_safe_boundary(
    tmp_path, prose,
):
    _db(tmp_path)
    assert any(
        "新增商誉" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "本次交易预计新增商誉约15.1亿元 [filing:1225505930]。",
    "交易对价26.13亿元乘57.84%，本次收购不构成商誉。",
])
def test_goodwill_requires_complete_formula_and_ppa_safety_boundary(tmp_path, prose):
    _db(tmp_path)
    assert any(
        "新增商誉" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "本次交易构成商誉。",
    "本次交易不构成商誉。",
    "本次收购不会形成商誉。",
    "本次交易将形成商誉。",
    "本次并购将产生商誉。",
])
def test_goodwill_classification_requires_primary_statutory_evidence(tmp_path, prose):
    _db(tmp_path)
    assert any(
        "不得断言交易构成或不构成商誉" in item
        for item in IndustryResearchService._production_accounting_policy_failures(prose)
    )


@pytest.mark.parametrize("prose", [
    "目前无法判断本次交易是否构成商誉。",
    "不应断言本次交易不构成商誉。",
    (
        "股权比例乘以交易价款不是商誉估算；"
        "最终商誉需以PPA或法定披露为准。"
    ),
    (
        "26.13亿元交易对价与57.84%股权比例的乘积不得等同于"
        "商誉/新增商誉；商誉须待PPA、可辨认净资产、公允价值及法定披露。"
    ),
])
def test_goodwill_uncertainty_warnings_and_complete_boundary_are_allowed(
    tmp_path, prose,
):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose) == []


def test_goodwill_double_negation_is_not_an_uncertainty_warning(tmp_path):
    _db(tmp_path)
    prose = "不能否认本次交易不构成商誉。"
    assert IndustryResearchService._production_accounting_policy_failures(prose)


@pytest.mark.parametrize("prose", [
    "股份支付费用1.200亿元 [filing:1225505930]。",
    "股份支付费用12000万元 [filing:1225505930]。",
    "扣除股份支付影响后的归母净利润1.260亿元 [filing:1225505930]。",
])
def test_governing_share_payment_display_precision_cannot_be_bypassed(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._production_accounting_policy_failures(prose)


def test_periodic_financial_facts_compute_canonical_period_and_display(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "cutoff": "2026-09-01T00:00:00",
        "evidence": [
            {
                "evidence_id": "financial:603306.SH:20250930",
                "kind": "financial_statement", "symbol": "603306.SH",
            },
            {
                "evidence_id": "financial:603306.SH:20251231",
                "kind": "financial_statement", "symbol": "603306.SH",
            },
        ],
        "financial_series": [
            {
                "period": "20250930", "period_label": "2025年末（错误上游标签）",
                "period_basis": "YTD_9M", "attributable_equity": 3_364_000_000,
            },
            {
                "period": "20251231", "period_basis": "ANNUAL",
                "total_assets": 5_994_000_000,
            },
        ],
    }
    spec = {"required_structured_blocks": ["subject_financial_periods"]}

    facts = IndustryResearchService._chapter_periodic_financial_facts(
        snapshot,
        {"financial:603306.SH:20250930", "financial:603306.SH:20251231"},
        spec,
    )
    by_period = {item["period"]: item for item in facts}

    assert by_period["20250930"]["period_label"] == "2025年第三季度末（YTD_9M）"
    assert by_period["20250930"]["canonical_display"]["attributable_equity"] == "33.64亿元"
    assert by_period["20251231"]["period_label"] == "2025年末（ANNUAL）"
    assert by_period["20251231"]["canonical_display"]["total_assets"] == "59.94亿元"


def test_governing_facts_and_filing_ids_reach_chapter_payload(tmp_path):
    _db(tmp_path)
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "research_contract": {"subject_name": "华懋科技", "symbol": "603306.SH"},
        "data_quality": {}, "fact_ledger": [], "financial_series": [],
        "valuation_series": [], "market_series": [],
        "evidence": [
            {"evidence_id": "event:1", "kind": "event", "title": "普通事件", "summary": "普通事件"},
            {"evidence_id": "filing:1225505930", "kind": "filing_text", "title": "半年报", "summary": "法定正文"},
            {"evidence_id": "filing:1225532560", "kind": "filing_text", "title": "交易报告", "summary": "法定正文"},
        ],
        "governing_statutory_facts": [
            {
                "entity": "华懋科技", "metric": "营业收入", "value": 1_091_459_912.33,
                "unit": "元", "period": "2026H1", "period_basis": "YTD_H1",
                "metric_basis": "statutory_gaap", "basis": "半年报",
                "usage_scope": "current_governing",
                "evidence_ids": ["filing:1225505930"],
                "supporting_evidence_ids": ["filing:1225505930"],
            },
            {
                "entity": "富创优越", "metric": "拟收购股权比例", "value": 57.84,
                "unit": "%", "period": "2026-08-26", "period_basis": "PROPOSED_TRANSACTION",
                "metric_basis": "proposed_acquisition", "basis": "交易报告",
                "usage_scope": "proposed_only", "condition": "非当前持股比例",
                "evidence_ids": ["filing:1225532560"],
                "supporting_evidence_ids": ["filing:1225532560"],
            },
        ],
    }
    spec = {"chapter_id": "company_scope", "title": "公司边界", "focus": "对象", "keywords": []}
    payload = IndustryResearchService._chapter_model_payload(
        "华懋科技", "公司研究", snapshot, spec, [snapshot["evidence"][0]],
    )

    assert {"filing:1225505930", "filing:1225532560"}.issubset(payload["allowed_evidence_ids"])
    assert len(payload["governing_statutory_facts"]) == 2
    assert all(
        set(item["supporting_evidence_ids"]).issubset(payload["allowed_evidence_ids"])
        for item in payload["governing_statutory_facts"]
    )
    assert "historical_only" in payload["structured_context_instruction"]


def test_statuory_table_row_applies_declared_unit_only_to_current_value(tmp_path):
    _db(tmp_path)
    claims = IndustryResearchService._statutory_table_claims(
        "单位：元 币种：人民币 主要会计数据 本报告期（1－6月） 上年同期 本期同比(%) "
        "扣除股份支付影响后的净利润 125,897,911.25 143,317,500.70 -12.15",
        desired_metrics={"adjusted_net_profit"},
    )

    assert len(claims) == 1
    assert claims[0]["numbers"] == {"125897911.25|currency"}
    assert "143317500.7|currency" not in claims[0]["numbers"]
    assert "-12.15|percent" not in claims[0]["numbers"]


def test_statutory_table_row_never_relabels_explicit_subsidiary_as_issuer(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "filing_documents": [{
            "announcement_id": "SUBSIDIARY-ROW",
            "report_period": "2026-06-30",
            "document_text": (
                "2026年半年度报告。单位：元 主要财务数据 "
                "富创优越归母净利润 218,000,000.00 100,000,000.00"
            ),
        }],
        "evidence": [
            {
                "evidence_id": "filing:SUBSIDIARY-ROW", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "report_period": "2026-06-30", "summary": "半年报正文见附件。",
            },
            {
                "evidence_id": "audio:WRONG-ENTITY", "kind": "audio_transcript",
                "company": "华懋科技", "symbol": "603306.SH",
                "summary": "华懋科技2026H1归母净利润2.18亿元。",
            },
        ],
    }

    candidates = IndustryResearchService._primary_precedence_candidates(snapshot)
    table_candidates = [
        item for item in candidates
        if item.get("candidate_origin") == "statutory_table_row"
    ]
    assert table_candidates
    assert all("华懋科技" not in set(item.get("entities") or []) for item in table_candidates)
    assert any("富创优越" in set(item.get("entities") or []) for item in table_candidates)

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "一级证据已确认" not in audio["model_summary"]
    assert "2.18亿元" not in audio["model_summary"]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert audio["hypothesis_projection"]["suppressed_count"] == 1


def test_wholly_owned_payer_phrase_is_not_target_legal_status(tmp_path):
    _db(tmp_path)
    fragment = (
        "公司拟通过发行股份及支付现金（含部分现金由华懋科技全资子公司支付）的方式，"
        "购买富创优越剩余57.84%股权。"
    )

    assert IndustryResearchService._precedence_legal_states(fragment) == set()
    assert IndustryResearchService._precedence_ownership_states(fragment) == {"proposed_acquisition"}


def test_audio_transport_never_falls_back_to_raw_sensitive_summary(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "示例公司", "symbol": "600001.SH"},
        "fact_ledger": [],
        "evidence": [{
            "evidence_id": "audio:RAW-ONLY", "kind": "audio_transcript",
            "source": "录音转写", "title": "RAW_TITLE客户独供",
            "company": "示例公司", "evidence_level": "ai_transcript",
            "summary": "RAW_ONLY_SECRET客户绑定且独供；预计2027年收入20亿元。",
        }],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][0]
    assert audio["model_title"]
    assert audio["model_summary"]
    assert "RAW_ONLY_SECRET" not in audio["model_summary"]
    assert "20亿元" not in audio["model_summary"]
    for row in (
        IndustryResearchService._compact_model_evidence(snapshot["evidence"])[0],
        IndustryResearchService._chapter_evidence_pack(snapshot["evidence"])[0],
    ):
        assert "RAW_ONLY_SECRET" not in row["summary"]
        assert "20亿元" not in row["summary"]
        assert "RAW_TITLE客户独供" not in row["title"]


def test_legacy_unverified_transport_fails_closed_without_safe_projection(tmp_path):
    _db(tmp_path)
    legacy_rows = [
        {
            "evidence_id": "audio:LEGACY", "kind": "audio_transcript",
            "title": "RAW_AUDIO_TITLE张坤", "summary": "RAW_AUDIO_SECRET：预计2027年利润20亿元。",
        },
        {
            "evidence_id": "note:LEGACY", "kind": "institution_note",
            "title": "RAW_NOTE_TITLE独供", "summary": "RAW_NOTE_SECRET：客户绑定且目标市值500亿元。",
        },
    ]

    compact = IndustryResearchService._compact_model_evidence(legacy_rows)
    packed = IndustryResearchService._chapter_evidence_pack(legacy_rows)
    encoded = json.dumps([*compact, *packed], ensure_ascii=False)

    for raw_value in (
        "RAW_AUDIO_TITLE", "RAW_AUDIO_SECRET", "20亿元",
        "RAW_NOTE_TITLE", "RAW_NOTE_SECRET", "500亿元",
    ):
        assert raw_value not in encoded
    assert encoded.count("安全投影尚未生成") >= 4


def test_analyze_snapshot_reprojects_legacy_audio_before_any_model_boundary(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)

    class UnconfiguredAnalyzer:
        configured = False
        model = "unconfigured"
        provider = "unconfigured"
        channel = "unconfigured"

    snapshot = {
        "topic": "示例公司", "research_type": "company",
        "subject": {"name": "示例公司", "symbol": "600001.SH", "resolved": True},
        "evidence": [{
            "evidence_id": "audio:LEGACY-ANALYZE", "kind": "audio_transcript",
            "company": "示例公司", "title": "RAW_TITLE张坤",
            "summary": "RAW_ANALYZE_SECRET：预计2027年利润20亿元。",
        }],
        "fact_ledger": [], "financial_series": [], "market_series": [],
        "valuation_series": [], "ownership_governance": [],
        "capital_market_activity": [], "industry_peer_matrix": {},
        "coverage": [], "source_status": [], "source_plan": [],
        "research_contract": {}, "data_quality": {}, "audio_pipeline": {},
    }

    with patch.object(service, "_research_analyzer", return_value=UnconfiguredAnalyzer()):
        service.analyze_snapshot("示例公司", "核验旧快照", snapshot)

    audio = snapshot["evidence"][0]
    assert audio["model_summary"]
    assert "RAW_ANALYZE_SECRET" not in audio["model_summary"]
    assert "20亿元" not in audio["model_summary"]


def test_audio_legal_status_requires_matching_primary_entity_and_transaction_state(tmp_path):
    _db(tmp_path)
    raw = (
        "华懋当前持有富创优越42.16%股权；"
        "华懋拟购富创优越57.84%股权；"
        "本次交易完成后，富创优越将成为华懋全资子公司并纳入公司合并报表；"
        "富创优越为华懋全资子公司；"
        "富创优越已纳入华懋合并报表；"
        "富创优越少数股东权益已被稀释；"
        "富创优越收购已完成。"
    )
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:TRANSACTION", "kind": "filing_text",
                "company": "华懋科技", "evidence_level": "factual",
                "summary": (
                    "华懋当前持有富创优越42.16%股权；"
                    "华懋拟购富创优越57.84%股权；"
                    "本次交易完成后，富创优越将成为华懋全资子公司并纳入公司合并报表。"
                ),
            },
            {
                "evidence_id": "audio:TRANSACTION", "kind": "audio_transcript",
                "source": "录音转写", "title": "交易状态交流",
                "company": "华懋科技", "evidence_level": "ai_transcript",
                "summary": raw,
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    model_summary = audio["model_summary"]
    assert audio["summary"] == raw
    assert "当前持有富创优越42.16%股权" in model_summary
    assert "拟购富创优越57.84%股权" in model_summary
    assert "交易完成后，富创优越将成为华懋全资子公司" in model_summary
    assert "并纳入公司合并报表" in model_summary
    assert "富创优越为华懋全资子公司" not in model_summary
    assert "富创优越已纳入华懋合并报表" not in model_summary
    assert "少数股东权益已被稀释" not in model_summary
    assert "收购已完成" not in model_summary
    projection = audio["hypothesis_projection"]
    assert projection["confirmed_count"] == 3
    assert projection["suppressed_count"] == 4

    for row in (
        IndustryResearchService._compact_model_evidence(snapshot["evidence"])[1],
        IndustryResearchService._chapter_evidence_pack(snapshot["evidence"])[1],
    ):
        assert "富创优越为华懋全资子公司" not in row["summary"]
        assert "富创优越已纳入华懋合并报表" not in row["summary"]
        assert "少数股东权益已被稀释" not in row["summary"]
        assert "收购已完成" not in row["summary"]


def test_audio_legal_status_does_not_cross_confirm_another_subsidiary(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [
            {
                "evidence_id": "filing:BLUE-TECH", "kind": "filing_text",
                "company": "华懋科技", "evidence_level": "factual",
                "summary": "蓝海科技已纳入华懋合并报表。",
            },
            {
                "evidence_id": "audio:BLUE-MATERIAL", "kind": "audio_transcript",
                "company": "华懋科技", "evidence_level": "ai_transcript",
                "summary": "蓝海材料已纳入华懋合并报表。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "蓝海材料已纳入华懋合并报表" not in audio["model_summary"]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert audio["hypothesis_projection"]["suppressed_count"] == 1


def test_audio_uses_bounded_statutory_fulltext_windows_missing_from_display_excerpt(tmp_path):
    _db(tmp_path)
    interim_text = (
        ("无关年报正文" * 20_000)
        + "。富创优越2026年上半年股份支付费用1.20亿元；"
        + "富创优越2026年上半年剔除股份支付费用影响后的归属于上市公司股东的净利润125,897,911.25元；"
        + "富创优越2026年上半年归母净利润同比-12.15%。"
    )
    transaction_text = (
        ("无关交易正文" * 18_000)
        + "。截至本公告日，华懋科技持有富创优越42.16%股权；"
        + "华懋科技拟购富创优越57.84%股权；"
        + "本次交易完成后，富创优越将成为华懋科技全资子公司并纳入公司合并报表。"
    )
    raw = (
        "富创优越2026H1股份支付费用1.20亿元；"
        "富创优越2026H1剔除股份支付后归母净利润约1.26亿元；"
        "富创优越2026H1归母净利润同比-12.15%；"
        "华懋科技当前持有富创优越42.16%股权；"
        "华懋科技拟购富创优越57.84%股权；"
        "本次交易完成后，富创优越将成为华懋科技全资子公司并纳入公司合并报表；"
        "张坤增持股份均价64.23元；"
        "三季度环比加速；"
        "2027年业绩预期上修。"
    )
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "filing_documents": [
            {
                "announcement_id": "1225505930", "title": "2026年半年度报告",
                "report_period": "2026-06-30", "document_text": interim_text,
                "text_hash": "interim-fulltext-hash", "excerpt": "半年报摘要未展示股份支付明细。",
            },
            {
                "announcement_id": "1225532560", "title": "重大资产购买报告书",
                "document_text": transaction_text,
                "text_hash": "transaction-fulltext-hash", "excerpt": "交易报告摘要未展示持股拆分。",
            },
        ],
        "evidence": [
            {
                "evidence_id": "filing:1225505930", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH", "evidence_level": "factual",
                "title": "2026年半年度报告 · 正文重点章节",
                "summary": "半年报摘要未展示股份支付明细。",
                "report_period": "2026-06-30",
            },
            {
                "evidence_id": "filing:1225532560", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH", "evidence_level": "factual",
                "title": "重大资产购买报告书 · 正文重点章节",
                "summary": "交易报告摘要未展示持股拆分。",
            },
            {
                "evidence_id": "audio:FULLTEXT-CHECK", "kind": "audio_transcript",
                "source": "录音转写", "title": "半年报交流录音",
                "company": "华懋科技", "symbol": "603306.SH",
                "evidence_level": "ai_transcript", "summary": raw,
            },
        ],
    }

    candidates = IndustryResearchService._primary_precedence_candidates(snapshot)
    document_candidates = [
        item for item in candidates if item.get("candidate_origin") == "statutory_document_window"
    ]
    assert document_candidates
    assert {item["evidence_id"] for item in document_candidates} == {
        "filing:1225505930", "filing:1225532560",
    }
    assert all(len(item["fragment"]) <= 1_200 for item in document_candidates)
    assert all(item["char_end"] > item["char_start"] for item in document_candidates)
    assert any("125,897,911.25元" in item["fragment"] for item in document_candidates)

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][2]
    model_summary = audio["model_summary"]
    for confirmed in ("1.20亿元", "约1.26亿元", "-12.15%", "42.16%", "57.84%"):
        assert confirmed in model_summary
    assert "交易完成后，富创优越将成为华懋科技全资子公司" in model_summary
    for suppressed in ("张坤", "64.23元", "三季度环比加速", "2027年业绩预期上修"):
        assert suppressed not in model_summary
    projection = audio["hypothesis_projection"]
    assert projection["confirmed_count"] == 6
    assert projection["suppressed_count"] == 3
    assert {item["primary_evidence_id"] for item in projection["confirmed"]} == {
        "filing:1225505930", "filing:1225532560",
    }
    assert all(item.get("primary_excerpt") for item in projection["confirmed"])
    assert any(
        "125,897,911.25元" in item["primary_excerpt"]
        for item in projection["confirmed"]
    )

    compact = IndustryResearchService._compact_model_evidence(snapshot["evidence"])
    transported = next(item for item in compact if item["evidence_id"] == "audio:FULLTEXT-CHECK")
    assert "张坤" not in transported["summary"]
    assert "三季度环比加速" not in transported["summary"]
    assert "2027年业绩预期上修" not in transported["summary"]


def test_fulltext_window_does_not_cross_confirm_adjacent_metric_number(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "filing_documents": [{
            "announcement_id": "METRIC-BINDING", "title": "2026年半年度报告",
            "report_period": "2026-06-30",
            "document_text": (
                "富创优越2026年上半年股份支付费用1.20亿元；"
                "富创优越2026年上半年剔除股份支付后归母净利润1.26亿元。"
            ),
        }],
        "evidence": [
            {
                "evidence_id": "filing:METRIC-BINDING", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "summary": "页面摘要未展示明细。", "report_period": "2026-06-30",
            },
            {
                "evidence_id": "audio:METRIC-BINDING", "kind": "audio_transcript",
                "company": "华懋科技", "symbol": "603306.SH",
                "summary": "富创优越2026H1股份支付费用1.26亿元。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "1.26亿元" not in audio["model_summary"]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert audio["hypothesis_projection"]["suppressed_count"] == 1


def test_fulltext_window_does_not_cross_confirm_current_and_proposed_stakes(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "filing_documents": [{
            "announcement_id": "STAKE-BINDING", "title": "重大资产购买报告书",
            "document_text": (
                "截至本公告日，华懋科技持有富创优越42.16%股权；"
                "华懋科技拟购富创优越57.84%股权。"
            ),
        }],
        "evidence": [
            {
                "evidence_id": "filing:STAKE-BINDING", "kind": "filing_text",
                "company": "华懋科技", "symbol": "603306.SH",
                "summary": "页面摘要未展示持股拆分。",
            },
            {
                "evidence_id": "audio:STAKE-BINDING", "kind": "audio_transcript",
                "company": "华懋科技", "symbol": "603306.SH",
                "summary": "华懋科技当前持有富创优越57.84%股权。",
            },
        ],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    audio = snapshot["evidence"][1]
    assert "当前持有富创优越57.84%" not in audio["model_summary"]
    assert audio["hypothesis_projection"]["confirmed_count"] == 0
    assert audio["hypothesis_projection"]["suppressed_count"] == 1


def test_audio_projection_suppresses_common_asr_role_name_forms(tmp_path):
    _db(tmp_path)
    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "fact_ledger": [],
        "evidence": [{
            "evidence_id": "audio:ASR-NAME", "kind": "audio_transcript",
            "company": "华懋科技", "symbol": "603306.SH",
            "title": "2026年半年报交流", "summary": (
                "董事会秘书张坤介绍公司情况；"
                "董秘张坤表示三季度会加速。"
            ),
        }],
    }

    IndustryResearchService._apply_primary_evidence_precedence(snapshot)

    model_summary = snapshot["evidence"][0]["model_summary"]
    assert "张坤" not in model_summary
    assert snapshot["evidence"][0]["hypothesis_projection"]["suppressed_count"] == 2


def test_fact_ledger_includes_each_peer_entity_and_evidence_id(tmp_path):
    _db(tmp_path)
    snapshot = {
        "topic": "光模块", "subject": {"name": "光模块", "symbol": None},
        "industry_peer_matrix": {
            "companies": [{
                "name": "同业甲", "symbol": "600001.SH",
                "periods": [{"period": "20260630", "revenue": 1200, "roe": 12.5}],
            }],
        },
    }

    facts = IndustryResearchService._build_fact_ledger(snapshot)
    revenue = next(item for item in facts if item["entity"] == "600001.SH" and item["metric"] == "营业收入")

    assert revenue["unit"] == "元"
    assert revenue["evidence_ids"] == ["industry-peer:600001.SH:20260630"]


def test_fact_ledger_keeps_all_five_peers_at_maximum_configured_periods(tmp_path):
    _db(tmp_path)
    periods = [f"202{year}{quarter}" for year in range(5, 7) for quarter in ("0331", "0630", "0930", "1231")]
    metric_row = {
        "revenue": 1, "net_profit": 2, "operating_cashflow": 3,
        "roe": 4, "gross_margin": 5, "revenue_yoy": 6,
        "net_profit_yoy": 7, "quarter_revenue_yoy": 8, "quarter_net_profit_yoy": 9,
    }
    snapshot = {
        "topic": "光模块", "subject": {"name": "光模块", "symbol": None},
        "industry_peer_matrix": {"companies": [
            {
                "name": f"同业{index}", "symbol": f"60000{index}.SH",
                "periods": [{"period": period, **metric_row} for period in periods],
            }
            for index in range(5)
        ]},
    }

    facts = IndustryResearchService._build_fact_ledger(snapshot)

    assert len(facts) == 360
    assert {item["entity"] for item in facts} == {f"60000{index}.SH" for index in range(5)}
    assert all(sum(item["entity"] == symbol for item in facts) == 72 for symbol in {f"60000{index}.SH" for index in range(5)})


def test_industry_peer_source_plan_requires_three_structured_companies(tmp_path):
    _db(tmp_path)
    snapshot = {
        "research_type": "industry", "coverage": [],
        "source_status": [{
            "key": "industry_peer_facts", "status": "partial", "count": 2,
            "selected": 5, "fact_count": 8,
        }],
        "industry_peer_matrix": {
            "status": "partial", "company_count": 3, "comparable_company_count": 2,
            "common_period": "20260630", "companies": [{}, {}, {}],
        },
    }

    peer = next(item for item in IndustryResearchService._source_plan(snapshot) if item["key"] == "industry_peer_facts")

    assert peer["required"] is True
    assert peer["status"] == "missing"
    assert peer["count"] == 2


def test_matched_audio_is_a_required_source_until_transcription_completes(tmp_path):
    _db(tmp_path)
    snapshot = {
        "research_type": "industry",
        "totals": {"audio_candidates": 2},
        "audio_pipeline": {"status": "unavailable", "candidate_count": 2},
        "coverage": [{
            "key": "audio_transcripts", "name": "相关录音转写",
            "status": "missing", "count": 0, "evidence_level": "ai_transcript",
        }],
        "source_status": [],
    }

    plan = IndustryResearchService._source_plan(snapshot)
    audio = next(item for item in plan if item["key"] == "audio_transcripts")

    assert audio["required"] is True
    assert audio["status"] == "missing"


def test_company_filing_source_plan_requires_annual_interim_and_quarter_texts():
    snapshot = {
        "research_type": "company",
        "coverage": [],
        "source_status": [{
            "key": "cninfo_report_text", "status": "covered", "count": 2, "requested": 2,
        }],
        "filing_documents": [
            {"title": "2025年年度报告", "filing_type": "annual", "report_period": "2025-12-31"},
            {"title": "2026年半年度报告", "filing_type": "interim", "report_period": "2026-06-30"},
        ],
        "audio_pipeline": {"candidate_count": 0},
    }

    partial = next(
        item for item in IndustryResearchService._source_plan(snapshot)
        if item["key"] == "filing_text"
    )
    assert partial["status"] == "partial"
    assert partial["covered_types"] == ["annual", "interim"]
    assert partial["missing_types"] == ["quarter"]

    snapshot["filing_documents"].append({
        "title": "2026年第一季度报告", "filing_type": "q1", "report_period": "2026-03-31",
    })
    covered = next(
        item for item in IndustryResearchService._source_plan(snapshot)
        if item["key"] == "filing_text"
    )
    assert covered["status"] == "covered"
    assert covered["missing_types"] == []


def test_audio_source_plan_discloses_selected_and_deferred_counts():
    snapshot = {
        "research_type": "company",
        "totals": {"audio_candidates": 10},
        "audio_pipeline": {
            "status": "partial", "candidate_count": 10, "selected_count": 8,
            "deferred_count": 2, "transcribed_count": 8, "max_files": 8,
        },
        "coverage": [{
            "key": "audio_transcripts", "status": "partial", "count": 8,
            "evidence_level": "ai_transcript",
        }],
        "source_status": [],
        "filing_documents": [],
    }

    audio = next(
        item for item in IndustryResearchService._source_plan(snapshot)
        if item["key"] == "audio_transcripts"
    )

    assert audio["candidate_count"] == 10
    assert audio["selected_count"] == 8
    assert audio["deferred_count"] == 2
    assert audio["transcribed_count"] == 8
    assert audio["max_files"] == 8


def test_data_quality_never_marks_missing_required_source_ready(tmp_path):
    _db(tmp_path)
    evidence = [
        {
            "evidence_id": f"fact:{index}", "kind": "web_search", "source": f"source-{index}",
            "date": "2026-08-30", "evidence_level": "factual", "url": f"https://example.com/{index}",
        }
        for index in range(20)
    ]
    snapshot = {
        "research_type": "industry", "evidence": evidence, "coverage": [],
        "research_contract": {"resolved": True}, "source_hash": "stable",
        "source_plan": [
            {"key": "web", "name": "互联网来源", "required": True, "status": "covered", "count": 20},
            {"key": "reports", "name": "研报 PDF 正文", "required": True, "status": "missing", "count": 0},
        ],
        "companies": [{"symbol": "1"}, {"symbol": "2"}, {"symbol": "3"}],
        "concept_context": {"items": [{"name": "光模块"}]},
        "totals": {"evidence": 20}, "collected_at": "2026-08-31",
    }

    quality = IndustryResearchService._assess_data_quality(snapshot)

    assert quality["status"] != "ready"
    assert "必需数据源未覆盖：研报 PDF 正文" in quality["critical_gaps"]


def test_data_quality_is_not_diluted_by_extra_traceable_viewpoints():
    factual = [
        {
            "evidence_id": f"fact-{index}",
            "kind": "industry_peer_fact",
            "source": f"official-{index % 10}",
            "date": "2026-08-01",
            "evidence_level": "factual",
            "original_available": True,
        }
        for index in range(20)
    ]
    viewpoints = [
        {
            "evidence_id": f"view-{index}",
            "kind": "broker_report",
            "source": f"broker-{index % 4}",
            "date": "2026-08-01",
            "evidence_level": "viewpoint",
            "url": f"https://example.com/report/{index}",
        }
        for index in range(100)
    ]
    base = {
        "research_type": "industry",
        "collected_at": "2026-08-31T00:00:00",
        "source_hash": "snapshot-hash",
        "research_contract": {"resolved": True},
        "source_plan": [],
        "coverage": [],
        "financial_series": [],
        "companies": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}],
        "concept_context": {"items": [{"name": "行业"}]},
        "industry_peer_matrix": {"comparable_company_count": 3},
        "totals": {"evidence": len(factual)},
        "evidence": factual,
    }
    facts_only = IndustryResearchService._assess_data_quality(base)
    expanded = IndustryResearchService._assess_data_quality({
        **base,
        "evidence": [*factual, *viewpoints],
        "totals": {"evidence": len(factual) + len(viewpoints)},
    })

    assert facts_only["dimensions"]["source_quality"] == 100
    assert expanded["dimensions"]["source_quality"] == 100
    assert expanded["metrics"]["factual_evidence_target"] == 20


def test_resolved_financial_revisions_do_not_reduce_consistency():
    evidence = [
        {
            "evidence_id": f"fact-{index}",
            "kind": "financial_statement",
            "source": f"official-{index % 10}",
            "date": "2026-08-01",
            "evidence_level": "factual",
            "original_available": True,
        }
        for index in range(20)
    ]
    snapshot = {
        "research_type": "company",
        "collected_at": "2026-08-31T00:00:00",
        "source_hash": "snapshot-hash",
        "research_contract": {"resolved": True},
        "source_plan": [],
        "coverage": [{"key": "announcements", "status": "covered", "count": 2}],
        "financial_series": [
            {"period": "20260331", "revision_count": 8},
            {"period": "20251231", "revision_count": 8},
        ],
        "market_series": [{} for _ in range(30)],
        "filing_documents": [{"title": "2025年年度报告"}],
        "totals": {"evidence": len(evidence)},
        "evidence": evidence,
    }

    quality = IndustryResearchService._assess_data_quality(snapshot)

    assert quality["dimensions"]["consistency"] == 100
    assert quality["metrics"]["resolved_financial_revisions"] == 16
    assert quality["metrics"]["unresolved_duplicate_periods"] == 0
    assert any("排除 16 个旧版/重复版本" in item for item in quality["warnings"])


def test_report_quality_gate_rejects_short_body_and_unknown_citation(tmp_path):
    _db(tmp_path)
    snapshot = {
        "evidence": [{"evidence_id": "event:1", "evidence_level": "factual"}],
        "data_quality": {"status": "ready", "overall_score": 95, "critical_gaps": []},
    }
    chapters = [{"model": "test", "summary": "有效章节"}]

    quality = IndustryResearchService._verify_report_quality(
        snapshot, chapters, "公司收入为 100 亿元，但本段引用了不存在的证据 [event:404]。",
    )

    assert quality["status"] == "limited"
    assert any("低于 20,000" in item for item in quality["critical_failures"])
    assert any("不在固定快照" in item for item in quality["critical_failures"])


def test_report_quality_gate_requires_completed_editorial_review(tmp_path):
    _db(tmp_path)
    snapshot = {
        "evidence": [{"evidence_id": "event:1", "evidence_level": "factual"}],
        "data_quality": {"status": "ready", "overall_score": 95, "critical_gaps": []},
    }
    body = ("这是有来源的研究事实，仍需结合口径和时点持续验证 [event:1]。" * 500)
    chapters = [{"model": "test", "summary": "有效章节"}]

    quality = IndustryResearchService._verify_report_quality(
        snapshot,
        chapters,
        body,
        editorial_review={"status": "failed", "unsupported_claims": [], "numeric_conflicts": [], "contradictions": []},
    )

    assert quality["status"] == "limited"
    assert "独立反方与跨章节一致性审查未完成" in quality["critical_failures"]

    for recommendation in (None, "unexpected", "limited"):
        invalid_review = {
            "status": "completed", "unsupported_claims": [],
            "numeric_conflicts": [], "contradictions": [],
        }
        if recommendation is not None:
            invalid_review["release_recommendation"] = recommendation
        invalid_quality = IndustryResearchService._verify_report_quality(
            snapshot, chapters, body, editorial_review=invalid_review,
        )
        assert invalid_quality["status"] == "limited"
        assert "独立总编未明确给出 ready 发布结论" in invalid_quality["critical_failures"]


def test_report_quality_names_the_exact_missing_governing_sentence(tmp_path):
    _db(tmp_path)
    h1 = "华懋科技2026H1归母净资产38.17亿元 [filing:1225505930]"
    year_end = "华懋科技2025年末总资产59.94亿元 [filing:1225505930]"
    snapshot = {
        "evidence": [{
            "evidence_id": "filing:1225505930", "kind": "filing_text",
            "evidence_level": "factual",
        }],
        "governing_statutory_facts": [
            {"metric": "归属于上市公司股东的净资产", "required_sentence": h1},
            {"metric": "总资产", "required_sentence": year_end},
        ],
        "data_quality": {"status": "ready", "overall_score": 95, "critical_gaps": []},
    }
    body = h1 + "。\n\n" + ("固定快照用于研究边界核验 [filing:1225505930]。" * 900)
    quality = IndustryResearchService._verify_report_quality(
        snapshot, [{"model": "test", "summary": "有效章节"}], body,
        editorial_review={
            "status": "completed", "release_recommendation": "ready",
            "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
        },
    )
    missing = [
        item for item in quality["critical_failures"]
        if item.startswith("法定原子句缺失：")
    ]
    assert missing == ["法定原子句缺失：华懋科技2025年末总资产59.94亿元"]
    assert "股份支付" not in missing[0]


def test_report_quality_requires_available_core_sources_to_be_used(tmp_path):
    _db(tmp_path)
    snapshot = {
        "evidence": [
            {"evidence_id": "filing:1", "kind": "filing_text", "evidence_level": "factual"},
            {"evidence_id": "report:1", "kind": "broker_report_text", "evidence_level": "institutional"},
            {"evidence_id": "audio:1", "kind": "audio_transcript", "evidence_level": "institutional"},
            {"evidence_id": "event:1", "kind": "event", "evidence_level": "factual"},
        ],
        "source_plan": [
            {"key": "filing_text", "name": "定期报告正文", "required": True, "status": "covered", "count": 3},
            {"key": "broker_report_text", "name": "券商研报正文", "required": True, "status": "covered", "count": 4},
            {"key": "audio_transcripts", "name": "录音转写", "required": True, "status": "covered", "count": 6},
        ],
        "data_quality": {"status": "ready", "overall_score": 98, "critical_gaps": []},
    }
    body_without_sources = ("固定快照用于研究边界核验 [event:1]。" * 900)
    review = {
        "status": "completed", "release_recommendation": "ready",
        "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
    }

    missing = IndustryResearchService._verify_report_quality(
        snapshot, [{"model": "test", "summary": "有效章节"}], body_without_sources,
        editorial_review=review,
    )
    body_with_sources = body_without_sources + (
        "\n\n定期报告正文用于核验公司披露 [filing:1]。"
        "\n\n券商研报正文仅作为有来源观点 [report:1]。"
        "\n\n录音转写仅作为待核验线索 [audio:1]。"
    )
    consumed = IndustryResearchService._verify_report_quality(
        snapshot, [{"model": "test", "summary": "有效章节"}], body_with_sources,
        editorial_review=review,
    )

    assert missing["metrics"]["required_source_consumption_failures"] == [
        "定期报告正文", "券商研报正文", "录音转写",
    ]
    assert any("正文未实际引用" in item for item in missing["critical_failures"])
    assert consumed["metrics"]["required_source_consumption_failures"] == []


def test_editorial_review_rejects_sparse_ready_model_response(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)

    class SparseEditor:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(_request):
            return {
                "choices": [{"message": {"content": '{"release_recommendation":"ready"}'}}],
                "usage": {"total_tokens": 1},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    with patch.object(service, "_research_analyzer", return_value=SparseEditor()):
        review, _usage = service._run_editorial_review(
            "华懋科技",
            {"research_contract": {}, "data_quality": {}, "industry_peer_matrix": {}},
            [{
                "chapter_id": "company_scope", "title": "研究对象", "summary": "摘要",
                "body_markdown": "固定证据 [event:1]", "evidence_ids": ["event:1"],
            }],
            [{
                "evidence_id": "event:1", "kind": "filing_text", "source": "巨潮公告",
                "title": "公司资料", "summary": "事实", "evidence_level": "factual",
            }],
        )

    assert review["status"] == "failed"
    assert review["release_recommendation"] == "limited"
    assert "结构不完整" in review["editor_note"]


def test_editorial_review_accepts_omitted_empty_advisory_fields(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)

    class CoreEditor:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(_request):
            return {
                "choices": [{"message": {"content": json.dumps({
                    "release_recommendation": "ready",
                    "contradictions": [],
                    "unsupported_claims": [],
                    "numeric_conflicts": [],
                }, ensure_ascii=False)}}],
                "usage": {},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    with patch.object(service, "_research_analyzer", return_value=CoreEditor()):
        review, _usage = service._run_editorial_review(
            "华懋科技",
            {"research_contract": {}, "data_quality": {}, "industry_peer_matrix": {}},
            [{
                "chapter_id": "company_scope", "title": "研究对象", "summary": "摘要",
                "body_markdown": "固定证据 [event:1]", "evidence_ids": ["event:1"],
            }],
            [{
                "evidence_id": "event:1", "kind": "event", "source": "事实库",
                "title": "公司资料", "summary": "事实", "evidence_level": "factual",
            }],
        )

    assert review["status"] == "completed"
    assert review["release_recommendation"] == "ready"
    assert review["missing_questions"] == []
    assert review["strongest_counterarguments"] == []
    assert review["editor_note"] == ""


def test_editorial_review_uses_safe_audio_projection_not_raw_summary(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    captured = []

    class CapturingEditor:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(request):
            captured.append(request)
            return {
                "choices": [{"message": {"content": json.dumps({
                    "release_recommendation": "ready",
                    "contradictions": [], "unsupported_claims": [], "numeric_conflicts": [],
                }, ensure_ascii=False)}}],
                "usage": {},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    audio = {
        "evidence_id": "audio:SAFE", "kind": "audio_transcript", "source": "录音转写",
        "title": "RAW_TITLE张坤", "summary": "RAW_POISON剩余58%且已经并表。",
        "model_title": "录音线索（细节待核验）",
        "model_summary": "【已隔离】具体数字与法律状态未获法定证据确认。",
    }
    snapshot = {
        "research_contract": {}, "data_quality": {}, "industry_peer_matrix": {},
        "evidence": [audio], "fact_ledger": [], "governing_statutory_facts": [],
    }
    with patch.object(service, "_research_analyzer", return_value=CapturingEditor()):
        review, _usage = service._run_editorial_review(
            "华懋科技",
            snapshot,
            [{
                "chapter_id": "events_risks", "title": "风险", "summary": "仅列线索",
                "body_markdown": "录音线索已隔离 [audio:SAFE]", "evidence_ids": ["audio:SAFE"],
            }],
            [audio],
        )

    assert review["status"] == "completed"
    payload = json.loads(captured[0]["messages"][1]["content"])
    encoded = json.dumps(payload["evidence"], ensure_ascii=False)
    assert "RAW_POISON" not in encoded
    assert "剩余58%" not in encoded
    assert "RAW_TITLE张坤" not in encoded
    assert "具体数字与法律状态未获法定证据确认" in encoded


def test_editorial_review_fails_closed_for_legacy_audio_without_projection(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    captured = []

    class CapturingEditor:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(request):
            captured.append(request)
            return {
                "choices": [{"message": {"content": json.dumps({
                    "release_recommendation": "ready",
                    "contradictions": [], "unsupported_claims": [], "numeric_conflicts": [],
                }, ensure_ascii=False)}}],
                "usage": {},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    audio = {
        "evidence_id": "audio:LEGACY-EDITOR", "kind": "audio_transcript",
        "source": "旧录音转写", "title": "RAW_EDITOR_TITLE张坤",
        "summary": "RAW_EDITOR_SECRET：剩余58%且已经并表。",
    }
    snapshot = {
        "research_contract": {}, "data_quality": {}, "industry_peer_matrix": {},
        "evidence": [audio], "fact_ledger": [], "governing_statutory_facts": [],
    }
    with patch.object(service, "_research_analyzer", return_value=CapturingEditor()):
        review, _usage = service._run_editorial_review(
            "华懋科技",
            snapshot,
            [{
                "chapter_id": "events_risks", "title": "风险", "summary": "旧任务",
                "body_markdown": "旧录音线索 [audio:LEGACY-EDITOR]",
                "evidence_ids": ["audio:LEGACY-EDITOR"],
            }],
            [audio],
        )

    assert review["status"] == "completed"
    payload = json.loads(captured[0]["messages"][1]["content"])
    encoded = json.dumps(payload["evidence"], ensure_ascii=False)
    assert "RAW_EDITOR_TITLE" not in encoded
    assert "RAW_EDITOR_SECRET" not in encoded
    assert "剩余58%" not in encoded
    assert "安全投影尚未生成" in encoded


def test_research_cutoff_metadata_is_not_treated_as_uncited_external_number(tmp_path):
    _db(tmp_path)
    paragraph = (
        "本报告研究截止时点为2026年8月31日，证据取自此前约两年的冻结快照。"
        "证据按可信度分为事实层、报告层与待核验层，并按研究契约控制使用边界。"
        "报告期、公告日和检索日分别记录，截止时点之后发布的资料不进入本次判断。"
    )

    audit = IndustryResearchService._citation_audit(paragraph, {"event:1"})

    assert audit["paragraph_count"] == 1
    assert audit["numeric_paragraphs"] == 0
    assert audit["numeric_citation_coverage_pct"] == 0
    assert audit["uncited_numeric_excerpts"] == []


def test_research_cutoff_metadata_never_hides_company_fact_in_same_paragraph(tmp_path):
    _db(tmp_path)
    paragraph = (
        "本报告研究截止时点为2026年8月31日，证据取自冻结快照；"
        "公司2026H1营业收入为10.91亿元。后续解释用于说明数据边界，"
        "但不改变该公司经营数字必须逐句引用一级证据的要求。"
    )
    audit = IndustryResearchService._citation_audit(paragraph, set())
    assert audit["numeric_paragraphs"] == 1
    assert audit["numeric_cited_paragraphs"] == 0


@pytest.mark.parametrize("fact", [
    "华懋科技于2026年完成重大收购",
    "华懋科技于2026年取得核心专利",
    "华懋科技于2026年遭到行政处罚",
])
def test_research_cutoff_metadata_consumes_only_metadata_atoms(tmp_path, fact):
    _db(tmp_path)
    paragraph = (
        "本报告研究截止时点为2026年8月31日，证据取自此前两年的冻结快照；"
        f"{fact}。该事项会直接影响研究判断，因此事实日期仍须逐句绑定来源，"
        "不得被前面的任务元数据一并豁免。"
    )
    audit = IndustryResearchService._citation_audit(paragraph, set())
    assert audit["numeric_paragraphs"] == 1


def test_report_quality_understands_hyphenated_evidence_ids_and_editor_veto(tmp_path):
    _db(tmp_path)
    snapshot = {
        "evidence": [{"evidence_id": "concept-stock:300308.SZ", "evidence_level": "factual"}],
        "data_quality": {"status": "ready", "overall_score": 95, "critical_gaps": []},
    }
    body = ("题材成分归因需要按截止日和来源数持续复核 [concept-stock:300308.SZ]。" * 600)
    quality = IndustryResearchService._verify_report_quality(
        snapshot,
        [{"model": "test", "summary": "有效章节"}],
        body,
        editorial_review={
            "status": "completed", "release_recommendation": "limited",
            "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
        },
    )

    assert quality["metrics"]["unique_citations"] == 1
    assert quality["metrics"]["unsupported_citations"] == []
    assert "独立总编未明确给出 ready 发布结论" in quality["critical_failures"]


def test_background_project_completes_and_persists_report(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    IndustryResearchTaskManager._instance = manager
    fake_report = {
        "one_sentence": "证据约束下的测试结论", "leaders": [], "caveats": [],
        "quality_assurance": {"status": "ready", "score": 90},
        "generation": {"status": "completed"},
    }
    direct = {
        "subject": {"research_type": "industry", "name": "光模块", "symbol": None, "resolved": True},
        "evidence": [], "financial_series": [], "market_series": [], "media_gallery": [], "source_status": [],
    }
    with (
        patch("src.services.industry_research_service.current_owner_id", return_value="user:7"),
        patch.object(ResearchReportLibraryService, "ensure_background_sync", return_value={"status": "ready"}),
        patch("src.services.industry_research_service.ZsxqMcpSyncWorker.get_instance") as sync_worker,
        patch.object(IndustryResearchSourceCollector, "collect", return_value=direct),
        patch.object(IndustryResearchService, "analyze_snapshot", return_value=fake_report),
    ):
        sync_worker.return_value.sync_now.return_value = {"totals": {}}
        project = service.create_project({"topic": "光模块", "lookback_days": 365})
        deadline = time.monotonic() + 5
        completed = None
        while time.monotonic() < deadline:
            completed = service.get_project(project["project_id"])
            if completed and completed["status"] == "completed":
                break
            time.sleep(0.05)

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["report"]["one_sentence"] == "证据约束下的测试结论"
    assert completed["snapshot"]["source_hash"]


def test_background_project_persists_post_analysis_snapshot_hash_chain(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    IndustryResearchTaskManager._instance = manager
    direct = {
        "subject": {"research_type": "industry", "name": "光模块", "symbol": None, "resolved": True},
        "evidence": [], "financial_series": [], "market_series": [],
        "media_gallery": [], "source_status": [],
    }

    def fake_analyze(analyzer_service, _topic, _objective, snapshot, **_kwargs):
        # Reproduce analysis-boundary mutation after the task row has already
        # persisted its collection snapshot.
        snapshot["coverage"] = [{"key": "post-analysis", "name": "分析后底稿", "count": 1}]
        snapshot["source_hash"] = analyzer_service._snapshot_hash(snapshot)
        return {
            "one_sentence": "分析后hash链一致",
            "long_form_char_count": 20_001,
            "evidence_snapshot_hash": snapshot["source_hash"],
            "quality_assurance": {"status": "ready", "score": 90},
            "generation": {"status": "completed"},
        }

    with (
        patch("src.services.industry_research_service.current_owner_id", return_value="user:hash"),
        patch.object(ResearchReportLibraryService, "ensure_background_sync", return_value={"status": "ready"}),
        patch("src.services.industry_research_service.ZsxqMcpSyncWorker.get_instance") as sync_worker,
        patch.object(IndustryResearchSourceCollector, "collect", return_value=direct),
        patch.object(IndustryResearchService, "analyze_snapshot", new=fake_analyze),
    ):
        sync_worker.return_value.sync_now.return_value = {"totals": {}}
        project = service.create_project({"topic": "光模块", "lookback_days": 365})
        deadline = time.monotonic() + 5
        completed = None
        while time.monotonic() < deadline:
            completed = service.get_project(project["project_id"])
            if completed and completed["status"] == "completed":
                break
            time.sleep(0.05)

    assert completed is not None
    recomputed = IndustryResearchService._snapshot_hash(completed["snapshot"])
    assert completed["source_hash"] == recomputed
    assert completed["snapshot"]["source_hash"] == recomputed
    assert completed["report"]["evidence_snapshot_hash"] == recomputed


def test_background_project_publishes_answer_first_draft_before_long_report(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    IndustryResearchTaskManager._instance = manager
    release = Event()
    direct = {
        "subject": {"research_type": "industry", "name": "机器人", "symbol": None, "resolved": True},
        "evidence": [], "financial_series": [], "market_series": [], "media_gallery": [], "source_status": [],
    }

    def fake_analyze(_self, _topic, _objective, _snapshot, progress_callback=None, draft_callback=None):
        assert draft_callback is not None
        draft_callback({
            "one_sentence": "先给用户可用结论",
            "executive_summary": "长篇报告仍在后台生成。",
            "leaders": [{"name": "测试龙头"}],
        })
        release.wait(timeout=3)
        return {"one_sentence": "最终结论", "chapters": [], "leaders": []}

    try:
        with (
            patch("src.services.industry_research_service.current_owner_id", return_value="user:8"),
            patch.object(ResearchReportLibraryService, "ensure_background_sync", return_value={"status": "ready"}),
            patch("src.services.industry_research_service.ZsxqMcpSyncWorker.get_instance") as sync_worker,
            patch.object(IndustryResearchSourceCollector, "collect", return_value=direct),
            patch.object(IndustryResearchService, "analyze_snapshot", new=fake_analyze),
        ):
            sync_worker.return_value.sync_now.return_value = {"totals": {}}
            project = service.create_project({"topic": "机器人", "lookback_days": 365})
            deadline = time.monotonic() + 5
            draft = None
            while time.monotonic() < deadline:
                draft = service.get_project(project["project_id"])
                if draft and (draft.get("report") or {}).get("one_sentence") == "先给用户可用结论":
                    break
                time.sleep(0.05)

            assert draft is not None
            assert draft["status"] == "analyzing"
            assert draft["progress"] >= 64
            assert draft["report"]["leaders"][0]["name"] == "测试龙头"
            assert "可先阅读" in draft["message"]
    finally:
        release.set()


def test_chapter_citation_audit_rejects_metadata_ids_and_ignores_figure_reference(tmp_path):
    _db(tmp_path)
    body = (
        "公司经营事实需要区分报告期与公告日，并以原始公告复核 [event:1]。"
        "渠道覆盖、同步状态和结构化字段只是任务元数据，不能充当事实证据 "
        "[coverage:announcements] [source_status:covered] [audio_pipeline:completed] "
        "[figure_id:financial_trend]。图表【financial_trend｜核心财务趋势】仅用于展示读法。"
    )

    audit = IndustryResearchService._citation_audit(body, {"event:1"})

    assert set(audit["unsupported_citations"]) == {
        "coverage:announcements", "source_status:covered",
        "audio_pipeline:completed", "figure_id:financial_trend",
    }
    assert "financial_trend" not in audit["citations"]


def test_chapter_allowlist_rejects_globally_real_but_unseen_evidence(tmp_path):
    _db(tmp_path)
    sentence = (
        "这一判断虽然引用了固定快照内的证据，但该证据并未下发给本章，"
        "因此不能通过本章引用白名单 [event:2]。"
    )
    body = sentence * 450
    snapshot = {
        "evidence": [
            {"evidence_id": "event:1", "evidence_level": "factual"},
            {"evidence_id": "event:2", "evidence_level": "factual"},
        ],
        "coverage": [{"key": "news", "name": "新闻", "count": 2, "status": "covered"}],
        "data_quality": {"status": "ready", "overall_score": 95, "critical_gaps": []},
    }
    chapters = [{
        "chapter_id": "company_scope", "model": "test", "summary": "有效章节",
        "body_markdown": body, "allowed_evidence_ids": ["event:1"],
    }]

    quality = IndustryResearchService._verify_report_quality(snapshot, chapters, body)

    assert quality["status"] == "limited"
    assert quality["metrics"]["unsupported_citations"] == []
    assert quality["metrics"]["chapter_disallowed_citations"] == ["company_scope:event:2"]
    assert any("章节引用白名单" in item for item in quality["critical_failures"])


def test_numeric_paragraph_citation_threshold_accepts_exactly_ninety_percent(tmp_path):
    _db(tmp_path)
    paragraphs = []
    for index in range(10):
        citation = " [financial:603306.SH:20260630]" if index < 9 else ""
        paragraphs.append(
            f"第{index + 1}项数字检查：2026年报告期的指标为{index + 10}%，"
            "该段同时说明主体、期间、单位与累计口径，长度足以进入程序化数字段落检查，"
            f"并要求读者回到原始财务证据复核，不能把预测值当成已经发生的事实。{citation}"
        )
    audit = IndustryResearchService._citation_audit(
        "\n\n".join(paragraphs), {"financial:603306.SH:20260630"},
    )

    assert audit["numeric_paragraphs"] == 10
    assert audit["numeric_cited_paragraphs"] == 9
    assert audit["numeric_citation_coverage_pct"] == 90.0
    assert audit["unsupported_citations"] == []


def test_shared_numeric_detector_excludes_question_number_but_keeps_chart_and_quality_values(tmp_path):
    _db(tmp_path)

    assert IndustryResearchService._has_substantive_numeric_claim(
        "问题1个：还需要核验哪些客户和产线？"
    ) is False
    assert IndustryResearchService._has_substantive_numeric_claim(
        "1. 还需要核验哪些客户和产线？"
    ) is False
    assert IndustryResearchService._has_substantive_numeric_claim(
        "图表显示样本覆盖95%，该数字需要回到固定证据复核。"
    ) is True
    assert IndustryResearchService._has_substantive_numeric_claim(
        "数据质量得分为83分，不能用作公司经营事实。"
    ) is True


def test_numeric_paragraph_tracks_whitelisted_figure_without_treating_it_as_evidence(tmp_path):
    _db(tmp_path)
    paragraph = (
        "截至2026年6月30日，该指标为12.5%，图中同时保留期间、单位和来源，"
        "本段只解释程序已经计算出的变化，不把图表外的信息扩展成公司事实。"
        "图表【financial_trend｜核心财务趋势】"
    )

    accepted = IndustryResearchService._citation_audit_with_figures(
        paragraph, set(), {"financial_trend"},
    )
    rejected = IndustryResearchService._citation_audit_with_figures(
        paragraph, set(), {"valuation_trend"},
    )

    assert accepted["numeric_paragraphs"] == 1
    assert accepted["numeric_cited_paragraphs"] == 0
    assert accepted["numeric_figure_supported_paragraphs"] == 1
    assert accepted["numeric_temporal_figure_supported_paragraphs"] == 0
    assert accepted["numeric_citation_coverage_pct"] == 0
    assert accepted["unsupported_figure_references"] == []
    assert rejected["numeric_cited_paragraphs"] == 0
    assert rejected["unsupported_figure_references"] == ["financial_trend"]


def test_date_only_timeline_interpretation_counts_as_numeric_support(tmp_path):
    _db(tmp_path)
    paragraph = (
        "图表【evidence_timeline｜证据发布时间分布】显示资料峰值出现在"
        "2026年8月；该峰值只表示信息供给集中，不代表行业景气拐点。"
        "图表【company_evidence｜公司证据密度】用于安排后续核验顺序，"
        "不把图表之外的公司经营数字写入结论。"
    )

    accepted = IndustryResearchService._citation_audit_with_figures(
        paragraph, set(), {"evidence_timeline", "company_evidence"},
    )
    rejected = IndustryResearchService._citation_audit_with_figures(
        paragraph, set(), {"company_evidence"},
    )

    assert accepted["numeric_paragraphs"] == 1
    assert accepted["numeric_cited_paragraphs"] == 0
    assert accepted["numeric_temporal_figure_supported_paragraphs"] == 1
    assert accepted["numeric_supported_paragraphs"] == 1
    assert accepted["numeric_citation_coverage_pct"] == 100
    assert accepted["uncited_numeric_excerpts"] == []
    assert rejected["numeric_temporal_figure_supported_paragraphs"] == 0
    assert rejected["numeric_citation_coverage_pct"] == 0


def test_timeline_figure_never_supports_measured_value_in_same_paragraph(tmp_path):
    _db(tmp_path)
    paragraph = (
        "图表【evidence_timeline｜证据发布时间分布】显示资料峰值出现在"
        "2026年8月，同时声称营业收入增长12.5%；这段话长度足以进入"
        "数字段落审计，并用于验证日期图表不能替代公司经营数值的一级证据。"
    )

    audit = IndustryResearchService._citation_audit_with_figures(
        paragraph, set(), {"evidence_timeline"},
    )

    assert audit["numeric_paragraphs"] == 1
    assert audit["numeric_temporal_figure_supported_paragraphs"] == 0
    assert audit["numeric_citation_coverage_pct"] == 0
    assert len(audit["uncited_numeric_excerpts"]) == 1


def test_report_sanitizer_removes_fake_citations_from_all_text_fields(tmp_path):
    _db(tmp_path)
    report = {
        "one_sentence": "结论 [data_quality:critical_gaps] [event:1]",
        "executive_summary": "资料限制 [data_quality:warnings]，事实来自 [event:1]。",
        "nested": {"note": "不能伪造 [coverage:announcements]", "evidence_ids": ["event:1", "event:9"]},
    }

    sanitized = IndustryResearchService._sanitize_report(report, {"event:1"})

    encoded = json.dumps(sanitized, ensure_ascii=False)
    assert "data_quality:" not in encoded
    assert "coverage:" not in encoded
    assert "[event:1]" in encoded
    assert sanitized["nested"]["evidence_ids"] == ["event:1"]


def test_editorial_appendix_prose_cannot_render_bare_numeric_counterarguments(tmp_path):
    _db(tmp_path)
    exact = (
        "股份支付费用1.20亿元，扣除股份支付影响后的归母净利润"
        "1.26亿元 [filing:1225505930]"
    )
    review = {
        "strongest_counterarguments": [
            "反方认为调整后利润1.26亿元仍不足以解释主营业务。",
            exact,
        ],
        "missing_questions": ["PE(TTM)246.92倍的四季分母明细是什么？"],
        "editor_note": "越南爬坡贡献利润2.83亿元。",
    }

    sanitized = IndustryResearchService._sanitize_editorial_narrative_fields(
        review, {"filing:1225505930"},
    )
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert exact in sanitized["strongest_counterarguments"]
    assert "1.26亿元仍不足" not in encoded
    assert "246.92" not in encoded
    assert "2.83" not in encoded
    assert "具体数值" not in encoded
    assert "需核验PE(TTM)各观察日的数据口径" in encoded
    assert "越南基地爬坡进度" in encoded
    assert sanitized["narrative_citation_sanitization"]["changed_fields"] == 3


def test_editorial_appendix_extracts_structured_counterargument_as_natural_language(tmp_path):
    _db(tmp_path)
    review = {
        "strongest_counterarguments": [{
            "claim": "当前PE(TTM)约246.92倍，可能因2025年利润基数退出而继续升高。",
            "values": [246.92, 2025],
            "evidence_ids": [],
            "release_blocking": True,
        }],
        "missing_questions": [{
            "question": "2026Q1末归母净资产34.75亿元是否有一级证据？",
            "periods": ["2026Q1"],
        }],
        "editor_note": "",
    }

    sanitized = IndustryResearchService._sanitize_editorial_narrative_fields(review, set())
    appendix = IndustryResearchService._build_research_governance_appendix(
        {}, sanitized,
    )
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert all(
        isinstance(item, str)
        for item in sanitized["strongest_counterarguments"]
        + sanitized["missing_questions"]
    )
    assert "需核验PE(TTM)各观察日的数据口径" in appendix
    assert "需回到相应法定报告核验资产与权益" in appendix
    assert "{'" not in appendix
    assert '"claim"' not in appendix
    assert "具体数值" not in encoded
    assert "246.92" not in encoded
    assert "34.75" not in encoded


def test_editorial_appendix_removes_legacy_numeric_placeholder_text(tmp_path):
    _db(tmp_path)
    review = {
        "strongest_counterarguments": [
            "当前PE(TTM)约具体数值已隐含预期，具体驱动仍需复核。",
            "收益法预测对毛利率敏感，参数变化对应具体数值评估值变动。",
        ],
        "missing_questions": ["相关报告期归母净资产具体数值是否有一级证据？"],
        "editor_note": "",
    }

    sanitized = IndustryResearchService._sanitize_editorial_narrative_fields(review, set())
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert "具体数值" not in encoded
    assert "需核验PE(TTM)各观察日的数据口径" in encoded
    assert "关键预测假设" in encoded
    assert "核验资产与权益的主体、期间、单位及会计口径" in encoded


def test_editorial_appendix_removes_nonnumeric_merger_and_goodwill_assertions(tmp_path):
    _db(tmp_path)
    review = {
        "strongest_counterarguments": [
            "富创优越已经成为全资子公司并纳入合并报表。",
            "本次交易必然形成新增商誉。",
        ],
        "missing_questions": [],
        "editor_note": "富创优越已完成并表，相关交易商誉已经确认。",
    }
    sanitized = IndustryResearchService._sanitize_editorial_narrative_fields(
        review, {"filing:1225532560"},
    )
    encoded = json.dumps(sanitized, ensure_ascii=False)
    assert "已经成为全资子公司" not in encoded
    assert "已完成并表" not in encoded
    assert "必然形成新增商誉" not in encoded
    assert "商誉已经确认" not in encoded
    assert "需核验" in encoded
    assert sanitized["narrative_citation_sanitization"]["changed_fields"] == 3


def test_canonical_goodwill_boundary_is_deduplicated_after_all_chapter_revisions(tmp_path):
    _db(tmp_path)
    boundary = industry_research_service._GOODWILL_SAFETY_BOUNDARY
    chapters = [
        {
            "chapter_id": "events_risks",
            "title": "事件与风险",
            "summary": boundary,
            "body_markdown": (
                "交易文件披露了购买价分摊仍待完成 [filing:deal]。\n\n"
                f"{boundary}\n\n{boundary}\n\n{boundary}"
            ),
            "open_questions": [boundary],
            "allowed_evidence_ids": ["filing:deal"],
            "allowed_figure_ids": [],
            "citation_validation": {"revision_attempted": True},
        },
        {
            "chapter_id": "decision_dashboard",
            "title": "决策仪表盘",
            "summary": "最终修订后的定性结论。",
            "body_markdown": (
                f"{boundary}\n\n"
                "交易草案中的收益法敏感性仍需独立复核 [filing:deal]。"
            ),
            "open_questions": [],
            "allowed_evidence_ids": ["filing:deal"],
            "allowed_figure_ids": [],
            "citation_validation": {"revision_attempted": True},
        },
    ]

    finalized = IndustryResearchService._deduplicate_canonical_safety_across_chapters(
        chapters
    )
    report = IndustryResearchService._assemble_long_form_report(
        "华懋科技", {"executive_summary": "修订完成。"}, finalized,
        research_type="company",
    )
    report = IndustryResearchService._deduplicate_canonical_safety_text(report)

    assert sum(
        str(chapter.get("body_markdown") or "").count(boundary)
        + str(chapter.get("summary") or "").count(boundary)
        + sum(str(item).count(boundary) for item in chapter.get("open_questions") or [])
        for chapter in finalized
    ) == 1
    assert report.count(boundary) == 1
    assert "购买价分摊仍待完成 [filing:deal]" in report
    assert "收益法敏感性仍需独立复核 [filing:deal]" in report
    assert finalized[0]["citation_validation"]["citations"] == ["filing:deal"]
    assert finalized[1]["citation_validation"]["citations"] == ["filing:deal"]


def test_canonical_goodwill_boundary_is_idempotent_through_sentence_guard(tmp_path):
    _db(tmp_path)
    boundary = industry_research_service._GOODWILL_SAFETY_BOUNDARY
    governing = [
        {
            "metric": "交易完成后股权状态",
            "supporting_evidence_ids": ["filing:1225532560"],
        },
        {
            "metric": "交易完成后合并范围状态",
            "supporting_evidence_ids": ["filing:1225532560"],
        },
    ]

    guarded = IndustryResearchService._enforce_production_accounting_boundaries(
        f"{boundary}\n\n{boundary}",
        {"filing:1225532560"},
        governing,
    )

    assert guarded == boundary
    assert guarded.count(boundary) == 1
    assert IndustryResearchService._production_accounting_policy_failures(guarded) == []


def test_chapter_storage_sanitizes_every_rendered_field_and_recomputes_audit(tmp_path):
    _db(tmp_path)
    body = ("公司事实来自固定证据，机构观点与待核验线索必须分层展示。" * 90) + " [event:1]"
    chapter = {
        "chapter_id": "company_scope",
        "title": "研究对象 [coverage:fake]",
        "summary": "摘要含伪引用 [data_quality:critical_gaps] [event:1]",
        "body_markdown": body,
        "open_questions": ["待核验 [event:404]"],
        "evidence_ids": ["event:1", "event:404"],
        "allowed_evidence_ids": ["event:1"],
        "allowed_figure_ids": [],
        "citation_validation": {"revision_attempted": False, "revision_accepted": False},
    }

    stored = IndustryResearchService._sanitize_chapter_for_storage(chapter)

    encoded = json.dumps(stored, ensure_ascii=False)
    assert "coverage:fake" not in encoded
    assert "data_quality:critical_gaps" not in encoded
    assert "event:404" not in encoded
    assert stored["evidence_ids"] == ["event:1"]
    audit = IndustryResearchService._citation_audit_with_figures(
        stored["body_markdown"], {"event:1"}, set(),
    )
    assert stored["citation_validation"]["citations"] == audit["citations"]
    assert stored["char_count"] == IndustryResearchService._count_report_chars(stored["body_markdown"])


def test_chapter_payload_is_citation_locked_and_context_bounded(tmp_path):
    _db(tmp_path)
    evidence = [{
        "evidence_id": f"event:{index}", "kind": "news_comments", "source": "公开新闻",
        "title": f"经营线索 {index}", "summary": ("信息摘要" * 1_000) + " [coverage:fake]",
        "evidence_level": "reported",
    } for index in range(60)] + [
        {"evidence_id": "filing:A1", "kind": "filing_text", "title": "年度报告", "summary": "年报" * 4_000},
        {"evidence_id": "broker:R1", "kind": "broker_report_text", "title": "深度研报", "summary": "研报" * 4_000},
        {"evidence_id": "audio:T1", "kind": "audio_transcript", "title": "业绩会录音", "summary": "录音" * 4_000},
        {"evidence_id": "web:W1", "kind": "web_fulltext", "title": "公司官网", "summary": "网页" * 4_000},
    ]
    selected = IndustryResearchService._select_chapter_evidence(
        evidence, {"keywords": ["经营", "年报"], "chapter_id": "financials"},
    )
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "research_contract": {"subject_name": "华懋科技", "symbol": "603306.SH", "cutoff": "2026-08-31"},
        "data_quality": {"status": "ready", "overall_score": 95, "critical_gaps": [], "warnings": []},
        "coverage": [{"key": f"source-{index}", "count": 10_000} for index in range(100)],
        "source_status": [{"key": f"status-{index}", "message": "x" * 1_000} for index in range(100)],
        "audio_pipeline": {"status": "completed", "transcript": "x" * 100_000},
        "financial_series": [{"period": index, "revenue": index} for index in range(100)],
        "market_series": [{"date": index, "close": index} for index in range(1_000)],
        "fact_ledger": [{
            "fact_id": f"metric:{index}", "entity": "603306.SH", "metric": "营业收入",
            "value": index, "unit": "元", "period": "20260630", "source": "Tushare",
            "evidence_ids": ["filing:A1"],
        } for index in range(100)],
    }
    spec = {
        "chapter_id": "financials", "title": "财务报表、盈利质量与现金流",
        "focus": "分析财务质量", "keywords": ["财务", "收入"],
    }

    payload = IndustryResearchService._chapter_model_payload(
        "华懋科技", "上市公司深度研究", snapshot, spec, selected,
    )
    encoded = json.dumps(payload, ensure_ascii=False)

    assert len(payload["allowed_evidence_ids"]) <= 18
    assert len(payload["supplied_evidence"]) <= 18
    assert len(payload["structured_fact_cards"]) <= 20
    assert len(payload["visualization_plan"]) <= 4
    assert len(encoded) < 35_000
    assert not ({"coverage", "source_status", "audio_pipeline", "financial_series", "market_series"} & payload.keys())
    assert "data_quality" not in payload
    assert payload["non_citable_limitations"]["instruction"].startswith("仅用于说明资料范围")
    assert "[coverage:fake]" not in encoded
    assert all(
        set(item["supporting_evidence_ids"]).issubset(payload["allowed_evidence_ids"])
        for item in payload["structured_fact_cards"]
    )


def test_financial_chapter_keeps_all_supported_periods_as_grouped_cards(tmp_path):
    _db(tmp_path)
    periods = [
        f"{year}{suffix}"
        for year in range(2026, 2022, -1)
        for suffix in ("1231", "0930", "0630", "0331")
    ][:16]
    financial_series = [{
        "period": period,
        "period_label": f"{period[:4]}报告期",
        "period_basis": "YTD_H1" if period.endswith("0630") else "ANNUAL",
        "statement_scope": "consolidated",
        "metric_basis": "statutory",
        "revenue": index + 1,
        "net_profit": index + 2,
    } for index, period in enumerate(periods)]
    evidence = [{
        "evidence_id": f"financial:603306.SH:{period}",
        "kind": "financial_statement",
        "symbol": "603306.SH",
        "title": f"华懋科技 {period} 财务快照",
        "summary": "结构化法定报表",
    } for period in periods]
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "cutoff": "2026-12-31T00:00:00",
        "financial_series": financial_series,
        "valuation_series": [],
        "evidence": evidence,
        "fact_ledger": [],
        "data_quality": {},
    }
    spec = next(
        item for item in industry_research_service._COMPANY_LONG_FORM_CHAPTERS
        if item["chapter_id"] == "financials"
    )
    selected = IndustryResearchService._select_chapter_evidence(
        evidence, spec, limit=24, subject_symbol="603306.SH",
    )
    payload = IndustryResearchService._chapter_model_payload(
        "华懋科技", "公司研究", snapshot, spec, selected,
    )

    cards = payload["periodic_financial_facts"]
    assert {item["period"] for item in cards} == set(periods)
    assert len(payload["allowed_evidence_ids"]) == 16
    supplied = {item["evidence_id"] for item in payload["supplied_evidence"]}
    assert all(
        set(item["supporting_evidence_ids"]).issubset(supplied)
        for item in cards
    )
    assert all("fact_id" not in item and "evidence_id" not in item for item in cards)


def test_structured_chapter_rejects_wrong_symbol_and_orphan_fact(tmp_path):
    _db(tmp_path)
    spec = next(
        item for item in industry_research_service._COMPANY_LONG_FORM_CHAPTERS
        if item["chapter_id"] == "financials"
    )
    wrong = {
        "evidence_id": "financial:300308.SZ:20250630",
        "kind": "financial_statement", "symbol": "300308.SZ",
        "title": "其他公司半年报", "summary": "营业收入100亿元",
    }
    selected = IndustryResearchService._select_chapter_evidence(
        [wrong], spec, limit=24, subject_symbol="603306.SH",
    )
    assert selected == []

    snapshot = {
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "financial_series": [{"period": "20250630", "revenue": 1_108_464_195.57}],
        "valuation_series": [], "evidence": [], "fact_ledger": [{
            "fact_id": "metric:synthetic", "entity": "603306.SH", "metric": "营业收入",
            "value": 1_108_464_195.57, "unit": "元", "period": "20250630",
            "evidence_ids": ["financial:603306.SH:20250630"],
        }],
    }
    assert IndustryResearchService._chapter_periodic_financial_facts(
        snapshot, {"financial:603306.SH:20250630"}, spec,
    ) == []
    assert IndustryResearchService._chapter_fact_cards(
        snapshot, {"financial:603306.SH:20250630"}, spec,
    )
    audit = IndustryResearchService._citation_audit(
        "营业收入11.08亿元 [metric:synthetic]",
        {"financial:603306.SH:20250630"},
    )
    assert audit["unsupported_citations"] == ["metric:synthetic"]


def test_valuation_breakpoint_uses_two_real_endpoints_and_preserves_them(tmp_path):
    _db(tmp_path)
    rows = [
        {"date": "20260828", "pe_ttm": 151.0, "close": 10.3, "total_market_value": 1030.0},
        {"date": "20260827", "pe_ttm": 150.0, "close": 10.2, "total_market_value": 1020.0},
        {"date": "20260826", "pe_ttm": 150.0, "close": 10.2, "total_market_value": 1020.0},
        {"date": "20260825", "pe_ttm": 100.0, "close": 10.0, "total_market_value": 1000.0},
        {"date": "20260824", "pe_ttm": 99.0, "close": 9.9, "total_market_value": 990.0},
        {"date": "20260821", "pe_ttm": 98.0, "close": 9.8, "total_market_value": 980.0},
    ]
    endpoint_ids = {
        "valuation:603306.SH:20260825", "valuation:603306.SH:20260826",
    }
    events = IndustryResearchService._valuation_change_events(
        rows, symbol="603306.SH", available_evidence_ids=endpoint_ids,
        cutoff="2026-08-31", limit=8,
    )
    assert len(events) == 1
    event = events[0]
    assert event["before_date"] == "20260825"
    assert event["after_date"] == "20260826"
    assert event["before_pe_ttm"] == 100.0
    assert event["after_pe_ttm"] == 150.0
    assert set(event["supporting_evidence_ids"]) == endpoint_ids
    assert event["claim_type"] == "derived"
    assert "待核验" in event["cause"]
    kept_dates = {
        item["date"] for item in IndustryResearchService._valuation_breakpoint_rows(rows, limit=2)
    }
    assert kept_dates == {"20260825", "20260826"}

    stable_denominator = [
        {"date": "20260826", "pe_ttm": 120, "close": 12, "total_market_value": 1200},
        {"date": "20260825", "pe_ttm": 100, "close": 10, "total_market_value": 1000},
    ]
    assert IndustryResearchService._valuation_change_events(
        stable_denominator, symbol="603306.SH",
        available_evidence_ids={
            "valuation:603306.SH:20260825", "valuation:603306.SH:20260826",
        },
    ) == []
    assert IndustryResearchService._valuation_change_events(
        rows, symbol="603306.SH",
        available_evidence_ids={"valuation:603306.SH:20260826"},
    ) == []


def test_chapter_repair_retries_and_never_restores_known_bad_raw_body(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    good_body = ("本段依据固定证据说明经营边界，并保留反证和持续跟踪方法。" * 105) + " [event:1]"
    bad_body = good_body + " [data_quality:critical_gaps]"

    class RepairAnalyzer:
        model = "kimi-for-coding"
        provider = "kimi"
        calls = 0

        @classmethod
        def _post_with_retry(cls, _request):
            cls.calls += 1
            body = bad_body if cls.calls == 1 else good_body
            return {
                "choices": [{"message": {"content": json.dumps({
                    "chapter_title": "研究对象", "summary": "摘要", "body_markdown": body,
                    "evidence_ids": ["event:1"], "open_questions": [],
                }, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapter = {
        "chapter_id": "company_scope", "title": "研究对象", "summary": "旧摘要",
        "body_markdown": "旧稿", "evidence_ids": [], "allowed_evidence_ids": ["event:1"],
        "allowed_figure_ids": [], "char_count": 2, "validation_failures": ["正文过短"],
        "citation_validation": {"numeric_paragraphs": 0, "uncited_numeric_excerpts": []},
    }
    selected = [{
        "evidence_id": "event:1", "kind": "filing_text", "source": "巨潮公告",
        "title": "公司资料", "summary": "固定证据", "evidence_level": "factual",
    }]
    snapshot = {"fact_ledger": [], "visualizations": []}
    spec = {"chapter_id": "company_scope", "title": "研究对象", "keywords": ["公司"]}

    with patch.object(service, "_research_analyzer", return_value=RepairAnalyzer()):
        revised, usage = service._repair_chapter_citations_once(
            "华懋科技", "公司深度研究", snapshot, spec, selected, chapter,
        )

    assert RepairAnalyzer.calls == 2
    assert usage["total_tokens"] == 10
    assert revised["citation_validation"]["revision_accepted"] is True
    assert len(revised["citation_validation"]["revision_attempts"]) == 2
    assert "data_quality:" not in revised["body_markdown"]
    stored_audit = IndustryResearchService._citation_audit_with_figures(
        revised["body_markdown"], {"event:1"}, set(),
    )
    assert stored_audit["unsupported_citations"] == revised["citation_validation"]["unsupported_citations"]
    assert stored_audit["numeric_citation_coverage_pct"] == revised["citation_validation"]["numeric_citation_coverage_pct"]


def test_chapter_repair_preserves_first_attempt_usage_when_second_call_fails(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    invalid_body = ("证据不足的旧表述需要继续修订并保留明确边界。" * 115) + " [coverage:fake]"

    class FlakyRepairAnalyzer:
        model = "kimi-for-coding"
        provider = "kimi"
        calls = 0

        @classmethod
        def _post_with_retry(cls, _request):
            cls.calls += 1
            if cls.calls == 2:
                raise RuntimeError("temporary upstream failure")
            return {
                "choices": [{"message": {"content": json.dumps({
                    "body_markdown": invalid_body, "evidence_ids": [], "open_questions": [],
                }, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapter = {
        "chapter_id": "company_scope", "title": "研究对象", "summary": "旧摘要",
        "body_markdown": "旧稿", "evidence_ids": [], "allowed_evidence_ids": ["event:1"],
        "allowed_figure_ids": [], "char_count": 2, "validation_failures": ["正文过短"],
        "citation_validation": {"numeric_paragraphs": 0, "uncited_numeric_excerpts": []},
    }
    spec = {"chapter_id": "company_scope", "title": "研究对象", "keywords": ["公司"]}

    with patch.object(service, "_research_analyzer", return_value=FlakyRepairAnalyzer()):
        revised, usage = service._repair_chapter_citations_once(
            "华懋科技", "公司深度研究", {"fact_ledger": [], "visualizations": []},
            spec, [], chapter,
        )

    assert FlakyRepairAnalyzer.calls == 2
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert revised["citation_validation"]["revision_accepted"] is False
    assert revised["citation_validation"]["fallback_normalized"] is True
    assert revised["citation_validation"]["revision_attempts"][1]["error"]


def test_analyze_snapshot_rewrites_editor_findings_and_rechecks_once(monkeypatch, tmp_path):
    db = _db(tmp_path)

    class SynthesisAnalyzer:
        configured = True
        model = "kimi-for-coding"
        provider = "kimi"
        channel = "kimi_code"

        @staticmethod
        def _post_with_retry(_request):
            return {"choices": [{"message": {"content": json.dumps({
                "one_sentence": "未经总编的一句话结论",
                "leaders": [{"name": "未经验证龙头", "evidence_ids": ["event:1"]}],
            }, ensure_ascii=False)}}], "usage": {}}

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapters = [{
        "chapter_id": "company_scope", "title": "研究对象", "summary": "摘要",
        "body_markdown": "正文 [event:1]", "evidence_ids": ["event:1"],
        "allowed_evidence_ids": ["event:1"], "char_count": 2_500, "model": "kimi",
    }]
    corrected = [{
        **chapters[0],
        "summary": "纠正摘要 [coverage:fake] [event:404]",
        "body_markdown": "纠正后的正文 [event:1]",
    }]
    initial_review = {
        "status": "completed", "release_recommendation": "limited",
        "unsupported_claims": [{"claim": "上市年份写错", "chapter": "company_scope", "reason": "原文不支持"}],
        "numeric_conflicts": [], "contradictions": [],
    }
    final_review = {
        "status": "completed", "release_recommendation": "ready",
        "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
    }
    snapshot = {
        "research_type": "company", "totals": {},
        "evidence": [{"evidence_id": "event:1", "kind": "event", "summary": "事实"}],
        "financial_series": [], "market_series": [], "valuation_series": [], "fact_ledger": [],
        "ownership_governance": [], "capital_market_activity": [], "data_quality": {},
        "coverage": [], "source_status": [], "source_plan": [],
    }
    service = IndustryResearchService(db)
    review_inputs = []
    review_outputs = iter([
        (initial_review, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        (final_review, {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}),
        (final_review, {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}),
    ])

    def capture_review(_topic, _snapshot, review_chapters, _evidence):
        review_inputs.append(review_chapters[0]["summary"])
        return next(review_outputs)

    with (
        patch.object(IndustryResearchService, "_research_analyzer", return_value=SynthesisAnalyzer()),
        patch.object(service, "_generate_long_form_chapters", return_value=(chapters, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})),
        patch.object(service, "_run_editorial_review", side_effect=capture_review) as review_mock,
        patch.object(service, "_repair_chapters_from_editorial_review", return_value=(
            corrected,
            {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            {"attempted": True, "affected_chapters": ["company_scope"], "failed_chapters": []},
        )) as repair_mock,
        patch.object(service, "_verify_report_quality", return_value={"status": "ready", "score": 100}),
    ):
        report = service.analyze_snapshot("华懋科技", "公司研究", snapshot)

    assert review_mock.call_count == 3
    repair_mock.assert_called_once()
    assert report["chapters"][0]["body_markdown"].startswith("纠正后的正文")
    assert report["chapters"][0]["summary"] == "纠正摘要"
    assert review_inputs == ["摘要", "纠正摘要", "纠正摘要"]
    assert report["leaders"] == []
    assert report["verified_cards_status"] == "reviewed_chapters_only"
    assert "未经总编的一句话结论" not in report["one_sentence"]
    assert report["editorial_review"]["revision_cycle"]["affected_chapters"] == ["company_scope"]
    assert report["usage"]["total_tokens"] == 57


def test_analyze_snapshot_always_reviews_final_text_and_keeps_reviewed_chapter_immutable(
    tmp_path,
):
    db = _db(tmp_path)

    class SynthesisAnalyzer:
        configured = True
        model = "kimi-for-coding"
        provider = "kimi"
        channel = "kimi_code"

        @staticmethod
        def _post_with_retry(_request):
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    boundary = industry_research_service._GOODWILL_SAFETY_BOUNDARY
    chapters = [{
        "chapter_id": "company_scope", "title": "研究对象", "summary": "摘要",
        "body_markdown": boundary, "evidence_ids": [], "allowed_evidence_ids": [],
        "allowed_figure_ids": [], "char_count": len(boundary), "model": "kimi",
    }]
    clean_review = {
        "status": "completed", "release_recommendation": "ready",
        "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
    }
    snapshot = {
        "research_type": "company", "totals": {},
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "research_contract": {"cutoff": "2026-08-31"},
        "evidence": [], "financial_series": [], "market_series": [],
        "valuation_series": [], "fact_ledger": [], "ownership_governance": [],
        "capital_market_activity": [], "data_quality": {}, "coverage": [],
        "source_status": [], "source_plan": [], "visualizations": [],
    }
    zero_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    service = IndustryResearchService(db)

    with (
        patch.object(IndustryResearchService, "_research_analyzer", return_value=SynthesisAnalyzer()),
        patch.object(service, "_generate_long_form_chapters", return_value=(chapters, zero_usage)),
        patch.object(
            service, "_run_editorial_review",
            side_effect=[(clean_review, zero_usage), (clean_review, zero_usage)],
        ) as review_mock,
        patch.object(service, "_verify_report_quality", return_value={"status": "ready", "score": 100}),
    ):
        report = service.analyze_snapshot("华懋科技", "公司研究", snapshot)

    assert review_mock.call_count == 2
    assert report["editorial_review"]["final_text_review"]["performed"] is True
    assert boundary in report["chapters"][0]["body_markdown"]
    assert boundary not in report["executive_summary"]
    assert report["long_form_report"].count(boundary) == 1
    assert report["long_form_report"].index(boundary) > report["long_form_report"].index("# 第1章")


def test_editorial_revision_aggregation_uses_each_chapters_latest_cycle_state(tmp_path):
    db = _db(tmp_path)

    class SynthesisAnalyzer:
        configured = True
        model = "kimi-for-coding"
        provider = "kimi"
        channel = "kimi_code"

        @staticmethod
        def _post_with_retry(_request):
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapters = [
        {
            "chapter_id": chapter_id, "title": chapter_id, "summary": "摘要",
            "body_markdown": "已审正文 [event:1]", "evidence_ids": ["event:1"],
            "allowed_evidence_ids": ["event:1"], "allowed_figure_ids": [],
            "char_count": 2_500, "model": "kimi",
        }
        for chapter_id in ("company_scope", "financials")
    ]
    review1 = {
        "status": "completed", "release_recommendation": "limited",
        "unsupported_claims": [{"claim": "c0", "chapter": "company_scope"}],
        "numeric_conflicts": [], "contradictions": [],
    }
    review2 = {
        "status": "completed", "release_recommendation": "limited",
        "unsupported_claims": [
            {"claim": "c0仍需修", "chapter": "company_scope"},
            {"claim": "c1新增", "chapter": "financials"},
        ],
        "numeric_conflicts": [], "contradictions": [],
    }
    review3 = {
        "status": "completed", "release_recommendation": "ready",
        "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
    }
    snapshot = {
        "research_type": "company", "totals": {},
        "subject": {"name": "华懋科技", "symbol": "603306.SH"},
        "research_contract": {"cutoff": "2026-08-31"},
        "evidence": [{"evidence_id": "event:1", "kind": "event", "summary": "事实"}],
        "financial_series": [], "market_series": [], "valuation_series": [], "fact_ledger": [],
        "ownership_governance": [], "capital_market_activity": [], "data_quality": {},
        "coverage": [], "source_status": [], "source_plan": [],
    }
    zero_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    service = IndustryResearchService(db)

    def quality_from_latest(_snapshot, _chapters, _markdown, *, editorial_review=None):
        failed = ((editorial_review or {}).get("revision_cycle") or {}).get("failed_chapters") or []
        return {"status": "limited" if failed else "ready", "score": 50 if failed else 100}

    with (
        patch.object(IndustryResearchService, "_research_analyzer", return_value=SynthesisAnalyzer()),
        patch.object(service, "_generate_long_form_chapters", return_value=(chapters, zero_usage)),
        patch.object(service, "_run_editorial_review", side_effect=[
            (review1, zero_usage), (review2, zero_usage), (review3, zero_usage),
            (review3, zero_usage),
        ]),
        patch.object(service, "_repair_chapters_from_editorial_review", side_effect=[
            (chapters, zero_usage, {
                "attempted": True, "affected_chapters": ["company_scope"],
                "accepted_chapters": ["company_scope"], "failed_chapters": [],
            }),
            (chapters, zero_usage, {
                "attempted": True, "affected_chapters": ["company_scope", "financials"],
                "accepted_chapters": ["financials"], "failed_chapters": ["company_scope"],
            }),
        ]),
        patch.object(service, "_verify_report_quality", side_effect=quality_from_latest),
    ):
        report = service.analyze_snapshot("华懋科技", "公司研究", snapshot)

    revision = report["editorial_review"]["revision_cycle"]
    assert revision["historical_failed_chapters"] == ["company_scope"]
    # The mocked chapters are intentionally too short for the final storage
    # gate.  Keep the cycle-2 ledger above, but current release state must come
    # from the exact stored chapters rather than the earlier model repair.
    assert revision["accepted_chapters"] == []
    assert set(revision["failed_chapters"]) == {"company_scope", "financials"}
    assert revision["final_storage_reconciled"] is True
    assert report["generation"]["status"] == "limited"


def test_editor_findings_route_period_and_forecast_errors_to_affected_chapters(tmp_path):
    _db(tmp_path)
    chapters = [
        {"chapter_id": "company_scope", "title": "研究对象、方法与证据质量", "body_markdown": "公司于2024年上市。"},
        {"chapter_id": "financials", "title": "财务报表、盈利质量与现金流", "body_markdown": "营业收入与Q2同比需要复核。"},
        {"chapter_id": "expectations_valuation", "title": "一致预期、估值变量与预期差", "body_markdown": "机构预测被写成事实。"},
    ]
    review = {
        "unsupported_claims": [{"claim": "公司于2024年上市", "chapter": "company_scope", "reason": "应为2014年"}],
        "numeric_conflicts": [{"metric": "营业收入", "values": [10, 12], "periods": ["Q2"], "resolution": "未解决"}],
        "contradictions": [{"issue": "机构预测被写成事实", "chapters": ["expectations_valuation"], "resolution": "改为预测层"}],
    }

    routed = IndustryResearchService._editorial_findings_by_chapter(chapters, review)

    assert set(routed) == {"company_scope", "financials", "expectations_valuation"}
    assert routed["company_scope"][0]["type"] == "unsupported_claim"
    assert routed["financials"][0]["type"] == "numeric_conflict"
    assert routed["expectations_valuation"][0]["type"] == "contradiction"


def test_editorial_semantic_gate_removes_unsupported_number_from_body_and_summary(tmp_path):
    _db(tmp_path)
    findings = [{
        "type": "unsupported_claim",
        "claim": "2026年第二季度营业收入同比增长1.37%",
        "reason": "固定快照只有累计口径，不能推出单季同比",
    }]

    body_failures = IndustryResearchService._editorial_repair_semantic_failures(
        "机构口径称2026年第二季度收入同比1.37%，该值待验证。",
        "第二季度收入同比1.37%。",
        findings,
    )
    summary_failures = IndustryResearchService._editorial_repair_semantic_failures(
        "现有累计披露不能推出第二季度单季同比，相关断言已删除。",
        "第二季度收入同比1.37%。",
        findings,
    )
    clean = IndustryResearchService._editorial_repair_semantic_failures(
        "现有累计披露不能推出第二季度单季同比，相关断言已删除。",
        "单季增速暂无可验证口径。",
        findings,
    )

    assert any("无支持数字" in item for item in body_failures)
    assert any("章节摘要" in item for item in summary_failures)
    assert clean == []


def test_editorial_semantic_gate_quarantines_unresolved_conflict(tmp_path):
    _db(tmp_path)
    findings = [{
        "type": "numeric_conflict",
        "metric": "2025年调整后利润",
        "values": ["3.5亿元", "3.02亿元"],
        "resolution": "未解决",
    }]

    bad = IndustryResearchService._editorial_repair_semantic_failures(
        "乐观情景采用3.5亿元，基准情景采用3.02亿元。",
        "利润口径为3.5亿元。",
        findings,
    )
    contained = IndustryResearchService._editorial_repair_semantic_failures(
        "来源冲突：资料分别列示3.5亿元和3.02亿元，两者均不纳入基准、乐观情景或估值。",
        "调整后利润存在来源口径冲突，现阶段不用于情景判断。",
        findings,
    )

    assert any("未隔离" in item for item in bad)
    assert any("章节摘要" in item for item in bad)
    assert contained == []


def test_editorial_semantic_gate_locks_claim_number_not_reason_workings(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "2026年Q2单季营收同比微增约1.37%",
        "reason": "H1累计10.91亿减Q1 5.12亿得Q2约5.79亿，且2025年基数7.19亿未经核验",
        "evidence_ids": [],
    }

    keys = IndustryResearchService._editorial_finding_number_keys(finding)

    assert keys == {"1.37"}


def test_editorial_semantic_gate_allows_explicit_secondary_source_attribution(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "2025年9月30日公司发布收购富创优越57.84%股权草案",
        "reason": "缺少原始公告，只能由券商研报转述",
        "evidence_ids": ["report:1"],
    }

    failures = IndustryResearchService._editorial_repair_semantic_failures(
        "据中邮证券研报转述，公司拟收购富创优越57.84%股权，该转述不纳入摘要、估值或情景；原始公告未纳入本任务快照。",
        "收购时点仍需回到交易所原始公告核验。",
        [finding],
    )

    assert failures == []


def test_editorial_semantic_gate_allows_corrected_primary_fact_with_direct_citation(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "2025年9月30日公司发布收购富创优越57.84%股权草案",
        "reason": "历史日期仅由券商转述，当前修订公告可直接证明交易比例",
        "evidence_ids": ["report:1", "announcement:2"],
    }

    failures = IndustryResearchService._editorial_repair_semantic_failures(
        "公司当前修订稿披露拟收购富创优越57.84%股权 [announcement:2]。",
        "公司仍在推进交易，尚未完成并表。",
        [finding],
        evidence_by_id={
            "report:1": {"kind": "broker_report_text", "evidence_level": "analytical"},
            "announcement:2": {
                "kind": "announcement", "evidence_level": "factual",
                "title": "交易修订稿", "summary": "拟收购富创优越57.84%股权",
            },
        },
    )

    assert failures == []


def test_editorial_semantic_gate_rejects_unrelated_primary_citation_and_weak_caveat(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "2025年9月30日公司发布收购富创优越57.84%股权草案",
        "reason": "历史日期仅由券商转述",
        "evidence_ids": ["announcement:2", "report:1"],
    }

    failures = IndustryResearchService._editorial_repair_semantic_failures(
        "据券商研报转述，公司于2025年9月30日发布57.84%股权草案，仍待核验 [announcement:2]。",
        "收购历史时点待核验。",
        [finding],
        evidence_by_id={
            "announcement:2": {
                "kind": "announcement", "evidence_level": "factual",
                "title": "利润分配公告", "summary": "每10股派发现金红利1元",
            },
            "report:1": {"kind": "broker_report_text", "summary": "拟收购57.84%股权"},
        },
    )

    assert any("无支持判断" in item or "硬隔离" in item for item in failures)


def test_editorial_semantic_gate_rejects_unsupported_negative_fact_without_number(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "截至2026年8月31日，未检索到股权质押、冻结或变更公告",
        "reason": "结构化质押数据表明实际存在质押",
        "evidence_ids": ["tushare:pledge:1"],
    }

    unchanged = IndustryResearchService._editorial_repair_semantic_failures(
        "截至2026年8月31日，尚未检索到股权质押、冻结或变更相关公告。",
        "公司未发生股权质押或冻结。",
        [finding],
    )
    corrected = IndustryResearchService._editorial_repair_semantic_failures(
        "旧稿关于未检索到股权质押的判断错误；实际存在质押，应以结构化事实为准。",
        "结构化事实显示公司存在股权质押。",
        [finding],
    )

    assert any("无支持判断" in item for item in unchanged)
    assert corrected == []


def test_editorial_semantic_gate_does_not_confuse_same_number_for_another_metric(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "2026年Q2单季营收同比微增约1.37%",
        "reason": "缺少可比基数",
        "evidence_ids": [],
    }

    failures = IndustryResearchService._editorial_repair_semantic_failures(
        "Q2单季营收同比无法由累计披露推出；同期ROE为1.37% [financial:1]。",
        "Q2单季营收同比暂无可验证口径。",
        [finding],
    )

    assert failures == []


def test_editorial_number_keys_keep_small_values_with_financial_units(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "metric": "调整后净利润",
        "values": ["1亿元", "2亿元"],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"1", "2"}


def test_snapshot_evidence_reserves_financial_periods_and_valuation_endpoints(tmp_path):
    _db(tmp_path)
    evidence = [
        {
            "evidence_id": f"announcement:{index}",
            "kind": "company_announcement",
            "importance": 90,
            "date": f"2026-08-{(index % 28) + 1:02d}",
        }
        for index in range(300)
    ]
    evidence.extend({
        "evidence_id": f"financial:603306.SH:{20261231 - index}",
        "kind": "financial_statement",
        "importance": 78,
    } for index in range(16))
    evidence.extend({
        "evidence_id": f"valuation:603306.SH:202608{index + 1:02d}",
        "kind": "valuation_fact",
        "importance": 78,
    } for index in range(24))

    stored = IndustryResearchService._select_snapshot_evidence(evidence, limit=260)
    stored_ids = {item["evidence_id"] for item in stored}

    assert len(stored) == 260
    assert sum(item["kind"] == "financial_statement" for item in stored) == 16
    assert sum(item["kind"] == "valuation_fact" for item in stored) == 24
    assert "financial:603306.SH:20261231" in stored_ids
    assert "valuation:603306.SH:20260824" in stored_ids


def test_editorial_numeric_conflict_locks_shared_delta_not_valid_primary_fact(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "2026年上半年毛利率同比降幅",
        "values": [
            "下降约3.3个百分点",
            "毛利率27.44%较去年同期下降约3.3个百分点",
        ],
        "units": ["百分点", "百分点"],
        "periods": ["2026年H1", "2026年H1"],
        "accounting_bases": ["法定报表", "管理层交流"],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"3.3"}


def test_editorial_numeric_conflict_keeps_distinct_competing_values(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净利润",
        "values": ["1.20亿元", "1.50亿元"],
        "units": ["亿元", "亿元"],
        "periods": ["2026年H1", "2026年H1"],
        "accounting_bases": ["法定报表", "法定报表"],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"1.2", "1.5"}


def test_editorial_broad_conflict_locks_only_explicitly_unverified_observation(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净资产",
        "values": ["33.64亿元", "34.30亿元", "38.17亿元"],
        "units": ["亿元", "亿元", "亿元"],
        "periods": ["2025Q3末", "2025年末", "2026H1"],
        "accounting_bases": [
            "statutory_attributable_equity",
            "未明确来源",
            "statutory_attributable_equity",
        ],
        "evidence_ids": ["filing:1225505930"],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"34.3"}


def test_editorial_broad_conflict_stays_closed_when_verified_residuals_still_conflict(
    tmp_path,
):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净资产",
        "values": ["33.64亿元", "35.00亿元", "34.30亿元"],
        "periods": ["2025Q3末", "2025Q3末", "2025年末"],
        "accounting_bases": [
            "statutory_attributable_equity",
            "statutory_attributable_equity",
            "未明确来源",
        ],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {
        "33.64", "35", "34.3",
    }


def test_editorial_broad_conflict_canonicalizes_period_and_basis_synonyms(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净资产",
        "values": ["33.64亿元", "35.00亿元", "34.30亿元"],
        "periods": ["2026H1", "2026年H1", "2025年末"],
        "accounting_bases": ["法定报表", "statutory_gaap", "未明确来源"],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {
        "33.64", "35", "34.3",
    }


@pytest.mark.parametrize("period", ["2026年1-6月", "2026年中期"])
def test_editorial_broad_conflict_canonicalizes_h1_period_aliases(tmp_path, period):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净资产",
        "values": ["33.64亿元", "35.00亿元", "34.30亿元"],
        "periods": ["2026H1", period, "2025年末"],
        "accounting_bases": ["法定报表", "企业会计准则", "未明确来源"],
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {
        "33.64", "35", "34.3",
    }


@pytest.mark.parametrize("periods", [
    ["2026H1", "2026Q2", "2025年末"],
    ["2025FY", "2025Q4", "2024年末"],
])
def test_editorial_balance_sheet_conflict_equates_same_cutoff_periods(
    tmp_path, periods,
):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净资产",
        "values": ["38.17亿元", "39.20亿元", "34.30亿元"],
        "periods": periods,
        "accounting_bases": ["法定报表", "法定报表", "未明确来源"],
        "resolution": "未解决",
    }
    assert IndustryResearchService._editorial_finding_number_keys(finding) == {
        "38.17", "39.2", "34.3",
    }


@pytest.mark.parametrize("prose", [
    (
        "1. **分部披露追踪**：待后续定期报告披露分产品收入结构。 "
        "2. **交割进度**：关注核准、交割公告及首次并表时点。 "
        "3. **费用拆解**：追踪费用率变化，识别驱动因素是否持续。 "
        "4. **客户验证**：须以法定披露为准。 "
        "5. **股份支付后续影响**：2026年下半年及2027年是否仍有费用摊销。"
    ),
    (
        "持续跟踪应优先补齐以下资料： - 2026年半年度报告研发费用明细； "
        "- 富创优越审计报告中的产能和成本结构； - 交易完成后的并表会计处理； "
        "- 2025年各季度利润表，以核验TTM分母构成。"
    ),
    (
        "仍需验证的问题：2026年第二季度单季法定利润表明细；"
        "股份支付费用的归属部门；富创优越2026年上半年未经审计财务数据；"
        "经营现金流补充资料明细。"
    ),
    (
        "- 富创优越2025年及2026年经审计数据的差异分析； "
        "- 2027年未来摊销计划待一级证据核验； - 主要客户待法定披露； "
        "- 越南产线进度需以经审计数据为准； - 配套资金用途待披露。"
    ),
])
def test_research_tracking_ledgers_do_not_inflate_uncited_numeric_coverage(tmp_path, prose):
    _db(tmp_path)
    audit = IndustryResearchService._citation_audit(prose, set())
    assert audit["numeric_paragraphs"] == 0
    assert audit["uncited_numeric_excerpts"] == []


@pytest.mark.parametrize("prose", [
    (
        "1. 2026H1营业收入为10.91亿元；2. 归母净利润为0.23亿元；"
        "3. 股份支付费用为1.20亿元。"
    ),
    "后续研究应优先补客户名单；2026H1营业收入为10.91亿元；仍需验证利润。",
    "后续研究需核验2027年利润2亿元假设，并补齐一级证据。",
    "数据库命中数20条；公司获得订单10亿元。",
    "数据库命中数20条；公司毛利率32%。",
    "数据库命中数20条；公司拥有合作伙伴200个。",
    "数据质量限制为83分；公司签订客户订单20条。",
    "公司于2026年8月完成富创优越交割。后续研究应优先关注并表影响。",
    (
        "- 公司于2026年完成收购，后续业绩需要关注；"
        "- 公司于2027年完成并表，需关注利润变化。"
    ),
    "后续研究应优先关注：公司于2026年8月完成富创优越收购，该事件对利润的影响待核验。",
    "后续研究应优先关注：公司于2026年8月已完成富创优越交割，是否影响利润尚待核验。",
    (
        "- 公司于2026年完成收购，是否影响利润待核验；"
        "- 2027年整合计划需持续关注；- 后续并表数据待披露。"
    ),
    "后续研究应优先关注：公司于2026年8月收购富创优越，后续并表影响仍待核验。",
    "后续研究应优先关注：重组方案于2026年8月生效，后续并表影响仍待核验。",
    "后续研究应优先关注：公司于2026年8月中标重大项目，后续收入影响待核验。",
    "后续研究应优先关注：公司于2026年8月被监管立案，后续影响待核验。",
    "后续研究应优先关注：公司于2026年8月签约重大客户，后续影响待核验。",
    "后续研究应优先关注：公司于2026年8月复牌，后续影响待核验。",
    "后续研究应优先关注计划核验客户名单以及公司于2026年完成收购。",
    "数据库命中数为公司订单20条。",
    "后续研究应优先关注：公司于2026年8月中标重大项目需在年报中核验。",
    "后续研究应优先关注：公司于2026年8月被监管立案需补齐资料。",
    "后续研究应优先关注：公司于2026年8月签约重大客户后续收入预测待核验。",
    "后续研究应优先关注计划核验客户名单然后公司于2026年完成收购。",
    "后续研究应优先关注计划核验客户名单接着公司于2026年完成收购。",
    "后续研究应优先关注计划核验客户名单之后公司于2026年完成收购。",
    "后续研究应优先关注：公司于2026年完成收购接着是否影响利润待核验。",
    "后续研究应优先关注：目标公司于2026年8月中标重大项目，后续收入影响待核验。",
    "后续研究应优先关注：2026年目标公司中标重大项目，后续收入影响待核验。",
    "后续研究应优先关注：公司已经中标3个重大项目，后续收入影响待核验。",
    "后续研究应优先关注：公司新增5条产线，后续产能影响待核验。",
    "后续研究应优先关注：2025年全年及2026年上半年目标公司均已中标重大项目。",
    "后续研究应优先关注：公司2026年半年报已正式发布，需核验披露内容。",
    "后续研究应优先关注：公司2026年财务数据已正式披露，需补齐原文。",
    "后续研究应优先关注：核验公司于2026年8月已完成交割，后续影响待核验。",
    "后续研究应优先关注：验证公司于2026年8月已经中标重大项目。",
    "后续研究应优先关注：华懋科技2026年半年报披露重大诉讼，需补齐原文。",
    "后续研究应优先关注：华懋科技2026年财务数据显示经营持续恶化。",
    "后续研究应优先关注：华懋科技2026年半年报发布并确认重大会计差错。",
    "后续研究应优先关注：华懋科技2026年财务数据反映经营持续恶化。",
    "后续研究应优先关注：华懋科技2026年半年报存在重大会计差错。",
    "后续研究应优先关注：华懋科技2026年数据中心建成投用。",
    "后续研究应优先关注：华懋科技已完成编制2026年半年报。",
    "后续研究应优先关注：董事会审议通过2026年半年报。",
])
def test_research_process_exemption_never_hides_amounts_or_percentages(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is True


def test_research_process_exemption_allows_genuinely_conditional_future_event(tmp_path):
    _db(tmp_path)
    prose = "后续研究应优先关注：公司是否计划于2027年完成交割，并补齐法定证据。"
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is False


def test_research_process_exemption_allows_cross_period_audited_data_tracking(tmp_path):
    _db(tmp_path)
    prose = (
        "持续跟踪应优先补齐以下资料：富创优越2025年全年及2026年上半年"
        "经审计财务数据的差异分析；2027年未来摊销计划待一级证据核验。"
    )
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is False


@pytest.mark.parametrize("prose", [
    "资料缺口：华懋科技已完成编制2026年半年报。",
    "仍需验证的问题：董事会审议通过2026年半年报。",
    "待补资料：华懋科技已经披露2026年半年报。",
    "资料缺口：董事会批准2026年半年报。",
    "仍需验证的问题：董事会通过2026年半年报。",
    "待补资料：公司发布2026年半年报。",
])
def test_research_governance_never_authorizes_completed_company_fact(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is False
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is True


@pytest.mark.parametrize("prose", [
    "资料缺口：待董事会批准2026年半年报。",
    "待补资料：公司尚未通过2026年半年报。",
    "仍需验证的问题：需董事会审议2026年半年报。",
    "待补资料：公司拟编制2026年半年报。",
])
def test_research_governance_keeps_conditional_report_requests_nonassertive(
    tmp_path, prose,
):
    _db(tmp_path)
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is False


def test_production_numbered_tracking_ledger_is_not_a_numeric_fact(tmp_path):
    _db(tmp_path)
    prose = (
        "1. **交易进展**：关注富创优越股权收购的注册、股东大会通过及交割完成公告。\n"
        "2. **运营数据**：补全2025年报及2026年中报全文，获取研发费用、产能利用率等明细。\n"
        "3. **越南产线**：核查是否有法定报告披露投资金额、投产进度及收入贡献。\n"
        "4. **股份支付**：跟踪摊销节奏，测算2026年下半年及2027年对利润的持续影响。\n"
        "5. **机构线索**：待安全投影完成后，验证技术路线与客户订单，不得单独作为事实。"
    )

    audit = IndustryResearchService._citation_audit(prose, set())

    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert audit["numeric_paragraphs"] == 0
    assert audit["uncited_numeric_excerpts"] == []


@pytest.mark.parametrize("prose", [
    (
        "1. 公司于2026年完成重大收购，关注后续影响；"
        "2. 待核验并表后的经营变化。"
    ),
    (
        "1. 公司已披露2026年半年报，关注市场反馈；"
        "2. 待补充下一期报告。"
    ),
    (
        "1. 公司营业收入数值为10亿元；"
        "2. 跟踪后续收入变化。"
    ),
])
def test_numbered_research_list_never_hides_completed_or_numeric_fact(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is False
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is True


def test_production_data_gap_pipeline_counts_are_not_company_facts(tmp_path):
    _db(tmp_path)
    prose = (
        "当前证据存在以下缺口，限制结论完整性：互联网网页正文丰富度不足，"
        "仅获公司简介1份，搜索链接与摘要无法替代全文阅读；录音与机构段子共16条候选，"
        "本次仅纳入8条转写，且均处于安全投影未生成状态；财务报告期已排除16个"
        "旧版/重复版本，但快照外材料未纳入。"
    )
    audit = IndustryResearchService._citation_audit(prose, set())
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert audit["numeric_paragraphs"] == 0


def test_bare_future_full_year_uncertainty_is_not_numeric_coverage(tmp_path):
    _db(tmp_path)
    prose = "尚不足以判断公司2026年全年业绩"

    assert IndustryResearchService._is_auditable_numeric_fact(prose) is False
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True


@pytest.mark.parametrize("prose", [
    "尚不足以判断公司2026年全年业绩为10亿元",
    "尚不足以判断公司2026年全年业绩是否增长20%",
    "公司2026年全年业绩已经增长20%",
])
def test_future_full_year_uncertainty_never_hides_measured_or_completed_fact(
    tmp_path, prose,
):
    _db(tmp_path)
    assert IndustryResearchService._is_auditable_numeric_fact(prose) is True


def test_evidence_layer_count_requires_its_deterministic_figure_or_is_removed(tmp_path):
    _db(tmp_path)
    prose = (
        "证据等级只用于解释固定快照的材料边界，不代表观点正确率，"
        "也不能替代公司事实的一级证据核验；事实层129条只表示材料数量。"
    )
    chart_bound = prose + " 图表【evidence_quality｜证据等级结构】"

    accepted = IndustryResearchService._citation_audit_with_figures(
        chart_bound, set(), {"evidence_quality"},
    )
    normalized_without_chart = IndustryResearchService._normalize_chapter_body(
        prose, set(), {"evidence_quality"},
    )
    normalized_wrong_chart = IndustryResearchService._normalize_chapter_body(
        prose + " 图表【source_mix｜多源证据构成】",
        set(), {"source_mix", "evidence_quality"},
    )

    assert accepted["numeric_pipeline_figure_supported_paragraphs"] == 1
    assert accepted["numeric_citation_coverage_pct"] == 100
    assert accepted["uncited_numeric_excerpts"] == []
    assert "事实层129条" not in normalized_without_chart
    assert "事实层证据" in normalized_without_chart
    assert "事实层129条" not in normalized_wrong_chart


def test_evidence_quality_figure_never_supports_company_count_in_same_paragraph(tmp_path):
    _db(tmp_path)
    prose = (
        "事实层129条只描述固定快照结构；公司已披露20个核心客户，"
        "后一个数量属于公司经营事实，不能借用研究管线图表。"
        "图表【evidence_quality｜证据等级结构】"
    )
    normalized = IndustryResearchService._normalize_chapter_body(
        prose, set(), {"evidence_quality"},
    )
    audit = IndustryResearchService._citation_audit_with_figures(
        normalized, set(), {"evidence_quality"},
    )

    assert "事实层129条" not in normalized
    assert "20个核心客户" in normalized
    assert audit["numeric_pipeline_figure_supported_paragraphs"] == 0
    assert audit["numeric_citation_coverage_pct"] == 0


def test_pipeline_counts_never_hide_company_count_in_same_paragraph(tmp_path):
    _db(tmp_path)
    prose = (
        "互联网网页正文仅获公司简介1份，录音与机构段子共16条候选，"
        "本次仅纳入8条转写；公司已披露20个核心客户。该段长度补足后仍应"
        "进入数字事实审计，不能让研究管线计数覆盖真实公司经营数量。"
    )
    audit = IndustryResearchService._citation_audit(prose, set())
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is False
    assert audit["numeric_paragraphs"] == 1


def test_production_unanswered_question_block_is_not_a_numeric_fact(tmp_path):
    _db(tmp_path)
    prose = (
        "**暂不能回答**：富创优越并表后的实际商誉金额（待购买价分摊）；"
        "光通信业务的定量贡献（待交易完成）；2026年单季度利润拆解"
        "（法定披露仅为累计H1）；越南产线对毛利率的定量影响（缺乏法定分部数据）；"
        "PE(TTM)变动的具体归因（缺乏逐季TTM分母明细）。"
    )
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._citation_audit(prose, set())["numeric_paragraphs"] == 0


def test_safe_projection_source_isolation_with_report_period_is_not_company_fact(tmp_path):
    _db(tmp_path)
    prose = (
        "本次快照中的机构纪要均标注安全投影尚未生成，原始断言不进入模型上下文。"
        "其中一条仅提供可核验问题线索，涉及其他主体2026年半年报业绩；"
        "定量预测片段已屏蔽，且不得直接进入华懋科技的财务事实或估值输入。"
    )
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._citation_audit(prose, set())["numeric_paragraphs"] == 0


def test_exact_cutoff_metadata_is_excluded_but_cannot_hide_amount(tmp_path):
    _db(tmp_path)
    metadata = (
        "本报告研究截止时点为2026年8月31日。该日期只界定任务冻结边界，"
        "报告期、公告日与检索日仍分开记录，不将任务时间写成公司事实。"
    )
    mixed = metadata + (
        "公司2026H1营业收入为10.91亿元，该金额是公司财务事实，"
        "必须另行绑定同句一级证据。"
    )

    metadata_audit = IndustryResearchService._citation_audit(metadata, set())
    mixed_audit = IndustryResearchService._citation_audit(mixed, set())

    assert metadata_audit["numeric_paragraphs"] == 0
    assert mixed_audit["numeric_paragraphs"] == 1
    assert mixed_audit["numeric_citation_coverage_pct"] == 0


@pytest.mark.parametrize("prose", [
    (
        "后续应优先获取2024-2025年年报，以及2026年第一季度、"
        "第二季度和第三季度报告，用于重建同口径单季序列。"
    ),
    (
        "待补资料：1. 2025年年报；2. 2026年第一季度报告；"
        "3. 2026年半年报。以上均是检索计划，不是已发生的经营结论。"
    ),
    (
        "资料缺口：仍需获取2024-2025年单季利润表和2026年"
        "半年度报告，才能按相同会计基础做差异分析。"
    ),
])
def test_period_report_requests_do_not_inflate_numeric_coverage(tmp_path, prose):
    _db(tmp_path)
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._citation_audit(prose, set())["numeric_paragraphs"] == 0


def test_audio_pipeline_counts_are_metadata_but_company_order_count_is_not(tmp_path):
    _db(tmp_path)
    pipeline = (
        "录音候选16条，已完成8条转写；这些数字只记录冻结快照"
        "的处理进度，不代表公司订单、客户或产能。"
    )
    mixed = pipeline + (
        "公司已获得订20条，该数量属于公司经营断言，"
        "不能由前面的管线计数代替证据。"
    )

    assert IndustryResearchService._citation_audit(pipeline, set())["numeric_paragraphs"] == 0
    assert IndustryResearchService._citation_audit(mixed, set())["numeric_paragraphs"] == 1


def test_parenthesized_ppa_question_ledger_is_research_process(tmp_path):
    _db(tmp_path)
    prose = (
        "暂不能回答：（1）PPA完成后的商誉金额是多少？"
        "（2）2026年并表时点何时确定？（3）后续摊销计划"
        "需要哪些法定资料？这是待获取的研究问题清单。"
    )
    assert IndustryResearchService._is_nonassertive_research_process_paragraph(prose) is True
    assert IndustryResearchService._citation_audit(prose, set())["numeric_paragraphs"] == 0


def test_recent_30_day_timeline_is_temporal_but_percentage_still_needs_evidence(tmp_path):
    _db(tmp_path)
    temporal = (
        "图表【evidence_timeline｜证据发布时间分布】显示最近30日资料发布更集中；"
        "该变化只表示信息供给节奏，不表示公司收入、利润或行业"
        "景气已发生变化，并保留回到时间轴核验的入口。"
    )
    measured = temporal.replace(
        "资料发布更集中", "资料发布数增长304.8%",
    )

    temporal_audit = IndustryResearchService._citation_audit_with_figures(
        temporal, set(), {"evidence_timeline"},
    )
    measured_audit = IndustryResearchService._citation_audit_with_figures(
        measured, set(), {"evidence_timeline"},
    )

    assert temporal_audit["numeric_temporal_figure_supported_paragraphs"] == 1
    assert temporal_audit["numeric_citation_coverage_pct"] == 100
    assert measured_audit["numeric_temporal_figure_supported_paragraphs"] == 0
    assert measured_audit["numeric_citation_coverage_pct"] == 0


def test_source_mix_figure_never_supports_temporal_peak(tmp_path):
    _db(tmp_path)
    prose = (
        "图表【source_mix｜多源证据构成】显示资料峰值出现在2026年8月；"
        "但该图是来源构成图而非时间轴，因此不能用来支撑峰值日期，"
        "即使它是当前章节允许的真实图表也不例外。"
    )
    audit = IndustryResearchService._citation_audit_with_figures(
        prose, set(), {"source_mix"},
    )
    assert audit["numeric_paragraphs"] == 1
    assert audit["numeric_figure_supported_paragraphs"] == 1
    assert audit["numeric_temporal_figure_supported_paragraphs"] == 0
    assert audit["numeric_citation_coverage_pct"] == 0


def test_square_figure_shorthand_is_normalized_only_for_allowed_id(tmp_path):
    _db(tmp_path)
    prose = (
        "该时间轴展示2026年8月资料分布，仅用于证据时点核验，"
        "不将图表引用冒充外部事实证据，并明确保留图表白名单校验。"
    )
    allowed_text = prose + " [figure:evidence_timeline]"
    unknown_text = prose + " [figure:unknown_timeline]"

    allowed = IndustryResearchService._citation_audit_with_figures(
        allowed_text, set(), {"evidence_timeline"},
    )
    unknown = IndustryResearchService._citation_audit_with_figures(
        unknown_text, set(), {"evidence_timeline"},
    )
    normalized = IndustryResearchService._normalize_chapter_body(
        allowed_text, set(), {"evidence_timeline"},
    )

    assert allowed["citations"] == []
    assert allowed["unsupported_citations"] == []
    assert allowed["figure_references"] == ["evidence_timeline"]
    assert allowed["unsupported_figure_references"] == []
    assert unknown["unsupported_citations"] == []
    assert unknown["unsupported_figure_references"] == ["unknown_timeline"]
    assert "[figure:evidence_timeline]" not in normalized
    assert "图表【evidence_timeline】" in normalized


def test_revision_number_gate_ignores_resolved_conflict_and_editor_explanation_numbers(tmp_path):
    _db(tmp_path)
    resolved = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "不同报告期净资产",
        "values": ["33.64亿元", "38.17亿元"],
        "periods": ["2025Q3", "2026H1"],
        "units": ["亿元", "亿元"],
        "accounting_bases": ["法定报表", "法定报表"],
        "evidence_ids": ["filing:q3", "filing:h1"],
        "resolved_by_program": True,
        "program_verification": "primary_period_series_v20",
        "resolution": "已解决：不同报告期分别列示，口径统一且不直接比较。",
        "reason": "编辑说明同时提到1.20亿元、2.83亿元，仅用于上下文。",
    }
    unsupported = {
        "type": "unsupported_claim",
        "claim": "PE(TTM)高基数退出导致估值回落246.92倍",
        "reason": "上下文另有1.20亿元、33.64亿元和2.83亿元法定事实",
        "context": "1.20、33.64、2.83均不是本项争议值",
    }

    assert IndustryResearchService._editorial_finding_number_keys(resolved) == set()
    assert IndustryResearchService._editorial_finding_number_keys(unsupported) == {"246.92"}


def test_numeric_conflict_never_trusts_model_only_deleted_resolution(tmp_path):
    _db(tmp_path)
    issue = {
        "type": "numeric_conflict",
        "entity": "华懋科技",
        "metric": "归母净资产",
        "values": ["33.64亿元", "38.17亿元"],
        "units": ["亿元", "亿元"],
        "periods": ["2025Q3", "2026H1"],
        "accounting_bases": ["法定报表", "法定报表"],
        "evidence_ids": ["filing:q3", "filing:h1"],
        "resolution": "已删除该冲突，正文已经修正。",
    }
    assert IndustryResearchService._review_issue_resolved(issue) is False
    assert IndustryResearchService._editorial_finding_number_keys(issue) == {"33.64", "38.17"}


def test_all_generation_revision_and_editor_prompts_share_production_accounting_rules(tmp_path):
    _db(tmp_path)
    prompts = (
        industry_research_service._SYSTEM_PROMPT,
        industry_research_service._LONG_FORM_CHAPTER_PROMPT,
        industry_research_service._CHAPTER_CITATION_REPAIR_PROMPT,
        industry_research_service._CHAPTER_EDITORIAL_REPAIR_PROMPT,
        industry_research_service._EDITORIAL_REVIEW_PROMPT,
    )
    for prompt in prompts:
        assert "高基数退出" in prompt
        assert "越南产线/工厂爬坡" in prompt
        assert "不等于新增商誉" in prompt
        assert "股份支付费用1.20亿元" in prompt
        assert "33.64亿元写成2025年末" in prompt
        assert "对法定财务影响为零" in prompt
        assert "汽车主业内生增长乏力" in prompt


def test_editorial_number_keys_ignore_source_ids_and_surrounding_comparator(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": (
            "2026年一季度营收5.12亿元、净利润0.12亿元，剔除股份支付后净利润约"
            "5100万元（event:97497），但法定扣非净利润为685.63万元（filing:1225224760）"
        ),
        "reason": "5100万元与法定披露的685.63万元存在数量级差异",
        "evidence_ids": ["event:97497", "filing:1225224760"],
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"5100"}


def test_editorial_number_keys_use_primary_claim_before_explanatory_parenthesis(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "2025年上半年营收11.08亿元（用于计算2026年H1同比-1.53%的基数）",
        "reason": "该数值由全年25.03亿元与前三季度17.84亿元推算",
        "evidence_ids": ["report:1"],
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"11.08"}


def test_editorial_number_keys_ignore_product_labels_shorthand_years_and_source_ids(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "机构预测26年1.6T出货至少增长10倍至150-200万只（event:97055）",
        "reason": "该预测缺少明确基数，10倍与150-200万只不能同时核验",
        "evidence_ids": ["event:97055"],
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == {"10", "150", "200"}


def test_editorial_number_gate_does_not_lock_incidental_numbers_in_contradiction(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "contradiction",
        "issue": "前三季度现金流1.95亿元与全年4.21亿元属于不同期间",
        "resolution": "未解决",
    }

    assert IndustryResearchService._editorial_finding_number_keys(finding) == set()


def test_editorial_secondary_claim_accepts_explicit_audio_attribution(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "unsupported_claim",
        "claim": "管理层预计1.6T出货至少增长10倍至150-200万只",
        "reason": "该预测缺少明确基数",
        "evidence_ids": ["audio:1"],
    }

    failures = IndustryResearchService._editorial_repair_semantic_failures(
        "据管理层录音，相关预测为至少增长10倍至150-200万只，仅作线索且不纳入估值。",
        "该预测缺少明确基数，不作为情景输入。",
        [finding],
    )

    assert failures == []


def test_editorial_semantic_gate_catches_rounded_variant_in_same_metric_context(tmp_path):
    _db(tmp_path)
    finding = {
        "type": "numeric_conflict",
        "metric": "2026年上半年营业收入同比变动",
        "values": ["-1.53%", "+1.3736%"],
        "resolution": "未解决",
    }

    failures = IndustryResearchService._editorial_repair_semantic_failures(
        "Q2单季营收同比约1.37%，并作为增长判断。",
        "上半年营收口径仍需统一。",
        [finding],
    )

    assert any("未隔离" in item for item in failures)


def test_editorial_finding_routes_same_conflict_across_chapters(tmp_path):
    _db(tmp_path)
    chapters = [
        {
            "chapter_id": "financials", "title": "财务质量", "summary": "调整后利润3.5亿元",
            "body_markdown": "财务章节把3.5亿元作为调整后利润。",
        },
        {
            "chapter_id": "decision_dashboard", "title": "决策看板", "summary": "乐观情景",
            "body_markdown": "乐观情景使用3.02亿元作为估值输入。",
        },
    ]
    review = {
        "unsupported_claims": [],
        "numeric_conflicts": [{
            "metric": "调整后利润", "values": ["3.5亿元", "3.02亿元"],
            "periods": ["2025年"], "chapters": ["financials"], "resolution": "未解决",
        }],
        "contradictions": [],
    }

    routed = IndustryResearchService._editorial_findings_by_chapter(chapters, review)

    assert set(routed) == {"financials", "decision_dashboard"}


def test_editorial_finding_routes_secondary_number_only_with_matching_context(tmp_path):
    _db(tmp_path)
    chapters = [
        {
            "chapter_id": "events_risks", "title": "事件风险", "summary": "收购事项",
            "body_markdown": "据研报转述，公司拟收购富创优越57.84%股权。",
        },
        {
            "chapter_id": "business_model", "title": "商业模式", "summary": "业务整合",
            "body_markdown": "富创优越57.84%股权对应的收购安排仍需公告核验。",
        },
        {
            "chapter_id": "financials", "title": "财务", "summary": "毛利率",
            "body_markdown": "某项历史毛利率为57.84%。",
        },
    ]
    review = {
        "unsupported_claims": [{
            "claim": "公司发布收购富创优越57.84%股权草案",
            "chapter": "events_risks",
            "reason": "只有券商研报转述",
            "evidence_ids": ["report:1"],
        }],
        "numeric_conflicts": [], "contradictions": [],
    }

    routed = IndustryResearchService._editorial_findings_by_chapter(chapters, review)

    assert set(routed) == {"events_risks", "business_model"}


def test_editorial_findings_do_not_repair_already_resolved_rounding(tmp_path):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "expectations_valuation", "title": "估值",
        "summary": "PE约247倍", "body_markdown": "精确值246.92倍，正文按247倍列示。",
    }]
    review = {
        "unsupported_claims": [], "contradictions": [],
        "numeric_conflicts": [{
            "metric": "PE(TTM)", "values": ["246.92倍", "247倍"],
            "chapters": ["expectations_valuation"],
            "resolution": "已统一采用246.92倍为精确值，247倍为四舍五入展示。",
        }],
    }

    routed = IndustryResearchService._editorial_findings_by_chapter(chapters, review)

    assert routed == {}


def test_editorial_repair_retries_until_flagged_number_is_really_removed(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    base_text = "公司业务边界以固定证据快照为准，事实、观点与线索分层表达。" * 105
    responses = iter([
        {
            "chapter_title": "研究对象", "summary": "第二季度收入同比1.37%。",
            "body_markdown": base_text + "\n\n2026年第二季度收入同比1.37%待验证 [event:1]。",
            "evidence_ids": ["event:1"], "open_questions": [],
        },
        {
            "chapter_title": "研究对象", "summary": "单季增速暂无可验证口径。",
            "body_markdown": base_text + "\n\n现有累计披露不能推出第二季度单季同比，相关断言已删除 [event:1]。",
            "evidence_ids": ["event:1"], "open_questions": ["补充单季可比口径"],
        },
    ])

    class RepairAnalyzer:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(_request):
            return {
                "choices": [{"message": {"content": json.dumps(next(responses), ensure_ascii=False)}}],
                "usage": {},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapters = [{
        "chapter_id": "company_scope", "title": "研究对象", "summary": "原摘要",
        "body_markdown": base_text + " [event:1]", "evidence_ids": ["event:1"],
        "allowed_evidence_ids": ["event:1"], "allowed_figure_ids": [],
    }]
    review = {
        "unsupported_claims": [{
            "claim": "2026年第二季度收入同比1.37%", "chapter": "company_scope",
            "reason": "累计口径不能推出单季同比",
        }],
        "numeric_conflicts": [], "contradictions": [],
    }
    evidence = [{
        "evidence_id": "event:1", "kind": "event", "source": "测试",
        "title": "累计财务口径", "summary": "仅提供累计口径",
    }]

    with patch.object(service, "_research_analyzer", return_value=RepairAnalyzer()):
        corrected, _usage, metadata = service._repair_chapters_from_editorial_review(
            "华懋科技", "公司研究", {"visualizations": []}, chapters, evidence, review,
        )

    assert metadata["accepted_chapters"] == ["company_scope"]
    assert len(corrected[0]["editorial_revision"]["attempts"]) == 2
    assert corrected[0]["editorial_revision"]["attempts"][0]["accepted"] is False
    assert any(
        "无支持数字" in item
        for item in corrected[0]["editorial_revision"]["attempts"][0]["validation_failures"]
    )
    assert "1.37" not in corrected[0]["body_markdown"]
    assert "1.37" not in corrected[0]["summary"]


def test_editorial_repair_can_import_editor_finding_evidence_and_persist_allowlist(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    base_text = "公司治理事实以固定证据快照为准，相关判断均需保留日期与来源。" * 85
    captured = []

    class RepairAnalyzer:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(request):
            captured.append(json.loads(request["messages"][1]["content"]))
            payload = {
                "chapter_title": "事件风险",
                "summary": "结构化事实显示公司存在股权质押。",
                "body_markdown": base_text + "\n\n截至快照日，公司存在5.15%股权质押 [tushare:pledge:1]。",
                "evidence_ids": ["tushare:pledge:1"],
                "open_questions": [],
            }
            return {
                "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
                "usage": {},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapters = [{
        "chapter_id": "events_risks", "title": "事件风险", "summary": "原摘要",
        "body_markdown": base_text + " [event:1]", "evidence_ids": ["event:1"],
        "allowed_evidence_ids": ["event:1"], "allowed_figure_ids": [],
    }]
    review = {
        "unsupported_claims": [{
            "claim": "公司未发生股权质押", "chapter": "events_risks",
            "reason": "与结构化数据矛盾", "evidence_ids": ["tushare:pledge:1"],
        }],
        "numeric_conflicts": [], "contradictions": [],
    }
    evidence = [
        {"evidence_id": "event:1", "kind": "event", "title": "旧事件", "summary": "旧证据"},
        {
            "evidence_id": "tushare:pledge:1", "kind": "company_capital",
            "title": "股权质押统计", "summary": "质押比例5.15%",
        },
    ]

    with patch.object(service, "_research_analyzer", return_value=RepairAnalyzer()):
        corrected, _usage, metadata = service._repair_chapters_from_editorial_review(
            "华懋科技", "公司研究", {"visualizations": []}, chapters, evidence, review,
        )

    assert metadata["accepted_chapters"] == ["events_risks"]
    assert "tushare:pledge:1" in captured[0]["allowed_evidence_ids"]
    assert any(
        item["evidence_id"] == "tushare:pledge:1"
        for item in captured[0]["supplied_evidence"]
    )
    assert "tushare:pledge:1" in corrected[0]["allowed_evidence_ids"]
    assert "tushare:pledge:1" in corrected[0]["evidence_ids"]


def test_editorial_repair_failure_metadata_keeps_both_attempt_diagnostics(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    base_text = "研究对象与证据范围必须保持一致，所有结论只使用固定证据快照。" * 110

    class RepairAnalyzer:
        model = "kimi-for-coding"

        @staticmethod
        def _post_with_retry(_request):
            payload = {
                "chapter_title": "研究对象", "summary": "Q2营收同比1.37%",
                "body_markdown": base_text + "\n\nQ2单季营收同比1.37%待验证 [event:1]。",
                "evidence_ids": ["event:1"], "open_questions": [],
            }
            return {
                "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
                "usage": {},
            }

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

        @staticmethod
        def _parse_json(content):
            return json.loads(content)

    chapters = [{
        "chapter_id": "company_scope", "title": "研究对象", "summary": "原摘要",
        "body_markdown": base_text + " [event:1]", "evidence_ids": ["event:1"],
        "allowed_evidence_ids": ["event:1"], "allowed_figure_ids": [],
    }]
    review = {
        "unsupported_claims": [{
            "claim": "Q2单季营收同比1.37%", "chapter": "company_scope",
            "reason": "无可比基数", "evidence_ids": [],
        }],
        "numeric_conflicts": [], "contradictions": [],
    }
    evidence = [{"evidence_id": "event:1", "kind": "event", "title": "累计口径", "summary": "累计"}]

    with patch.object(service, "_research_analyzer", return_value=RepairAnalyzer()):
        _corrected, _usage, metadata = service._repair_chapters_from_editorial_review(
            "华懋科技", "公司研究", {"visualizations": []}, chapters, evidence, review,
        )

    details = metadata["failure_details"]["company_scope"]
    assert len(details["attempts"]) == 2
    assert all(item["validation_failures"] for item in details["attempts"])


def test_editor_resolution_distinguishes_fixed_precision_from_unresolved_conflict(tmp_path):
    _db(tmp_path)
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "统一采用合并报表披露值，差异来自四舍五入。",
    }) is True
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "未解决，需补充单体与并表口径原文。",
    }) is False
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "建议后续复核并统一口径。",
    }) is False
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "需要统一口径。",
    }) is False
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "差异来自未知原因。",
    }) is False
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "2.18亿元与约2.2亿元属于四舍五入，可统一为2.18亿元。",
    }) is True
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "口径统一：前三季度与全年属于期间差异，两个累计值逻辑自洽，建议修正文案。",
    }) is True
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "口径统一：H1与Q1可逻辑自洽，但仍需确认是否为同一口径。",
    }) is False
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "已解决：Q2约0.11亿元，与Q1基本持平。建议统一表述。",
    }) is True
    assert IndustryResearchService._review_issue_resolved({
        "resolution": "已解决部分，但仍需补充2025H1法定基数。",
    }) is False


def test_editor_dimension_normalizer_only_annotates_different_periods_and_bases(tmp_path):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [
            {
                "entity": "华懋科技",
                "metric": "剔除股份支付后归母净利润",
                "values": ["1.26亿元（H1累计）", "5100万元（Q1单季）"],
                "units": ["亿元", "万元"],
                "periods": ["2026年H1", "2026年Q1"],
                "accounting_bases": ["剔除股份支付", "剔除股份支付"],
                "resolution": "未解决",
            },
            {
                "entity": "富创优越",
                "metric": "富创优越净利润",
                "values": ["2.29亿元（实际）", "2.5亿元（业绩承诺）"],
                "units": ["亿元", "亿元"],
                "periods": ["2025年全年", "2026年承诺"],
                "accounting_bases": ["实际", "业绩承诺"],
                "resolution": "未解决",
            },
        ],
    }

    normalized = IndustryResearchService._normalize_editorial_dimensions(review)

    assert len(normalized["numeric_conflicts"]) == 2
    assert len(normalized["candidate_noncomparable_issues"]) == 1
    assert normalized["candidate_noncomparable_issues"][0]["entity"] == "华懋科技"
    assert normalized["candidate_noncomparable_issues"][0]["release_blocking"] is True
    assert normalized["release_recommendation"] == "limited"


def test_editor_dimension_detector_separates_reported_and_share_payment_adjusted_profit(tmp_path):
    _db(tmp_path)
    issue = {
        "entity": "华懋科技",
        "metric": "归母净利润",
        "values": ["2328.57万元", "12589.79万元"],
        "units": ["万元", "万元"],
        "periods": ["2026年H1", "2026年H1"],
        "accounting_bases": ["法定报表", "剔除股份支付后调整值"],
        "resolution": "两者为不同会计口径，不直接比较。",
    }

    assert IndustryResearchService._numeric_conflict_has_distinct_dimensions(issue) is True


def test_editor_dimension_normalizer_keeps_same_period_same_basis_conflict(tmp_path):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技",
            "metric": "2026年H1归母净利润",
            "values": ["1.20亿元（法定报表）", "1.50亿元（法定报表）"],
            "units": ["亿元", "亿元"],
            "periods": ["2026年H1", "2026年H1"],
            "accounting_bases": ["法定报表", "法定报表"],
            "resolution": "未解决",
        }],
    }

    normalized = IndustryResearchService._normalize_editorial_dimensions(review)

    assert len(normalized["numeric_conflicts"]) == 1
    assert normalized["release_recommendation"] == "limited"


def test_editor_dimension_normalizer_treats_period_aliases_as_same_period(tmp_path):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技",
            "metric": "归母净利润",
            "values": ["1.20亿元", "1.50亿元"],
            "units": ["亿元", "亿元"],
            "periods": ["2026年H1", "2026上半年"],
            "accounting_bases": ["法定报表", "审计实际数"],
            "resolution": "未解决",
        }],
    }

    normalized = IndustryResearchService._normalize_editorial_dimensions(review)

    assert len(normalized["numeric_conflicts"]) == 1
    assert normalized.get("candidate_noncomparable_issues") is None
    assert normalized["release_recommendation"] == "limited"


@pytest.mark.parametrize("missing_field", [
    "entity", "metric", "units", "periods", "accounting_bases",
])
def test_editor_dimension_normalizer_missing_dimension_stays_blocking(tmp_path, missing_field):
    _db(tmp_path)
    issue = {
        "entity": "华懋科技",
        "metric": "归母净利润",
        "values": ["1.20亿元", "1.50亿元"],
        "units": ["亿元", "亿元"],
        "periods": ["2026年H1", "2026年Q1"],
        "accounting_bases": ["法定报表", "法定报表"],
        "resolution": "未解决",
    }
    issue.pop(missing_field)
    review = {
        "release_recommendation": "ready",
        "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [issue],
    }

    normalized = IndustryResearchService._normalize_editorial_dimensions(review)

    assert normalized["numeric_conflicts"] == [issue]
    assert normalized.get("candidate_noncomparable_issues") is None
    assert normalized["release_recommendation"] == "limited"


def test_editor_dimension_normalizer_uses_structured_arrays_as_release_contract(tmp_path):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [],
    }

    normalized = IndustryResearchService._normalize_editorial_dimensions(review)

    assert normalized["release_recommendation"] == "ready"


def _huamao_q1_h1_numeric_review():
    return {
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技",
            "metric": "归母净资产/归属于上市公司股东的所有者权益",
            "values": [3_475_323_616.35, 3_817_464_934.50],
            "units": ["元", "元"],
            "periods": ["2026Q1", "2026H1"],
            "accounting_bases": [
                "statutory_attributable_equity",
                "statutory_attributable_equity",
            ],
            "evidence_ids": ["filing:q1", "filing:h1"],
            "resolution": "非冲突。两个值属于不同期间，是正常时序数据。",
        }],
    }


def _huamao_q1_h1_evidence():
    return {
        "filing:q1": {
            "evidence_id": "filing:q1", "kind": "filing_text",
            "title": "华懋科技2026年第一季度报告",
            "summary": "归属于上市公司股东的所有者权益3475323616.35元。",
            "symbol": "603306.SH", "period": "20260331",
        },
        "filing:h1": {
            "evidence_id": "filing:h1", "kind": "filing_text",
            "title": "华懋科技2026年半年度报告",
            "summary": "归属于上市公司股东的所有者权益3817464934.50元。",
            "symbol": "603306.SH", "period": "20260630",
        },
    }


def _huamao_q1_h1_chapters(*, causal=False, q1_citation_same_sentence=True):
    q1_citation = " [filing:q1]" if q1_citation_same_sentence else ""
    q1_tail = "" if q1_citation_same_sentence else "\n\n来源 [filing:q1]。"
    causal_tail = "，表明盈利质量改善" if causal else ""
    return [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2026年第一季度归属于上市公司股东的所有者权益"
            f"34.75亿元{q1_citation}。{q1_tail}\n\n"
            "华懋科技2026年上半年归属于上市公司股东的所有者权益"
            f"38.17亿元 [filing:h1]{causal_tail}。"
        ),
    }]


def test_editor_numeric_period_series_resolves_only_after_primary_evidence_binding(tmp_path):
    _db(tmp_path)
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(),
        _huamao_q1_h1_chapters(),
        _huamao_q1_h1_evidence(),
    )
    issue = reconciled["numeric_conflicts"][0]
    assert issue["resolved_by_program"] is True
    assert issue["program_verification"] == "primary_period_series_v20"
    assert issue["release_blocking"] is False
    assert IndustryResearchService._review_issue_resolved(issue) is True
    normalized = IndustryResearchService._normalize_editorial_dimensions(reconciled)
    assert normalized["release_recommendation"] == "ready"
    assert normalized.get("candidate_noncomparable_issues") is None


def _huamao_governing_editor_case():
    evidence = [
        {
            "evidence_id": "filing:1225505930", "kind": "filing_text",
            "company": "华懋科技", "symbol": "603306.SH",
            "title": "华懋科技2026年半年度报告", "report_period": "20260630",
            "document_text": (
                "华懋科技2026年半年度归属于上市公司股东的净利润"
                "23,285,735.42元。"
                "2026年上半年因实施员工持股计划产生的股份支付费用"
                "1.20亿元，较去年同期增加1.12亿元。"
                "2026年半年度报告 单位：元 币种：人民币 "
                "主要会计数据 本报告期（1－6月） 上年同期 "
                "本期比上年同期增减(%) 扣除股份支付影响后的净利润 "
                "125,897,911.25 143,317,500.70 -12.15。"
            ),
        },
        {
            "evidence_id": "financial:603306.SH:20260630",
            "kind": "financial_statement", "symbol": "603306.SH",
            "title": "华懋科技 20260630 财务快照", "period": "20260630",
            "summary": (
                "营业收入1091459912.33；归母净利润23285735.42；"
                "经营现金流282722433.18。"
            ),
        },
        {
            "evidence_id": "filing:1224752345", "kind": "filing_text",
            "company": "华懋科技", "symbol": "603306.SH",
            "title": "华懋科技2025年第三季度报告", "report_period": "20250930",
            "document_text": (
                "华懋科技2025年第三季度报告：归属于上市公司股东的净资产"
                "3,363,507,381.94元。"
            ),
        },
    ]
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": evidence,
    }
    facts = IndustryResearchService._build_governing_statutory_facts(snapshot)
    return (
        {item["evidence_id"]: item for item in evidence},
        facts,
        snapshot["subject"],
    )


def _huamao_h1_same_fact_review():
    return {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [], "missing_questions": [],
        "numeric_conflicts": [{
            "metric": "2026H1归母净利润",
            "values": ["0.23亿元", "2,328.57万元", "2328.57万元"],
            "units": ["亿元", "万元", "万元"],
            "periods": ["2026H1", "2026H1", "2026H1"],
            "accounting_bases": ["statutory", "statutory", "statutory"],
            "evidence_ids": [
                "filing:1225505930", "filing:1225505930", "filing:1225505930",
            ],
            "resolution": "统一口径为0.23亿元或2,328.57万元，不得混用。",
            "release_blocking": True,
        }],
    }


def test_editor_h1_same_statutory_fact_reconciles_equivalent_rounding_only_after_canonical_report(
    tmp_path,
):
    _db(tmp_path)
    evidence, governing, subject = _huamao_governing_editor_case()
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2026H1归母净利润2328.57万元 "
            "[filing:1225505930]。"
        ),
    }]

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_h1_same_fact_review(), chapters, evidence,
        expected_subject=subject, governing_facts=governing,
    )

    issue = reconciled["numeric_conflicts"][0]
    assert issue["resolved_by_program"] is True
    assert issue["program_verification"] == "governing_same_fact_representation_v25"
    assert issue["governing_exact_value"] == "23285735.42"
    assert issue["release_blocking"] is False
    assert IndustryResearchService._review_issue_resolved(issue) is True
    assert IndustryResearchService._editorial_finding_number_keys(issue) == set()
    normalized = IndustryResearchService._normalize_editorial_dimensions(reconciled)
    assert normalized["release_recommendation"] == "ready"


def _huamao_v26_same_fact_case(metric_key):
    cases = {
        "statutory_profit": {
            "metric": "归母净利润",
            "values": [23_285_735.42, 23_285_735.42],
            "units": ["元", "元"],
            "bases": ["statutory", "statutory"],
            "evidence_ids": [
                "financial:603306.SH:20260630", "filing:1225505930",
            ],
            "body": (
                "华懋科技2026H1归母净利润2328.57万元 "
                "[filing:1225505930]。"
            ),
            "exact": "23285735.42",
        },
        "share_payment": {
            "metric": "股份支付费用",
            "values": [1.2, 120_000_000],
            "units": ["亿元", "元"],
            "bases": [
                "share_based_payment_expense", "share_based_payment_expense",
            ],
            "evidence_ids": ["filing:1225505930", "filing:1225505930"],
            "body": (
                "2026H1，股份支付费用1.20亿元，扣除股份支付影响后的"
                "归母净利润1.26亿元 [filing:1225505930]。"
            ),
            "exact": "120000000",
        },
        "adjusted_profit": {
            "metric": "扣除股份支付影响后的归母净利润",
            "values": [125_897_911.25, 125_897_911.25],
            "units": ["元", "元"],
            "bases": [
                "non_gaap_excluding_share_based_payment",
                "non_gaap_excluding_share_based_payment",
            ],
            "evidence_ids": ["filing:1225505930", "filing:1225505930"],
            "body": (
                "2026H1，股份支付费用1.20亿元，扣除股份支付影响后的"
                "归母净利润1.26亿元 [filing:1225505930]。"
            ),
            "exact": "125897911.25",
        },
    }
    case = cases[metric_key]
    review = {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [], "missing_questions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": case["metric"],
            "values": list(case["values"]), "units": list(case["units"]),
            "periods": ["2026H1", "2026H1"],
            "accounting_bases": list(case["bases"]),
            "evidence_ids": list(case["evidence_ids"]),
            "resolution": "模型认为需要统一展示口径。",
            "release_blocking": True,
        }],
    }
    chapters = [{"chapter_id": "financials", "body_markdown": case["body"]}]
    return review, chapters, case


@pytest.mark.parametrize(
    "metric_key", ["statutory_profit", "share_payment", "adjusted_profit"],
)
def test_editor_v26_production_same_fact_findings_resolve_only_from_governing_contract(
    tmp_path, metric_key,
):
    _db(tmp_path)
    evidence, governing, subject = _huamao_governing_editor_case()
    review, chapters, case = _huamao_v26_same_fact_case(metric_key)

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject=subject, governing_facts=governing,
    )

    issue = reconciled["numeric_conflicts"][0]
    assert issue["resolved_by_program"] is True
    assert issue["program_verification"] == "governing_same_fact_representation_v25"
    assert issue["governing_exact_value"] == case["exact"]
    assert issue["governing_evidence_id"] == "filing:1225505930"
    assert issue["verified_observation_evidence_ids"] == case["evidence_ids"]
    assert issue["release_blocking"] is False
    assert IndustryResearchService._review_issue_resolved(issue) is True


def _huamao_v27_balance_editor_case(*, q3_structured_value=None):
    h1_text = (
        "华懋（厦门）新材料科技股份有限公司2026年半年度报告\n"
        "单位：元 币种：人民币\n"
        "本报告期末 上年度末 本报告期末比上年度末增减(%)\n"
        "归属于上市公司股东的净资产 "
        "3,817,464,934.50 3,429,966,675.77 11.30\n"
        "总资产 6,171,145,144.82 5,993,670,009.88 2.96\n"
    )
    q3_structured_summary = "总资产5800000000.00；总负债2436492618.06。"
    if q3_structured_value is not None:
        q3_structured_summary += f"归母净资产{q3_structured_value}。"
    evidence = [
        {
            "evidence_id": "filing:1225505930", "kind": "filing_text",
            "company": "华懋科技", "symbol": "603306.SH",
            "title": "华懋科技2026年半年度报告", "report_period": "20260630",
            "document_text": h1_text,
        },
        {
            "evidence_id": "financial:603306.SH:20251231",
            "kind": "financial_statement", "symbol": "603306.SH",
            "title": "华懋科技 20251231 财务快照", "period": "20251231",
            "summary": "总资产5993670009.88；总负债2563703334.11。",
        },
        {
            "evidence_id": "filing:1224752345", "kind": "filing_text",
            "company": "华懋科技", "symbol": "603306.SH",
            "title": "华懋科技2025年第三季度报告", "report_period": "20250930",
            "document_text": (
                "华懋科技2025年第三季度报告：归属于上市公司股东的净资产"
                "3,363,507,381.94元。"
            ),
        },
        {
            "evidence_id": "financial:603306.SH:20250930",
            "kind": "financial_statement", "symbol": "603306.SH",
            "title": "华懋科技 20250930 财务快照", "period": "20250930",
            "summary": q3_structured_summary,
        },
    ]
    snapshot = {
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "evidence": evidence,
    }
    return (
        {item["evidence_id"]: item for item in evidence},
        IndustryResearchService._build_governing_statutory_facts(snapshot),
        snapshot["subject"],
    )


def test_editor_v27_identical_year_end_assets_resolve_from_two_exact_sources(tmp_path):
    _db(tmp_path)
    evidence, governing, subject = _huamao_v27_balance_editor_case()
    review = {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [], "missing_questions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": "2025年末总资产",
            "values": [5_993_670_009.88, 5_993_670_009.88],
            "units": ["元", "元"], "periods": ["2025FY", "20251231"],
            "accounting_bases": ["statutory_balance_sheet", "statutory_balance_sheet"],
            "evidence_ids": [
                "filing:1225505930", "financial:603306.SH:20251231",
            ],
            "resolution": "模型建议复核。", "release_blocking": True,
        }],
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": "华懋科技2025年末总资产59.94亿元 [filing:1225505930]。",
    }]

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject=subject, governing_facts=governing,
    )

    issue = reconciled["numeric_conflicts"][0]
    assert issue["resolved_by_program"] is True
    assert issue["governing_period"] == "2025FY"
    assert issue["governing_exact_value"] == "5993670009.88"
    assert issue["release_blocking"] is False
    assert IndustryResearchService._review_issue_resolved(issue) is True


def test_editor_v27_q3_equity_phantom_structured_observation_is_not_a_conflict(tmp_path):
    _db(tmp_path)
    evidence, governing, subject = _huamao_v27_balance_editor_case()
    review = {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [], "missing_questions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": "2025年第三季度末归母净资产",
            "values": [3_363_507_381.94, 3_363_507_381.94],
            "units": ["元", "元"], "periods": ["2025Q3", "20250930"],
            "accounting_bases": [
                "statutory_attributable_equity", "statutory_attributable_equity",
            ],
            "evidence_ids": [
                "filing:1224752345", "financial:603306.SH:20250930",
            ],
            "resolution": "Tushare未直接显示该字段，需要复核。",
            "release_blocking": True,
        }],
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025Q3归母净资产33.64亿元 [filing:1224752345]。"
        ),
    }]

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject=subject, governing_facts=governing,
    )

    assert reconciled["numeric_conflicts"] == []
    assert reconciled["reclassified_numeric_conflicts"][0]["program_verification"] == (
        "absent_structured_metric_v27"
    )


@pytest.mark.parametrize(
    "mutation",
    ["competing_value", "wrong_subject", "wrong_period", "wrong_basis", "wrong_report"],
)
def test_editor_v27_balance_same_fact_guards_fail_closed(tmp_path, mutation):
    _db(tmp_path)
    q3_value = 3_400_000_000.0 if mutation == "competing_value" else None
    evidence, governing, subject = _huamao_v27_balance_editor_case(
        q3_structured_value=q3_value,
    )
    issue = {
        "entity": "华懋科技", "metric": "2025年第三季度末归母净资产",
        "values": [3_363_507_381.94, 3_363_507_381.94],
        "units": ["元", "元"], "periods": ["2025Q3", "20250930"],
        "accounting_bases": [
            "statutory_attributable_equity", "statutory_attributable_equity",
        ],
        "evidence_ids": [
            "filing:1224752345", "financial:603306.SH:20250930",
        ],
        "resolution": "需要复核。", "release_blocking": True,
    }
    body = "华懋科技2025Q3归母净资产33.64亿元 [filing:1224752345]。"
    if mutation == "wrong_subject":
        issue["entity"] = "胜宏科技"
    elif mutation == "wrong_period":
        issue["periods"][1] = "2025FY"
    elif mutation == "wrong_basis":
        issue["accounting_bases"][1] = "management_adjusted"
    elif mutation == "wrong_report":
        body = "华懋科技2025Q3归母净资产34.00亿元 [filing:1224752345]。"
    review = {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [], "missing_questions": [],
        "numeric_conflicts": [issue],
    }

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, [{"chapter_id": "financials", "body_markdown": body}], evidence,
        expected_subject=subject, governing_facts=governing,
    )

    assert len(reconciled["numeric_conflicts"]) == 1
    assert reconciled["numeric_conflicts"][0].get("resolved_by_program") is not True


@pytest.mark.parametrize(
    ("metric_key", "mutation"),
    [
        ("statutory_profit", "wrong_entity"),
        ("statutory_profit", "wrong_metric"),
        ("statutory_profit", "wrong_period"),
        ("statutory_profit", "wrong_basis"),
        ("statutory_profit", "wrong_unit"),
        ("statutory_profit", "wrong_evidence"),
        ("statutory_profit", "wrong_financial_payload"),
        ("statutory_profit", "real_value_conflict"),
        ("statutory_profit", "noncanonical_report"),
        ("share_payment", "wrong_metric"),
        ("share_payment", "wrong_basis"),
        ("share_payment", "real_value_conflict"),
        ("share_payment", "noncanonical_report"),
        ("adjusted_profit", "wrong_metric"),
        ("adjusted_profit", "wrong_basis"),
        ("adjusted_profit", "real_value_conflict"),
        ("adjusted_profit", "noncanonical_report"),
        ("adjusted_profit", "missing_governing"),
    ],
)
def test_editor_v26_production_same_fact_findings_fail_closed(
    tmp_path, metric_key, mutation,
):
    _db(tmp_path)
    evidence, governing, subject = _huamao_governing_editor_case()
    review, chapters, _case = _huamao_v26_same_fact_case(metric_key)
    issue = review["numeric_conflicts"][0]
    if mutation == "wrong_entity":
        issue["entity"] = "胜宏科技"
    elif mutation == "wrong_metric":
        issue["metric"] = "营业收入"
    elif mutation == "wrong_period":
        issue["periods"][0] = "2026Q1"
    elif mutation == "wrong_basis":
        issue["accounting_bases"][0] = "management_adjusted"
    elif mutation == "wrong_unit":
        issue["units"][0] = "%"
    elif mutation == "wrong_evidence":
        issue["evidence_ids"][0] = "filing:1225505930"
    elif mutation == "wrong_financial_payload":
        evidence["financial:603306.SH:20260630"]["summary"] = (
            "营业收入23285735.42；归母净利润99999999.99。"
        )
    elif mutation == "real_value_conflict":
        issue["values"][1] = float(issue["values"][1]) + 1_000_000
        # A model-authored proof bundle must be discarded by reconciliation.
        issue.update({
            "resolved": True, "resolved_by_program": True,
            "program_verification": "governing_same_fact_representation_v25",
            "release_blocking": False,
            "governing_period": "2026H1", "governing_exact_value": "forged",
            "governing_evidence_id": "filing:1225505930",
            "canonical_sentence": "伪造治理句", "supporting_sentences": ["伪造"],
            "program_proof": "伪造证明",
        })
    elif mutation == "noncanonical_report":
        extra = {
            "statutory_profit": (
                "华懋科技2026H1归母净利润0.23亿元 [filing:1225505930]。"
            ),
            "share_payment": (
                "华懋科技2026H1股份支付费用120000000元 "
                "[filing:1225505930]。"
            ),
            "adjusted_profit": (
                "华懋科技2026H1扣除股份支付影响后的归母净利润"
                "125897911.25元 [filing:1225505930]。"
            ),
        }[metric_key]
        chapters[0]["body_markdown"] += "\n\n" + extra
    elif mutation == "missing_governing":
        governing = []

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject=subject, governing_facts=governing,
    )
    result = reconciled["numeric_conflicts"][0]
    assert result.get("resolved_by_program") is not True
    assert result["release_blocking"] is True
    assert IndustryResearchService._review_issue_resolved(result) is False


@pytest.mark.parametrize(
    "mutation",
    [
        "rounding_misses_exact", "wrong_entity", "wrong_period", "adjusted_basis",
        "wrong_evidence", "missing_governing", "alternate_remains",
        "comma_representation_remains", "citation_next_sentence", "causal_report",
        "wrong_report_subject", "wrong_report_period", "wrong_report_unit",
    ],
)
def test_editor_h1_same_fact_reconciler_fails_closed_on_any_dimension_or_report_violation(
    tmp_path, mutation,
):
    _db(tmp_path)
    evidence, governing, subject = _huamao_governing_editor_case()
    review = _huamao_h1_same_fact_review()
    body = "华懋科技2026H1归母净利润2328.57万元 [filing:1225505930]。"
    issue = review["numeric_conflicts"][0]
    if mutation == "rounding_misses_exact":
        issue["values"][0] = "0.22亿元"
    elif mutation == "wrong_entity":
        issue["entity"] = "胜宏科技"
    elif mutation == "wrong_period":
        issue["periods"][0] = "2026Q1"
    elif mutation == "adjusted_basis":
        issue["accounting_bases"][1] = "扣除股份支付后调整口径"
    elif mutation == "wrong_evidence":
        issue["evidence_ids"][2] = "filing:other"
    elif mutation == "missing_governing":
        governing = []
    elif mutation == "alternate_remains":
        body += "\n\n华懋科技2026H1归母净利润0.23亿元 [filing:1225505930]。"
    elif mutation == "comma_representation_remains":
        body = "华懋科技2026H1归母净利润2,328.57万元 [filing:1225505930]。"
    elif mutation == "citation_next_sentence":
        body = "华懋科技2026H1归母净利润2328.57万元。来源 [filing:1225505930]。"
    elif mutation == "causal_report":
        body = (
            "华懋科技2026H1归母净利润2328.57万元，"
            "主要来自经营改善 [filing:1225505930]。"
        )
    elif mutation == "wrong_report_subject":
        body = "胜宏科技2026H1归母净利润2328.57万元 [filing:1225505930]。"
    elif mutation == "wrong_report_period":
        body = "华懋科技2026Q1归母净利润2328.57万元 [filing:1225505930]。"
    elif mutation == "wrong_report_unit":
        body = "华懋科技2026H1归母净利润2328.57亿元 [filing:1225505930]。"

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, [{"chapter_id": "financials", "body_markdown": body}], evidence,
        expected_subject=subject, governing_facts=governing,
    )
    result = reconciled["numeric_conflicts"][0]
    assert result.get("resolved_by_program") is not True
    assert result["release_blocking"] is True
    assert IndustryResearchService._review_issue_resolved(result) is False
    assert IndustryResearchService._editorial_finding_number_keys(
        {"type": "numeric_conflict", **result}
    )


def _huamao_q3_missing_endpoint_review():
    return {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [], "missing_questions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": "2025Q3归母净资产",
            "values": ["33.64亿元", "未直接给出2025年末归母净资产"],
            "units": ["亿元", "亿元"],
            "periods": ["2025Q3", "2025FY"],
            "accounting_bases": [
                "statutory_attributable_equity", "statutory_attributable_equity",
            ],
            "evidence_ids": ["filing:1224752345", "filing:1225505930"],
            "resolution": "未解决。建议删除差额表述，仅分别列示两个期间原始值。",
            "release_blocking": True,
        }],
    }


def test_editor_q3_value_and_missing_fy_endpoint_are_reclassified_not_numeric_conflict(
    tmp_path,
):
    _db(tmp_path)
    evidence, governing, subject = _huamao_governing_editor_case()
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025Q3归母净资产33.64亿元 [filing:1224752345]。\n\n"
            "华懋科技2026H1归母净利润2328.57万元 [filing:1225505930]。"
        ),
    }]

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q3_missing_endpoint_review(), chapters, evidence,
        expected_subject=subject, governing_facts=governing,
    )

    assert reconciled["numeric_conflicts"] == []
    assert len(reconciled["reclassified_numeric_conflicts"]) == 1
    assert reconciled["reclassified_numeric_conflicts"][0]["program_verification"] == (
        "non_numeric_missing_endpoint_v25"
    )
    assert any("2025年末归母净资产" in item for item in reconciled["missing_questions"])
    normalized = IndustryResearchService._normalize_editorial_dimensions(reconciled)
    assert normalized["release_recommendation"] == "ready"


@pytest.mark.parametrize(
    "mutation",
    [
        "second_value_is_numeric", "wrong_entity", "wrong_period", "wrong_evidence",
        "wrong_basis", "missing_governing", "q3_masquerades_as_fy",
        "cross_period_difference", "cross_period_causality", "wrong_q3_value",
        "citation_next_sentence",
    ],
)
def test_editor_q3_missing_endpoint_reclassification_fails_closed_on_real_conflict_or_misuse(
    tmp_path, mutation,
):
    _db(tmp_path)
    evidence, governing, subject = _huamao_governing_editor_case()
    review = _huamao_q3_missing_endpoint_review()
    issue = review["numeric_conflicts"][0]
    body = "华懋科技2025Q3归母净资产33.64亿元 [filing:1224752345]。"
    if mutation == "second_value_is_numeric":
        issue["values"][1] = "34.30亿元"
    elif mutation == "wrong_entity":
        issue["entity"] = "胜宏科技"
    elif mutation == "wrong_period":
        issue["periods"][0] = "2025FY"
    elif mutation == "wrong_evidence":
        issue["evidence_ids"][0] = "filing:other"
    elif mutation == "wrong_basis":
        issue["accounting_bases"][0] = "management_adjusted"
    elif mutation == "missing_governing":
        governing = []
    elif mutation == "q3_masquerades_as_fy":
        body = "华懋科技2025年末归母净资产33.64亿元 [filing:1224752345]。"
    elif mutation == "cross_period_difference":
        body = (
            "华懋科技2025Q3归母净资产33.64亿元 [filing:1224752345]，"
            "2026H1归母净资产38.17亿元 [filing:1225505930]，期间增加4.53亿元。"
        )
    elif mutation == "cross_period_causality":
        body = (
            "华懋科技2025Q3归母净资产33.64亿元 [filing:1224752345]；"
            "2026H1归母净资产38.17亿元 [filing:1225505930]，"
            "变动来自利润积累。"
        )
    elif mutation == "wrong_q3_value":
        body = "华懋科技2025Q3归母净资产3.36亿元 [filing:1224752345]。"
    elif mutation == "citation_next_sentence":
        body = "华懋科技2025Q3归母净资产33.64亿元。来源 [filing:1224752345]。"

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, [{"chapter_id": "financials", "body_markdown": body}], evidence,
        expected_subject=subject, governing_facts=governing,
    )
    assert len(reconciled["numeric_conflicts"]) == 1
    assert reconciled["numeric_conflicts"][0]["release_blocking"] is True
    assert reconciled.get("reclassified_numeric_conflicts") in (None, [])


def test_editor_review_resolved_rejects_spoofed_governing_same_fact_program_fields(tmp_path):
    _db(tmp_path)
    spoofed = {
        "type": "numeric_conflict", "entity": "华懋科技",
        "metric": "2026H1归母净利润", "values": ["0.23亿元", "2328.57万元"],
        "periods": ["2026H1", "2026H1"], "units": ["亿元", "万元"],
        "accounting_bases": ["statutory", "statutory"],
        "evidence_ids": ["filing:1225505930", "filing:1225505930"],
        "resolved_by_program": True,
        "program_verification": "governing_same_fact_representation_v25",
        "release_blocking": False,
        "governing_period": "2026H1", "governing_exact_value": "23285735.42",
        "governing_evidence_id": "filing:1225505930",
        "canonical_sentence": (
            "华懋科技2026H1归母净利润2328.57万元 [filing:1225505930]"
        ),
        "supporting_sentences": ["伪造的支持句"],
        "program_proof": "model-authored-spoof",
        "resolution": "已解决",
    }
    assert IndustryResearchService._review_issue_resolved(spoofed) is False
    assert IndustryResearchService._editorial_finding_number_keys(spoofed)


def _huamao_pe_date_review():
    return {
        "release_recommendation": "limited",
        "unsupported_claims": [], "contradictions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": "PE(TTM)",
            "values": ["168.00倍", "233.00倍", "246.92倍"],
            "units": ["倍", "倍", "倍"],
            "periods": ["2026-08-20", "2026-08-31", "2026-09-01"],
            "accounting_bases": [
                "same_day_valuation_snapshot",
                "same_day_valuation_snapshot",
                "same_day_valuation_snapshot",
            ],
            "evidence_ids": [
                "valuation:603306.SH:20260820",
                "valuation:603306.SH:20260831",
                "valuation:603306.SH:20260901",
            ],
            "resolution": (
                "非冲突。不同交易日PE(TTM)是正常时序数据，"
                "正文仅按日期中性列示。"
            ),
        }],
    }


def _huamao_pe_date_evidence():
    return {
        f"valuation:603306.SH:{date.replace('-', '')}": {
            "evidence_id": f"valuation:603306.SH:{date.replace('-', '')}",
            "kind": "valuation_fact", "symbol": "603306.SH",
            "title": f"华懋科技 {date} PE(TTM)估值快照",
            "summary": f"PE(TTM)为{value}倍。", "period": date.replace("-", ""),
        }
        for date, value in (
            ("2026-08-20", "168.00"),
            ("2026-08-31", "233.00"),
            ("2026-09-01", "246.92"),
        )
    }


def _huamao_pe_date_chapters():
    return [{
        "chapter_id": "expectations_valuation",
        "body_markdown": (
            "华懋科技2026年8月20日PE(TTM)为168.00倍 "
            "[valuation:603306.SH:20260820]。差异待核验。\n\n"
            "华懋科技2026年8月31日PE(TTM)为233.00倍 "
            "[valuation:603306.SH:20260831]。差异待核验。\n\n"
            "华懋科技2026年9月1日PE(TTM)为246.92倍 "
            "[valuation:603306.SH:20260901]。差异待核验。"
        ),
    }]


def test_editor_pe_multi_date_neutral_series_resolves_after_exact_primary_binding(tmp_path):
    _db(tmp_path)
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_pe_date_review(),
        _huamao_pe_date_chapters(),
        _huamao_pe_date_evidence(),
    )
    issue = reconciled["numeric_conflicts"][0]
    assert issue["resolved_by_program"] is True
    assert issue["program_verification"] == "primary_period_series_v20"
    assert issue["release_blocking"] is False
    normalized = IndustryResearchService._normalize_editorial_dimensions(reconciled)
    assert normalized["release_recommendation"] == "ready"


def test_editor_pe_production_compact_series_resolves_with_exact_valuation_scope(tmp_path):
    _db(tmp_path)
    values = [168.3423, 233.9584, 244.3349, 243.0417, 246.9211]
    dates = ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31"]
    evidence_ids = [
        f"valuation:603306.SH:{date.replace('-', '')}" for date in dates
    ]
    review = {
        "status": "completed",
        "release_recommendation": "limited",
        "unsupported_claims": [],
        "contradictions": [{
            "issue": (
                "PE(TTM)跨日期变化被暗示为分母端变化，但终稿已经要求"
                "只作中性列示"
            ),
            "chapters": ["expectations_valuation"],
            "evidence_ids": evidence_ids,
            "resolution": "未解决：删除暗示性因果表述",
        }],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": "PE(TTM)",
            "values": values, "units": ["倍"] * len(values),
            "periods": dates,
            "accounting_bases": ["market_observation"] * len(values),
            "evidence_ids": evidence_ids,
            "resolution": (
                "非冲突：不同交易日PE(TTM)属于不同日期的市场观察值，"
                "正文仅按日期中性列示"
            ),
            "release_blocking": True,
        }],
    }
    evidence = {
        evidence_id: {
            "evidence_id": evidence_id,
            "kind": "valuation_fact",
            "symbol": "603306.SH",
            "title": f"华懋科技 {date.replace('-', '')} 估值与筹码事实",
            "summary": f"PE(TTM) {value} 倍。",
            "date": date.replace("-", ""),
        }
        for evidence_id, date, value in zip(evidence_ids, dates, values)
    }
    chapter = {
        "chapter_id": "expectations_valuation",
        "body_markdown": (
            "截至2026年8月31日，华懋科技PE(TTM)为246.9211倍 "
            "[valuation:603306.SH:20260831]；"
            "8月25日PE(TTM)为168.3423倍 [valuation:603306.SH:20260825]；"
            "8月26日PE(TTM)为233.9584倍 [valuation:603306.SH:20260826]；"
            "8月27日PE(TTM)为244.3349倍 [valuation:603306.SH:20260827]；"
            "8月28日PE(TTM)为243.0417倍 [valuation:603306.SH:20260828]。\n"
            "各日期PE(TTM)仅作中性列示，差异待核验；"
            "不得据此推导业务原因或估值趋势。"
        ),
        "summary": "各日期PE(TTM)仅作中性列示，差异待核验。",
        "validation_failures": [],
        "storage_validation_acceptable": True,
    }
    reconciled = IndustryResearchService._reconcile_final_editorial_state(
        review,
        [chapter],
        evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )

    numeric_issue = reconciled["numeric_conflicts"][0]
    contradiction = reconciled["contradictions"][0]
    assert numeric_issue["program_verification"] == "primary_period_series_v20"
    assert numeric_issue["release_blocking"] is False
    assert contradiction["program_verification"] == "neutral_primary_period_series_v29"
    assert IndustryResearchService._review_issue_resolved(contradiction) is True
    assert reconciled["release_recommendation"] == "ready"


def test_final_editorial_state_uses_final_storage_validation_not_historical_failures(tmp_path):
    _db(tmp_path)
    review = {
        "status": "completed",
        "release_recommendation": "ready",
        "unsupported_claims": [], "numeric_conflicts": [], "contradictions": [],
        "revision_cycle": {
            "attempted": True,
            "cycles": [{"cycle": 1, "failed_chapters": ["company_scope"]}],
            "affected_chapters": ["company_scope"],
            "accepted_chapters": [],
            "failed_chapters": ["company_scope"],
        },
    }
    chapters = [{
        "chapter_id": "company_scope",
        "body_markdown": "华懋科技研究范围已经完成最终存储校验。",
        "summary": "研究范围清晰。",
        "validation_failures": [],
        "storage_validation_acceptable": True,
    }]

    reconciled = IndustryResearchService._reconcile_final_editorial_state(
        review, chapters, {},
    )

    revision = reconciled["revision_cycle"]
    assert revision["historical_failed_chapters"] == ["company_scope"]
    assert revision["failed_chapters"] == []
    assert revision["accepted_chapters"] == ["company_scope"]
    assert revision["final_storage_reconciled"] is True


def test_evidence_freshness_figure_supports_only_temporal_buckets(tmp_path):
    _db(tmp_path)
    temporal = "图表【evidence_freshness】按7日内、8-30日和1年以上展示证据时间桶。"
    measured = "图表【evidence_freshness】显示归母净利润1.26亿元。"

    assert IndustryResearchService._has_only_temporal_numeric_claims(temporal) is True
    assert IndustryResearchService._has_allowed_temporal_figure(
        temporal, {"evidence_freshness"},
    ) is True
    assert IndustryResearchService._has_only_temporal_numeric_claims(measured) is False


def test_editor_pe_multi_date_series_stays_blocking_on_wrong_primary_value(tmp_path):
    _db(tmp_path)
    evidence = _huamao_pe_date_evidence()
    evidence["valuation:603306.SH:20260831"]["summary"] = "PE(TTM)为230.00倍。"
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_pe_date_review(), _huamao_pe_date_chapters(), evidence,
    )
    issue = reconciled["numeric_conflicts"][0]
    assert issue.get("resolved_by_program") is not True
    assert issue["release_blocking"] is True


def test_editor_numeric_period_series_accepts_leading_period_clause(tmp_path):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "截至2026年第一季度，华懋科技归属于上市公司股东的所有者权益"
            "34.75亿元 [filing:q1]。\n\n"
            "截至2026年上半年，华懋科技归属于上市公司股东的所有者权益"
            "38.17亿元 [filing:h1]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0]["resolved_by_program"] is True


def test_editor_numeric_period_series_accepts_correct_markdown_table(tmp_path):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "| 主体 | 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
            "|---|---|---:|---|\n"
            "| 华懋科技 | 2026Q1 | 34.75亿元 | [filing:q1] |\n"
            "| 华懋科技 | 2026H1 | 38.17亿元 | [filing:h1] |"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    issue = reconciled["numeric_conflicts"][0]
    assert issue["resolved_by_program"] is True
    assert issue["program_verification"] == "primary_period_series_v20"


def test_editor_numeric_period_series_accepts_table_after_intro_line(tmp_path):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "以下为法定报表口径：\n"
            "| 主体 | 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
            "|---|---|---:|---|\n"
            "| 华懋科技 | 2026Q1 | 34.75亿元 | [filing:q1] |\n"
            "| 华懋科技 | 2026H1 | 38.17亿元 | [filing:h1] |"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0]["resolved_by_program"] is True


def test_editor_numeric_period_series_accepts_equal_values_with_distinct_binding(tmp_path):
    _db(tmp_path)
    review = _huamao_q1_h1_numeric_review()
    review["numeric_conflicts"][0]["values"] = [
        3_475_323_616.35, 3_475_323_616.35,
    ]
    evidence = _huamao_q1_h1_evidence()
    evidence["filing:h1"]["summary"] = (
        "归属于上市公司股东的所有者权益3475323616.35元。"
    )
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "| 主体 | 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
            "|---|---|---:|---|\n"
            "| 华懋科技 | 2026Q1 | 34.75亿元 | [filing:q1] |\n"
            "| 华懋科技 | 2026H1 | 34.75亿元 | [filing:h1] |"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
    )
    assert reconciled["numeric_conflicts"][0]["resolved_by_program"] is True


def test_editor_numeric_period_series_accepts_generic_value_column_under_metric_heading(
    tmp_path,
):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "### 华懋科技归属于上市公司股东的所有者权益复核\n\n"
            "| 期间 | 数值 | 来源 |\n"
            "|---|---:|---|\n"
            "| 2026Q1 | 34.75亿元 | [filing:q1] |\n"
            "| 2026H1 | 38.17亿元 | [filing:h1] |"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0]["resolved_by_program"] is True


@pytest.mark.parametrize(
    ("q1_value", "h1_value"),
    [
        ("34.75亿元", "38.17亿元"),
        ("347532.36万元", "381746.49万元"),
        ("3475323616.35元", "3817464934.50元"),
    ],
)
def test_editor_numeric_period_series_accepts_equivalent_currency_units(
    tmp_path, q1_value, h1_value,
):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "| 主体 | 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
            "|---|---|---:|---|\n"
            f"| 华懋科技 | 2026Q1 | {q1_value} | [filing:q1] |\n"
            f"| 华懋科技 | 2026H1 | {h1_value} | [filing:h1] |"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0]["resolved_by_program"] is True


def test_editor_numeric_period_series_accepts_explicit_unit_from_metric_header(tmp_path):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "| 主体 | 期间 | 归属于上市公司股东的所有者权益（亿元） | 来源 |\n"
            "|---|---|---:|---|\n"
            "| 华懋科技 | 2026Q1 | 34.75 | [filing:q1] |\n"
            "| 华懋科技 | 2026H1 | 38.17 | [filing:h1] |"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0]["resolved_by_program"] is True


@pytest.mark.parametrize("suffix", ["万元", "元", "%", "倍", ""])
def test_editor_numeric_period_series_rejects_wrong_or_missing_report_unit(
    tmp_path, suffix,
):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2026Q1归属于上市公司股东的所有者权益"
            f"34.75{suffix} [filing:q1]。\n\n"
            "华懋科技2026H1归属于上市公司股东的所有者权益"
            f"38.17{suffix} [filing:h1]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0].get("resolved_by_program") is not True


@pytest.mark.parametrize("phrase", [
    "反映出盈利改善", "说明盈利改善", "体现资本扩张",
    "由此可见经营改善", "得益于经营积累",
])
def test_editor_numeric_period_series_rejects_expanded_causal_language(
    tmp_path, phrase,
):
    _db(tmp_path)
    chapters = _huamao_q1_h1_chapters()
    chapters[0]["body_markdown"] = chapters[0]["body_markdown"].replace(
        "34.75亿元 [filing:q1]", f"34.75亿元 [filing:q1]，{phrase}",
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_q1_h1_numeric_review(), chapters, _huamao_q1_h1_evidence(),
    )
    assert reconciled["numeric_conflicts"][0].get("resolved_by_program") is not True


def test_editor_numeric_period_series_rejects_shared_cutoff_date_for_q2_vs_h1(
    tmp_path,
):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited", "unsupported_claims": [],
        "contradictions": [],
        "numeric_conflicts": [{
            "entity": "华懋科技", "metric": "营业收入",
            "values": [1_000_000_000.0, 2_000_000_000.0],
            "units": ["元", "元"], "periods": ["2026Q2", "2026H1"],
            "accounting_bases": ["single_quarter", "ytd_h1"],
            "evidence_ids": ["financial:q2", "financial:h1"],
            "resolution": "非冲突。两个值属于不同期间，是正常时序数据。",
        }],
    }
    evidence = {
        "financial:q2": {
            "evidence_id": "financial:q2", "kind": "financial_statement",
            "title": "华懋科技2026Q2单季营业收入", "summary": "营业收入10亿元",
            "period": "20260630",
        },
        "financial:h1": {
            "evidence_id": "financial:h1", "kind": "financial_statement",
            "title": "华懋科技2026H1累计营业收入", "summary": "营业收入20亿元",
            "period": "20260630",
        },
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技20260630营业收入10亿元 [financial:q2]。\n\n"
            "华懋科技20260630营业收入20亿元 [financial:h1]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
    )
    assert reconciled["numeric_conflicts"][0].get("resolved_by_program") is not True


@pytest.mark.parametrize("mutation", [
    "same_period_alias", "citation_next_sentence", "evidence_swapped",
    "evidence_wrong_value", "evidence_wrong_entity", "evidence_wrong_metric",
    "evidence_wrong_period", "causal_sentence", "cross_chapter_wrong_period",
    "swapped_markdown_table", "causal_markdown_header",
    "equal_values_swapped_evidence", "generic_table_wrong_binding",
    "multi_metric_adjacent_value", "wrong_report_entity_prose",
    "wrong_report_entity_table", "metric_mention_other_value",
    "multi_source_wrong_binding", "nonexact_entity_table",
    "comparison_entity_prose",
    "minority_equity_alias", "extra_target_value", "both_periods",
    "both_citations", "hidden_html", "hidden_code", "strikethrough",
    "negated_report", "negated_evidence",
    "forged_program_flag",
])
def test_editor_numeric_period_series_fails_closed_on_incomplete_binding(
    tmp_path, mutation,
):
    _db(tmp_path)
    review = _huamao_q1_h1_numeric_review()
    chapters = _huamao_q1_h1_chapters()
    evidence = _huamao_q1_h1_evidence()
    issue = review["numeric_conflicts"][0]
    if mutation == "same_period_alias":
        issue["periods"] = ["2026H1", "2026上半年"]
    elif mutation == "citation_next_sentence":
        chapters = _huamao_q1_h1_chapters(q1_citation_same_sentence=False)
    elif mutation == "evidence_swapped":
        issue["evidence_ids"] = ["filing:h1", "filing:q1"]
    elif mutation == "evidence_wrong_value":
        evidence["filing:q1"]["summary"] = (
            "归属于上市公司股东的所有者权益3485323616.35元。"
        )
    elif mutation == "evidence_wrong_entity":
        evidence["filing:q1"]["title"] = "其他公司2026年第一季度报告"
    elif mutation == "evidence_wrong_metric":
        evidence["filing:q1"]["summary"] = "总资产3475323616.35元。"
    elif mutation == "evidence_wrong_period":
        evidence["filing:q1"]["title"] = "华懋科技2026年半年度报告"
        evidence["filing:q1"]["period"] = "20260630"
    elif mutation == "causal_sentence":
        chapters = _huamao_q1_h1_chapters(causal=True)
    elif mutation == "cross_chapter_wrong_period":
        chapters.append({
            "chapter_id": "technology_operations",
            "body_markdown": (
                "华懋科技2026H1归属于上市公司股东的所有者权益"
                "34.75亿元 [filing:q1]。"
            ),
        })
    elif mutation == "swapped_markdown_table":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
                "|---|---:|---|\n"
                "| 2026Q1 | 38.17亿元 | [filing:h1] |\n"
                "| 2026H1 | 34.75亿元 | [filing:q1] |"
            ),
        }]
    elif mutation == "causal_markdown_header":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 期间 | 归属于上市公司股东的所有者权益（表明资本扩张由经营积累驱动） | 来源 |\n"
                "|---|---:|---|\n"
                "| 2026Q1 | 34.75亿元 | [filing:q1] |\n"
                "| 2026H1 | 38.17亿元 | [filing:h1] |"
            ),
        }]
    elif mutation == "equal_values_swapped_evidence":
        issue["values"] = [3_475_323_616.35, 3_475_323_616.35]
        evidence["filing:h1"]["summary"] = (
            "归属于上市公司股东的所有者权益3475323616.35元。"
        )
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
                "|---|---:|---|\n"
                "| 2026Q1 | 34.75亿元 | [filing:h1] |\n"
                "| 2026H1 | 34.75亿元 | [filing:q1] |"
            ),
        }]
    elif mutation == "generic_table_wrong_binding":
        chapters = _huamao_q1_h1_chapters()
        chapters[0]["body_markdown"] += (
            "\n\n### 华懋科技归属于上市公司股东的所有者权益复核\n\n"
            "| 期间 | 数值 | 来源 |\n"
            "|---|---:|---|\n"
            "| 2026H1 | 34.75亿元 | [filing:q1] |"
        )
    elif mutation == "multi_metric_adjacent_value":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 主体 | 期间 | 营业收入 | 归属于上市公司股东的所有者权益 | 来源 |\n"
                "|---|---|---:|---:|---|\n"
                "| 华懋科技 | 2026Q1 | 34.75亿元 | 99.99亿元 | [filing:q1] |\n"
                "| 华懋科技 | 2026H1 | 38.17亿元 | 88.88亿元 | [filing:h1] |"
            ),
        }]
    elif mutation == "wrong_report_entity_prose":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "胜宏科技2026Q1归属于上市公司股东的所有者权益"
                "34.75亿元 [filing:q1]。\n\n"
                "胜宏科技2026H1归属于上市公司股东的所有者权益"
                "38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "wrong_report_entity_table":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 主体 | 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
                "|---|---|---:|---|\n"
                "| 胜宏科技 | 2026Q1 | 34.75亿元 | [filing:q1] |\n"
                "| 胜宏科技 | 2026H1 | 38.17亿元 | [filing:h1] |"
            ),
        }]
    elif mutation == "metric_mention_other_value":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "华懋科技2026Q1归属于上市公司股东的所有者权益待核验，"
                "华懋科技营业收入34.75亿元 [filing:q1]。\n\n"
                "华懋科技2026H1归属于上市公司股东的所有者权益待核验，"
                "华懋科技营业收入38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "multi_source_wrong_binding":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 主体 | 期间 | 营业收入 | 营收来源 | 归属于上市公司股东的所有者权益 | 权益来源 |\n"
                "|---|---|---:|---|---:|---|\n"
                "| 华懋科技 | 2026Q1 | 10亿元 | [filing:q1] | 34.75亿元 | [filing:h1] |\n"
                "| 华懋科技 | 2026H1 | 20亿元 | [filing:h1] | 38.17亿元 | [filing:q1] |"
            ),
        }]
    elif mutation == "nonexact_entity_table":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "| 主体 | 期间 | 归属于上市公司股东的所有者权益 | 来源 |\n"
                "|---|---|---:|---|\n"
                "| 胜宏科技（对比华懋科技） | 2026Q1 | 34.75亿元 | [filing:q1] |\n"
                "| 非华懋科技 | 2026H1 | 38.17亿元 | [filing:h1] |"
            ),
        }]
    elif mutation == "comparison_entity_prose":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "胜宏科技（对比华懋科技）2026Q1归属于上市公司股东的所有者权益"
                "34.75亿元 [filing:q1]。\n\n"
                "胜宏科技（对比华懋科技）2026H1归属于上市公司股东的所有者权益"
                "38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "minority_equity_alias":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "华懋科技2026Q1少数股东权益34.75亿元 [filing:q1]。\n\n"
                "华懋科技2026H1少数股东权益38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "extra_target_value":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "华懋科技2026Q1归属于上市公司股东的所有者权益"
                "34.75亿元（另值99.99亿元） [filing:q1]。\n\n"
                "华懋科技2026H1归属于上市公司股东的所有者权益"
                "38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "both_periods":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "华懋科技2026Q1/2026H1归属于上市公司股东的所有者权益"
                "34.75亿元 [filing:q1]。\n\n"
                "华懋科技2026Q1/2026H1归属于上市公司股东的所有者权益"
                "38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "both_citations":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "华懋科技2026Q1归属于上市公司股东的所有者权益"
                "34.75亿元 [filing:q1] [filing:h1]。\n\n"
                "华懋科技2026H1归属于上市公司股东的所有者权益"
                "38.17亿元 [filing:q1] [filing:h1]。"
            ),
        }]
    elif mutation in {"hidden_html", "hidden_code", "strikethrough"}:
        visible = _huamao_q1_h1_chapters()[0]["body_markdown"]
        wrappers = {
            "hidden_html": ("<!--", "-->"),
            "hidden_code": ("```text\n", "\n```"),
            "strikethrough": ("~~", "~~"),
        }
        prefix, suffix = wrappers[mutation]
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": f"{prefix}{visible}{suffix}",
        }]
    elif mutation == "negated_report":
        chapters = [{
            "chapter_id": "financials",
            "body_markdown": (
                "华懋科技2026Q1归属于上市公司股东的所有者权益"
                "并非34.75亿元 [filing:q1]。\n\n"
                "华懋科技2026H1归属于上市公司股东的所有者权益"
                "不应采用38.17亿元 [filing:h1]。"
            ),
        }]
    elif mutation == "negated_evidence":
        evidence["filing:q1"]["summary"] = (
            "归属于上市公司股东的所有者权益并非3475323616.35元。"
        )
    elif mutation == "forged_program_flag":
        issue["resolved_by_program"] = True
        issue["program_verification"] = "primary_period_series_v20"
        evidence["filing:q1"]["summary"] = "总资产3485323616.35元。"

    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
    )
    result = reconciled["numeric_conflicts"][0]
    assert result.get("resolved_by_program") is not True
    assert result.get("release_blocking") is not False
    assert IndustryResearchService._review_issue_resolved(result) is False


@pytest.mark.parametrize("missing", [
    "entity", "metric", "units", "periods", "accounting_bases", "evidence_ids",
])
def test_editor_numeric_period_series_requires_complete_dimensions(tmp_path, missing):
    _db(tmp_path)
    review = _huamao_q1_h1_numeric_review()
    review["numeric_conflicts"][0].pop(missing)
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, _huamao_q1_h1_chapters(), _huamao_q1_h1_evidence(),
    )
    assert IndustryResearchService._review_issue_resolved(
        reconciled["numeric_conflicts"][0]
    ) is False


def test_editor_unsupported_q1_attributable_equity_accepts_statutory_alias(tmp_path):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited", "contradictions": [],
        "numeric_conflicts": [],
        "unsupported_claims": [{
            "claim": "华懋科技2026年一季度末归母净资产为34.75亿元",
            "chapter": "financials",
            "reason": "需核查是否逐句引用对应一级证据",
            "evidence_ids": ["filing:1225224760"],
            "entity": "华懋科技",
        }],
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2026年一季度末归属于上市公司股东的所有者权益为"
            "34.75亿元 [filing:1225224760]。"
        ),
    }]
    evidence = {
        "filing:1225224760": {
            "evidence_id": "filing:1225224760", "kind": "filing_text",
            "symbol": "603306.SH", "title": "华懋科技2026年第一季度报告",
            "summary": "华懋科技归属于上市公司股东的所有者权益3,475,323,616.35元。",
            "period": "20260331",
        },
    }
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    assert reconciled["resolved_supported_claims"][0]["release_blocking"] is False


def test_editor_unsupported_q1_attributable_equity_rejects_minority_equity_alias(tmp_path):
    _db(tmp_path)
    review = {
        "release_recommendation": "limited", "contradictions": [],
        "numeric_conflicts": [],
        "unsupported_claims": [{
            "claim": "华懋科技2026年一季度末归母净资产为34.75亿元",
            "chapter": "financials", "reason": "需核查是否逐句引用对应一级证据",
            "evidence_ids": ["filing:1225224760"], "entity": "华懋科技",
        }],
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2026年一季度末少数股东权益为34.75亿元 "
            "[filing:1225224760]。"
        ),
    }]
    evidence = {
        "filing:1225224760": {
            "evidence_id": "filing:1225224760", "kind": "filing_text",
            "symbol": "603306.SH", "title": "华懋科技2026年第一季度报告",
            "summary": "少数股东权益3,475,323,616.35元。", "period": "20260331",
        },
    }
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1


def _huamao_q1_unsupported_alias_case(body):
    review = {
        "release_recommendation": "limited", "contradictions": [],
        "numeric_conflicts": [],
        "unsupported_claims": [{
            "claim": "华懋科技2026年一季度末归母净资产为34.75亿元",
            "chapter": "financials",
            "reason": "需核查是否逐句引用对应一级证据",
            "evidence_ids": ["filing:1225224760"],
            "entity": "华懋科技",
        }],
    }
    chapters = [{"chapter_id": "financials", "body_markdown": body}]
    evidence = {
        "filing:1225224760": {
            "evidence_id": "filing:1225224760", "kind": "filing_text",
            "symbol": "603306.SH", "title": "华懋科技2026年第一季度报告",
            "summary": "华懋科技归属于上市公司股东的所有者权益3,475,323,616.35元。",
            "period": "2026-03-31",
        },
    }
    return review, chapters, evidence


def test_editor_unsupported_q1_alias_accepts_standardized_period_and_metric_basis(
    tmp_path,
):
    _db(tmp_path)
    review, chapters, evidence = _huamao_q1_unsupported_alias_case(
        "华懋科技2026Q1归属于母公司所有者权益34.75亿元 "
        "[filing:1225224760]。",
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    assert reconciled["resolved_supported_claims"][0]["release_blocking"] is False


@pytest.mark.parametrize("mutation", [
    "wrong_period", "wrong_unit", "wrong_subject", "cross_fact_atom",
])
def test_editor_unsupported_q1_alias_reconciliation_fails_closed(
    tmp_path, mutation,
):
    _db(tmp_path)
    bodies = {
        "wrong_period": (
            "华懋科技2026年上半年归属于母公司所有者权益34.75亿元 "
            "[filing:1225224760]。"
        ),
        "wrong_unit": (
            "华懋科技2026Q1归属于母公司所有者权益34.75万元 "
            "[filing:1225224760]。"
        ),
        "wrong_subject": (
            "胜宏科技2026Q1归属于母公司所有者权益34.75亿元 "
            "[filing:1225224760]。"
        ),
        "cross_fact_atom": (
            "华懋科技2026Q1归属于母公司所有者权益34.75亿元，"
            "华懋科技2026Q1营业收入10亿元 [filing:1225224760]。"
        ),
    }
    review, chapters, evidence = _huamao_q1_unsupported_alias_case(
        bodies[mutation],
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1
    assert reconciled.get("resolved_supported_claims") in (None, [])


def _huamao_period_basis_unsupported_case(metric, claim_period, body_period):
    is_equity = "净资产" in metric
    claim_metric = "归母净资产" if is_equity else "营业收入"
    report_metric = (
        "归属于上市公司股东的所有者权益" if is_equity else "营业收入"
    )
    value = "34.75亿元" if is_equity else "10.00亿元"
    evidence_value = "3,475,323,616.35元" if is_equity else "1,000,000,000元"
    review = {
        "release_recommendation": "limited", "contradictions": [],
        "numeric_conflicts": [],
        "unsupported_claims": [{
            "claim": f"华懋科技{claim_period}{claim_metric}为{value}",
            "chapter": "financials", "reason": "需核查是否逐句引用对应一级证据",
            "evidence_ids": ["filing:period-basis"], "entity": "华懋科技",
        }],
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            f"华懋科技{body_period}{report_metric}为{value} "
            "[filing:period-basis]。"
        ),
    }]
    period_is_h1 = "H1" in body_period
    evidence = {
        "filing:period-basis": {
            "evidence_id": "filing:period-basis", "kind": "filing_text",
            "symbol": "603306.SH",
            "title": (
                "华懋科技2026年半年度报告" if period_is_h1
                else "华懋科技2026年第二季度报告"
            ),
            "summary": (
                f"华懋科技{body_period}{report_metric}{evidence_value}。"
            ),
            "period": "20260630",
        },
    }
    return review, chapters, evidence


@pytest.mark.parametrize(("claim_period", "body_period"), [
    ("2026Q2", "2026H1"),
    ("2026H1", "2026Q2"),
])
def test_editor_unsupported_flow_metric_rejects_q2_h1_basis_aliasing(
    tmp_path, claim_period, body_period,
):
    _db(tmp_path)
    review, chapters, evidence = _huamao_period_basis_unsupported_case(
        "营业收入", claim_period, body_period,
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1


def test_editor_unsupported_balance_sheet_metric_accepts_q2_h1_same_date_alias(
    tmp_path,
):
    _db(tmp_path)
    review, chapters, evidence = _huamao_period_basis_unsupported_case(
        "归母净资产", "2026Q2", "2026H1",
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    assert reconciled["resolved_supported_claims"][0]["release_blocking"] is False


def test_editor_unsupported_evidence_numeric_atom_requires_issuer_subject(tmp_path):
    _db(tmp_path)
    review, chapters, evidence = _huamao_q1_unsupported_alias_case(
        "华懋科技2026Q1归属于母公司所有者权益34.75亿元 "
        "[filing:1225224760]。",
    )
    evidence["filing:1225224760"]["summary"] = (
        "子公司甲2026Q1归属于上市公司股东的所有者权益"
        "3,475,323,616.35元。"
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1


@pytest.mark.parametrize(("kind", "evidence_id"), [
    ("market", "filing:1225224760"),
    ("valuation_fact", "filing:1225224760"),
    ("event", "filing:evil"),
])
def test_editor_unsupported_financial_metric_rejects_wrong_authority_kind(
    tmp_path, kind, evidence_id,
):
    _db(tmp_path)
    review, chapters, evidence = _huamao_q1_unsupported_alias_case(
        "华懋科技2026Q1归属于母公司所有者权益34.75亿元 "
        "[filing:1225224760]。",
    )
    item = evidence.pop("filing:1225224760")
    item["evidence_id"] = evidence_id
    item["kind"] = kind
    evidence[evidence_id] = item
    review["unsupported_claims"][0]["evidence_ids"] = [evidence_id]
    chapters[0]["body_markdown"] = chapters[0]["body_markdown"].replace(
        "filing:1225224760", evidence_id,
    )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1


def _huamao_total_assets_unsupported_review():
    return {
        "release_recommendation": "limited",
        "contradictions": [], "numeric_conflicts": [],
        "unsupported_claims": [{
            "claim": "2025年末总资产59.94亿元",
            "chapter": "financials",
            "reason": "需核查是否逐句引用对应一级证据",
            "evidence_ids": ["financial:603306.SH:20251231"],
        }],
    }


def _huamao_total_assets_evidence(
    value=5_993_670_009.88, entity="华懋科技", symbol="603306.SH",
):
    return {
        "financial:603306.SH:20251231": {
            "evidence_id": "financial:603306.SH:20251231",
            "kind": "financial_statement", "symbol": symbol,
            "title": f"{entity} 20251231 财务快照",
            "summary": f"{entity}总资产 {value} 元。", "period": "20251231",
        },
    }


def test_editor_unsupported_citation_finding_resolves_on_exact_primary_sentence(tmp_path):
    _db(tmp_path)
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025年末总资产59.94亿元 "
            "[financial:603306.SH:20251231]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_total_assets_unsupported_review(), chapters,
        _huamao_total_assets_evidence(),
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    assert len(reconciled["resolved_supported_claims"]) == 1


def test_editor_resolves_year_end_total_assets_from_real_multi_metric_financial_summary(
    tmp_path,
):
    """Tushare financial summaries expose CNY amounts as bare decimals."""

    _db(tmp_path)
    evidence = {
        "financial:603306.SH:20251231": {
            "evidence_id": "financial:603306.SH:20251231",
            "kind": "financial_statement", "symbol": "603306.SH",
            "title": "华懋科技2025年年度财务快照",
            "summary": (
                "华懋科技2025年末主要财务指标：营业收入2093696190.47，"
                "归母净利润291239647.38，总资产5993670009.88，"
                "归属于上市公司股东的所有者权益3429966675.77"
            ),
            "period": "20251231",
        },
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025年末总资产59.94亿元 "
            "[financial:603306.SH:20251231]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        _huamao_total_assets_unsupported_review(), chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    assert reconciled["resolved_supported_claims"][0]["release_blocking"] is False


def test_editor_resolves_year_end_total_assets_from_h1_comparative_filing_column(
    tmp_path,
):
    _db(tmp_path)
    review = _huamao_total_assets_unsupported_review()
    review["unsupported_claims"][0]["evidence_ids"] = ["filing:1225505930"]
    evidence = {
        "filing:1225505930": {
            "evidence_id": "filing:1225505930", "kind": "filing_text",
            "symbol": "603306.SH", "company": "华懋科技",
            "title": "华懋科技2026年半年度报告", "period": "20260630",
            "document_text": (
                "华懋科技合并资产负债表\n单位：元 币种：人民币\n"
                "项目 本报告期末 上年度末 本报告期末比上年度末增减（%）\n"
                "归属于上市公司股东的净资产 "
                "3817464934.50 3429966675.77 11.31\n"
                "总资产 6171145144.82 5993670009.88 2.96"
            ),
        },
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025年末总资产59.94亿元 [filing:1225505930]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    assert reconciled["resolved_supported_claims"][0]["release_blocking"] is False


@pytest.mark.parametrize("mutation", ["swapped_columns", "wrong_prior_value", "wrong_subject"])
def test_editor_h1_comparative_column_reconciliation_fails_closed(
    tmp_path, mutation,
):
    _db(tmp_path)
    review = _huamao_total_assets_unsupported_review()
    review["unsupported_claims"][0]["evidence_ids"] = ["filing:1225505930"]
    row = "总资产 6171145144.82 5993670009.88 2.96"
    company = "华懋科技"
    symbol = "603306.SH"
    if mutation == "swapped_columns":
        row = "总资产 5993670009.88 6171145144.82 2.96"
    elif mutation == "wrong_prior_value":
        row = "总资产 6171145144.82 6008970568.47 2.96"
    elif mutation == "wrong_subject":
        company = "胜宏科技"
        symbol = "300476.SZ"
    evidence = {
        "filing:1225505930": {
            "evidence_id": "filing:1225505930", "kind": "filing_text",
            "symbol": symbol, "company": company,
            "title": f"{company}2026年半年度报告", "period": "20260630",
            "document_text": (
                f"{company}合并资产负债表\n单位：元 币种：人民币\n"
                "项目 本报告期末 上年度末 本报告期末比上年度末增减（%）\n"
                f"{row}"
            ),
        },
    }
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025年末总资产59.94亿元 [filing:1225505930]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1


def test_editor_acknowledged_year_end_total_assets_cannot_remain_release_blocking(
    tmp_path,
):
    _db(tmp_path)
    review = _huamao_total_assets_unsupported_review()
    review["unsupported_claims"][0].update({
        "reason": "数值与一级法定证据一致，已确认合规，但仍列为发布阻断。",
        "release_blocking": True,
    })
    chapters = [{
        "chapter_id": "financials",
        "body_markdown": (
            "华懋科技2025年末总资产59.94亿元 "
            "[financial:603306.SH:20251231]。"
        ),
    }]
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, chapters, _huamao_total_assets_evidence(),
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert reconciled["unsupported_claims"] == []
    resolved = reconciled["resolved_supported_claims"][0]
    assert resolved["resolved_by_program"] is True
    assert resolved["program_verification"] == "primary_same_sentence_fact_v20"
    assert resolved["release_blocking"] is False
    normalized = IndustryResearchService._normalize_editorial_dimensions(reconciled)
    assert normalized["release_recommendation"] == "ready"


@pytest.mark.parametrize("mutation", [
    "wrong_value", "wrong_entity", "causal_claim", "causal_body", "citation_next_sentence",
    "wrong_report_entity", "missing_report_entity", "wrong_report_unit",
    "extra_same_metric_value",
])
def test_editor_unsupported_citation_reconciliation_fails_closed(tmp_path, mutation):
    _db(tmp_path)
    review = _huamao_total_assets_unsupported_review()
    evidence = _huamao_total_assets_evidence()
    body = "华懋科技2025年末总资产59.94亿元 [financial:603306.SH:20251231]。"
    if mutation == "wrong_value":
        evidence = _huamao_total_assets_evidence(5_960_000_000.0)
    elif mutation == "wrong_entity":
        evidence = _huamao_total_assets_evidence(
            entity="胜宏科技", symbol="300476.SZ",
        )
    elif mutation == "causal_claim":
        review["unsupported_claims"][0]["claim"] = (
            "2025年末总资产59.94亿元主要来自富创优越并表"
        )
    elif mutation == "causal_body":
        body = (
            "华懋科技2025年末总资产59.94亿元主要来自富创优越并表 "
            "[financial:603306.SH:20251231]。"
        )
    elif mutation == "citation_next_sentence":
        body = (
            "华懋科技2025年末总资产59.94亿元。"
            "来源 [financial:603306.SH:20251231]。"
        )
    elif mutation == "wrong_report_entity":
        body = (
            "胜宏科技2025年末总资产59.94亿元 "
            "[financial:603306.SH:20251231]。"
        )
    elif mutation == "missing_report_entity":
        body = "2025年末总资产59.94亿元 [financial:603306.SH:20251231]。"
    elif mutation == "wrong_report_unit":
        body = (
            "华懋科技2025年末总资产59.94万元 "
            "[financial:603306.SH:20251231]。"
        )
    elif mutation == "extra_same_metric_value":
        body = (
            "华懋科技2025年末总资产59.94亿元或60.09亿元 "
            "[financial:603306.SH:20251231]。"
        )
    reconciled = IndustryResearchService._reconcile_supported_editorial_findings(
        review, [{"chapter_id": "financials", "body_markdown": body}], evidence,
        expected_subject={"name": "华懋科技", "symbol": "603306.SH"},
    )
    assert len(reconciled["unsupported_claims"]) == 1
