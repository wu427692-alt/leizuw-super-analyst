"""Bounded ask-stock fallback regressions; no model or provider I/O."""

from datetime import datetime, timezone
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.request_identity import current_owner_id, reset_current_user_id, set_current_user_id
from src.services import agent_stock_context_service as service


@pytest.fixture
def local(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("fallback tests must not contact upstream")

    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    workspace = Mock(return_value={})
    monkeypatch.setattr(
        "src.services.investment_monitor_service.InvestmentMonitorService",
        lambda: SimpleNamespace(stock_workspace=workspace),
    )
    db = Mock()
    db.get_latest_fundamental_snapshot.return_value = None
    monkeypatch.setattr(service, "get_db", lambda: db)
    return workspace, db


def _upstream():
    return {
        "_meta": {"status": "ok", "source": "tushare_direct_fallback"},
        "resources": {"daily_basic": [{"trade_date": "20260901", "pe_ttm": 10.2}]},
    }


def test_workspace_failure_still_queries_existing_bounded_upstream_and_keeps_prior_facts(local, monkeypatch):
    workspace, _ = local
    workspace.side_effect = RuntimeError("private database diagnostic must not enter the model prompt")
    query = Mock(return_value=_upstream())
    monkeypatch.setattr(service, "_query_upstream_pack", query)

    result = service.hydrate_agent_stock_context(
        {"stock_code": "603306", "analysis_context_pack_summary": "Caller supplied evidence"},
        "分析603306",
    )

    workspace.assert_called_once_with("603306.SH", days=365)
    query.assert_called_once_with("603306.SH")
    assert result["stock_code"] == "603306"
    assert result["upstream_fallback_context"]["resources"]["daily_basic"]["latest"]["pe_ttm"] == 10.2
    summary = result["analysis_context_pack_summary"]
    assert "Caller supplied evidence" in summary
    assert '"local_workspace": "RuntimeError"' in summary
    assert "private database diagnostic" not in summary
    assert "已回写本地" not in summary


def test_cache_failure_does_not_block_upstream_or_drop_workspace_evidence(local, monkeypatch):
    workspace, db = local
    workspace.return_value = {"agent_context": {"analysis_context_pack_summary": "Workspace evidence"}}
    db.get_latest_fundamental_snapshot.side_effect = OSError("cache unavailable")
    query = Mock(return_value=_upstream())
    monkeypatch.setattr(service, "_query_upstream_pack", query)

    result = service.hydrate_agent_stock_context({}, "分析603306")

    query.assert_called_once_with("603306.SH")
    assert "Workspace evidence" in result["analysis_context_pack_summary"]
    assert '"local_cache": "OSError"' in result["analysis_context_pack_summary"]
    assert result["upstream_fallback_context"]["meta"]["status"] == "ok"


def test_late_upstream_failure_keeps_both_existing_evidence_sources(local, monkeypatch):
    workspace, _ = local
    workspace.return_value = {"agent_context": {"analysis_context_pack_summary": "Workspace evidence"}}
    monkeypatch.setattr(service, "_query_upstream_pack", Mock(side_effect=RuntimeError("pool unavailable")))

    result = service.hydrate_agent_stock_context(
        {"stock_code": "603306", "analysis_context_pack_summary": "Caller evidence"}, "分析603306",
    )

    summary = result["analysis_context_pack_summary"]
    assert "Caller evidence\nWorkspace evidence" in summary
    assert '"upstream_fallback": "RuntimeError"' in summary
    assert "upstream_fallback_context" not in result


def test_all_reads_fail_without_fabricating_facts_or_silent_success(local, monkeypatch):
    workspace, db = local
    workspace.side_effect = RuntimeError("workspace unavailable")
    db.get_latest_fundamental_snapshot.side_effect = OSError("cache unavailable")
    monkeypatch.setattr(service, "_query_upstream_pack", Mock(side_effect=TimeoutError("unavailable")))

    result = service.hydrate_agent_stock_context({}, "分析603306")

    summary = result["analysis_context_pack_summary"]
    for source in ("local_workspace", "local_cache", "upstream_fallback"):
        assert source in summary
    assert "部分读取失败" in summary
    assert "upstream_fallback_context" not in result
    assert "realtime_quote" not in result


def test_workspace_failure_uses_fresh_cache_without_extra_queries(local, monkeypatch):
    workspace, db = local
    workspace.side_effect = RuntimeError("workspace unavailable")
    cached = _upstream()
    cached["_meta"]["fetched_at"] = datetime.now(timezone.utc).isoformat()
    db.get_latest_fundamental_snapshot.return_value = cached
    query = Mock(side_effect=AssertionError("a fresh cache must not trigger extra queries"))
    monkeypatch.setattr(service, "_query_upstream_pack", query)

    result = service.hydrate_agent_stock_context({}, "分析603306")

    query.assert_not_called()
    assert result["upstream_fallback_context"]["resources"]["daily_basic"]["latest"]["pe_ttm"] == 10.2
    assert "local_workspace" in result["analysis_context_pack_summary"]


def test_complete_workspace_does_not_expand_queries_and_keeps_nonempty_caller_summary(local, monkeypatch):
    workspace, _ = local
    workspace.return_value = {
        "stock": {
            "market": {"price": 100}, "valuation": {"pe_ttm": 10},
            "fundamentals": {"roe": 5}, "evidence": {"source_count": 2},
        },
        "agent_context": {"analysis_context_pack_summary": ""},
    }
    query = Mock(side_effect=AssertionError("a complete workspace must not trigger extra queries"))
    monkeypatch.setattr(service, "_query_upstream_pack", query)

    result = service.hydrate_agent_stock_context(
        {"stock_code": "603306", "analysis_context_pack_summary": "Caller evidence"}, "分析603306",
    )

    query.assert_not_called()
    assert result["analysis_context_pack_summary"] == "Caller evidence"


def test_nested_real_provider_threads_keep_request_identity_and_existing_resource_bounds(local, monkeypatch):
    workspace, db = local
    workspace.side_effect = RuntimeError("workspace unavailable")
    seen = []
    caller_thread = threading.get_ident()

    class Gateway:
        def query(self, name, *, params):
            seen.append((name, current_owner_id(), threading.get_ident(), params))
            return {"rows": [{"trade_date": "20260901", "row": n} for n in range(25)]}

    monkeypatch.setattr(service, "TushareGatewayService", Gateway)
    for user_id in (11, 22):
        token = set_current_user_id(user_id)
        try:
            result = service.hydrate_agent_stock_context({}, "分析603306")
            assert current_owner_id() == f"user:{user_id}"
            resources = result["upstream_fallback_context"]["resources"]
            assert all(item["count_in_context"] == 20 for item in resources.values())
            assert all(len(item["recent"]) <= 5 for item in resources.values())
        finally:
            reset_current_user_id(token)

    expected = {"daily", "daily_basic", "fina_indicator", "income", "cashflow", "forecast",
                "report_rc", "stk_surv", "moneyflow", "cyq_perf", "margin_detail", "hk_hold"}
    assert len(seen) == 24
    for user_id in (11, 22):
        calls = [item for item in seen if item[1] == f"user:{user_id}"]
        assert {item[0] for item in calls} == expected
        assert all(item[2] != caller_thread for item in calls)
        assert all(item[3]["ts_code"] == "603306.SH" for item in calls)
        assert all(set(item[3]) == {"ts_code", "start_date", "end_date"} for item in calls)
    assert current_owner_id() is None
    assert db.save_fundamental_snapshot.call_count == 2


@pytest.mark.parametrize("mode,status", [("empty", "empty"), ("failed", "failed"), ("partial", "partial")])
def test_empty_and_failed_resources_are_not_presented_as_complete_success(local, monkeypatch, mode, status):
    class Gateway:
        def query(self, name, *, params):
            if mode == "failed" or (mode == "partial" and name != "daily"):
                raise TimeoutError("private provider diagnostic")
            return {"rows": [{"close": 100}] if mode == "partial" else []}

    monkeypatch.setattr(service, "TushareGatewayService", Gateway)

    result = service.hydrate_agent_stock_context({}, "分析603306")

    upstream = result["upstream_fallback_context"]
    assert upstream["meta"]["status"] == status
    assert "空资源及失败状态不代表已取得数据" in result["analysis_context_pack_summary"]
    assert "private provider diagnostic" not in result["analysis_context_pack_summary"]
    if mode == "partial":
        assert upstream["resources"]["daily"]["latest"] == {"close": 100}
        assert len(upstream["meta"]["failed_resources"]) == 11


def test_cache_write_failure_does_not_drop_successful_resource_evidence(local, monkeypatch):
    _, db = local
    db.save_fundamental_snapshot.side_effect = OSError("disk unavailable")

    class Gateway:
        def query(self, name, *, params):
            return {"rows": [{"close": 100}] if name == "daily" else []}

    monkeypatch.setattr(service, "TushareGatewayService", Gateway)

    result = service.hydrate_agent_stock_context({}, "分析603306")

    assert result["upstream_fallback_context"]["resources"]["daily"]["latest"] == {"close": 100}
    assert "已回写本地" not in result["analysis_context_pack_summary"]
