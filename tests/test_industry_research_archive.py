"""Real SQLite coverage for the owner-scoped research task archive."""

from datetime import datetime, timedelta
import json
from unittest.mock import patch

import pytest
from sqlalchemy import event

from src.services.industry_research_service import IndustryResearchError, IndustryResearchService
from src.storage import DatabaseManager, IndustryResearchProjectRecord


@pytest.fixture
def archive_db(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'research-archive.db'}")
    statuses = ("queued", "collecting", "analyzing", "completed", "limited", "failed")
    created = datetime(2026, 9, 1, 8)
    with db.get_session() as session:
        for index in range(42):
            session.add(IndustryResearchProjectRecord(
                project_id=f"mine-{index:02}", owner_id="user:1",
                topic=f"{'光模块' if index < 36 else '机器人'}课题 {index}", objective="研究任务",
                status=statuses[index % len(statuses)],
                # Equal timestamps exercise the id tie-breaker; insertion
                # order alone must not reverse the archive's chronology.
                created_at=created - timedelta(days=index // 2),
                evidence_snapshot_json=json.dumps({"evidence": ["PRIVATE_EVIDENCE_BODY" * 1000]}),
                report_json=json.dumps({
                    "one_sentence": f"摘要 {index}", "long_form_char_count": 21000,
                    "generation": {"status": "completed", "actual_chars": 21000},
                    "chapters": [{"body_markdown": "LONG_REPORT_BODY" * 1000}],
                    "long_form_report": "LONG_REPORT_BODY" * 1000,
                }),
            ))
        for index in range(12):
            session.add(IndustryResearchProjectRecord(
                project_id=f"other-{index}", owner_id="user:2", topic="光模块他人私有研究",
                objective="不应跨用户可见", status=statuses[index % len(statuses)], created_at=created,
            ))
        session.add(IndustryResearchProjectRecord(
            project_id="legacy", owner_id=None, topic="旧版无归属研究", objective="不得混入用户档案",
            status="completed", created_at=created,
        ))
        session.commit()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_archive_paginates_beyond_first_thirty_with_stable_order_and_true_total(archive_db):
    service = IndustryResearchService(archive_db)
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:1"):
        first = service.list_projects(30)
        second = service.list_projects(12, offset=30)
        beyond = service.list_projects(12, offset=60)

    expected_order = [f"mine-{index:02}" for pair in range(21) for index in (pair * 2 + 1, pair * 2)]
    assert [item["project_id"] for item in first["items"]] == expected_order[:30]
    assert [item["project_id"] for item in second["items"]] == expected_order[30:]
    assert first["total"] == second["total"] == beyond["total"] == 42
    assert len(first["items"]) == 30 and len(second["items"]) == 12
    assert beyond["items"] == []
    assert {key: second[key] for key in ("limit", "offset", "query", "status")} == {
        "limit": 12, "offset": 30, "query": "", "status": "all",
    }
    assert first["counts"] == {"all": 42, "running": 21, "completed": 7, "limited": 7, "failed": 7}


@pytest.mark.parametrize("status", ["all", "running", "completed", "limited", "failed"])
def test_archive_counts_follow_query_but_not_selected_status(archive_db, status):
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:1"):
        result = IndustryResearchService(archive_db).list_projects(
            2, offset=2, query="  光模块  ", status=status,
        )

    assert result["query"] == "光模块"
    assert result["counts"] == {"all": 36, "running": 18, "completed": 6, "limited": 6, "failed": 6}
    assert result["total"] == result["counts"][status]
    assert len(result["items"]) == 2
    assert all("光模块" in item["topic"] for item in result["items"])
    if status != "all":
        allowed = {"queued", "collecting", "analyzing"} if status == "running" else {status}
        assert all(item["status"] in allowed for item in result["items"])


@pytest.mark.parametrize("needle,expected_id", [("%", "percent"), ("_", "underscore"), ("/", "slash")])
def test_title_search_treats_sql_wildcards_and_escape_character_literally(archive_db, needle, expected_id):
    with archive_db.get_session() as session:
        for project_id, topic in (
            ("percent", "利润100%兑现"), ("percent-decoy", "利润1000兑现"),
            ("underscore", "AI_算力"), ("underscore-decoy", "AIx算力"), ("slash", "路径/专题"),
        ):
            session.add(IndustryResearchProjectRecord(
                project_id=project_id, owner_id="user:1", topic=topic, objective="标题搜索",
                status="completed",
            ))
        session.add(IndustryResearchProjectRecord(
            project_id="other-wildcard", owner_id="user:2", topic="其他用户 %_ /",
            objective="搜索也须隔离", status="completed",
        ))
        session.commit()
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:1"):
        result = IndustryResearchService(archive_db).list_projects(query=needle)

    assert [item["project_id"] for item in result["items"]] == [expected_id]
    assert result["total"] == result["counts"]["all"] == result["counts"]["completed"] == 1


def test_archive_never_includes_other_owners_in_rows_totals_or_status_counts(archive_db):
    service = IndustryResearchService(archive_db)
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:2"):
        other = service.list_projects(query="光模块")
    with patch("src.services.industry_research_service.current_owner_id", return_value=None):
        legacy = service.list_projects()
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:missing"):
        missing = service.list_projects()

    assert other["total"] == 12
    assert all(item["project_id"].startswith("other-") for item in other["items"])
    assert other["counts"] == {"all": 12, "running": 6, "completed": 2, "limited": 2, "failed": 2}
    assert [item["project_id"] for item in legacy["items"]] == ["legacy"]
    assert legacy["total"] == 1
    assert missing["items"] == [] and missing["total"] == 0
    assert missing["counts"] == {"all": 0, "running": 0, "completed": 0, "limited": 0, "failed": 0}


def test_archive_only_projects_metadata_and_report_preview_from_database(archive_db):
    statements = []
    serialized_rows = []
    service = IndustryResearchService(archive_db)
    original_serializer = service._serialize_project

    def capture_statement(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    def capture_row(row, *, include_snapshot):
        serialized_rows.append(row)
        return original_serializer(row, include_snapshot=include_snapshot)

    event.listen(archive_db._engine, "before_cursor_execute", capture_statement)
    try:
        with patch("src.services.industry_research_service.current_owner_id", return_value="user:1"), patch.object(
            service, "_serialize_project", side_effect=capture_row,
        ):
            result = service.list_projects(1)
    finally:
        event.remove(archive_db._engine, "before_cursor_execute", capture_statement)

    assert len(statements) == 2  # One grouped count and one bounded metadata page.
    page_statement = statements[-1]
    assert "evidence_snapshot_json" not in page_statement
    assert page_statement.count("json_extract(") == 5
    assert "LIMIT" in page_statement and "OFFSET" in page_statement
    assert serialized_rows[0].evidence_snapshot_json is None
    assert "LONG_REPORT_BODY" not in serialized_rows[0].report_json
    assert result["items"][0]["snapshot"] is None
    assert result["items"][0]["report"] == {
        "one_sentence": "摘要 1", "long_form_char_count": 21000,
        "generation": {"status": "completed", "actual_chars": 21000},
    }


def test_archive_handles_invalid_old_report_json_without_loading_snapshot(archive_db):
    with archive_db.get_session() as session:
        session.add(IndustryResearchProjectRecord(
            project_id="corrupt-preview", owner_id="user:1", topic="损坏的旧摘要",
            objective="可列出并进入详情处理", report_json="not-json", status="failed",
        ))
        session.commit()
    with patch("src.services.industry_research_service.current_owner_id", return_value="user:1"):
        result = IndustryResearchService(archive_db).list_projects(query="损坏的旧摘要")
    assert result["total"] == 1
    assert result["items"][0]["report"] is None


def test_archive_rejects_unknown_status(archive_db):
    with pytest.raises(IndustryResearchError, match="状态筛选"):
        IndustryResearchService(archive_db).list_projects(status="not-a-status")
