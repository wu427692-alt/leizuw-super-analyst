"""Real page normalization, private checkpoints, PDF streaming and ZIP64 offline."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import io
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import zipfile

import pytest
import requests

from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.cninfo_announcement_service import CninfoAnnouncementService, CNINFO_SEARCH_URL
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService, DocumentAcquisitionPaused, _extract_requested_date_range
from src.services.data_acquisition_task_service import DataAcquisitionTaskService


PDF = b"%PDF-1.4\n% fixture actual streamed bytes\n%%EOF\n"


class Response:
    status_code = 200

    def __init__(self, payload=None, length=None, fail=False):
        self.payload = payload
        self.headers = {} if length is None else {"Content-Length": str(length), "ETag": '"stable"'}
        self.length = length
        self.fail = fail

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def close(self):
        pass

    def iter_content(self, chunk_size):
        if self.fail:
            raise requests.ConnectionError("fixture interrupted")
        remaining = self.length
        first = True
        while remaining:
            amount = min(chunk_size, remaining)
            block = (PDF + b"x" * amount)[:amount] if first else b"x" * amount
            first = False
            remaining -= amount
            yield block


class Source:
    def __init__(self, total):
        self.total = total
        self.pages = []
        self.fail_page = None
        self.invalid_empty = False
        self.long_title = False
        self.missing_date = False
        self.wrong_code = False
        self.truncate_last = False

    def post(self, url, *, data, **kwargs):
        if url == CNINFO_SEARCH_URL:
            return Response([{"code": "300750", "category": "A股", "type": "szj", "delisted": "false",
                              "orgId": "verified", "zwjc": "宁德时代"}])
        page = int(data["pageNum"])
        self.pages.append(page)
        if page == self.fail_page:
            raise requests.ConnectionError("fixture page failed")
        if self.invalid_empty:
            return Response({"announcements": [], "totalpages": 0, "totalAnnouncement": self.total, "hasMore": True})
        start = (page - 1) * 100
        published = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        rows = [{"announcementId": f"id-{index}", "secCode": "300750", "secName": "宁德时代",
                 "announcementTitle": "同名公告：董事会决议", "announcementTime": published,
                 "adjunctUrl": f"finalpage/{index}.PDF"} for index in range(start, min(start + 100, self.total))]
        for row in rows:
            if self.long_title: row["announcementTitle"] = "关于董事会审议公司跨年度投资项目并进行关联交易的信息披露公告" * 40
            if self.missing_date: row.pop("announcementTime")
            if self.wrong_code: row["secCode"] = "600000"
        if self.truncate_last and page == (self.total + 99) // 100:
            rows = rows[:10]
        if getattr(self, "omit_totalpages", False):
            return Response({"announcements": rows})
        if getattr(self, "short_then_rest", False):
            published = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
            def item(index):
                return {"announcementId": f"id-{index}", "secCode": "300750", "secName": "宁德时代",
                        "announcementTitle": "同名公告：董事会决议", "announcementTime": published,
                        "adjunctUrl": f"finalpage/{index}.PDF"}
            if page == 1:
                return Response({"announcements": [item(index) for index in range(30)],
                                 "totalpages": 1, "totalAnnouncement": 35, "hasMore": True})
            if page == 2:
                return Response({"announcements": [item(index) for index in range(30, 35)],
                                 "totalpages": 1, "totalAnnouncement": 35, "hasMore": False})
            return Response({"announcements": [], "totalpages": 1, "totalAnnouncement": 35, "hasMore": False})
        return Response({"announcements": rows, "totalpages": (self.total + 99) // 100,
                         "totalAnnouncement": self.total, "hasMore": page * 100 < self.total})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    source = Source(151)
    state = {"gets": [], "failed": False, "fail_at": None, "length": len(PDF), "quota": 0,
             "concurrency": False, "active": 0, "peak": 0}
    mutex = threading.Lock()

    class DownloadSession:
        def __init__(self):
            self.cookies = SimpleNamespace(set=lambda *a, **k: None)

        def get(self, url, **kwargs):
            state["gets"].append(url)
            fail = state["fail_at"] == url and state["failed"]
            response = Response(length=state["length"], fail=fail)
            if state["concurrency"]:
                with mutex:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                threading.Event().wait(.01)
                def close():
                    with mutex: state["active"] -= 1
                response.close = close
            return response

        def close(self):
            pass

    monkeypatch.setattr("src.services.data_acquisition_service.get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db")))
    monkeypatch.setattr("src.services.data_acquisition_task_service.UserQuotaService", lambda: SimpleNamespace(
        consume=lambda *a, **k: state.__setitem__("quota", state["quota"] + 1)))
    monkeypatch.setattr("requests.Session", DownloadSession)
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("unexpected real network"))
    monkeypatch.setattr("src.services.data_acquisition_service.time.sleep", lambda _: None)
    cninfo = CninfoAnnouncementService(session=source, retries=0, request_interval=0)
    def factory():
        return DataAcquisitionService(planner=SimpleNamespace(model="offline", plan=lambda _: pytest.fail("unexpected AI")),
            cninfo=cninfo, financial=object(), monitor=object(), output_root=tmp_path / "packages")
    identity = set_current_user_id(74)
    try:
        yield factory, source, state
    finally:
        reset_current_user_id(identity)


def plan(*, limit=None):
    return {"title": "宁德时代近半年公告", "scope": {"symbols": ["300750.SZ"], "company_names": ["宁德时代"]},
            "tasks": [{"id": "announcements", "source": "cninfo", "resource": "announcements", "label": "宁德时代公告", "params": {}}],
            "document_export": {"version": 1, "kinds": ["announcements"], "confirmed": True,
                                "selection_mode": "all_matching" if limit is None else "explicit_limit", "requested_limit": limit},
            "include_files": True}


def wait(manager, task_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = manager.get(task_id)
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(.02)
    raise AssertionError("task did not finish")


def test_151_documents_one_zip_with_titles_and_real_all_pages(runtime):
    factory, source, state = runtime
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-test-151-documents")
    assert result["complete"] and result["summary"]["row_count"] == 151
    assert source.pages == [1, 2]
    assert len(state["gets"]) == 151
    assert "宁德时代" in result["filename"] and "151份" in result["filename"]
    with zipfile.ZipFile(factory().package_path(result["job_id"])) as archive:
        pdfs = [name for name in archive.namelist() if name.endswith(".pdf")]
        assert len(pdfs) == len(set(pdfs)) == 151
        assert all("宁德时代" in name and "同名公告" in name for name in pdfs)
        assert archive.testzip() is None
        assert all(archive.read(name).startswith(b"%PDF-") for name in pdfs)
        assert "owner_id" not in json.loads(archive.read("manifest.json"))
    assert len(list(factory().output_root.glob("*/*.zip"))) == 1


def test_failed_download_resume_same_job_skips_committed_pages_and_files(runtime):
    factory, source, state = runtime
    state.update(fail_at="https://static.cninfo.com.cn/finalpage/3.PDF", failed=True)
    service = factory()
    with pytest.raises(DocumentAcquisitionPaused, match="保留"):
        service.run("下载宁德时代近半年全部公告", plan(), job_id="acq-same-durable-job")
    assert not (service.output_root / "acq-same-durable-job" / "manifest.json").exists()
    before = list(state["gets"])
    state["failed"] = False
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-same-durable-job")
    assert result["job_id"] == "acq-same-durable-job" and result["complete"]
    assert source.pages == [1, 2]
    for index in range(3):
        assert state["gets"].count(f"https://static.cninfo.com.cn/finalpage/{index}.PDF") == 1
    for index in range(4, 8):
        assert state["gets"].count(f"https://static.cninfo.com.cn/finalpage/{index}.PDF") == 1
    assert len(state["gets"]) > len(before)


def test_page_failure_resume_uses_saved_next_page(runtime):
    factory, source, state = runtime
    source.fail_page = 2
    with pytest.raises(DataAcquisitionError):
        factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-page-checkpoint")
    source.fail_page = None
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-page-checkpoint")
    assert result["summary"]["row_count"] == 151
    assert source.pages == [1, 2, 2]


def test_103mb_document_uses_actual_stream_without_old_50mb_cap(runtime):
    factory, source, state = runtime
    source.total = 1
    state["length"] = 103 * 1024 * 1024
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-large-streamed-pdf")
    assert result["summary"]["downloaded_bytes"] == 103 * 1024 * 1024
    with zipfile.ZipFile(factory().package_path(result["job_id"])) as archive:
        assert next(item for item in archive.infolist() if item.filename.endswith(".pdf")).file_size == state["length"]


def test_short_page_with_has_more_keeps_rows_and_reads_the_next_page(runtime):
    factory, source, state = runtime
    source.total = 35
    source.short_then_rest = True
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-hasmore-short-page")
    assert result["summary"]["row_count"] == 35
    assert source.pages[:2] == [1, 2]
    assert len(state["gets"]) == 35


def test_short_page_without_totalpages_is_saved(runtime):
    factory, source, state = runtime
    source.total = 30
    source.omit_totalpages = True
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-omitted-totalpages")
    assert result["summary"]["row_count"] == 30
    assert state["gets"] and source.pages == [1]


def test_empty_page_inconsistent_total_never_claims_complete(runtime):
    factory, source, _ = runtime
    source.invalid_empty = True
    with pytest.raises(DataAcquisitionError, match="不完整"):
        factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-empty-bad-total")
    assert not list(factory().output_root.glob("*/*.zip"))


def test_unrequested_research_channel_rejected_before_metadata_or_download(runtime):
    factory, source, state = runtime
    raw = plan()
    raw["tasks"].append({"source": "tushare", "resource": "research_report", "params": {}})
    raw["document_export"]["kinds"].append("research_reports")
    with pytest.raises(DataAcquisitionError, match="未要求研究报告"):
        factory().run("下载宁德时代近半年全部公告", raw)
    assert not source.pages and not state["gets"]


def test_explicit_limit_only_and_halfyear_is_six_calendar_months(runtime):
    factory, _, state = runtime
    result = factory().run("下载宁德时代近半年公告，只要20份", plan(limit=20), job_id="acq-explicit-twenty")
    assert result["summary"]["row_count"] == 20 and len(state["gets"]) == 20
    start, end = _extract_requested_date_range("近半年所有公告")
    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
    assert (last.year - first.year) * 12 + last.month - first.month == 6
    with pytest.raises(DataAcquisitionError, match="数量"):
        factory().validate_plan("下载宁德时代近半年全部公告", plan(limit=30))


def test_same_job_other_owner_cannot_resume_or_download(runtime):
    factory, source, _ = runtime
    source.total = 1
    factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-private-document")
    token = set_current_user_id(75)
    try:
        with pytest.raises(DataAcquisitionError, match="无权"):
            factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-private-document")
        with pytest.raises(DataAcquisitionError, match="无权"):
            factory().package_path("acq-private-document")
    finally:
        reset_current_user_id(token)


def test_persistent_task_resume_preserves_job_and_consumes_quota_once(runtime, tmp_path):
    factory, source, state = runtime
    source.total = 4
    state.update(fail_at="https://static.cninfo.com.cn/finalpage/1.PDF", failed=True)
    manager = DataAcquisitionTaskService(service_factory=factory, task_root=tmp_path / "tasks")
    try:
        task = manager.submit("下载宁德时代近半年全部公告", plan())
        failed = wait(manager, task["task_id"])
        assert failed["status"] == "failed" and failed["resumable"]
        assert failed["job_id"] == task["job_id"] and failed.get("result") is None
        assert "_plan" not in failed and "_request" not in failed and "owner_id" not in failed
        state["failed"] = False
        manager._executor.shutdown(wait=True)
        manager = DataAcquisitionTaskService(service_factory=factory, task_root=tmp_path / "tasks")
        resumed = manager.resume(task["task_id"])
        done = wait(manager, task["task_id"])
        assert done["status"] == "completed" and done["result"]["complete"]
        assert resumed["task_id"] == task["task_id"] and done["job_id"] == task["job_id"]
        assert state["quota"] == 1
        assert state["gets"].count("https://static.cninfo.com.cn/finalpage/0.PDF") == 1
    finally:
        manager._executor.shutdown(wait=True)


def test_downloads_are_concurrent_bounded_per_host(runtime):
    factory, source, state = runtime
    source.total = 25
    state["concurrency"] = True
    factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-concurrent-stream")
    assert 1 < state["peak"] <= 4 <= 8
    assert state["active"] == 0


def test_long_chinese_title_real_filesystem_names_are_unique_and_bounded(runtime):
    factory, source, _ = runtime
    source.total, source.long_title = 3, True
    result = factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-long-title-files")
    with zipfile.ZipFile(factory().package_path(result["job_id"])) as archive:
        names = [name for name in archive.namelist() if name.endswith(".pdf")]
        assert len(names) == len(set(names)) == 3
        assert all(len(Path(name).name.encode("utf-8")) <= 240 for name in names)
        assert all("关于董事会审议" in name for name in names)


@pytest.mark.parametrize("user_text", ["只下载宁德时代近半年公告，不要研报", "宁德时代近半年财报不是券商研报"])
def test_negative_report_mentions_do_not_authorize_report_source(runtime, user_text):
    factory, _, state = runtime
    raw = plan()
    raw["tasks"].append({"id": "reports", "source": "tushare", "resource": "research_report", "params": {}})
    raw["document_export"]["kinds"].append("research_reports")
    with pytest.raises(DataAcquisitionError, match="未要求研究报告"):
        factory().validate_plan(user_text, raw)
    raw.pop("document_export")
    with pytest.raises(DataAcquisitionError, match="未要求研究报告"):
        factory().validate_plan(user_text, raw, require_ready=True)
    assert not state["gets"]


def test_negative_announcements_do_not_authorize_announcement_source(runtime):
    factory, _, _ = runtime
    with pytest.raises(DataAcquisitionError, match="公告"):
        factory().validate_plan("只要宁德时代近半年研报，不要公告", plan())


@pytest.mark.parametrize("phrase", ["不止30份", "30份只是预览，全部下载", "全部不限制数量，30份不是上限"])
def test_preview_or_unlimited_count_never_becomes_product_cap(runtime, phrase):
    factory, _, _ = runtime
    with pytest.raises(DataAcquisitionError, match="数量"):
        factory().validate_plan("下载宁德时代近半年公告，" + phrase, plan(limit=30))


@pytest.mark.parametrize("field", ["missing_date", "wrong_code"])
def test_unverifiable_metadata_cannot_claim_all_documents_complete(runtime, field):
    factory, source, state = runtime
    setattr(source, field, True)
    source.total = 1
    with pytest.raises(DataAcquisitionError, match="代码|目标公司|日期"):
        factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-unverified-metadata")
    assert not state["gets"]


def test_selected_announcement_is_revalidated_and_does_not_widen_scope(runtime):
    factory, source, state = runtime
    source.total = 5
    raw = plan()
    today = datetime.now(timezone.utc).date().isoformat()
    raw["tasks"][0]["params"] = {"start_date": today, "end_date": today, "selected_announcement_ids": ["id-2"]}
    result = factory().run("下载宁德时代这份公告", raw, job_id="acq-selected-exact-id")
    assert result["summary"]["row_count"] == 1
    assert state["gets"] == ["https://static.cninfo.com.cn/finalpage/2.PDF"]


def test_terminal_total_151_but_only_110_returned_never_publishes(runtime):
    factory, source, state = runtime
    source.truncate_last = True
    with pytest.raises(DataAcquisitionError, match="数量与分页覆盖"):
        factory().run("下载宁德时代近半年全部公告", plan(), job_id="acq-bad-advertised-total")
    assert not state["gets"]


def test_chinese_explicit_count_and_task_local_limit_contract(runtime):
    factory, _, state = runtime
    raw = plan()
    raw["document_export"]["selection_mode"] = "explicit_limit"
    raw["tasks"][0]["params"]["requested_limit"] = 20
    result = factory().run("下载宁德时代近半年公告，只要二十份", raw, job_id="acq-chinese-task-limit")
    assert result["summary"]["row_count"] == len(state["gets"]) == 20


def test_global_limit_is_not_repeated_for_every_dataset(runtime):
    factory, source, state = runtime
    source.total = 6
    raw = plan(limit=5)
    raw["tasks"].append({"id": "announcements-again", "source": "cninfo", "resource": "announcements", "params": {}})
    result = factory().run("下载宁德时代近半年公告，只要5份", raw, job_id="acq-global-not-per-task")
    assert result["summary"]["row_count"] == len(state["gets"]) == 5
    assert result["datasets"][1]["retrieval"]["intentional_limit"] is True


def test_task_private_ledger_uses_owner_only_permissions(runtime, tmp_path):
    factory, source, _ = runtime
    source.total = 1
    manager = DataAcquisitionTaskService(service_factory=factory, task_root=tmp_path / "private-tasks")
    try:
        task = manager.submit("下载宁德时代近半年公告", plan())
        wait(manager, task["task_id"])
        assert manager.task_root.stat().st_mode & 0o777 == 0o700
        assert manager._task_path(task["task_id"]).stat().st_mode & 0o777 == 0o600
    finally:
        manager._executor.shutdown(wait=True)


def test_restart_repair_does_not_mark_other_process_active_task_failed(runtime, tmp_path):
    factory, _, _ = runtime
    task_id = "acq-other-process-active"
    first = DataAcquisitionTaskService(service_factory=factory, task_root=tmp_path / "tasks")
    first._write_state({"task_id": task_id, "owner_id": "user:74", "status": "running", "document_export": True})
    lease = first._execution_lock(task_id)
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    second = DataAcquisitionTaskService(service_factory=factory, task_root=tmp_path / "tasks")
    try:
        assert second.get(task_id)["status"] == "running"
        lease.close()
        second._repair_interrupted_tasks()
        assert second.get(task_id)["phase"] == "interrupted"
    finally:
        lease.close()
        first._executor.shutdown(wait=True)
        second._executor.shutdown(wait=True)


def test_packaging_space_pause_keeps_all_files_and_resume_only_packages(runtime):
    factory, source, state = runtime
    source.total = 4
    service = factory()
    check_space = service._document_space
    def resource_pause(path, required=0):
        if required:
            raise DocumentAcquisitionPaused("可用磁盘空间不足，断点保留")
        return check_space(path, required)
    service._document_space = resource_pause
    with pytest.raises(DocumentAcquisitionPaused, match="空间不足"):
        service.run("下载宁德时代近半年公告", plan(), job_id="acq-space-paused-package")
    assert len(state["gets"]) == 4
    assert not (service.output_root / "acq-space-paused-package").exists()
    result = factory().run("下载宁德时代近半年公告", plan(), job_id="acq-space-paused-package")
    assert result["complete"] and len(state["gets"]) == 4 and source.pages == [1]


def test_two_company_document_ranges_remain_separate_not_first_range_broadcast(runtime):
    factory, _, _ = runtime
    raw = plan()
    first = "宁德时代公告2026-04-22至2026-10-22"
    second = "宁德时代公告2025-01-01至2025-12-31"
    raw["tasks"][0]["params"] = {"start_date": "20260422", "end_date": "20261022", "date_evidence": first}
    raw["tasks"].append({"id": "announcements-old-year", "source": "cninfo", "resource": "announcements",
                         "params": {"start_date": "20250101", "end_date": "20251231", "date_evidence": second}})
    normalized = factory().validate_plan("下载" + first + "以及" + second, raw)
    assert normalized["scope"]["start_date"] == "2025-01-01"
    assert normalized["tasks"][0]["resolved_scope"]["start_date"] == "2026-04-22"
    assert normalized["tasks"][1]["resolved_scope"]["start_date"] == "2025-01-01"


def test_document_redirect_cannot_fetch_unknown_host(runtime, monkeypatch, tmp_path):
    class RedirectSession:
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            response = Response()
            response.status_code = 302
            response.headers = {"Location": "https://unknown.invalid/private"}
            return response
        def close(self): pass
    monkeypatch.setattr("requests.Session", RedirectSession)
    with pytest.raises(DataAcquisitionError, match="离开已核验"):
        DataAcquisitionService._download_one("https://static.cninfo.com.cn/source.pdf", tmp_path / "source.pdf", remaining=1000, document_mode=True)
