"""No-data and attachment failures must not be advertised as successful downloads."""

import json
import time
from types import SimpleNamespace
import zipfile

import pytest

import src.services.data_acquisition_service as acquisition
from src.services.data_acquisition_service import DataAcquisitionService
from src.services.data_acquisition_task_service import DataAcquisitionTaskService


class Announcements:
    def __init__(self):
        self.rows = []
        self.calls = []

    def fetch(self, **params):
        self.calls.append(params)
        return list(self.rows)

    def search_securities(self, query, **kwargs):
        pytest.fail("known local security should not require network resolution")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(acquisition, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")))
    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: pytest.fail("unexpected network"))
    monkeypatch.setattr(acquisition.EssayDailyReportService, "_daily_watchlist", lambda: [])
    monkeypatch.setattr(acquisition, "get_stock_entity_index_records", lambda: [[
        "688828.SH", "688828", "国仪公司", "guoyigongsi", "gygs", ["国仪"], "CN", "stock", True, 100,
    ]])
    return DataAcquisitionService(
        planner=SimpleNamespace(model="offline", plan=lambda _: pytest.fail("unexpected AI")),
        financial=SimpleNamespace(query=lambda **_: {"rows": [{"ts_code": "688828.SH", "close": 1}]}),
        monitor=object(), cninfo=Announcements(), output_root=tmp_path / "packages",
    )


def plan_for(*, daily=False, include_files=False):
    tasks = [{"id": "announcements", "source": "cninfo", "resource": "announcements",
              "params": {"symbols": ["688828.SH"]}}]
    if daily:
        tasks.append({"id": "daily", "source": "tushare", "resource": "daily",
                      "params": {"ts_code": "688828.SH"}})
    return {"scope": {"symbols": ["688828.SH"]}, "tasks": tasks, "include_files": include_files}


def test_empty_upstream_result_is_no_data_not_success(service):
    result = service.run("获取国仪公司所有公告", plan_for())
    assert service.cninfo.calls
    assert all(call["symbols"] == ["688828.SH"] for call in service.cninfo.calls)
    assert min(call["start_date"].isoformat() for call in service.cninfo.calls) == "1990-01-01"
    assert result["status"] == "failed"
    assert result["summary"]["row_count"] == 0
    assert result["summary"]["success_count"] == 0
    assert result["summary"]["failed_count"] == 1
    dataset = result["datasets"][0]
    assert dataset["status"] == "failed"
    assert dataset["outcome"] == "no_data"
    assert "未返回" in dataset["error"] or "未取得" in dataset["error"]
    assert "未取得" in service.result_message(result)
    # A diagnostic package remains inspectable, but it is not a successful data delivery.
    with zipfile.ZipFile(service.package_path(result["job_id"])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["status"] == "failed"
    assert manifest["datasets"][0]["outcome"] == "no_data"


def test_one_no_data_channel_keeps_other_channel_and_marks_partial(service):
    result = service.run("获取国仪公司所有公告和行情", plan_for(daily=True))
    assert result["status"] == "partial"
    assert result["summary"]["row_count"] == 1
    assert result["summary"]["success_count"] == 1
    assert result["summary"]["failed_count"] == 1
    assert result["datasets"][0]["outcome"] == "no_data"
    assert result["datasets"][1]["status"] == "success"
    assert "部分" in service.result_message(result)


def test_requested_attachment_failure_downgrades_package_but_preserves_rows(service):
    service.cninfo.rows = [{
        "announcement_id": "offline-test", "title": "国仪公司测试公告",
        "symbol": "688828.SH", "pdf_url": "",
    }]
    result = service.run("下载国仪公司所有公告PDF", plan_for(include_files=True))
    assert result["summary"]["row_count"] == 1
    assert result["summary"]["failed_file_count"] == 1
    assert result["summary"]["downloaded_file_count"] == 0
    assert result["status"] == "partial"
    assert result["datasets"][0]["attachments"][0]["status"] == "failed"
    assert "部分" in service.result_message(result)


def test_background_no_data_is_failed_and_keeps_diagnostic_result(service, tmp_path):
    manager = DataAcquisitionTaskService(service_factory=lambda: service, task_root=tmp_path / "tasks")
    try:
        state = manager.submit("获取国仪公司所有公告", plan_for())
        deadline = time.monotonic() + 5
        while state["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.02)
            state = manager.get(state["task_id"])
        assert state["status"] == "failed"
        assert state["phase"] == "failed"
        assert state["result"]["status"] == "failed"
        assert state["result"]["datasets"][0]["outcome"] == "no_data"
        assert state["job_id"] == state["result"]["job_id"]
        assert "未取得" in state["message"]
        assert state["error"]
        assert service.package_path(state["job_id"]).is_file()
    finally:
        manager._executor.shutdown(wait=True)
