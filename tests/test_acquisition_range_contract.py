"""Date scope is determined by user intent, never by a planner's default today.

These are offline contract tests: no AI, dataset APIs, or downloads are called.
"""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import src.services.data_acquisition_service as acquisition
from src.services.data_acquisition_service import DataAcquisitionService


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2026, 10, 17, 18, 0, 0, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class MetadataOnlyIssuers:
    def search_securities(self, query, **kwargs):
        if query in {"国仪", "国仪公司", "688828", "688828.SH"}:
            return [{"name": "国仪公司", "symbol": "688828.SH", "org_id": "offline-issuer"}]
        return []

    def fetch(self, **kwargs):
        pytest.fail("plan validation must not fetch datasets")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(acquisition, "datetime", FrozenDateTime)
    monkeypatch.setattr(acquisition, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")))
    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: pytest.fail("unexpected network"))
    monkeypatch.setattr(acquisition.EssayDailyReportService, "_daily_watchlist", lambda: [])
    monkeypatch.setattr(acquisition, "get_stock_entity_index_records", lambda: [[
        "688828.SH", "688828", "国仪公司", "guoyigongsi", "gygs", ["国仪"], "CN", "stock", True, 100,
    ]])
    return DataAcquisitionService(
        planner=SimpleNamespace(model="offline", plan=lambda _: pytest.fail("unexpected AI")),
        financial=SimpleNamespace(query=lambda **_: pytest.fail("unexpected dataset API")),
        monitor=object(), cninfo=MetadataOnlyIssuers(), output_root=tmp_path / "packages",
    )


def poisoned_plan(*, report=True):
    """Mirror the actual bad plan: both global and channel dates say today."""
    tasks = [{
        "id": "announcements", "source": "cninfo", "resource": "announcements",
        "params": {"company_name": "国仪公司", "start_date": "20261018", "end_date": "20261018", "trade_date": "20261018"},
    }]
    if report:
        tasks.append({
            "id": "reports", "source": "tushare", "resource": "research_report",
            "params": {"company_name": "国仪公司", "start_date": "20261018", "end_date": "20261018", "trade_date": "20261018"},
        })
    return {
        "scope": {
            "company_names": ["国仪公司"], "start_date": "2026-10-18", "end_date": "2026-10-18",
        },
        "tasks": tasks,
    }


def assert_channel_dates_match(plan, start, end):
    assert plan["scope"]["start_date"] == start
    assert plan["scope"]["end_date"] == end
    assert plan["preflight"]["can_execute"] is True
    for task in plan["tasks"]:
        assert str(task["params"]["start_date"]).replace("-", "") == start.replace("-", "")
        assert str(task["params"]["end_date"]).replace("-", "") == end.replace("-", "")
        assert "trade_date" not in task["params"]
        assert task["resolved_scope"]["start_date"] == start
        assert task["resolved_scope"]["end_date"] == end


@pytest.mark.parametrize("request_text", [
    "打包国仪公司所有公告、研报",
    "下载国仪公司全部公告与研究报告",
    "获取国仪公司的全部历史公告和研报",
])
def test_all_history_request_ignores_invented_today_window(service, request_text):
    normalized = service.validate_plan(request_text, poisoned_plan())
    assert_channel_dates_match(normalized, "1990-01-01", "2026-10-18")
    assert normalized["scope"]["time_range_mode"] == "all_history"


def test_explicit_relative_period_overrides_all_wording_and_model_dates(service):
    normalized = service.validate_plan("打包国仪公司最近两年所有公告和研报", poisoned_plan())
    assert_channel_dates_match(normalized, "2024-10-18", "2026-10-18")
    assert normalized["scope"].get("time_range_mode") != "all_history"


@pytest.mark.parametrize("request_text", [
    "下载国仪公司2024-01-02至2025-12-31的所有公告和研报",
    "下载国仪公司20240102到20251231的所有公告和研报",
    "下载国仪公司2024年1月2日至2025年12月31日全部公告和研报",
])
def test_explicit_dates_are_authoritative_for_both_channels(service, request_text):
    normalized = service.validate_plan(request_text, poisoned_plan())
    assert_channel_dates_match(normalized, "2024-01-02", "2025-12-31")
    assert normalized["scope"].get("time_range_mode") != "all_history"


def test_unspecified_period_has_visible_two_year_default_not_model_today(service):
    normalized = service.validate_plan("下载国仪公司公告和研报", poisoned_plan())
    assert_channel_dates_match(normalized, "2024-10-18", "2026-10-18")
    assert normalized["scope"].get("time_range_mode") != "all_history"


def test_announcements_only_does_not_depend_on_report_normalizer(service):
    normalized = service.validate_plan("打包国仪公司所有公告", poisoned_plan(report=False))
    assert_channel_dates_match(normalized, "1990-01-01", "2026-10-18")
    assert normalized["scope"]["time_range_mode"] == "all_history"


def test_revalidation_preserves_user_range_and_does_not_reintroduce_model_dates(service):
    request_text = "打包国仪公司最近两年全部公告、研报"
    first = service.validate_plan(request_text, poisoned_plan())
    again = service.validate_plan(request_text, deepcopy(first))
    assert_channel_dates_match(again, "2024-10-18", "2026-10-18")
    assert again["scope"] == first["scope"]
    assert [task["params"] for task in again["tasks"]] == [task["params"] for task in first["tasks"]]


@pytest.mark.parametrize(("request_text", "day"), [
    ("国仪公司今天所有公告和研报", "2026-10-18"),
    ("国仪公司今日全部公告和研报", "2026-10-18"),
    ("国仪公司昨天全部公告和研报", "2026-10-17"),
    ("国仪公司昨日所有公告和研报", "2026-10-17"),
    ("国仪公司2026-08-18全部公告和研报", "2026-08-18"),
    ("国仪公司20260818全部公告和研报", "2026-08-18"),
])
def test_explicit_single_day_is_not_broadened_to_all_history(service, request_text, day):
    normalized = service.validate_plan(request_text, poisoned_plan())
    assert_channel_dates_match(normalized, day, day)
    assert normalized["scope"].get("time_range_mode") != "all_history"


@pytest.mark.parametrize("request_text", [
    "国仪公司2024-01-02以来全部公告和研报",
    "国仪公司2024-01-02至今所有公告和研报",
    "国仪公司2024-01-02到今天全部公告和研报",
])
def test_single_start_with_present_end_is_not_collapsed_to_today(service, request_text):
    normalized = service.validate_plan(request_text, poisoned_plan())
    assert_channel_dates_match(normalized, "2024-01-02", "2026-10-18")


def test_company_search_words_do_not_trigger_full_market_topic_scan(service):
    plan = poisoned_plan()
    plan["scope"]["keywords"] = ["国仪"]
    plan["tasks"][1]["params"].update(query="国仪", keyword="国仪公司")
    normalized = service.validate_plan("打包国仪公司所有公告、研报", plan)
    report = normalized["tasks"][1]
    assert normalized["scope"]["keywords"] == []
    assert report["params"]["topics"] == []
    assert "keyword" not in report["params"] and "query" not in report["params"]
    assert report["params"]["ts_codes"] == ["688828.SH"]

