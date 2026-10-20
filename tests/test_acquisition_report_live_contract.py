"""The cross-source report export must query the provider, not the search cache.

These tests deliberately use the actual FinancialDataService/TushareGatewayService
chain where practical. Only the HTTP client is replaced; no local report library
is populated and no files or model calls are needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService
from src.services.financial_data_service import (
    FinancialDataService,
    FinancialDataUpstreamError,
    TushareGatewayService,
)


def _report(index=0, **overrides):
    row = {
        "trade_date": "20260824", "title": f"国仪公司首次覆盖研究-{index}",
        "abstr": "高端科学仪器及量子科技业务研究。", "report_type": "个股研报",
        "author": "研究员", "name": "国仪公司", "ts_code": "688828.SH",
        "inst_csname": "测试证券", "ind_name": "专用设备",
        "url": f"https://reports.example/report-{index}.pdf?version=1",
    }
    row.update(overrides)
    return row


def _scope():
    return {
        "symbols": ["688828.SH"], "company_names": ["国仪公司"], "keywords": [],
        "start_date": "1990-01-01", "end_date": "2026-10-18",
        "time_range_mode": "all_history", "market_wide": False,
    }


def _task(**params):
    return {
        "source": "tushare", "resource": "research_report", "fields": [],
        "params": {"ts_codes": ["688828.SH"], "start_date": "19900101",
                   "end_date": "20261018", **params},
    }


def _service(tmp_path, financial):
    return DataAcquisitionService(
        planner=MagicMock(), financial=financial, monitor=MagicMock(),
        cninfo=MagicMock(), output_root=tmp_path,
    )


def test_financial_report_queries_are_live_and_keep_pdf_urls():
    client = MagicMock()
    client.query.side_effect = [pd.DataFrame([_report(1)]), pd.DataFrame([_report(2)])]
    gateway = TushareGatewayService(client=client)
    financial = FinancialDataService(
        tushare=gateway, research_notes=MagicMock(), monitor=MagicMock(),
    )
    params = {"ts_code": "688828.SH", "start_date": "20170101", "end_date": "20261018",
              "limit": 1000, "offset": 0}

    first = financial.query(source="tushare", resource="research_report", params=params,
                            fields=["title", "ts_code", "url"])
    second = financial.query(source="tushare", resource="research_report", params=params,
                             fields=["title", "ts_code", "url"])

    assert client.query.call_count == 2
    assert first["rows"][0]["url"] == _report(1)["url"]
    assert second["rows"][0]["title"] == _report(2)["title"]
    assert first["rows"] != second["rows"]
    financial.research_notes.list_notes.assert_not_called()
    financial._monitor.list_events.assert_not_called()


def test_provider_permission_failure_is_not_an_empty_success(tmp_path):
    client = MagicMock()
    client.query.side_effect = RuntimeError("provider authorization unavailable")
    financial = FinancialDataService(
        tushare=TushareGatewayService(client=client), research_notes=MagicMock(), monitor=MagicMock(),
    )

    with pytest.raises(FinancialDataUpstreamError):
        _service(tmp_path, financial)._execute_research_reports(_task(), _scope())


def test_exact_company_all_history_uses_one_wide_range_and_drops_stale_day(tmp_path):
    calls = []

    class LiveFinancial:
        def query(self, **kwargs):
            calls.append(kwargs)
            return {"rows": [_report()]}

    rows = _service(tmp_path, LiveFinancial())._execute_research_reports(
        _task(trade_date="20261018", time_range_mode="all_history"), _scope(),
    )

    assert len(calls) == 1, "An exact company must not scan 36 years of empty monthly windows"
    params = calls[0]["params"]
    assert params["ts_code"] == "688828.SH"
    assert params["start_date"] <= "20170101"
    assert params["end_date"] == "20261018"
    assert "trade_date" not in params
    assert "time_range_mode" not in params
    assert rows[0]["url"] == _report()["url"]
    assert "abstr" in calls[0]["fields"]


@pytest.mark.parametrize("invalid", [{"error": "permission expired", "rows": []}, {}, {"rows": None},
                                      {"rows": "not-a-page"}, {"rows": ["not-a-row"]}])
def test_invalid_provider_page_is_not_silently_treated_as_no_reports(tmp_path, invalid):
    class InvalidFinancial:
        def query(self, **kwargs):
            return invalid

    with pytest.raises((DataAcquisitionError, FinancialDataUpstreamError)):
        _service(tmp_path, InvalidFinancial())._execute_research_reports(_task(), _scope())


def test_report_pagination_keeps_code_period_and_broker_on_every_page(tmp_path):
    calls = []

    class PagedFinancial:
        def query(self, **kwargs):
            params = kwargs["params"]
            calls.append(params.copy())
            page = [_report(index) for index in range(1000)] if params["offset"] == 0 else [_report(1000)]
            return {"rows": page}

    rows = _service(tmp_path, PagedFinancial())._execute_research_reports(
        _task(inst_csname="测试证券", max_results=1500), _scope(),
    )

    assert len(rows) == 1001
    assert [call["offset"] for call in calls] == [0, 1000]
    for call in calls:
        assert call["ts_code"] == "688828.SH"
        assert call["end_date"] == "20261018"
        assert call["inst_csname"] == "测试证券"
        assert call["limit"] == 1000


def test_exact_company_result_does_not_export_unrelated_provider_rows(tmp_path):
    class MixedFinancial:
        def query(self, **kwargs):
            return {"rows": [_report(), _report(9, name="其他公司", ts_code="600519.SH",
                                               title="其他公司的首次覆盖", abstr="不相关业务")]}

    rows = _service(tmp_path, MixedFinancial())._execute_research_reports(_task(), _scope())

    assert [row["ts_code"] for row in rows] == ["688828.SH"]


def test_provider_rows_outside_requested_report_dates_are_not_exported(tmp_path):
    class MixedDatesFinancial:
        def query(self, **kwargs):
            return {"rows": [_report(0, trade_date="20260824"),
                             _report(1, trade_date="20260730"),
                             _report(2, trade_date="20260901")]}

    scope = {**_scope(), "start_date": "2026-08-01", "end_date": "2026-08-31"}
    task = _task(start_date="20260801", end_date="20260831")

    rows = _service(tmp_path, MixedDatesFinancial())._execute_research_reports(task, scope)

    assert [row["trade_date"] for row in rows] == ["20260824"]


def test_company_with_topic_only_adds_related_industry_reports(tmp_path):
    class RelatedFinancial:
        def query(self, **kwargs):
            if kwargs["params"].get("ts_code"):
                return {"rows": [_report(0, title="国仪公司：量子仪器首次覆盖")]}
            return {"rows": [
                _report(1, title="量子仪器行业研究", ts_code=None, name=None,
                        abstr="国仪公司高端量子仪器业务进展。", report_type="行业研报"),
                _report(2, title="量子科技行业研究", ts_code=None, name=None,
                        abstr="其他公司的量子业务进展。", report_type="行业研报"),
                _report(3, title="其他公司：量子业务首次覆盖", ts_code="600519.SH", name="其他公司",
                        abstr="其他公司的量子业务进展。"),
            ]}

    scope = {**_scope(), "start_date": "2026-08-01", "end_date": "2026-08-31", "keywords": ["量子"]}
    task = _task(start_date="20260801", end_date="20260831", topics=["量子"])

    rows = _service(tmp_path, RelatedFinancial())._execute_research_reports(task, scope)

    assert {row["url"] for row in rows} == {_report(0)["url"], _report(1)["url"]}


@pytest.mark.parametrize("filters,expected", [
    ({"title_query": "深度研究"}, [0]),
    ({"content_query": "行业"}, [1]),
    ({"broker": "开源证券"}, [0]),
    ({"title_query": "深度研究", "content_query": "科学仪器", "broker": "开源证券"}, [0]),
])
def test_explicit_report_filters_are_literal_and_applied_after_live_recall(tmp_path, filters, expected):
    calls = []

    class FilterFinancial:
        def query(self, **kwargs):
            calls.append(kwargs["params"].copy())
            return {"rows": [
                _report(0, title="国仪公司深度研究", abstr="高端科学仪器业务", inst_csname="开源证券"),
                _report(1, title="国仪公司业绩点评", abstr="行业业务动态", inst_csname="华金证券"),
            ]}

    rows = _service(tmp_path, FilterFinancial())._execute_research_reports(_task(**filters), _scope())

    assert {row["url"] for row in rows} == {_report(index)["url"] for index in expected}
    assert all(not ({"title_query", "content_query", "broker"} & call.keys()) for call in calls)


def test_repeated_report_page_fails_instead_of_claiming_complete_history(tmp_path):
    calls = []
    page = [_report(index) for index in range(1000)]

    class RepeatedPageFinancial:
        def query(self, **kwargs):
            calls.append(kwargs["params"]["offset"])
            return {"rows": page}

    with pytest.raises(DataAcquisitionError, match="分页重复"):
        _service(tmp_path, RepeatedPageFinancial())._execute_research_reports(_task(), _scope())

    assert calls == [0, 1000]
