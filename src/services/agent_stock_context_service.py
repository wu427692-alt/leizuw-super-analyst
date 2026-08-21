# -*- coding: utf-8 -*-
"""Local-first factual context hydration for ask-stock chat.

The agent tool loop remains available for follow-up exploration, but a stock
question should never reach the model with only a ticker.  This service loads
the shared monitoring workspace first and, only when core blocks are absent,
queries bounded upstream Tushare resources and persists the result for later
turns.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from src.services.financial_data_service import TushareGatewayService
from src.storage import get_db

logger = logging.getLogger(__name__)

_CACHE_QUERY_ID = "agent_stock_context_v1"
_CACHE_TTL_SECONDS = 15 * 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_a_share(code: str) -> str:
    from data_provider.base import normalize_stock_code

    normalized = normalize_stock_code(str(code or "").strip()).upper().replace(".SS", ".SH")
    if "." in normalized:
        return normalized
    if normalized.isdigit() and len(normalized) == 6:
        suffix = "BJ" if normalized.startswith(("4", "8", "92")) else "SH" if normalized.startswith(("6", "9")) else "SZ"
        return f"{normalized}.{suffix}"
    return normalized


def _is_a_share(code: str) -> bool:
    canonical = _canonical_a_share(code).upper()
    return canonical.endswith((".SH", ".SZ", ".BJ")) and canonical.split(".", 1)[0].isdigit()


def _cache_is_fresh(payload: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(payload, dict):
        return False
    raw = str((payload.get("_meta") or {}).get("fetched_at") or "").strip()
    if not raw:
        return False
    try:
        fetched_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return (_now_utc() - fetched_at.astimezone(timezone.utc)).total_seconds() <= _CACHE_TTL_SECONDS
    except ValueError:
        return False


def _workspace_needs_upstream(stock: Dict[str, Any]) -> bool:
    market = stock.get("market") or {}
    valuation = stock.get("valuation") or {}
    fundamentals = stock.get("fundamentals") or {}
    evidence = stock.get("evidence") or {}
    return bool(
        not market.get("price")
        or (valuation.get("pe_ttm") is None and valuation.get("pb") is None)
        or not any(value is not None for value in fundamentals.values())
        or int(evidence.get("source_count") or 0) < 2
    )


def _date_ranges() -> Dict[str, str]:
    now = datetime.now()
    return {
        "end": now.strftime("%Y%m%d"),
        "start_45": (now - timedelta(days=45)).strftime("%Y%m%d"),
        "start_180": (now - timedelta(days=180)).strftime("%Y%m%d"),
        "start_550": (now - timedelta(days=550)).strftime("%Y%m%d"),
    }


def _query_upstream_pack(code: str) -> Dict[str, Any]:
    canonical = _canonical_a_share(code)
    if not _is_a_share(canonical):
        return {"_meta": {"status": "not_supported", "stock_code": canonical}}

    dates = _date_ranges()
    jobs = {
        "daily": {"ts_code": canonical, "start_date": dates["start_45"], "end_date": dates["end"]},
        "daily_basic": {"ts_code": canonical, "start_date": dates["start_45"], "end_date": dates["end"]},
        "fina_indicator": {"ts_code": canonical, "start_date": dates["start_550"], "end_date": dates["end"]},
        "income": {"ts_code": canonical, "start_date": dates["start_550"], "end_date": dates["end"]},
        "cashflow": {"ts_code": canonical, "start_date": dates["start_550"], "end_date": dates["end"]},
        "forecast": {"ts_code": canonical, "start_date": dates["start_550"], "end_date": dates["end"]},
        "report_rc": {"ts_code": canonical, "start_date": dates["start_550"], "end_date": dates["end"]},
        "stk_surv": {"ts_code": canonical, "start_date": dates["start_550"], "end_date": dates["end"]},
        "moneyflow": {"ts_code": canonical, "start_date": dates["start_45"], "end_date": dates["end"]},
        "cyq_perf": {"ts_code": canonical, "start_date": dates["start_45"], "end_date": dates["end"]},
        "margin_detail": {"ts_code": canonical, "start_date": dates["start_45"], "end_date": dates["end"]},
        "hk_hold": {"ts_code": canonical, "start_date": dates["start_180"], "end_date": dates["end"]},
    }
    rows_by_resource: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    def run(api_name: str, params: Dict[str, str]) -> tuple[str, Dict[str, Any]]:
        return api_name, TushareGatewayService().query(api_name, params=params)

    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="agent-tushare") as pool:
        futures = {pool.submit(run, name, params): name for name, params in jobs.items()}
        for future in as_completed(futures):
            api_name = futures[future]
            try:
                _name, response = future.result()
                rows = list(response.get("rows") or [])
                # Keep a bounded evidence set in the prompt/cache.  The source
                # API can still be called by the data tool for a deeper query.
                rows_by_resource[api_name] = rows[:20]
            except Exception as exc:  # one unavailable endpoint must not erase the rest
                errors[api_name] = type(exc).__name__

    payload = {
        "_meta": {
            "status": "ok" if rows_by_resource else "failed",
            "stock_code": canonical,
            "fetched_at": _now_utc().isoformat().replace("+00:00", "Z"),
            "source": "tushare_direct_fallback",
            "successful_resources": sorted(rows_by_resource),
            "failed_resources": errors,
        },
        "resources": rows_by_resource,
    }
    try:
        get_db().save_fundamental_snapshot(
            _CACHE_QUERY_ID,
            canonical,
            payload,
            source_chain=[{"provider": "tushare", "result": payload["_meta"]["status"]}],
            coverage={name: bool(rows) for name, rows in rows_by_resource.items()},
        )
    except Exception as exc:
        logger.info("Agent upstream context cache write failed for %s: %s", canonical, type(exc).__name__)
    return payload


def _compact_upstream_pack(payload: Dict[str, Any]) -> Dict[str, Any]:
    resources = payload.get("resources") or {}
    compact: Dict[str, Any] = {}
    for name, rows in resources.items():
        safe_rows = list(rows or [])
        compact[name] = {
            "count_in_context": len(safe_rows),
            "latest": safe_rows[0] if safe_rows else None,
            "recent": safe_rows[:5] if name in {"report_rc", "stk_surv", "forecast"} else safe_rows[:2],
        }
    return {"meta": payload.get("_meta") or {}, "resources": compact}


def hydrate_agent_stock_context(context: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Resolve the stock, load shared facts, and fill only material gaps."""
    from src.agent.stock_scope import resolve_stock_scope
    from src.data.stock_index_loader import get_index_stock_name
    from src.services.investment_monitor_service import InvestmentMonitorService

    resolution = resolve_stock_scope(message, context)
    enriched = dict(resolution.effective_context)
    stock_code = str(enriched.get("stock_code") or "").strip()
    if not stock_code:
        return enriched

    canonical = _canonical_a_share(stock_code)
    # Keep the public agent-context contract stable.  Shared workspaces and
    # upstream APIs use exchange-qualified codes internally, while the chat
    # executor historically receives the user-resolved code (for example
    # ``600519``).  Mixing those two forms breaks session/context assertions
    # and can make a follow-up turn look like a stock switch.
    resolved_stock_code = stock_code
    enriched["stock_code"] = resolved_stock_code
    if not str(enriched.get("stock_name") or "").strip():
        enriched["stock_name"] = get_index_stock_name(canonical) or ""

    shared = InvestmentMonitorService().stock_workspace(canonical, days=365)
    stock = dict(shared.get("stock") or {})
    agent_context = dict(shared.get("agent_context") or {})
    previous_summary = str(enriched.get("analysis_context_pack_summary") or "").strip()
    shared_summary = str(agent_context.get("analysis_context_pack_summary") or "").strip()
    enriched.update(agent_context)
    enriched["stock_code"] = resolved_stock_code
    if previous_summary and shared_summary:
        enriched["analysis_context_pack_summary"] = f"{previous_summary}\n{shared_summary}"
    enriched["unified_context_version"] = shared.get("version")

    cached = get_db().get_latest_fundamental_snapshot(_CACHE_QUERY_ID, canonical)
    upstream = cached if _cache_is_fresh(cached) else None
    if _workspace_needs_upstream(stock) and upstream is None:
        upstream = _query_upstream_pack(canonical)
    if isinstance(upstream, dict) and (upstream.get("resources") or {}):
        compact = _compact_upstream_pack(upstream)
        fallback_summary = (
            "\n[本地缺口的上游直连补充]\n"
            "以下为 Tushare 接口按需获取并已回写本地的数据；日期字段必须原样引用，"
            "不得把研报预测写成公司已实现业绩。\n"
            + json.dumps(compact, ensure_ascii=False, default=str)
        )
        current = str(enriched.get("analysis_context_pack_summary") or "")
        enriched["analysis_context_pack_summary"] = current + fallback_summary
        enriched["upstream_fallback_context"] = compact

    return enriched
