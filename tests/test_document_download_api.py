"""Document exports expose only a verified single package and same-task resume."""
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import HTTPException
from api.v1.endpoints import data_acquisition as api
from src.services.data_acquisition_service import DataAcquisitionError


def test_document_resume_keeps_original_task_and_does_not_consume_new_quota(monkeypatch):
    calls = []
    task = {"task_id": "acq-fixed-document", "job_id": "same-job", "status": "queued"}
    service = SimpleNamespace(resume=lambda task_id: calls.append(task_id) or task)
    monkeypatch.setattr(api.DataAcquisitionTaskService, "get_instance", lambda: service)
    monkeypatch.setattr(api, "UserQuotaService", lambda: pytest.fail("resume must not charge a new task"))
    assert api.resume_task(task["task_id"]) is task
    assert calls == [task["task_id"]]


def test_document_resume_does_not_fallback_to_submitting_a_new_job(monkeypatch):
    def denied(_):
        raise DataAcquisitionError("取数任务不存在或无权访问")
    monkeypatch.setattr(api.DataAcquisitionTaskService, "get_instance", lambda: SimpleNamespace(resume=denied))
    with pytest.raises(HTTPException) as error:
        api.resume_task("acq-other-user")
    assert error.value.status_code == 400


def test_incomplete_document_manifest_cannot_activate_download_even_if_zip_exists(monkeypatch):
    monkeypatch.setattr(api, "DataAcquisitionService", lambda: SimpleNamespace(
        get_job=lambda _: {"document_export": True, "complete": False},
        package_path=lambda _: pytest.fail("not-ready package must not be streamed")))
    with pytest.raises(HTTPException) as error:
        api.download("unfinished-documents")
    assert error.value.status_code == 409


def test_document_filename_is_human_readable_and_range_headers_remain_private(monkeypatch, tmp_path):
    path = tmp_path / "opaque-job.zip"
    path.write_bytes(b"PK\x03\x04test")
    monkeypatch.setattr(api, "DataAcquisitionService", lambda: SimpleNamespace(
        get_job=lambda _: {"document_export": True, "complete": True,
                           "filename": "宁德时代_公告_2026-04-22至2026-10-22_104份.zip"},
        package_path=lambda _: path))
    response = api.download("opaque-job")
    assert "宁德时代_公告_2026-04-22至2026-10-22_104份.zip" in unquote(response.headers["content-disposition"])
    assert response.headers["content-encoding"] == "identity"
    assert response.headers["cache-control"] == "private, no-store, no-transform"


def test_other_owner_manifest_denial_does_not_reveal_filename(monkeypatch):
    def denied(_):
        raise DataAcquisitionError("数据包不存在或无权访问")
    monkeypatch.setattr(api, "DataAcquisitionService", lambda: SimpleNamespace(get_job=denied))
    with pytest.raises(HTTPException) as error:
        api.download("other-owner")
    assert error.value.status_code == 404


def test_long_chinese_package_title_preserves_zip_extension_with_safe_utf8_filename(monkeypatch, tmp_path):
    path = tmp_path / "package.zip"
    path.write_bytes(b"PK\x03\x04test")
    monkeypatch.setattr(api, "DataAcquisitionService", lambda: SimpleNamespace(
        get_job=lambda _: {"document_export": True, "complete": True,
                           "filename": "完整公告名称" * 100 + ".zip"}, package_path=lambda _: path))
    response = api.download("long-title")
    assert response.filename.endswith(".zip")
    assert len(response.filename.encode("utf-8")) <= 234
    assert "完整公告名称" in response.filename


def test_resume_preserves_server_owned_plan_instead_of_accepting_client_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(api.DataAcquisitionTaskService, "get_instance", lambda: SimpleNamespace(
        resume=lambda task_id: calls.append(task_id) or {"task_id": task_id}))
    assert api.resume_task("acq-frozen-scope")["task_id"] == "acq-frozen-scope"
    assert calls == ["acq-frozen-scope"]
