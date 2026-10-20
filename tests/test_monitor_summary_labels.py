"""Structured monitor summaries use the supplied field's actual meaning.

Offline event construction only: no source synchronization, database writes,
historical rewrites, external data provider, or AI execution.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.investment_monitor_service import (
    BUILTIN_MONITORING_SOURCES,
    InvestmentMonitorService,
)
from src.storage import DatabaseManager


class SummaryGateway:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def query(self, api_name, params=None, fields=None):
        self.calls.append(api_name)
        return {"rows": deepcopy(self.rows.get(api_name, []))}


@pytest.fixture
def summary_service(monkeypatch):
    monkeypatch.setattr(
        DatabaseManager, "get_instance",
        lambda: pytest.fail("Summary construction must not initialize the application database"),
    )
    unused = SimpleNamespace()
    gateway = SummaryGateway()
    service = InvestmentMonitorService(
        repository=unused, tushare=gateway, stock_service=unused,
        cninfo=unused, exchanges=unused, guba=unused,
        announcement_artifacts=unused, initialize_sources=False,
    )
    service._watchlist_override = ["603306.SH"]
    service._name_cache = {"603306.SH": "华懋科技"}
    return service, gateway


def source(key):
    return next(item for item in BUILTIN_MONITORING_SOURCES if item["source_key"] == key)


@pytest.mark.parametrize("net_amount", [88.5, -42.75])
def test_capital_snapshot_does_not_relabel_total_net_moneyflow_as_main_force(summary_service, net_amount):
    service, gateway = summary_service
    moneyflow = {
        "trade_date": "20260910", "net_mf_amount": net_amount,
        "buy_lg_amount": 200, "sell_lg_amount": 170,
        "buy_elg_amount": 120, "sell_elg_amount": 115,
    }
    gateway.rows = {
        "moneyflow": [moneyflow],
        "cyq_perf": [{"trade_date": "20260910", "weight_avg": 75.1, "winner_rate": 0.4}],
    }

    result, = service._tushare_capital_events(source("tushare.capital"))

    assert f"资金净流入 {net_amount}" in result["summary"]
    assert "主力净流入" not in result["summary"]
    assert result["metrics"]["moneyflow"] == moneyflow
    assert result["raw_payload"]["moneyflow"] == moneyflow
    evidence = result["metrics"]["_evidence"]
    assert evidence["content_nature"] == "derived_summary"
    assert "moneyflow" in evidence["origin_apis"]
    assert set(gateway.calls) == {"margin", "cyq_perf", "margin_detail", "moneyflow", "hk_hold", "cyq_chips"}


@pytest.mark.parametrize("winner", [None, 0, 0.3, 0.7, 3.52, 7.62, 70, 100])
def test_capital_snapshot_profit_distribution_never_creates_a_directional_opinion(summary_service, winner):
    service, gateway = summary_service
    perf = {"trade_date": "20260910", "weight_avg": 75.1, "winner_rate": winner}
    gateway.rows = {"cyq_perf": [perf]}

    result, = service._tushare_capital_events(source("tushare.capital"))

    assert result["event_type"] == "capital_chip_snapshot"
    assert result["sentiment"] == "neutral"
    assert result["metrics"]["chip_performance"] == perf
    assert result["raw_payload"]["chip_performance"] == perf


def test_capital_snapshot_summary_preserves_actual_zero_values(summary_service):
    service, gateway = summary_service
    gateway.rows = {
        "cyq_perf": [{"trade_date": "20260910", "weight_avg": 0, "winner_rate": 0}],
        "margin_detail": [{"trade_date": "20260910", "rzye": 0, "rzmre": 0}],
        "margin": [{"trade_date": "20260910", "exchange_id": "SSE", "rzrqye": 0}],
        "moneyflow": [{"trade_date": "20260910", "net_mf_amount": 0}],
        "hk_hold": [{"trade_date": "20260910", "ratio": 0}],
    }

    result, = service._tushare_capital_events(source("tushare.capital"))

    for text in ("筹码平均成本 0", "获利比例 0", "融资余额 0", "融资买入 0",
                 "全市场两融余额 0", "资金净流入 0", "最近披露北向持股比例 0"):
        assert text in result["summary"]
    assert result["sentiment"] == "neutral"


def test_capital_snapshot_summary_does_not_invent_zero_for_missing_source_values(summary_service):
    service, _gateway = summary_service

    result, = service._tushare_capital_events(source("tushare.capital"))

    for label in ("筹码平均成本", "获利比例", "融资余额", "融资买入",
                  "全市场两融余额", "资金净流入", "最近披露北向持股比例"):
        assert f"{label} -" in result["summary"]
    assert result["sentiment"] == "neutral"


@pytest.mark.parametrize("net_income,roe", [(123.4, 8.6), (-25.0, -1.2), (0, 0)])
def test_express_summary_keeps_net_income_and_diluted_roe_distinct_from_attribution_and_growth(net_income, roe):
    row = {
        "revenue": 900, "n_income": net_income, "diluted_roe": roe,
        # Different fields must not silently replace the specified raw metrics.
        "n_income_attr_p": 77, "yoy_net_profit": 55,
    }
    assert InvestmentMonitorService._company_summary("express", row) == (
        f"营业收入 900，净利润 {net_income}，摊薄ROE {roe}。"
    )


def test_company_express_event_carries_the_fixed_summary_without_mutating_raw_fields(summary_service):
    service, gateway = summary_service
    row = {
        "ts_code": "603306.SH", "ann_date": "20260910", "end_date": "20260630",
        "revenue": 900, "n_income": 123.4, "diluted_roe": 8.6,
        "n_income_attr_p": 77, "yoy_net_profit": 55,
    }
    gateway.rows = {"express": [row]}

    result, = service._tushare_company_events(source("tushare.company"))

    assert result["event_type"] == "earnings_express"
    assert result["summary"] == "营业收入 900，净利润 123.4，摊薄ROE 8.6。"
    assert result["raw_payload"] == row
    assert {key: value for key, value in result["metrics"].items() if key != "_evidence"} == row
    evidence = result["metrics"]["_evidence"]
    assert evidence["origin_apis"] == ["express"]
    # The existing contract marks express as source_record despite its generated
    # summary. Readers must also recognize its structured event type, not assume
    # that every source_record contains an upstream article or a full transcript.
    assert evidence["content_nature"] == "source_record"
    assert set(gateway.calls) == {"forecast", "express", "repurchase", "stk_holdertrade", "share_float"}
