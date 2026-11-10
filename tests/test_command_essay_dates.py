"""Calendar dates across real temporary SQLite, feed API, Excel and ZIP."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.v1.endpoints import command, essay_radar
from src.repositories.essay_analysis_repo import EssayAnalysisRepository, _feed_count_cache, feed_date_bounds
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.essay_analysis_service import EssayAnalysisService
from src.services import report_pack_service as packs
from src.services import essay_export_service as exports
from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote


@pytest.fixture
def dated_library(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'dated-notes.sqlite'}")
    ResearchNote.__table__.create(engine)
    EssayAnalysisRecord.__table__.create(engine)
    class Database:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session
    repo = EssayAnalysisRepository(db_manager=Database())
    service = object.__new__(EssayAnalysisService)
    service.repo = repo
    monkeypatch.setattr(essay_radar, "EssayAnalysisService", lambda: service)
    monkeypatch.setattr(DatabaseManager, "get_instance", lambda: pytest.fail("No production DB initialization"))
    monkeypatch.setattr(command, "_internal_call", lambda *a, **k: pytest.fail("No external API"))
    monkeypatch.setattr(command, "_complete", lambda *a, **k: pytest.fail("No model calls"))
    monkeypatch.setattr(packs, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(packs, "CACHE_DIR", tmp_path / "cache")
    _feed_count_cache.clear()
    expected = {}
    with Session(engine) as session:
        def add(company, suffix, created):
            ident = f"{company}-{suffix}"
            session.add(ResearchNote(topic_id=ident, group_id="test", group_name="测试机构",
                topic_type="talk", title=f"{company} 观点 {suffix}",
                content=f"{company} 原文 {suffix} 完整结尾", content_hash=ident,
                symbol_codes="", created_at=created))
            return ident
        for company in ("宁德时代", "比亚迪"):
            # UTC-naive note timestamps, Shanghai inclusive 10/1 through 10/15.
            add(company, "before", datetime(2026, 9, 30, 15, 59, 59, 999999))
            first = add(company, "start", datetime(2026, 9, 30, 16))
            middle = add(company, "middle", datetime(2026, 10, 8, 4))
            last = add(company, "end", datetime(2026, 10, 15, 15, 59, 59, 999999))
            add(company, "after", datetime(2026, 10, 15, 16))
            expected[company] = {first, middle, last}
        for index in range(121):
            expected["宁德时代"].add(add("宁德时代", f"bulk-{index:03d}",
                datetime(2026, 10, 9, 1) + timedelta(seconds=index)))
        session.commit()
    app = FastAPI()
    app.include_router(essay_radar.router)
    identity = set_current_user_id(31)
    try:
        yield service, repo, TestClient(app), expected, tmp_path
    finally:
        reset_current_user_id(identity)
        _feed_count_cache.clear()
        engine.dispose()


def test_bounds_are_shanghai_inclusive_days():
    assert feed_date_bounds("2026-10-01", "2026-10-15") == (
        datetime(2026, 9, 30, 16), datetime(2026, 10, 15, 16))


@pytest.mark.parametrize("company", ["宁德时代", "比亚迪"])
@pytest.mark.parametrize("scope", ["title", "full"])
def test_exact_dates_override_days_across_real_count_rows_iter_and_api(dated_library, company, scope):
    service, repo, client, expected, _ = dated_library
    filters = dict(query=company, query_scope=scope, start_date="2026-10-01", end_date="2026-10-15")
    data = service.list_feed(**filters, days=1, page_size=100)
    assert data["total"] == len(expected[company])
    assert data["scope"] == "calendar_date_range" and data["date_timezone"] == "Asia/Shanghai"
    rows = list(repo.iter_feed(**filters, cutoff=datetime(2099, 1, 1)))
    assert {row["topic_id"] for row in rows} == expected[company]
    response = client.get("/feed", params={**filters, "days": 1, "page_size": 100})
    assert response.status_code == 200 and response.json()["total"] == len(expected[company])


def test_single_day_boundaries_and_count_cache_are_exact(dated_library):
    service, _, _, expected, _ = dated_library
    base = dict(query="宁德时代", query_scope="full", page_size=100)
    first = service.list_feed(**base, start_date="2026-10-01", end_date="2026-10-01")
    entire = service.list_feed(**base, start_date="2026-10-01", end_date="2026-10-15")
    last = service.list_feed(**base, start_date="2026-10-15", end_date="2026-10-15")
    assert {row["topic_id"] for row in first["items"]} == {"宁德时代-start"}
    assert entire["total"] == len(expected["宁德时代"])
    assert {row["topic_id"] for row in last["items"]} == {"宁德时代-end"}


@pytest.mark.parametrize("dates,expected", [
    ({"end_date": "2026-10-15"}, {"比亚迪-before", "比亚迪-start", "比亚迪-middle", "比亚迪-end"}),
    ({"start_date": "2026-10-01"}, {"比亚迪-start", "比亚迪-middle", "比亚迪-end", "比亚迪-after"}),
])
def test_one_sided_dates_override_relative_days(dated_library, dates, expected):
    service, _, _, _, _ = dated_library
    result = service.list_feed(query="比亚迪", days=1, **dates)
    assert {row["topic_id"] for row in result["items"]} == expected


@pytest.mark.parametrize("dates", [
    {"start_date": "2026-10-16", "end_date": "2026-10-15"},
    {"start_date": "2026-02-30"}, {"end_date": "2026-13-01"},
    {"start_date": "20261001"}, {"end_date": "2026-10-15T00:00:00"},
])
def test_invalid_ranges_are_422_not_an_unfiltered_query(dated_library, dates):
    _, _, client, _, _ = dated_library
    for path in ("/feed", "/feed/export"):
        assert client.get(path, params={"query": "宁德时代", **dates}).status_code == 422
    result = command._query_essay_radar({"query": "宁德时代", **dates}, "")
    assert result["ok"] is False and result["status"] == 422


def test_excel_keeps_full_originals_and_exact_calendar_receipt(dated_library):
    service, _, _, expected, _ = dated_library
    exported = service.export_feed_excel(query="宁德时代", query_scope="title", days=1,
        start_date="2026-10-01", end_date="2026-10-15")
    path = Path(exported["path"])
    try:
        workbook = load_workbook(path, read_only=True)
        try:
            rows = list(workbook["原文全文"].iter_rows(min_row=2, values_only=True))
            assert {row[0] for row in rows} == expected["宁德时代"]
            assert all(str(row[5]).endswith("完整结尾") for row in rows)
            conditions = dict(workbook["导出条件"].iter_rows(min_row=2, values_only=True))
            assert conditions["时间范围"] == "2026-10-01 至 2026-10-15（上海日期，含首尾）"
            assert exported["row_count"] == 124
        finally:
            workbook.close()
    finally:
        path.unlink()


def install_real_api(monkeypatch, client):
    queries = []
    def internal(method, path, cookie, params=None, **kwargs):
        assert method == "GET" and path == "/api/v1/essay-radar/feed"
        queries.append(dict(params["query"]))
        response = client.get("/feed", params=params["query"])
        return {"ok": response.status_code == 200, "status": response.status_code, "data": response.json()}
    monkeypatch.setattr(command, "_internal_call", internal)
    return queries


def test_command_pages_and_actual_zip_keep_calendar_scope(dated_library, monkeypatch):
    _, repo, client, expected, tmp_path = dated_library
    queries = install_real_api(monkeypatch, client)
    filters = dict(query="宁德时代", query_scope="title", days=25,
                   start_date="2026-10-01", end_date="2026-10-15")
    collected = command._collect_essay_feed(filters, "")
    assert collected["ok"] and collected["data"]["complete"]
    assert len(queries) == 2 and all("days" not in query for query in queries)
    assert all(query["start_date"] == "2026-10-01" and query["end_date"] == "2026-10-15" for query in queries)
    jobs = exports.EssayExportService(root=tmp_path / "jobs", repo=repo)
    monkeypatch.setattr(exports, "get_essay_export_service", lambda: jobs)
    monkeypatch.setattr(jobs, "start_worker", lambda: None)
    result = command._package_essay_feed(filters, "", "date-test")
    assert result["ok"] and result["status"] == "submitted" and not result["data"]["download_ready"]
    task_id = result["task"]["task_id"]
    for _ in range(20):
        if jobs.get(task_id)["status"] == "completed":
            break
        assert jobs.run_once()
    assert jobs.get(task_id)["status"] == "completed"
    archive_path = jobs.download_file(task_id)
    with zipfile.ZipFile(archive_path) as archive:
        rows = json.loads(archive.read("小作文.json"))
        receipt = json.loads(archive.read("检索与完整性.json"))
        assert {row["topic_id"] for row in rows} == expected["宁德时代"]
        assert receipt["count"] == receipt["expected_total"] == len(rows) == 124
        assert receipt["filters"]["start_date"] == "2026-10-01"
        assert receipt["filters"]["end_date"] == "2026-10-15"
        assert "days" not in receipt["filters"]
        assert all(row["content"].endswith("完整结尾") for row in rows)


@pytest.mark.parametrize("message", [
    "下载宁德时代2026-10-01至2026-10-15小作文",
    "下载宁德时代2026年10月1日至15日小作文",
    "下载宁德时代2026年10月1日到2026年10月15日小作文",
])
def test_literal_range_overrides_days_guess(message):
    assert command._essay_filters_for_user({"query": "宁德时代", "days": 25}, message) == {
        "query": "宁德时代", "analysis_status": "essay",
        "start_date": "2026-10-01", "end_date": "2026-10-15"}


def test_real_model_error_replay_preserves_dates_when_changing_company(dated_library, monkeypatch):
    _, _, client, expected, _ = dated_library
    messages = [
        {"role": "user", "content": "下载宁德时代2026年10月1日至10月15日标题包含公司名的小作文，只要原文，不要录音。"},
        {"role": "assistant", "content": "原条件已明确，尚未执行。"},
        {"role": "user", "content": "日期和其他条件不变，换成比亚迪。"},
    ]
    turn = 0
    def llm(conversation, *args, **kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return "", [{"id": "replay", "type": "function", "function": {
                "name": "essay_radar", "arguments": json.dumps({
                    "query": "比亚迪", "query_scope": "title", "days": 25, "page_size": 100})}}]
        return "已按原日期获取比亚迪原文", []
    queries = install_real_api(monkeypatch, client)
    monkeypatch.setattr(command, "_llm_turn", llm)
    result = command._run_agent("general", messages, "", [], workspace="date-test")
    assert len(queries) == 1
    assert queries[0]["query"] == "比亚迪"
    assert queries[0]["start_date"] == "2026-10-01" and queries[0]["end_date"] == "2026-10-15"
    assert "days" not in queries[0]
    assert result["steps"][0]["ok"]
    assert len(result["widgets"][0]["rows"]) == len(expected["比亚迪"])
    assert all("比亚迪" in row[1] for row in result["widgets"][0]["rows"])


def test_new_relative_period_clears_old_model_date_values():
    messages = [{"role": "user", "content": "宁德时代2026-10-01至2026-10-15小作文"},
                {"role": "user", "content": "其他条件不变，改成最近两天"}]
    assert command._essay_dates_from_messages(messages, {
        "query": "宁德时代", "days": 2, "start_date": "2026-10-01", "end_date": "2026-10-15",
    }) == {"start_date": None, "end_date": None}
    assert command._essay_dates_from_messages(messages, {"query": "宁德时代", "days": 2}) == {
        "start_date": None, "end_date": None}


def test_today_correct_calendar_dates_are_not_cleared():
    messages = [{"role": "user", "content": "宁德时代2026-10-01至2026-10-15小作文"},
                {"role": "user", "content": "改成今天"}]
    assert command._essay_dates_from_messages(messages, {
        "query": "宁德时代", "start_date": "2026-10-22", "end_date": "2026-10-22",
    }) == {}


def test_multi_company_dates_require_exact_target_quotation():
    text = "宁德时代2026-10-01至2026-10-15；比亚迪2026-09-01至2026-09-15，下载小作文"
    messages = [{"role": "user", "content": text}]
    assert command._essay_dates_from_messages(messages, {"query": "比亚迪", "days": 25}) == {}
    args = {"query": "比亚迪", "date_evidence": "比亚迪2026-09-01至2026-09-15"}
    assert command._essay_dates_from_messages(messages, args) == {
        "start_date": "2026-09-01", "end_date": "2026-09-15"}
    args["date_evidence"] = "比亚迪2026-10-01至2026-10-15"
    assert command._essay_dates_from_messages(messages, args) == {}


def test_choice_card_uses_actual_date_range_and_counts(dated_library, monkeypatch):
    _, _, client, _, _ = dated_library
    install_real_api(monkeypatch, client)
    monkeypatch.setattr(command, "_question_result", lambda question, *args: question)
    result = command._essay_scope_question({"query": "比亚迪", "days": 25},
        "下载2026年10月1日至15日比亚迪小作文", "general", [], "", "date-test", None)
    assert "2026-10-01 至 2026-10-15" in result["context"]
    assert "最近 25 天" not in result["context"]
    options = {option["id"]: option for option in result["options"]}
    assert options["full"]["count"] == options["title"]["count"] == 3
    assert "count" not in options["focused"] and "候选 3 篇" in options["focused"]["description"]
