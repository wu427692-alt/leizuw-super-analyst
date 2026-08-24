from __future__ import annotations

import json
from pathlib import Path
import time
import zipfile

from src.services.data_acquisition_service import DataAcquisitionService
from src.services.data_acquisition_task_service import DataAcquisitionTaskService


class FakePlanner:
    api_key = "configured"
    model = "test-model"

    def plan(self, request_text):
        return {
            "title": "测试数据包", "objective": request_text, "model": self.model,
            "generated_at": "2026-08-19T00:00:00Z", "caveats": [],
            "tasks": [
                {"id": "market", "source": "tushare", "resource": "daily", "label": "日线行情",
                 "reason": "验证行情", "params": {"ts_code": "603306.SH"}, "fields": []},
                {"id": "notes", "source": "zsxq", "resource": "research_notes", "label": "调研纪要",
                 "reason": "验证纪要", "params": {"symbol": "603306.SH"}, "fields": []},
            ],
        }


class FakeTushare:
    available = True


class FakeFinancial:
    tushare = FakeTushare()

    def query(self, *, source, resource, params, fields=None):
        if source == "tushare":
            return {"rows": [{"ts_code": params["ts_code"], "trade_date": "20260818", "close": 42.5}]}
        return {"rows": [{"topic_id": "1", "title": "华懋科技调研", "symbols": ["603306.SH"], "images": [{"view_url": "/media/1"}]}]}


class FakeMonitor:
    def list_events(self, **params):
        return {"items": []}

    def list_announcements(self, **params):
        return {"items": []}


def test_acquisition_generates_auditable_multi_format_package(tmp_path: Path):
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )

    plan = service.plan("获取华懋科技数据")
    result = service.run("获取华懋科技数据", plan)

    assert result["status"] == "success"
    assert result["summary"]["task_count"] == 2
    assert result["summary"]["success_count"] == 2
    assert result["summary"]["failed_count"] == 0
    assert result["summary"]["row_count"] == 2
    assert result["summary"]["include_files"] is False
    package = service.package_path(result["job_id"])
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "README.md" in names
        assert any(name.endswith(".xlsx") for name in names)
        assert sum(name.endswith(".json") for name in names) >= 3
        assert sum(name.endswith(".csv") for name in names) == 2
        assert any(name.startswith("tushare/") for name in names)
        assert any(name.startswith("zsxq/") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["datasets"][0]["source"] == "tushare"
        assert manifest["datasets"][1]["source"] == "zsxq"


def test_acquisition_reports_real_task_and_packaging_progress(tmp_path: Path):
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    updates = []

    result = service.run("获取华懋科技数据", progress_callback=updates.append)

    progress = [item["progress"] for item in updates]
    assert progress == sorted(progress)
    assert progress[-1] == 100
    assert {item["phase"] for item in updates} >= {"validating", "fetching", "exporting", "packaging", "completed"}
    assert any(item.get("completed_tasks") == 2 for item in updates)
    assert result["summary"]["task_count"] == 2


def test_background_acquisition_task_persists_progress_and_result(tmp_path: Path):
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    manager = DataAcquisitionTaskService(service_factory=lambda: service, task_root=tmp_path / ".tasks")

    submitted = manager.submit("获取华懋科技数据", FakePlanner().plan("获取华懋科技数据"))
    assert submitted["status"] == "queued"
    assert submitted["progress"] == 0
    assert len(submitted["tasks"]) == 2

    deadline = time.monotonic() + 5
    current = submitted
    while current["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.02)
        current = manager.get(submitted["task_id"])

    assert current["status"] == "completed"
    assert current["progress"] == 100
    assert current["result"]["summary"]["row_count"] == 2
    assert service.package_path(current["job_id"]).is_file()


def test_acquisition_keeps_package_when_one_source_fails(tmp_path: Path):
    class PartiallyFailingFinancial(FakeFinancial):
        def query(self, *, source, resource, params, fields=None):
            if source == "tushare":
                raise RuntimeError("secret upstream detail")
            return super().query(source=source, resource=resource, params=params, fields=fields)

    service = DataAcquisitionService(
        planner=FakePlanner(), financial=PartiallyFailingFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    result = service.run("获取华懋科技数据")

    assert result["status"] == "partial"
    assert result["summary"]["success_count"] == 1
    assert result["summary"]["failed_count"] == 1
    assert "secret upstream detail" not in json.dumps(result, ensure_ascii=False)
    assert service.package_path(result["job_id"]).is_file()


def test_market_wide_news_is_filtered_to_requested_company(tmp_path: Path):
    class NewsPlanner(FakePlanner):
        def plan(self, request_text):
            return {
                "title": "定向新闻", "objective": request_text, "model": self.model,
                "generated_at": "2026-08-19T00:00:00Z", "caveats": [],
                "scope": {"symbols": ["603306.SH"], "company_names": ["华懋科技"],
                          "keywords": [], "start_date": "2026-08-01", "end_date": "2026-08-19",
                          "market_wide": False},
                "tasks": [{"id": "news", "source": "tushare", "resource": "major_news",
                           "label": "公司新闻", "reason": "只看目标公司", "params": {}, "fields": []}],
            }

    class MarketNewsFinancial(FakeFinancial):
        def query(self, *, source, resource, params, fields=None):
            return {"rows": [
                {"title": "华懋科技发布新产品", "content": "603306 相关进展"},
                {"title": "其他公司公告", "content": "与目标公司无关"},
            ]}

    service = DataAcquisitionService(
        planner=NewsPlanner(), financial=MarketNewsFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    result = service.run("只获取华懋科技新闻")
    dataset_file = tmp_path / result["job_id"] / result["datasets"][0]["files"][0]
    rows = json.loads(dataset_file.read_text(encoding="utf-8"))

    assert result["summary"]["row_count"] == 1
    assert rows == [{"title": "华懋科技发布新产品", "content": "603306 相关进展"}]


def test_zsxq_task_splits_planner_phrase_and_uses_scope_keywords(tmp_path: Path):
    calls = []

    class KeywordFinancial(FakeFinancial):
        def query(self, *, source, resource, params, fields=None):
            del source, resource, fields
            calls.append(dict(params))
            if params.get("query") == "无人机":
                return {"rows": [{"topic_id": "drone-1", "title": "无人机产业纪要", "content": "低空经济需求"}]}
            return {"rows": []}

    service = DataAcquisitionService(
        planner=FakePlanner(), financial=KeywordFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    rows = service._execute_task(
        {"source": "zsxq", "resource": "research_notes", "params": {"query": "低空经济 无人机"}},
        {"symbols": [], "company_names": [], "keywords": ["低空经济", "无人机"],
         "start_date": "", "end_date": "", "market_wide": False},
    )

    assert [item.get("query") for item in calls] == ["低空经济 无人机", "低空经济", "无人机"]
    assert [row["topic_id"] for row in rows] == ["drone-1"]


def test_zsxq_text_match_is_not_rejected_by_unrelated_legacy_symbol_tag(tmp_path: Path):
    class LegacyTaggedFinancial(FakeFinancial):
        def query(self, *, source, resource, params, fields=None):
            del source, resource, params, fields
            return {"rows": [{"topic_id": "1", "title": "华懋科技产业跟踪",
                              "content": "正文明确讨论华懋", "symbols": ["000001.SZ"]}]}

    service = DataAcquisitionService(
        planner=FakePlanner(), financial=LegacyTaggedFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    rows = service._execute_task(
        {"source": "zsxq", "resource": "research_notes", "params": {}},
        {"symbols": ["603306.SH"], "company_names": ["华懋科技"], "keywords": [],
         "start_date": "", "end_date": "", "market_wide": False},
    )

    assert len(rows) == 1


def test_cninfo_announcement_task_queries_official_source_directly(tmp_path: Path):
    class AnnouncementPlanner(FakePlanner):
        def plan(self, request_text):
            return {
                "title": "仅公告", "objective": request_text, "model": self.model,
                "generated_at": "2026-08-19T00:00:00Z", "caveats": [],
                "scope": {"symbols": ["603306.SH"], "company_names": [], "keywords": [],
                          "start_date": "2026-08-12", "end_date": "2026-08-19", "market_wide": False},
                "tasks": [{"id": "announcement", "source": "cninfo", "resource": "announcements",
                           "label": "华懋科技公告", "reason": "仅公告",
                           "params": {"ts_code": "603306.SH", "start_date": "20260812",
                                      "end_date": "20260819", "unsupported": "drop-me"}, "fields": []}],
            }

    class StrictCninfo:
        def fetch(self, *, start_date, end_date, symbols, categories, keyword, page_size, max_pages):
            del categories, keyword, page_size, max_pages
            assert symbols == ["603306.SH"]
            assert start_date.isoformat() == "2026-08-12"
            assert end_date.isoformat() == "2026-08-19"
            return [{"announcement_id": "1", "code": "603306", "title": "华懋科技公告",
                     "pdf_url": "https://static.cninfo.com.cn/finalpage/test.pdf"}]

    service = DataAcquisitionService(
        planner=AnnouncementPlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), cninfo=StrictCninfo(), output_root=tmp_path,
    )
    result = service.run("只获取华懋科技近7天巨潮公告")
    assert result["status"] == "success"
    assert result["summary"]["row_count"] == 1


def test_file_request_downloads_original_files_into_zip(tmp_path: Path, monkeypatch):
    class FilePlanner(FakePlanner):
        def plan(self, request_text):
            return {
                "title": "公告文件包", "objective": request_text, "model": self.model,
                "generated_at": "2026-08-19T00:00:00Z", "caveats": [], "include_files": True,
                "scope": {"symbols": ["603306.SH"], "company_names": ["华懋科技"], "keywords": [],
                          "start_date": "2026-08-12", "end_date": "2026-08-19", "market_wide": False},
                "tasks": [{"id": "announcement", "source": "cninfo", "resource": "announcements",
                           "label": "巨潮公告", "reason": "现场检索并下载", "params": {}, "fields": []}],
            }

    class FileCninfo:
        def fetch(self, **kwargs):
            return [{"announcement_id": "1", "code": "603306", "title": "年度报告",
                     "pdf_url": "https://static.cninfo.com.cn/finalpage/test.pdf"}]

    def fake_download(url, target, *, remaining):
        del url, remaining
        target.write_bytes(b"%PDF-test")
        return target.stat().st_size

    monkeypatch.setattr(DataAcquisitionService, "_download_one", staticmethod(fake_download))
    service = DataAcquisitionService(planner=FilePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(),
                                     cninfo=FileCninfo(), output_root=tmp_path)
    result = service.run("下载华懋科技公告PDF文件")
    assert result["summary"]["downloaded_file_count"] == 1
    with zipfile.ZipFile(service.package_path(result["job_id"])) as archive:
        assert any(name.startswith("attachments/cninfo/") and name.endswith(".pdf") for name in archive.namelist())


def test_scope_resolves_configured_watchlist_name_to_symbol(monkeypatch) -> None:
    monkeypatch.setenv("ESSAY_WATCHLIST", "603306.SH:华懋科技,300476.SZ:胜宏科技")
    scope = DataAcquisitionService.normalize_scope(
        {"company_names": ["华懋科技"], "market_wide": False},
        [{"params": {"company_name": "华懋科技"}}],
        "只获取华懋科技公告",
    )
    assert scope["symbols"] == ["603306.SH"]
    assert scope["company_names"] == ["华懋科技"]


def test_tianyancha_entity_resolution_keeps_l0_relevance_order(tmp_path: Path, monkeypatch):
    service = DataAcquisitionService(planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path)
    monkeypatch.setattr(service, "_run_tyc", lambda command, key: {"items": [
        {"id": 1, "name": "华懋（厦门）新材料科技股份有限公司"},
        {"id": 2, "name": "深圳市华懋科技有限公司"},
    ]})
    assert service._resolve_tyc_company("华懋科技")["id"] == 1
