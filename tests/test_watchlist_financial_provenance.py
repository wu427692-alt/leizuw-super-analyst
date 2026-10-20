"""Whole-table provenance through the real repository and both stock views."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from tests.test_investment_monitor import monitor  # noqa: F401 — shared isolated DB fixture


@pytest.fixture
def financial_workspace(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "_workspace_latest_quote", lambda code: None)
    monkeypatch.setattr(
        "src.services.investment_monitor_service.EssayConsensusService",
        lambda **kwargs: SimpleNamespace(snapshot=lambda *args, **kwargs: {}),
    )
    monkeypatch.setattr(monitor.tushare, "query", lambda *args, **kwargs: pytest.fail("no upstream read"))
    source = monitor.repo.get_source("tushare.fundamentals")

    def save(metrics):
        monitor.repo.upsert_events([monitor._base_event(
            source, external_id="same-provider-key", event_type="fundamental_snapshot",
            perspective="company", title="财务来源验收", summary="原始五表字段",
            symbols=["600519.SH"], sentiment="neutral", importance=50,
            event_at=datetime(2026, 8, 30), tags=[], metrics=metrics, raw=metrics,
        )])
        return next(row["id"] for row in monitor.list_events(
            days=3650, event_type="fundamental_snapshot",
        )["items"] if {key: value for key, value in row["metrics"].items() if key != "_evidence"} == metrics)

    def views():
        return [monitor.stock_workspace("600519.SH", days=3650, refresh=True, view=view)
                for view in ("overview", "full")]

    return monitor, save, views


def test_each_financial_table_keeps_its_real_source_and_agent_context(financial_workspace):
    service, save, views = financial_workspace
    income = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1",
              "revenue": 200, "n_income_attr_p": 0}
    cash = {"end_date": "20260331", "ann_date": "20260420", "report_type": "1", "n_cashflow_act": -10}
    income_id = save({"income": income, "cashflow": cash})
    indicator = {"end_date": "20260930", "ann_date": "20261025", "roe": 0, "q_sales_yoy": 12}
    indicator_id = save({"indicator": indicator, "income": {"end_date": "20260331", "revenue": 100}})
    first, full = views()
    financial = full["stock"]["fundamentals"]
    assert first["stock"]["fundamentals"] == financial
    assert financial["revenue"] == 200 and financial["net_profit"] == 0
    assert financial["operating_cashflow"] == -10 and financial["roe"] == 0
    assert financial["source_event_id"] is None
    for key, expected_id, period in (("indicator", indicator_id, "2026-09-30"),
                                     ("income", income_id, "2026-06-30"),
                                     ("cashflow", income_id, "2026-03-31")):
        origin = financial["sources"][key]
        assert origin["event_id"] == expected_id and origin["period"] == period
        assert origin["selection_status"] == "selected"
        assert service.event_detail(expected_id)["metrics"][key]["end_date"].replace("-", "") == period.replace("-", "")
    assert full["agent_context"]["fundamental_context"]["fundamentals"] == financial
    assert full["agent_context"]["analysis_context_pack_summary"].count('"sources"') >= 1
    signal = next(row for row in full["stock"]["signals"] if "收入同比" in row["title"])
    assert signal["event_id"] == indicator_id


def test_conflicting_versions_do_not_become_a_definite_amount_or_growth_signal(financial_workspace):
    service, save, views = financial_workspace
    row = {"end_date": "20260630", "ann_date": "20260820", "q_sales_yoy": 5, "roe": 10}
    first_id = save({"indicator": row, "income": {"end_date": "20260630", "revenue": 100}})
    second_id = save({"indicator": {**row, "q_sales_yoy": -5, "roe": 2}})
    for payload in views():
        stock = payload["stock"]
        financial = stock["fundamentals"]
        assert financial["roe"] is financial["revenue_yoy"] is None
        assert financial["revenue"] == 100
        assert financial["source_event_id"] is None
        assert financial["indicator_period"] == "2026-06-30"
        source = financial["sources"]["indicator"]
        assert source["selection_status"] == "conflict" and source["event_id"] is None
        assert set(source["candidate_event_ids"]) == {first_id, second_id}
        assert source["candidate_count"] == 2
        assert all("收入同比" not in row["title"] for row in stock["signals"])
    assert service.event_detail(first_id)["metrics"]["indicator"] == row
    assert service.event_detail(second_id)["metrics"]["indicator"]["q_sales_yoy"] == -5


def test_newer_null_is_not_filled_with_an_older_statement(financial_workspace):
    service, save, views = financial_workspace
    old = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "revenue": 100}
    old_id = save({"income": old})
    new = {**old, "f_ann_date": "20260825", "update_flag": "1", "revenue": None, "n_income_attr_p": 0}
    new_id = save({"income": new})
    for payload in views():
        financial = payload["stock"]["fundamentals"]
        assert financial["revenue"] is None and financial["net_profit"] == 0
        assert financial["sources"]["income"]["event_id"] == new_id
        assert financial["sources"]["income"]["announced_at"] == "2026-08-25"
        assert financial["source_event_id"] == new_id
    assert service.event_detail(old_id)["metrics"]["income"] == old
    assert service.event_detail(new_id)["metrics"]["income"] == new


def test_empty_table_does_not_remove_a_previously_available_whole_source(financial_workspace):
    _, save, views = financial_workspace
    prior = {"end_date": "20260630", "revenue": 100}
    prior_id = save({"income": prior})
    new_id = save({"income": {}, "indicator": {"end_date": "20260630", "roe": 0}})
    assert prior_id != new_id
    for payload in views():
        financial = payload["stock"]["fundamentals"]
        assert financial["revenue"] == 100 and financial["roe"] == 0
        assert financial["sources"]["income"]["event_id"] == prior_id
        assert financial["sources"]["indicator"]["event_id"] == new_id
        assert "cashflow" not in financial["sources"]
        assert financial["source_event_id"] is None


def test_collector_retains_equal_revision_candidates_without_cartesian_product(monitor, monkeypatch):
    income = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "revenue": 100}
    alternate = {**income, "revenue": 200}
    cash = {"end_date": "20260630", "ann_date": "20260820", "report_type": "1", "n_cashflow_act": 5}
    data = {"income": [income, alternate, income], "cashflow": [cash, {**cash, "n_cashflow_act": 10}]}
    calls = []

    def query(api_name, params=None, fields=None):
        calls.append(api_name)
        return {"rows": data.get(api_name, [])}

    monkeypatch.setattr(monitor.tushare, "query", query)
    monkeypatch.setattr(monitor, "_stock_name", lambda symbol: "测试公司")
    source = monitor.repo.get_source("tushare.fundamentals")
    rows = monitor._tushare_fundamental_events(source)
    assert len(rows) == 3  # base + one alternative per table, not 2 × 2
    assert set(calls) == {"fina_indicator", "income", "balancesheet", "cashflow", "forecast"}
    assert len(calls) == 5
    assert {row["metrics"]["income"]["revenue"] for row in rows} == {100, 200}
    assert {row["metrics"]["cashflow"]["n_cashflow_act"] for row in rows} == {5, 10}
    assert all(row["raw_payload"] == {key: value for key, value in row["metrics"].items() if key != "_evidence"}
               for row in rows)
    monitor.repo.upsert_events(rows)
    bundle = monitor.repo.symbol_workspace_bundle(symbol="600519.SH", days=3650, overview=True)
    assert bundle["financial_sections"]["income"]["selection_status"] == "conflict"
    assert bundle["financial_sections"]["cashflow"]["selection_status"] == "conflict"
    for values in data.values():
        values.reverse()
    repeated = monitor._tushare_fundamental_events(source)
    assert monitor.repo.upsert_events(repeated) == {"created": 0, "updated": 0, "received": 3}
