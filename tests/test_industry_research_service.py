from datetime import date
from threading import Event
import time
from unittest.mock import patch

from src.services.industry_research_service import IndustryResearchService, IndustryResearchTaskManager
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
            report_key="report-1", trade_date=date.today(), title="光模块产业链深度研究",
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

    assert snapshot["totals"] == {"evidence": 3, "reports": 1, "notes": 1, "events": 1, "media_files": 0}
    assert {item["evidence_id"] for item in snapshot["evidence"]} == {"report:1", "note:note-1", "event:1"}
    assert next(item for item in snapshot["coverage"] if item["key"] == "news_comments")["count"] == 1
    assert snapshot["companies"][0]["name"] == "中际旭创"
    assert snapshot["evidence"][0]["original_available"] is True


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


def test_queued_project_does_not_expose_empty_snapshot_object(tmp_path):
    _db(tmp_path)
    row = IndustryResearchProjectRecord(project_id="queued", topic="光模块", objective="长篇研究")

    serialized = IndustryResearchService._serialize_project(row, include_snapshot=True)

    assert serialized["snapshot"] is None


def test_background_project_completes_and_persists_report(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    IndustryResearchTaskManager._instance = manager
    fake_report = {"one_sentence": "证据约束下的测试结论", "leaders": [], "caveats": []}
    with (
        patch("src.services.industry_research_service.current_owner_id", return_value="user:7"),
        patch.object(IndustryResearchService, "analyze_snapshot", return_value=fake_report),
    ):
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


def test_background_project_publishes_answer_first_draft_before_long_report(tmp_path):
    db = _db(tmp_path)
    service = IndustryResearchService(db)
    manager = IndustryResearchTaskManager(db=db, worker_count=1)
    IndustryResearchTaskManager._instance = manager
    release = Event()

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
            patch.object(IndustryResearchService, "analyze_snapshot", new=fake_analyze),
        ):
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
