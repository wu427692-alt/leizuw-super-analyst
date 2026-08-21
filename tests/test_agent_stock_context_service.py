from __future__ import annotations

from unittest.mock import patch

from src.agent.stock_scope import resolve_stock_scope
from src.data.stock_index_loader import resolve_stock_mentions
from src.services.agent_stock_context_service import hydrate_agent_stock_context


def test_stock_name_mentions_resolve_mainland_listing_by_default():
    assert resolve_stock_mentions("胜宏科技现在怎么看", limit=5)[0]["stock_code"] == "300476.SZ"
    scope = resolve_stock_scope("分析华懋科技的财务和研报", {})
    assert scope.effective_context["stock_code"] == "603306"
    assert scope.stock_scope is not None
    assert scope.stock_scope.allowed_stock_codes == {"603306"}


def test_hydrator_fetches_and_injects_upstream_only_for_local_gaps():
    shared = {
        "version": "test",
        "stock": {
            "market": {}, "valuation": {}, "fundamentals": {},
            "evidence": {"source_count": 0},
        },
        "agent_context": {
            "analysis_context_pack_summary": "\n[本地底稿]\n{}",
            "evidence_count": 0,
            "source_count": 0,
        },
    }
    upstream = {
        "_meta": {"status": "ok", "source": "tushare_direct_fallback"},
        "resources": {"daily_basic": [{"trade_date": "20260821", "pe_ttm": 10.2}]},
    }
    with (
        patch("src.services.investment_monitor_service.InvestmentMonitorService.stock_workspace", return_value=shared),
        patch("src.services.agent_stock_context_service.get_db") as db,
        patch("src.services.agent_stock_context_service._query_upstream_pack", return_value=upstream) as query,
    ):
        db.return_value.get_latest_fundamental_snapshot.return_value = None
        context = hydrate_agent_stock_context({}, "分析华懋科技")

    query.assert_called_once_with("603306.SH")
    assert context["stock_code"] == "603306"
    assert context["stock_name"] == "华懋科技"
    assert "本地缺口的上游直连补充" in context["analysis_context_pack_summary"]
    assert context["upstream_fallback_context"]["resources"]["daily_basic"]["latest"]["pe_ttm"] == 10.2
