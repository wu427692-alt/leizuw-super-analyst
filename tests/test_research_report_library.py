from datetime import date, timedelta
from io import BytesIO
from unittest.mock import Mock

from openpyxl import load_workbook
import pytest

from src.services.research_report_library_service import ResearchReportLibraryError, ResearchReportLibraryService
from src.storage import DatabaseManager, utc_naive_now


class FakeFinancial:
    def __init__(self):
        self.calls = []

    def query(self, *, source, resource, params, fields=None):
        self.calls.append({"source": source, "resource": resource, "params": params, "fields": fields})
        if params["offset"]:
            return {"rows": []}
        return {"rows": [
            {
                "trade_date": "20260820",
                "title": "低空经济产业链深度研究",
                "abstr": "无人机、eVTOL与低空基础设施进入商业化阶段。",
                "report_type": "行业研报",
                "author": "研究员甲",
                "name": "",
                "ts_code": "",
                "inst_csname": "测试证券",
                "ind_name": "航空装备",
                "url": "https://example.com/low-altitude.pdf",
            },
            {
                "trade_date": "20260819",
                "title": "华懋科技公司点评",
                "abstr": "安全气囊材料业务跟踪。",
                "report_type": "个股研报",
                "author": "研究员乙",
                "name": "华懋科技",
                "ts_code": "603306.SH",
                "inst_csname": "示例证券",
                "ind_name": "汽车零部件",
                "url": "https://example.com/603306.pdf",
            },
        ]}


def test_two_year_report_library_sync_search_facets_and_export(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'reports.db'}")
    financial = FakeFinancial()
    service = ResearchReportLibraryService(db_manager=db, financial=financial)
    try:
        windows = service._date_windows(date(2026, 8, 18), date(2026, 8, 24))
        service._write_state(
            status="queued", progress=0, start_date=date(2026, 8, 18), end_date=date(2026, 8, 24),
            total_windows=len(windows), completed_windows=0, scanned_rows=0, saved_rows=0,
        )
        service._run_sync(date(2026, 8, 18), date(2026, 8, 24), windows)

        status = service.status()
        assert status["status"] == "completed"
        assert status["total"] == 2
        assert status["pdf_count"] == 2
        assert financial.calls[0]["resource"] == "research_report"
        assert "abstr" in financial.calls[0]["fields"]

        by_title = service.search(title_query="低空经济", broker="测试证券")
        assert by_title["total"] == 1
        assert by_title["items"][0]["title"] == "低空经济产业链深度研究"
        assert "深度研究" in by_title["items"][0]["tags"]

        by_content = service.search(content_query="安全气囊", company="华懋科技")
        assert by_content["total"] == 1
        assert by_content["items"][0]["ts_code"] == "603306.SH"

        facets = service.facets()
        assert {item["value"] for item in facets["brokers"]} == {"测试证券", "示例证券"}
        assert any(item["value"] == "深度研究" for item in facets["tags"])

        export = service.export_selected([by_title["items"][0]["id"]])
        workbook = load_workbook(BytesIO(export), read_only=True)
        sheet = workbook["已选研报"]
        assert sheet.max_row == 2
        assert sheet.cell(2, 2).value == "低空经济产业链深度研究"
        assert sheet.cell(2, 11).value == "https://example.com/low-altitude.pdf"
    finally:
        service._executor.shutdown(wait=True, cancel_futures=True)
        DatabaseManager.reset_instance()


@pytest.fixture
def report_service(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'selection.db'}")
    service = ResearchReportLibraryService(db_manager=db, financial=FakeFinancial())
    service._upsert([
        {"trade_date": "20260820", "title": f"跨页研报{index:02d}", "abstr": "=SUM(1,2)",
         "report_type": "行业研报", "inst_csname": "测试证券", "url": f"https://example.com/{index}.pdf"}
        for index in range(35)
    ])
    yield service
    service._executor.shutdown(wait=True, cancel_futures=True)
    DatabaseManager.reset_instance()


def test_export_keeps_exact_cross_page_selection_and_pdf_links(report_service):
    first = report_service.search(page=1, page_size=30)
    second = report_service.search(page=2, page_size=30)
    assert first["total"] == 35
    assert len(second["items"]) == 5
    chosen = [first["items"][0], second["items"][0]]
    workbook = load_workbook(BytesIO(report_service.export_selected([row["id"] for row in chosen])))
    sheet = workbook["已选研报"]
    assert sheet.max_row == 3
    assert {sheet.cell(row, 2).value for row in (2, 3)} == {row["title"] for row in chosen}
    assert {sheet.cell(row, 11).hyperlink.target for row in (2, 3)} == {row["pdf_url"] for row in chosen}
    assert sheet.cell(2, 3).data_type == "s"
    assert sheet.cell(2, 3).value == "=SUM(1,2)"


def test_export_rejects_missing_records_instead_of_empty_or_partial_success(report_service):
    existing = report_service.search()["items"][0]["id"]
    for ids in ([999999], [existing, 999999]):
        with pytest.raises(ResearchReportLibraryError, match="1篇已不可用"):
            report_service.export_selected(ids)


def test_export_rejects_oversized_selection_and_deduplicates_ids(report_service):
    with pytest.raises(ResearchReportLibraryError, match="最多导出1000篇"):
        report_service.export_selected(range(1001))
    existing = report_service.search()["items"][0]["id"]
    workbook = load_workbook(BytesIO(report_service.export_selected([existing, existing])))
    assert workbook["已选研报"].max_row == 2


@pytest.mark.parametrize("latest_age", [0, 1, 2, 3])
def test_due_library_checks_recent_window_even_with_today_report(report_service, latest_age):
    today = date.today()
    report_service.status = Mock(return_value={
        "status": "completed", "total": 35, "earliest_trade_date": (today - timedelta(days=730)).isoformat(),
        "latest_trade_date": (today - timedelta(days=latest_age)).isoformat(),
        "completed_at": (utc_naive_now() - timedelta(hours=7)).isoformat(),
    })
    report_service.start_sync = Mock(return_value={"status": "queued"})
    assert report_service.ensure_background_sync()["status"] == "queued"
    report_service.start_sync.assert_called_once_with(
        start_date=today - timedelta(days=14), end_date=today, only_if_due=True,
    )


@pytest.mark.parametrize("state,age_hours", [("completed", 1), ("running", 7), ("queued", 7)])
def test_increment_cadence_does_not_resubmit_recent_or_running_sync(report_service, state, age_hours):
    today = date.today()
    report_service.status = Mock(return_value={
        "status": state, "total": 35, "earliest_trade_date": (today - timedelta(days=730)).isoformat(),
        "latest_trade_date": today.isoformat(), "completed_at": (utc_naive_now() - timedelta(hours=age_hours)).isoformat(),
        "updated_at": utc_naive_now().isoformat(),
    })
    report_service.start_sync = Mock()
    report_service.ensure_background_sync()
    report_service.start_sync.assert_not_called()
