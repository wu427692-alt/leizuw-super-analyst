from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import zipfile

import pytest

from src.services.data_acquisition_service import DataAcquisitionService
from src.services.data_acquisition_service import DataAcquisitionError
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


def test_background_acquisition_task_is_owner_scoped(tmp_path: Path):
    owner = {"value": "user:1"}
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    manager = DataAcquisitionTaskService(
        service_factory=lambda: service,
        task_root=tmp_path / ".tasks",
        owner_getter=lambda: owner["value"],
    )
    submitted = manager.submit("获取华懋科技数据", FakePlanner().plan("获取华懋科技数据"))
    assert "owner_id" not in submitted
    assert manager.list_tasks()["items"][0]["task_id"] == submitted["task_id"]

    owner["value"] = "user:2"
    assert manager.list_tasks() == {"items": [], "total": 0}
    with pytest.raises(DataAcquisitionError, match="无权访问"):
        manager.get(submitted["task_id"])
    manager._executor.shutdown(wait=True)


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


def test_research_report_request_enforces_topics_two_years_and_download(tmp_path: Path):
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    today = datetime.now(timezone(timedelta(hours=8))).date()
    expected_start = today.replace(year=today.year - 2)
    raw_plan = {
        "title": "研报下载",
        "tasks": [{
            "id": "reports", "source": "tushare", "resource": "research_report",
            "label": "研报", "reason": "下载", "fields": [],
            "params": {"keyword": "低空经济 无人机", "start_date": "20260801", "end_date": "20260821"},
        }],
        "scope": {"symbols": [], "company_names": [], "keywords": [],
                  "start_date": "2026-08-01", "end_date": "2026-08-21", "market_wide": False},
    }

    plan = service._validate_plan(
        raw_plan, "我要下载研报，主题要是低空经济或者无人机方向，最好都是深度研究，最近两年的",
    )

    assert plan["include_files"] is True
    assert plan["scope"]["start_date"] == expected_start.isoformat()
    assert plan["scope"]["end_date"] == today.isoformat()
    assert set(plan["scope"]["keywords"]) >= {"低空经济", "无人机"}
    params = plan["tasks"][0]["params"]
    assert params["start_date"] == expected_start.strftime("%Y%m%d")
    assert params["end_date"] == today.strftime("%Y%m%d")
    assert params["keyword_mode"] == "any"
    assert params["depth_preference"] == "prefer"
    assert params["ai_filter"] is False


def test_research_report_pipeline_filters_before_export_and_never_sends_internal_keywords(
    tmp_path: Path, monkeypatch,
):
    calls = []

    class ReportFinancial(FakeFinancial):
        def query(self, *, source, resource, params, fields=None):
            del source, fields
            assert resource == "research_report"
            calls.append(dict(params))
            if params["end_date"] != "20260821" or params["offset"]:
                return {"rows": []}
            return {"rows": [
                {"trade_date": "20260820", "title": "低空经济产业链深度研究", "abstr": "低空经济 eVTOL 产业链全景",
                 "report_type": "行业研报", "ind_name": "航空", "url": "https://example.com/low.pdf"},
                {"trade_date": "20260820", "title": "保险行业深度研究", "abstr": "银保业务趋势",
                 "report_type": "行业研报", "ind_name": "保险", "url": "https://example.com/insurance.pdf"},
                {"trade_date": "20260819", "title": "无人机行业周报", "abstr": "UAV 需求跟踪",
                 "report_type": "行业研报", "ind_name": "军工", "url": "https://example.com/drone.pdf"},
            ]}

    monkeypatch.setenv("DATA_ACQUISITION_REPORT_AI_FILTER", "0")
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=ReportFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    task = {
        "source": "tushare", "resource": "research_report", "fields": [],
        "params": {"start_date": "20240821", "end_date": "20260821", "topics": ["低空经济", "无人机"],
                   "keyword": "低空经济 无人机", "keyword_mode": "any", "depth_preference": "prefer",
                   "ai_filter": True, "max_results": 100},
    }
    scope = {"symbols": [], "company_names": [], "keywords": ["低空经济", "无人机"],
             "start_date": "2024-08-21", "end_date": "2026-08-21", "market_wide": False}

    rows = service._execute_tushare(task, scope)

    assert [row["title"] for row in rows] == ["低空经济产业链深度研究", "无人机行业周报"]
    assert rows[0]["筛选命中主题"] == "低空经济"
    assert rows[0]["深度评分"] > rows[1]["深度评分"]
    assert all(not ({"topics", "keywords", "keyword", "query", "keyword_mode", "depth_preference", "ai_filter"} & set(call)) for call in calls)
    assert min(call["start_date"] for call in calls) == "20240821"
    assert max(call["end_date"] for call in calls) == "20260821"


def test_research_report_without_any_scope_is_blocked(tmp_path: Path):
    service = DataAcquisitionService(
        planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    raw_plan = {
        "tasks": [{"source": "tushare", "resource": "research_report", "params": {}, "fields": []}],
        "scope": {"symbols": [], "company_names": [], "keywords": [], "market_wide": False},
    }
    try:
        service._validate_plan(raw_plan, "下载一些研报")
    except DataAcquisitionError as exc:
        assert "已阻止全量获取" in str(exc)
    else:
        raise AssertionError("unscoped research report acquisition should be blocked")


def test_research_report_ai_review_excludes_incidental_keyword_mentions(tmp_path: Path, monkeypatch):
    class ReviewPlanner(FakePlanner):
        base_url = "https://api.example.test"
        timeout = 30

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"items": [
                {"id": 0, "relevant": True, "score": 94, "depth_score": 86,
                 "matched_topics": ["低空经济"], "reason": "主题深度研究"},
                {"id": 1, "relevant": False, "score": 8, "depth_score": 80,
                 "matched_topics": [], "reason": "仅作为下游提及"},
            ]}, ensure_ascii=False)}}]}

    monkeypatch.setattr("src.services.data_acquisition_service.requests.post", lambda *args, **kwargs: FakeResponse())
    service = DataAcquisitionService(
        planner=ReviewPlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path,
    )
    rows = [
        {"title": "低空经济产业深度", "abstr": "eVTOL 产业链", "ind_name": "航空",
         "report_type": "行业研报", "相关性评分": 80, "深度评分": 80},
        {"title": "镁行业深度", "abstr": "下游包括汽车、低空经济和无人机", "ind_name": "金属",
         "report_type": "行业研报", "相关性评分": 80, "深度评分": 80},
    ]

    reviewed = service._ai_review_research_reports(rows, ["低空经济", "无人机"], "prefer")

    assert [row["title"] for row in reviewed] == ["低空经济产业深度"]
    assert reviewed[0]["AI语义复核"] == "通过"


def test_eastmoney_pdf_challenge_is_solved_without_browser_execution():
    script = """<script>
    function a(a){function n(){t=a[_0x649a(\"0x7\")](t,2952200192);}
    var e={WTKkN:2108261434,bOYDu:56644541,dtzqS:function(a,n){return a+n},wyeCN:152937612},t=0;}
    document.cookie=\"__tst_status=\"+a(0),document.cookie=\"EO_Bot_Ssid=\"+a(1);
    </script>"""

    assert DataAcquisitionService._solve_eastmoney_pdf_challenge(script) == {
        "EO_Bot_Ssid": "2952200192",
        "__tst_status": "2317843587#",
    }


def test_tianyancha_entity_resolution_keeps_l0_relevance_order(tmp_path: Path, monkeypatch):
    service = DataAcquisitionService(planner=FakePlanner(), financial=FakeFinancial(), monitor=FakeMonitor(), output_root=tmp_path)
    monkeypatch.setattr(service, "_run_tyc", lambda command, key: {"items": [
        {"id": 1, "name": "华懋（厦门）新材料科技股份有限公司"},
        {"id": 2, "name": "深圳市华懋科技有限公司"},
    ]})
    assert service._resolve_tyc_company("华懋科技")["id"] == 1
