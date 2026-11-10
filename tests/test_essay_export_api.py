"""Authenticated HTTP/export integration with a real temporary source database."""
from contextlib import contextmanager
from datetime import datetime
import csv
import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from api.v1.endpoints import command, financial_data
from src.repositories.essay_analysis_repo import EssayAnalysisRepository
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services import essay_export_service
from src.services.essay_export_service import EssayExportService
from src.storage import EssayAnalysisRecord, ResearchNote

PREFIX = "/api/v1/financial-data/essays/package/tasks"


class OwnerTestClient(TestClient):
    """Supply explicit headers with either upstream or repo threadless client."""
    def request(self, method, url, **kwargs):
        headers = dict(getattr(self, "owner_headers", {"x-test-user": "17"}))
        headers.update(kwargs.pop("headers", {}) or {})
        return super().request(method, url, headers=headers, **kwargs)


@pytest.fixture()
def export_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'source.sqlite3'}")
    ResearchNote.__table__.create(engine)
    EssayAnalysisRecord.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(ResearchNote), [
            {"topic_id": str(number), "group_id": "fixture", "group_name": "机构甲", "title": "宁德时代",
             "content": f"宁德时代完整原文 {number}" + "金融原始材料。" * 30,
             "topic_type": "talk", "content_hash": str(number), "created_at": datetime(2026, 10, 20)}
            for number in range(237)])
        connection.execute(insert(EssayAnalysisRecord), {"topic_id": "236", "status": "completed",
            "model": "fixture", "prompt_version": "fixture", "input_hash": "fixture",
            "tags_json": '["动力电池","储能"]', "summary": "已有分析标签，不重新分析"})

    class Database:
        @contextmanager
        def get_session(self):
            with Session(engine) as session:
                yield session

    service = EssayExportService(root=tmp_path / "jobs", repo=EssayAnalysisRepository(db_manager=Database()))
    monkeypatch.setattr(financial_data, "_essay_exports", lambda: service)
    monkeypatch.setattr(essay_export_service, "get_essay_export_service", lambda: service)
    monkeypatch.setattr(service, "start_worker", lambda: None)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        # Authentication middleware is separately tested by auth API tests.
        # This harness supplies its actual request-local service identity.
        value = request.headers.get("x-test-user")
        token = set_current_user_id(int(value) if value is not None else None)
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)

    app.include_router(financial_data.router, prefix="/api/v1/financial-data")
    with OwnerTestClient(app) as client:
        yield client, service, tmp_path
    service.stop_worker()
    engine.dispose()


def complete(client, service, task_id):
    for _ in range(20):
        state = client.get(f"{PREFIX}/{task_id}").json()
        if state["status"] in {"completed", "no_results", "failed"}:
            return state
        assert service.run_once()
    pytest.fail("integration job did not finish")


def test_submit_returns_202_unknown_total_then_real_progress_and_no_early_file(export_api):
    client, service, _ = export_api
    response = client.post(PREFIX, json={"filters": {"query": "宁德时代"}, "workspace_id": "meeting"})
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["status"] == "queued" and task["progress"] is None and task["target_total"] is None
    assert task["requested_limit"] is None and task["workspace_id"] == "meeting"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(task["poll_path"] + "/download").status_code == 404
    service.run_once()
    state = client.get(task["poll_path"]).json()
    assert state["matched_total"] == state["target_total"] == 237
    assert state["retrieved_count"] == 0 and state["result"] is None


def test_focused_api_preserves_unknown_count_paginates_evidence_and_checks_owner(export_api):
    from src.services.essay_focus_service import EssayFocusService
    client, service, _ = export_api
    def completion(payload):
        source = json.loads(payload["messages"][1]["content"])
        return {"classification": "focused", "reason": "测试原文以公司为研究主体",
                "primary_subjects": ["宁德时代"], "target_role": "central",
                "substantive_analysis": True, "document_shape": "single_company",
                "scope_assessment": {
                    "organizing_question": "以目标公司为核心的确定性 API 导出夹具。",
                    "coherence": "target_line",
                    "blocks": [{"block_id": block["block_id"], "role": "target_core",
                                "topic": "目标公司的原文研究",
                                "relation": "测试原文各块共同围绕目标；仅验证 API 与任务契约，不代替真实模型验收。",
                                "supports_block_ids": []} for block in source["scope_blocks"]]},
                "evidence": [{"quote": source["original"][:15], "subject": "宁德时代"}]}
    service.focus_service = EssayFocusService(completion=completion)
    task = client.post(PREFIX, json={"filters": {"query": "宁德时代", "query_scope": "focused"},
                                    "workspace_id": "semantic", "requested_limit": 3}).json()
    assert task["matched_total"] is None and task["candidate_total"] is None
    for _ in range(300):
        state = client.get(task["poll_path"]).json()
        if state["status"] in {"completed", "no_results", "failed"}:
            break
        service.run_once()
    assert state["status"] == "completed", state
    assert state["candidate_total"] == state["screened_count"] == state["matched_total"] == 237
    assert state["target_total"] == state["result"]["count"] == 3
    response = client.get(state["focus_review_path"], params={"page": 2, "page_size": 5})
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    assert response.json()["total"] == 237 and len(response.json()["items"]) == 5
    assert all(row["review"]["coverage"]["complete"] for row in response.json()["items"])
    assert client.get(state["focus_review_path"], params={"workspace_id": "wrong"}).status_code == 404
    assert client.get(state["focus_review_path"], headers={"x-test-user": "18"}).status_code == 404
    assert client.get(state["focus_review_path"], params={"page_size": 101}).status_code == 422


def test_all_routes_completed_archive_manifest_csv_full_text_and_range(export_api):
    client, service, _ = export_api
    created = client.post(PREFIX, json={"filters": {"query": "宁德时代", "query_scope": "title",
        "start_date": "2026-10-19", "end_date": "2026-10-21"}, "workspace_id": "meeting"}).json()
    listing = client.get(PREFIX).json()
    assert listing["total"] == 1 and listing["items"][0]["task_id"] == created["task_id"]
    state = complete(client, service, created["task_id"])
    assert state["status"] == "completed" and state["packaged_count"] == state["retrieved_count"] == 237
    assert state["complete"] and state["library_complete"] and state["result"]["count"] == 237
    response = client.get(state["result"]["download_url"])
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert len(response.content) == state["result"]["size"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        rows = json.loads(archive.read("小作文.json"))
        assert len(rows) == len({row["topic_id"] for row in rows}) == 237
        assert rows[0]["tags"] == ["动力电池", "储能"]
        assert rows[0]["content"] == "宁德时代完整原文 236" + "金融原始材料。" * 30
        csv_rows = list(csv.DictReader(io.StringIO(archive.read("小作文.csv").decode("utf-8-sig"))))
        assert len(csv_rows) == 237 and csv_rows[0]["content"] == rows[0]["content"]
        receipt = json.loads(archive.read("检索与完整性.json"))
        assert receipt["complete"] and receipt["count"] == receipt["matched_total"] == 237
        assert receipt["filters"]["start_date"] == "2026-10-19" and not receipt["user_limited"]
    ranged = client.get(state["result"]["download_url"], headers={"Range": "bytes=0-1023"})
    # This is exercised against the installed Starlette FileResponse, not a
    # mocked transport; this runtime supports RFC byte ranges.
    assert ranged.status_code == 206 and ranged.content == response.content[:1024]
    assert ranged.headers["content-range"] == f"bytes 0-1023/{len(response.content)}"


def test_explicit_limit_is_applied_but_not_reported_as_failed_partial(export_api):
    client, service, _ = export_api
    created = client.post(PREFIX, json={"filters": {"query": "宁德时代"}, "requested_limit": 20}).json()
    state = complete(client, service, created["task_id"])
    assert state["status"] == "completed" and state["request_complete"] and not state["library_complete"]
    assert state["matched_total"] == 237 and state["target_total"] == state["result"]["count"] == 20


@pytest.mark.parametrize("body,status", [
    ({"requested_limit": True}, 422), ({"requested_limit": -1}, 422), ({"requested_limit": 0}, 422),
    ({"requested_limit": "20"}, 422), ({"filters": {"unknown": "must not broaden"}}, 400),
    ({"filters": {"cookie": "must not persist"}}, 400),
    ({"filters": {"subject": "宁德时代"}}, 400),
    ({"filters": {"analysis_status": "all-maybe"}}, 400),
    ({"filters": {"query_scope": "topic"}}, 400), ({"extra": "forbidden"}, 422),
])
def test_invalid_limits_or_unknown_filters_are_rejected_without_jobs(export_api, body, status):
    client, _, tmp_path = export_api
    response = client.post(PREFIX, json=body)
    assert response.status_code == status, response.text
    assert not list((tmp_path / "jobs").rglob("task.sqlite3"))


def test_empty_query_result_no_zip_and_no_fake_success(export_api):
    client, service, tmp_path = export_api
    task = client.post(PREFIX, json={"filters": {"query": "绝对未出现关键词"}}).json()
    state = complete(client, service, task["task_id"])
    assert state["status"] == "no_results" and state["result"] is None
    assert state["matched_total"] == 0 and not list((tmp_path / "jobs").rglob("*.zip"))
    assert client.get(task["poll_path"] + "/download").status_code == 404


def test_owner_and_explicit_workspace_validation_across_poll_resume_download(export_api):
    client, service, _ = export_api
    task = client.post(PREFIX, json={"filters": {"query": "宁德时代"}, "workspace_id": "private"}).json()
    assert client.get(task["poll_path"], params={"workspace_id": "wrong"}).status_code == 404
    assert client.post(task["resume_path"], params={"workspace_id": "wrong"}).status_code == 404
    assert client.get(PREFIX, params={"workspace_id": "wrong"}).json()["total"] == 0
    state = complete(client, service, task["task_id"])
    assert client.get(state["result"]["download_url"], params={"workspace_id": "wrong"}).status_code == 404
    assert client.get(state["result"]["download_url"], params={"workspace_id": "private"}).status_code == 200
    other = {"x-test-user": "18"}
    assert client.get(task["poll_path"], headers=other).status_code == 404
    assert client.post(task["resume_path"], headers=other).status_code == 404
    assert client.get(state["result"]["download_url"], headers=other).status_code == 404
    assert client.get(PREFIX, headers=other).json()["total"] == 0


def test_anonymous_access_cannot_create_list_poll_resume_or_download(export_api):
    client, _, _ = export_api
    task = client.post(PREFIX, json={"filters": {"query": "宁德时代"}}).json()
    client.owner_headers = {}
    assert client.post(PREFIX, json={"filters": {"query": "宁德时代"}}).status_code == 401
    assert client.get(PREFIX).status_code == 401
    assert client.get(task["poll_path"]).status_code == 401
    assert client.post(task["resume_path"]).status_code == 401
    assert client.get(task["poll_path"] + "/download").status_code == 401


def test_resume_route_preserves_original_identity_counts_and_single_ledger(export_api):
    client, service, tmp_path = export_api
    task = client.post(PREFIX, json={"filters": {"query": "宁德时代"}, "workspace_id": "meeting"}).json()
    service.run_once()  # frozen membership
    service.run_once()  # durable collected payloads
    before = client.get(task["poll_path"]).json()
    resumed = client.post(task["resume_path"]).json()
    assert resumed["task_id"] == task["task_id"] and resumed["retrieved_count"] == before["retrieved_count"] == 237
    assert len(list((tmp_path / "jobs").rglob("task.sqlite3"))) == 1
    assert complete(client, service, task["task_id"])["status"] == "completed"


def test_command_submits_unlimited_by_default_without_cookies_or_sync_fetch(export_api, monkeypatch):
    _, _, tmp_path = export_api
    monkeypatch.setattr(command, "_internal_call", lambda *args, **kwargs: pytest.fail("must not collect in HTTP request"))
    token = set_current_user_id(17)
    try:
        result = command._package_essay_feed({"query": "宁德时代", "query_scope": "full", "days": 2},
                                             "private-session-cookie-never-store", workspace="meeting")
    finally:
        reset_current_user_id(token)
    assert result["ok"] and result["status"] == "submitted"
    assert result["task"]["requested_limit"] is None and not result["data"]["download_ready"]
    assert result["task"]["progress"] is None and result["task"]["target_total"] is None
    assert "download_url" not in result["data"]
    for path in (tmp_path / "jobs").rglob("*"):
        if path.is_file():
            assert b"private-session-cookie-never-store" not in path.read_bytes()


def test_command_passes_only_explicit_request_limit_and_never_changes_filters(export_api):
    _, service, _ = export_api
    token = set_current_user_id(17)
    try:
        user_filters = command._essay_filters_for_user({"query": "宁德时代", "limit": 20}, "下载全部提及宁德时代的小作文")
        assert "limit" not in user_filters
        unlimited = command._package_essay_feed(user_filters, "")
        assert unlimited["task"]["requested_limit"] is None
        chosen = command._essay_filters_for_user({"query": "宁德时代", "limit": 20}, "只下载宁德时代最近20篇小作文")
        assert chosen["limit"] == 20
        result = command._package_essay_feed({**chosen, "query_scope": "title"}, "")
        assert result["task"]["requested_limit"] == 20
        assert result["task"]["filters"]["query_scope"] == "title"
        assert result["task"]["filters"]["query"] == "宁德时代"
        assert service.get(result["task"]["task_id"])["requested_limit"] == 20
    finally:
        reset_current_user_id(token)
