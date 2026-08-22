# -*- coding: utf-8 -*-
"""Event studies and reproducible strategies built from analyzed ZSXQ essays."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
import random
import re
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.orm import load_only

from src.data.stock_index_loader import get_index_stock_name, get_stock_name_index_map
from src.request_identity import current_owner_id
from src.services.financial_data_service import TushareGatewayService
from src.storage import (
    DatabaseManager,
    EssayAnalysisRecord,
    EssayQuantRuleRecord,
    EssayQuantRunRecord,
    ResearchNote,
    StockDaily,
    utc_naive_now,
)

_SH_TZ = timezone(timedelta(hours=8))
_HYPE_WORDS = ("强烈推荐", "重点推荐", "坚定看好", "目标价", "空间巨大", "翻倍", "买入", "增持", "重仓", "主升浪")
_BROKER_NAMES = (
    "中信建投", "中信", "中金", "华泰", "国泰海通", "海通", "广发", "招商", "申万宏源", "申万",
    "国金", "国盛", "天风", "兴业", "民生", "浙商", "东吴", "长江", "光大", "国信", "东方",
    "中泰", "方正", "银河", "华创", "德邦", "开源", "财通", "东兴", "西部", "东北", "国联民生",
    "国联", "国海", "国元", "华安", "中银", "平安", "太平洋", "信达", "首创", "华西", "中邮",
)
_SELLSIDE_SECTORS = (
    "电子", "机械", "策略", "通信", "传媒", "医药", "汽车", "计算机", "电新", "新能源", "电力设备",
    "化工", "有色", "钢铁", "煤炭", "银行", "非银", "地产", "家电", "军工", "社服", "食品饮料",
    "轻工", "农业", "互联网", "科技", "新材料", "前瞻", "宏观", "固收", "金融工程",
)

_STRATEGY_TYPES = {"essay_event", "multi_factor", "hybrid_intelligence", "institution_track", "portfolio"}
_OWNER_UNSET = object()

_METHOD_DEFINITIONS = [
    {
        "key": "event_study", "name": "事件研究",
        "purpose": "检验单条小作文观点出现后，股票在多个事件窗的绝对与超额收益。",
        "used_data": ["AI结构化小作文", "Tushare日线", "沪深300基准"],
        "engine": "同股同机构去重 → 次日开盘进入 → 5/10/20日事件窗 → 时间顺序样本外检验",
        "output": "事件后路径、胜率、超额收益、95%置信区间",
        "template": {"name": "小作文事件后超额收益", "strategy_type": "essay_event", "signal_direction": "all", "lookback_days": 365, "holding_periods": [5, 10, 20], "min_importance": 60, "min_confidence": 0.5, "dedupe_window_days": 3, "transaction_cost_bps": 12, "portfolio_size": 10, "validation_method": "walk_forward"},
    },
    {
        "key": "multi_factor", "name": "多因子选股",
        "purpose": "检验重要度、置信度、信息增量与热度组合后，排名靠前样本是否更有效。",
        "used_data": ["AI重要度/置信度", "信息增量", "小作文热度", "Tushare日线"],
        "engine": "四因子加权评分 → 选取前40%样本 → 因子三分层 → 高低组收益差检验",
        "output": "因子分层、强弱差、入选样本收益与稳定性",
        "template": {"name": "非结构化多因子选股", "strategy_type": "multi_factor", "signal_direction": "bullish", "lookback_days": 365, "holding_periods": [10, 20, 60], "min_importance": 45, "min_confidence": 0.4, "dedupe_window_days": 5, "transaction_cost_bps": 15, "portfolio_size": 15, "validation_method": "walk_forward"},
    },
    {
        "key": "hybrid_intelligence", "name": "情报 × 趋势共振",
        "purpose": "只验证观点方向与事件发生前市场趋势一致的样本，避免把事后行情用于筛选。",
        "used_data": ["AI观点方向", "重要度/置信度", "事件日前20日日线", "基准行情"],
        "engine": "高质量观点过滤 → 事件日前MA5/MA20趋势确认 → 共振样本事件研究",
        "output": "共振覆盖率、趋势信号、事件路径与样本外收益",
        "template": {"name": "情报与价格趋势共振", "strategy_type": "hybrid_intelligence", "signal_direction": "all", "lookback_days": 365, "holding_periods": [5, 20, 60], "min_importance": 65, "min_confidence": 0.6, "dedupe_window_days": 3, "transaction_cost_bps": 15, "portfolio_size": 10, "validation_method": "walk_forward"},
    },
    {
        "key": "institution_track", "name": "机构胜率追踪",
        "purpose": "仅保留可识别券商研究团队的观点，比较其在相同口径下的历史有效性。",
        "used_data": ["券商研究组识别", "AI观点", "Tushare日线", "市场基准"],
        "engine": "剔除匿名来源 → 同团队同股聚类 → 贝叶斯收缩胜率 → 时间稳定性检验",
        "output": "机构排名、校正胜率、平均超额与样本资格",
        "template": {"name": "券商研究组胜率追踪", "strategy_type": "institution_track", "signal_direction": "all", "lookback_days": 730, "holding_periods": [5, 20, 60], "min_importance": 50, "min_confidence": 0.4, "dedupe_window_days": 7, "transaction_cost_bps": 12, "portfolio_size": 10, "validation_method": "time_split"},
    },
    {
        "key": "portfolio", "name": "信号组合",
        "purpose": "把每只股票最新且质量最高的情报信号转成有数量、成本和去重约束的组合。",
        "used_data": ["最新小作文信号", "机构历史排名", "价格趋势", "Tushare日线"],
        "engine": "单股保留最新高质量信号 → 机构与趋势排序 → 等权建仓 → 60日组合回放",
        "output": "持仓清单、组合净值、年化收益、最大回撤与胜率",
        "template": {"name": "情报信号约束组合", "strategy_type": "portfolio", "signal_direction": "bullish", "lookback_days": 365, "holding_periods": [20, 30, 60], "min_importance": 60, "min_confidence": 0.5, "dedupe_window_days": 7, "transaction_cost_bps": 20, "portfolio_size": 10, "validation_method": "walk_forward"},
    },
]


class EssayQuantError(ValueError):
    pass


def _loads(value: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


class EssayQuantService:
    """Turn essay mentions into bounded, auditable daily-bar event studies."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        tushare: Optional[TushareGatewayService] = None,
        owner_id: object = _OWNER_UNSET,
    ):
        self.db = db or DatabaseManager.get_instance()
        self.tushare = tushare or TushareGatewayService()
        # Background threads do not inherit request ContextVars. Callers that
        # leave the request lifecycle must capture and pass the owner explicitly.
        self.owner_id = current_owner_id() if owner_id is _OWNER_UNSET else owner_id
        self._coverage: Dict[str, Any] = {}
        self._target_price_date: Optional[date] = None

    def list_rules(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayQuantRuleRecord)
                .where(self._owner_clause(EssayQuantRuleRecord))
                .order_by(desc(EssayQuantRuleRecord.updated_at))
            ).scalars().all()
        return {"items": [self._rule_dict(row) for row in rows], "total": len(rows)}

    def save_rule(self, payload: Dict[str, Any], rule_id: Optional[int] = None) -> Dict[str, Any]:
        fields = self._normalize_rule(payload)
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayQuantRuleRecord).where(
                    EssayQuantRuleRecord.id == int(rule_id),
                    self._owner_clause(EssayQuantRuleRecord),
                )
            ).scalar_one_or_none() if rule_id else None
            if rule_id and row is None:
                raise EssayQuantError("量化规则不存在")
            if row is None:
                row = EssayQuantRuleRecord(owner_id=self.owner_id)
                session.add(row)
            advanced_keys = {"strategy_type", "raw_note_policy", "dedupe_window_days", "transaction_cost_bps", "validation_method"}
            for key, value in fields.items():
                if key not in {"holding_periods", *advanced_keys}:
                    setattr(row, key, value)
            row.holding_periods_json = json.dumps(fields["holding_periods"])
            row.research_config_json = json.dumps({key: fields[key] for key in advanced_keys}, ensure_ascii=False)
            row.updated_at = utc_naive_now()
            session.commit()
            session.refresh(row)
            return self._rule_dict(row)

    def list_runs(self, limit: int = 30) -> Dict[str, Any]:
        requested = max(1, min(limit, 100))
        with self.db.get_session() as session:
            # result_json contains the full event path and can be hundreds of
            # kilobytes per run.  Read only the tiny rule document while
            # deciding which maintenance rows to hide, then hydrate just the
            # investor-visible rows.  The previous query materialized as many
            # as 400 complete backtests on every history refresh.
            candidates = session.execute(
                select(EssayQuantRunRecord)
                .options(load_only(EssayQuantRunRecord.id, EssayQuantRunRecord.rule_json))
                .where(self._owner_clause(EssayQuantRunRecord))
                .order_by(desc(EssayQuantRunRecord.id))
                # Fetch a wider window because system-owned maintenance runs
                # are deliberately filtered from the investor-facing history.
                .limit(max(20, min(limit * 4, 400)))
            ).scalars().all()
            visible_ids = [
                row.id for row in candidates
                if not self._is_system_run(_loads(row.rule_json, {}))
            ][:requested]
            rows = [] if not visible_ids else session.execute(
                select(EssayQuantRunRecord)
                .where(EssayQuantRunRecord.id.in_(visible_ids))
                .order_by(desc(EssayQuantRunRecord.id))
                .execution_options(populate_existing=True)
            ).scalars().all()
        items = []
        for row in rows:
            rule = _loads(row.rule_json, {})
            result = _loads(row.result_json, {})
            robustness = result.get("robustness") or {}
            validation = robustness.get("validation") or {}
            test_excess = validation.get("test_average_excess_return")
            ci = robustness.get("confidence_interval_95") or [None, None]
            max_drawdown = (result.get("portfolio") or {}).get("max_drawdown")
            items.append({
                "id": row.id, "name": rule.get("name") or f"研究 #{row.id}",
                "strategy_type": rule.get("strategy_type", "essay_event"),
                "event_count": row.event_count, "mature_event_count": row.mature_event_count,
                "price_cutoff": row.price_cutoff,
                "primary_average_excess": robustness.get("average_excess_return"),
                "out_of_sample_excess": test_excess,
                "confidence_interval": ci,
                "max_drawdown": max_drawdown,
                "verdict": self._research_verdict(
                    mature_count=int(row.mature_event_count or 0),
                    out_of_sample_excess=test_excess,
                    confidence_interval=ci,
                    max_drawdown=max_drawdown,
                ),
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            })
            if len(items) >= requested:
                break
        return {"items": items, "total": len(items)}

    def get_run(self, run_id: int) -> Dict[str, Any]:
        """Return one immutable result only when it belongs to this owner."""
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayQuantRunRecord).where(
                    EssayQuantRunRecord.id == int(run_id),
                    self._owner_clause(EssayQuantRunRecord),
                )
            ).scalar_one_or_none()
        if row is None:
            raise EssayQuantError("量化运行结果不存在")
        result = _loads(row.result_json, {})
        result["run_id"] = row.id
        result["rule_id"] = row.rule_id
        result["snapshot_hash"] = (row.source_hash or "")[:16]
        return result

    def research_catalog(self) -> Dict[str, Any]:
        """Return real local data inventory plus the supported research methods."""
        definitions = (
            ("essays", "知识星球", "research_notes", "created_at", "事件研究、情报因子、机构追踪"),
            ("essay_analysis", "AI 结构化观点", "essay_analysis_records", "updated_at", "方向、重要度、置信度、个股映射"),
            ("market", "Tushare 行情", "stock_daily", "date", "收益、波动、动量、基准"),
            ("announcements", "公告与全渠道事件", "monitoring_events", "event_at", "事件窗口、风险过滤"),
            ("news", "财经新闻", "intelligence_items", "published_at", "舆情因子、催化验证"),
            ("fundamentals", "财务与估值", "fundamental_snapshot", "created_at", "基本面因子、估值分层"),
            ("portfolio", "组合与持仓", "portfolio_daily_snapshots", "snapshot_date", "归因、风险、实盘对照"),
        )
        assets = []
        with self.db.get_session() as session:
            table_names = set(session.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars().all())
            for key, name, table, date_column, usage in definitions:
                if table not in table_names:
                    assets.append({"key": key, "name": name, "count": 0, "latest_at": None, "usage": usage, "status": "not_ready"})
                    continue
                row = session.execute(text(f'SELECT COUNT(*) AS count, MAX("{date_column}") AS latest_at FROM "{table}"')).mappings().one()
                assets.append({"key": key, "name": name, "count": int(row["count"] or 0),
                               "latest_at": str(row["latest_at"]) if row["latest_at"] is not None else None,
                               "usage": usage, "status": "ready" if row["count"] else "empty"})
        return {"generated_at": utc_naive_now().isoformat() + "Z", "assets": assets, "methods": _METHOD_DEFINITIONS,
                "safeguards": ["时间顺序切分", "重复信号聚类", "交易成本", "置信区间", "参数敏感性", "样本外验证"]}

    def latest_dashboard(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            candidates = session.execute(
                select(EssayQuantRunRecord)
                .options(load_only(EssayQuantRunRecord.id, EssayQuantRunRecord.rule_json))
                .where(self._owner_clause(EssayQuantRunRecord))
                .order_by(desc(EssayQuantRunRecord.id)).limit(100)
            ).scalars().all()
            candidate = next((
                item for item in candidates
                if not self._is_system_run(_loads(item.rule_json, {}))
            ), None)
            row = session.execute(
                select(EssayQuantRunRecord)
                .where(EssayQuantRunRecord.id == candidate.id)
                .execution_options(populate_existing=True)
            ).scalar_one() if candidate is not None else None
        if row is not None:
            result = _loads(row.result_json, {})
            result["run_id"] = row.id
            result["rule_id"] = row.rule_id
            result["snapshot_hash"] = (row.source_hash or "")[:16]
            return result
        return self.run(self._normalize_rule({}), refresh_prices=False, max_symbols=30, persist=False)

    @staticmethod
    def _is_system_run(rule: Dict[str, Any]) -> bool:
        """Keep automated maintenance snapshots out of user research history."""
        return str(rule.get("name") or "").strip() == "后台全量机构预计算"

    @staticmethod
    def _research_verdict(
        *,
        mature_count: int,
        out_of_sample_excess: Any,
        confidence_interval: Any,
        max_drawdown: Any,
    ) -> str:
        """Return a conservative, explainable research-use verdict."""
        try:
            low, high = confidence_interval[:2]
        except (TypeError, ValueError):
            low, high = None, None
        if mature_count < 30:
            return "样本不足"
        if out_of_sample_excess is None:
            return "等待样本外检验"
        if float(out_of_sample_excess) <= 0:
            return "暂不采用"
        if low is None or high is None or float(low) <= 0 <= float(high):
            return "仅可观察"
        if max_drawdown is not None and float(max_drawdown) <= -20:
            return "风险偏高"
        return "可进入模拟观察"

    def latest_institution_dashboard(self) -> Dict[str, Any]:
        """Return the durable all-institution baseline, never a user's custom rule."""
        with self.db.get_session() as session:
            candidates = session.execute(
                select(EssayQuantRunRecord)
                .options(load_only(EssayQuantRunRecord.id, EssayQuantRunRecord.rule_json))
                .where(EssayQuantRunRecord.owner_id.is_(None))
                .order_by(desc(EssayQuantRunRecord.id)).limit(100)
            ).scalars().all()
            candidate = next((item for item in candidates if (
                (rule := _loads(item.rule_json, {})).get("name") == "后台全量机构预计算"
                and not rule.get("source_query")
            )), None)
            row = session.execute(
                select(EssayQuantRunRecord)
                .where(EssayQuantRunRecord.id == candidate.id)
                .execution_options(populate_existing=True)
            ).scalar_one() if candidate is not None else None
        if row is not None:
            result = _loads(row.result_json, {})
            result["run_id"] = row.id
            result["rule_id"] = row.rule_id
            return result
        return self.latest_dashboard()

    def run(
        self,
        payload: Dict[str, Any],
        *,
        refresh_prices: bool = True,
        max_symbols: int = 30,
        persist: bool = True,
        rule_id: Optional[int] = None,
        apply_adjustment: bool = False,
    ) -> Dict[str, Any]:
        # A service instance may execute more than one rule (tests, worker and
        # direct callers). Never carry the previous run's market target into a
        # refresh-disabled or failed refresh.
        self._target_price_date = None
        rule = self._normalize_rule(payload)
        if rule_id is not None:
            with self.db.get_session() as session:
                owned_rule = session.execute(
                    select(EssayQuantRuleRecord.id).where(
                        EssayQuantRuleRecord.id == int(rule_id),
                        self._owner_clause(EssayQuantRuleRecord),
                    )
                ).scalar_one_or_none()
            if owned_rule is None:
                raise EssayQuantError("量化规则不存在")
        events = self._essay_events(rule)
        symbol_counts = Counter(event["symbol"] for event in events)
        all_symbols = list(symbol_counts)
        refresh_limit = max(2, min(int(max_symbols), 60))
        warnings: List[str] = []
        bulk_refreshed_symbols = 0
        if refresh_prices and all_symbols:
            self._target_price_date, bulk_refreshed_symbols, freshness_warnings = self._hydrate_market_freshness(
                all_symbols, rule["benchmark_code"],
            )
            warnings.extend(freshness_warnings)
        existing_prices = self._price_map(all_symbols, rule["lookback_days"] + max(rule["holding_periods"]) + 60)
        unpriced = [symbol for symbol, _ in symbol_counts.most_common() if not existing_prices.get(symbol.split(".")[0])]
        priced = [symbol for symbol, _ in symbol_counts.most_common() if existing_prices.get(symbol.split(".")[0])]
        # Cross-sectional daily refresh keeps every resolved symbol current in a
        # handful of Tushare calls. Per-symbol calls are reserved for missing
        # history (and optional adjustment experiments), not routine freshness.
        refresh_symbols = (unpriced + priced if apply_adjustment else unpriced)[:refresh_limit]
        factor_map: Dict[str, Dict[date, float]] = {}
        if refresh_prices and refresh_symbols:
            factor_map, refresh_warnings = self._hydrate_prices(
                refresh_symbols, rule["benchmark_code"], rule["lookback_days"] + max(rule["holding_periods"]) + 45,
                include_factors=apply_adjustment,
            )
            warnings.extend(refresh_warnings)
            if not apply_adjustment:
                factor_map = {}
        price_map = self._price_map(all_symbols + [rule["benchmark_code"]], rule["lookback_days"] + max(rule["holding_periods"]) + 60)
        evaluated = [self._evaluate_event(event, price_map, factor_map, rule) for event in events]
        selected_events, method_analysis = self._apply_strategy(rule, evaluated, price_map)
        self._coverage.update({
            "resolved_symbol_count": len(all_symbols),
            "priced_symbol_count": sum(bool(price_map.get(symbol.split(".")[0])) for symbol in all_symbols),
            "price_refresh_symbol_count": bulk_refreshed_symbols + (len(refresh_symbols) if refresh_prices else 0),
        })
        dashboard = self._dashboard(rule, selected_events, price_map, factor_map, warnings, bool(factor_map))
        dashboard["method_analysis"] = method_analysis
        dashboard["data_quality"]["method_source_event_count"] = len(evaluated)
        dashboard["data_quality"]["method_selected_event_count"] = len(selected_events)
        if persist:
            source_hash = hashlib.sha256(json.dumps([
                (row["topic_id"], row["symbol"], row["event_at"], row.get("entry_date"), row.get("returns"))
                for row in selected_events
            ], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            with self.db.get_session() as session:
                run = EssayQuantRunRecord(
                    owner_id=self.owner_id,
                    rule_id=rule_id,
                    rule_json=json.dumps(rule, ensure_ascii=False),
                    result_json=json.dumps(dashboard, ensure_ascii=False, default=str),
                    source_hash=source_hash,
                    price_cutoff=dashboard["data_quality"].get("price_cutoff"),
                    event_count=len(selected_events),
                    mature_event_count=dashboard["summary"]["mature_event_count"],
                )
                session.add(run)
                session.commit()
                session.refresh(run)
                dashboard["run_id"] = run.id
                dashboard["rule_id"] = rule_id
        return dashboard

    def _owner_clause(self, model):
        """Keep global baselines separate from each approved user's workspace."""
        if self.owner_id:
            return model.owner_id == self.owner_id
        return model.owner_id.is_(None)

    def _apply_strategy(
        self,
        rule: Dict[str, Any],
        events: List[Dict[str, Any]],
        prices: Dict[str, List[Dict[str, Any]]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Apply the method-specific, pre-declared sample construction rule."""
        strategy = rule["strategy_type"]
        definition = next(
            (item for item in _METHOD_DEFINITIONS if item["template"]["strategy_type"] == strategy),
            _METHOD_DEFINITIONS[0],
        )
        selected = list(events)
        diagnostics: List[Dict[str, Any]] = []
        selection_rule = "全部通过基础质量、方向和去重条件的事件"

        if strategy == "multi_factor":
            for event in events:
                event["method_score"] = round(
                    float(event.get("importance_score") or 0) * 0.40
                    + float(event.get("confidence_score") or 0) * 100 * 0.25
                    + float(event.get("novelty_score") or 0) * 0.20
                    + float(event.get("hype_score") or 0) * 0.15,
                    3,
                )
            ranked = sorted(events, key=lambda item: item["method_score"], reverse=True)
            keep = max(1, math.ceil(len(ranked) * 0.40)) if ranked else 0
            selected = ranked[:keep]
            selection_rule = "重要度40% + 置信度25% + 信息增量20% + 热度15%，保留前40%"
            diagnostics = [
                {"label": "候选事件", "value": len(events), "note": "完成基础质量过滤"},
                {"label": "高分入选", "value": len(selected), "note": "按复合分数前40%"},
                {"label": "最低入选分", "value": selected[-1]["method_score"] if selected else None, "note": "本次动态阈值"},
            ]
        elif strategy == "hybrid_intelligence":
            aligned: List[Dict[str, Any]] = []
            trend_ready = 0
            for event in events:
                state = self._pre_event_trend(event, prices)
                event.update(state)
                if state["trend_ready"]:
                    trend_ready += 1
                if state["trend_aligned"]:
                    aligned.append(event)
            selected = aligned
            selection_rule = "只使用事件发生日前的20个交易日；看多要求MA5≥MA20，看空要求MA5≤MA20"
            diagnostics = [
                {"label": "观点候选", "value": len(events), "note": "高质量AI观点"},
                {"label": "趋势可计算", "value": trend_ready, "note": "事件日前至少20条日线"},
                {"label": "方向共振", "value": len(selected), "note": "观点与事前趋势一致"},
            ]
        elif strategy == "institution_track":
            selected = [event for event in events if event.get("research_group") != "其他来源"]
            groups = {event["research_group"] for event in selected}
            selection_rule = "仅保留可确定识别到券商与研究方向的团队观点，匿名或模糊来源不参评"
            diagnostics = [
                {"label": "全部事件", "value": len(events), "note": "基础事件池"},
                {"label": "机构事件", "value": len(selected), "note": "排除其他来源"},
                {"label": "研究团队", "value": len(groups), "note": "可单独计算胜率"},
            ]
        elif strategy == "portfolio":
            latest_by_symbol: Dict[str, Dict[str, Any]] = {}
            for event in sorted(events, key=lambda item: (item["event_at"], item.get("importance_score", 0)), reverse=True):
                latest_by_symbol.setdefault(event["symbol"], event)
            selected = sorted(
                latest_by_symbol.values(),
                key=lambda item: (
                    float(item.get("importance_score") or 0),
                    float(item.get("confidence_score") or 0),
                    float(item.get("novelty_score") or 0),
                ),
                reverse=True,
            )[: max(rule["portfolio_size"] * 3, rule["portfolio_size"])]
            selection_rule = "每只股票只保留最新观点，再按重要度、置信度和信息增量排序形成组合候选池"
            diagnostics = [
                {"label": "原始事件", "value": len(events), "note": "基础事件池"},
                {"label": "去重股票", "value": len(latest_by_symbol), "note": "单股仅保留最新观点"},
                {"label": "组合候选", "value": len(selected), "note": f"最多{max(rule['portfolio_size'] * 3, rule['portfolio_size'])}只"},
            ]
        else:
            diagnostics = [
                {"label": "有效事件", "value": len(events), "note": "基础质量与方向过滤后"},
                {"label": "事件窗口", "value": "/".join(str(item) for item in rule["holding_periods"]), "note": "交易日"},
                {"label": "基准", "value": rule["benchmark_code"], "note": "计算超额收益"},
            ]

        return selected, {
            "strategy_type": strategy,
            "name": definition["name"],
            "purpose": definition["purpose"],
            "used_data": definition["used_data"],
            "engine": definition["engine"],
            "output": definition["output"],
            "selection_rule": selection_rule,
            "source_event_count": len(events),
            "selected_event_count": len(selected),
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _pre_event_trend(event: Dict[str, Any], prices: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        history = [
            row for row in prices.get(event["symbol"].split(".")[0], [])
            if row["date"] <= event["event_date"] and row.get("close") not in (None, 0)
        ]
        if len(history) < 20:
            return {"trend_ready": False, "trend_aligned": False, "pre_event_ma5": None, "pre_event_ma20": None}
        closes = [float(row["close"]) for row in history[-20:]]
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes) / 20
        bearish = event.get("stance") == "bearish"
        aligned = ma5 <= ma20 if bearish else ma5 >= ma20
        return {
            "trend_ready": True,
            "trend_aligned": aligned,
            "pre_event_ma5": round(ma5, 4),
            "pre_event_ma20": round(ma20, 4),
        }

    def _essay_events(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        now = utc_naive_now()
        cutoff = now - timedelta(days=rule["lookback_days"] + rule["first_mention_window_days"])
        query_terms = self._source_query_terms(rule["source_query"])
        content_column = (
            ResearchNote.content
            if rule["raw_note_policy"] == "include"
            else func.substr(ResearchNote.content, 1, 3000)
        ).label("content")
        statement = (
            select(
                ResearchNote.topic_id.label("topic_id"),
                ResearchNote.group_id.label("group_id"),
                ResearchNote.group_name.label("group_name"),
                ResearchNote.author_name.label("author_name"),
                ResearchNote.title.label("title"),
                content_column,
                ResearchNote.symbol_codes.label("symbol_codes"),
                ResearchNote.created_at.label("note_created_at"),
                EssayAnalysisRecord.status.label("analysis_status"),
                EssayAnalysisRecord.summary.label("analysis_summary"),
                EssayAnalysisRecord.importance_score.label("importance_score"),
                EssayAnalysisRecord.confidence_score.label("confidence_score"),
                EssayAnalysisRecord.stock_mentions_json.label("stock_mentions_json"),
                func.coalesce(func.json_extract(EssayAnalysisRecord.raw_response, "$.novelty_score"), 0).label("novelty_score"),
            )
            .outerjoin(EssayAnalysisRecord, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
            .where(ResearchNote.created_at >= cutoff)
        )
        for term in query_terms:
            pattern = f"%{term}%"
            statement = statement.where(or_(
                ResearchNote.group_name.ilike(pattern),
                ResearchNote.author_name.ilike(pattern),
                ResearchNote.title.ilike(pattern),
                ResearchNote.content.ilike(pattern),
            ))
        with self.db.get_session() as session:
            rows = session.execute(statement.order_by(ResearchNote.created_at)).mappings().all()
        raw_events: List[Dict[str, Any]] = []
        note_ids: set[str] = set()
        analyzed_note_ids: set[str] = set()
        resolved_note_ids: set[str] = set()
        invalid_symbol_mentions = 0
        # Natural-language planning may return several space-separated search
        # concepts. Match all meaningful terms and leave direction to the
        # dedicated stance filter; treating the entire string as one literal
        # phrase silently produced zero-event backtests.
        for row in rows:
            topic_id = str(row["topic_id"] or "")
            note_ids.add(topic_id)
            searchable = " ".join((row["group_name"] or "", row["author_name"] or "", row["title"] or "", row["content"] or "")).lower()
            completed = row["analysis_status"] == "completed"
            if completed:
                analyzed_note_ids.add(topic_id)
            if completed and (
                int(row["importance_score"] or 0) < rule["min_importance"]
                or float(row["confidence_score"] or 0) < rule["min_confidence"]
            ):
                continue
            novelty = int(row["novelty_score"] or 0)
            if not completed and rule["raw_note_policy"] == "exclude":
                continue
            mentions = _loads(row["stock_mentions_json"], []) if completed else []
            if not mentions:
                stance = self._raw_note_stance(searchable)
                mentions = self._raw_note_mentions(row, stance)
            for mention in mentions:
                symbol = self._canonical_code(mention.get("ts_code"))
                if not symbol or not get_index_stock_name(symbol):
                    if symbol:
                        invalid_symbol_mentions += 1
                    continue
                resolved_note_ids.add(topic_id)
                stance = str(mention.get("stance") or "neutral").lower()
                if rule["signal_direction"] == "bullish" and stance != "bullish":
                    continue
                if rule["signal_direction"] == "bearish" and stance != "bearish":
                    continue
                summary = str(row["analysis_summary"] or "") if completed else str(row["title"] or row["content"] or "")[:180]
                importance = int(row["importance_score"] or 0) if completed else 0
                confidence = float(row["confidence_score"] or 0) if completed else 0.0
                text = f"{row['title'] or ''} {summary} {row['content'] or ''}"
                hype = min(100, importance // 2 + novelty // 4 + sum(word in text for word in _HYPE_WORDS) * 6)
                local_time = row["note_created_at"].replace(tzinfo=timezone.utc).astimezone(_SH_TZ)
                raw_events.append({
                    "topic_id": topic_id, "symbol": symbol,
                    "stock_name": str(mention.get("name") or symbol), "stance": stance,
                    "event_at": local_time.isoformat(), "event_date": local_time.date(),
                    "title": row["title"], "summary": summary,
                    "source_group": row["group_name"], "research_group": self._research_group(row),
                    "importance_score": importance,
                    "confidence_score": confidence,
                    "novelty_score": novelty, "hype_score": hype,
                    "rationale": str(mention.get("rationale") or ""),
                    "analysis_status": "completed" if completed else "raw_unanalyzed",
                    "url": f"https://wx.zsxq.com/group/{row['group_id']}/topic/{topic_id}",
                })
        first_seen: Dict[str, date] = {}
        eligible_cutoff = datetime.now(_SH_TZ).date() - timedelta(days=rule["lookback_days"])
        result = []
        cluster_last_seen: Dict[Tuple[str, str, str], date] = {}
        duplicate_event_count = 0
        for event in raw_events:
            previous = first_seen.get(event["symbol"])
            event["first_mention"] = previous is None or (event["event_date"] - previous).days > rule["first_mention_window_days"]
            first_seen[event["symbol"]] = event["event_date"]
            if event["event_date"] < eligible_cutoff:
                continue
            if rule["first_mention_only"] and not event["first_mention"]:
                continue
            cluster_key = (event["symbol"], event["research_group"], event["stance"])
            cluster_previous = cluster_last_seen.get(cluster_key)
            if cluster_previous and (event["event_date"] - cluster_previous).days <= rule["dedupe_window_days"]:
                duplicate_event_count += 1
                continue
            cluster_last_seen[cluster_key] = event["event_date"]
            result.append(event)
        self._coverage = {
            "notes_scanned": len(note_ids),
            "analyzed_note_count": len(analyzed_note_ids),
            "raw_note_count": len(note_ids - analyzed_note_ids),
            "resolved_note_count": len(resolved_note_ids),
            "unresolved_note_count": len(note_ids - resolved_note_ids),
            "research_group_count": len({event["research_group"] for event in result if event["research_group"] != "其他来源"}),
            "invalid_symbol_mentions_filtered": invalid_symbol_mentions,
            "duplicate_event_count": duplicate_event_count,
            "raw_note_policy": rule["raw_note_policy"],
        }
        return result

    @staticmethod
    def _source_query_terms(value: Any) -> List[str]:
        stopwords = {
            "看多", "看空", "多头", "空头", "bullish", "bearish", "全部", "所有",
            "小作文", "文章", "语料", "纪要", "研报观点",
        }
        terms = [
            term.strip().lower()
            for term in re.split(r"[\s,，、;；|]+", str(value or ""))
            if term.strip()
        ]
        return [term for term in terms if term not in stopwords]

    @staticmethod
    def _raw_note_stance(text: str) -> str:
        bullish_words = ("推荐", "看好", "买入", "增持", "上调", "超预期", "景气", "增长")
        bearish_words = ("看空", "卖出", "减持", "下调", "不及预期", "风险", "下滑", "恶化")
        bullish = sum(word in text for word in bullish_words)
        bearish = sum(word in text for word in bearish_words)
        if bullish > bearish:
            return "bullish"
        if bearish > bullish:
            return "bearish"
        return "neutral"

    @staticmethod
    def _raw_note_mentions(note: ResearchNote, stance: str) -> List[Dict[str, Any]]:
        matches: Dict[str, str] = {}
        value_for = note.get if hasattr(note, "get") else lambda key, default=None: getattr(note, key, default)
        for value in (value_for("symbol_codes", "") or "").split(","):
            symbol = EssayQuantService._canonical_code(value)
            name = get_index_stock_name(symbol) if symbol else None
            if symbol and name:
                matches[symbol] = name
        text = f"{value_for('title', '') or ''} {value_for('content', '') or ''}"
        for symbol, name in EssayQuantService._stock_names_in_text(text).items():
            matches.setdefault(symbol, name)
        configured = os.getenv("ESSAY_WATCHLIST") or "603306.SH:华懋科技,300476.SZ:胜宏科技"
        for item in configured.split(","):
            raw_symbol, _, raw_name = item.strip().partition(":")
            symbol = EssayQuantService._canonical_code(raw_symbol)
            name = raw_name.strip()
            if symbol and name and name in text:
                matches[symbol] = name
        return [
            {
                "ts_code": symbol,
                "name": name,
                "stance": stance,
                "confidence": 0.0,
                "rationale": "原文代码或关注股名称确定性匹配，未做 AI 分析",
            }
            for symbol, name in matches.items()
        ]

    @staticmethod
    @lru_cache(maxsize=1)
    def _stock_name_trie() -> Dict[str, Any]:
        """Build a deterministic local A-share name matcher without calling AI."""
        names: Dict[str, str] = {}
        for code, name in get_stock_name_index_map().items():
            symbol = EssayQuantService._canonical_code(code)
            clean_name = str(name or "").strip()
            if symbol and len(clean_name) >= 3 and not clean_name.upper().startswith(("ST", "*ST")):
                names.setdefault(clean_name, symbol)
        root: Dict[str, Any] = {}
        for name, symbol in names.items():
            node = root
            for char in name:
                node = node.setdefault(char, {})
            node.setdefault("__matches__", []).append((name, symbol))
        return root

    @staticmethod
    def _stock_names_in_text(text: str, limit: int = 12) -> Dict[str, str]:
        root = EssayQuantService._stock_name_trie()
        found: Dict[str, str] = {}
        source = str(text or "")
        for start in range(len(source)):
            node = root
            for char in source[start:start + 12]:
                child = node.get(char)
                if not isinstance(child, dict):
                    break
                node = child
                for name, symbol in node.get("__matches__", []):
                    found.setdefault(symbol, name)
                    if len(found) >= limit:
                        return found
        return found

    def _hydrate_market_freshness(
        self,
        symbols: Sequence[str],
        benchmark: str,
    ) -> Tuple[Optional[date], int, List[str]]:
        """Refresh recent completed sessions for every resolved quant symbol.

        Tushare's ``daily(trade_date=...)`` is cross-sectional. Using it here
        replaces the old 60-symbol rotating refresh, which could report a fresh
        global cutoff while most of the event universe remained stale.
        """
        if not self.tushare.available:
            return None, 0, ["Tushare 未配置，本次无法同步量化行情最新交易日"]

        now = datetime.now(_SH_TZ)
        end = now.date()
        start = end - timedelta(days=21)
        warnings: List[str] = []
        try:
            calendar_rows = self.tushare.query(
                "trade_cal",
                params={
                    "exchange": "SSE",
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": end.strftime("%Y%m%d"),
                    "is_open": "1",
                },
                fields="cal_date,is_open",
            )["rows"]
            open_dates = sorted(
                datetime.strptime(str(row["cal_date"]), "%Y%m%d").date()
                for row in calendar_rows
                if str(row.get("is_open") or "1") == "1" and row.get("cal_date")
            )
        except Exception as exc:  # noqa: BLE001
            open_dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)
                          if (start + timedelta(days=offset)).weekday() < 5]
            warnings.append(f"交易日历读取失败，使用工作日回退：{type(exc).__name__}")

        # Intraday bars are incomplete and must not leak into an event-study
        # exit. Before the close, use the prior completed session.
        if now.hour < 16:
            open_dates = [item for item in open_dates if item < end]
        else:
            open_dates = [item for item in open_dates if item <= end]
        if not open_dates:
            return None, 0, warnings + ["最近未找到已完成的 A 股交易日"]

        target = open_dates[-1]
        recent_count = max(1, min(int(os.getenv("ESSAY_QUANT_RECENT_TRADE_DAYS", "5")), 15))
        requested_codes = {str(symbol).split(".")[0] for symbol in symbols}
        refreshed_codes: set[str] = set()
        for trade_date in open_dates[-recent_count:]:
            try:
                rows = self.tushare.query(
                    "daily",
                    params={"trade_date": trade_date.strftime("%Y%m%d")},
                    fields="ts_code,trade_date,open,high,low,close,pct_chg,vol,amount",
                )["rows"]
                selected = [row for row in rows if str(row.get("ts_code") or "").split(".")[0] in requested_codes]
                self._save_daily_cross_section(selected, "tushare:daily:cross_section")
                if trade_date == target:
                    refreshed_codes.update(str(row.get("ts_code") or "").split(".")[0] for row in selected)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{trade_date.isoformat()} 全市场日线同步失败：{type(exc).__name__}")

        try:
            index_rows = self.tushare.query(
                "index_daily",
                params={
                    "ts_code": benchmark,
                    "start_date": open_dates[-recent_count].strftime("%Y%m%d"),
                    "end_date": target.strftime("%Y%m%d"),
                },
                fields="ts_code,trade_date,open,high,low,close,pct_chg,vol,amount",
            )["rows"]
            self._save_daily(benchmark, index_rows, "tushare:index_daily")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"基准 {benchmark} 最新日线同步失败：{type(exc).__name__}")
        return target, len(refreshed_codes), warnings

    def _save_daily_cross_section(self, rows: Sequence[Dict[str, Any]], source: str) -> None:
        normalized: List[Tuple[str, date, Dict[str, Any]]] = []
        for item in rows:
            code = str(item.get("ts_code") or "").split(".")[0]
            raw_date = str(item.get("trade_date") or "")
            if not re.fullmatch(r"\d{6}", code) or not re.fullmatch(r"\d{8}", raw_date):
                continue
            normalized.append((code, datetime.strptime(raw_date, "%Y%m%d").date(), item))
        if not normalized:
            return
        codes = {code for code, _, _ in normalized}
        dates = {trade_date for _, trade_date, _ in normalized}
        with self.db.get_session() as session:
            existing = {
                (row.code, row.date): row
                for row in session.execute(
                    select(StockDaily).where(StockDaily.code.in_(codes), StockDaily.date.in_(dates))
                ).scalars().all()
            }
            for code, trade_date, item in normalized:
                row = existing.get((code, trade_date))
                if row is None:
                    row = StockDaily(code=code, date=trade_date)
                    session.add(row)
                    existing[(code, trade_date)] = row
                for key in ("open", "high", "low", "close", "pct_chg"):
                    value = item.get(key)
                    setattr(row, key, float(value) if value not in (None, "") else None)
                row.volume = float(item["vol"]) if item.get("vol") not in (None, "") else None
                row.amount = float(item["amount"]) * 1000 if item.get("amount") not in (None, "") else None
                row.data_source = source
            session.commit()

    def _hydrate_prices(
        self,
        symbols: Sequence[str],
        benchmark: str,
        days: int,
        *,
        include_factors: bool = False,
    ) -> Tuple[Dict[str, Dict[date, float]], List[str]]:
        if not self.tushare.available:
            return {}, ["Tushare 未配置，本次仅使用本地日线且无法进行复权"]
        start = (datetime.now(_SH_TZ) - timedelta(days=max(60, days))).strftime("%Y%m%d")
        end = datetime.now(_SH_TZ).strftime("%Y%m%d")
        factors: Dict[str, Dict[date, float]] = {}
        warnings: List[str] = []

        def fetch(symbol: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
            daily = self.tushare.query("daily", params={"ts_code": symbol, "start_date": start, "end_date": end})["rows"]
            adj = (
                self.tushare.query("adj_factor", params={"ts_code": symbol, "start_date": start, "end_date": end})["rows"]
                if include_factors else []
            )
            return symbol, daily, adj

        price_workers = max(1, min(int(os.getenv("ESSAY_QUANT_PRICE_WORKERS", "4")), 4))
        with ThreadPoolExecutor(max_workers=price_workers, thread_name_prefix="essay-quant-price") as executor:
            future_map = {executor.submit(fetch, symbol): symbol for symbol in symbols}
            for future in as_completed(future_map):
                symbol = future_map[future]
                try:
                    _, daily_rows, adj_rows = future.result()
                    self._save_daily(symbol, daily_rows, "tushare:daily")
                    factors[symbol] = {
                        datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(): float(row["adj_factor"])
                        for row in adj_rows if row.get("trade_date") and row.get("adj_factor") not in (None, "")
                    }
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{symbol} 行情补取失败：{type(exc).__name__}")
        try:
            rows = self.tushare.query("index_daily", params={"ts_code": benchmark, "start_date": start, "end_date": end})["rows"]
            self._save_daily(benchmark, rows, "tushare:index_daily")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"基准 {benchmark} 补取失败：{type(exc).__name__}")
        return factors, warnings

    def _save_daily(self, symbol: str, rows: Sequence[Dict[str, Any]], source: str) -> None:
        code = symbol.split(".")[0]
        with self.db.get_session() as session:
            existing = {
                row.date: row for row in session.execute(select(StockDaily).where(StockDaily.code == code)).scalars().all()
            }
            for item in rows:
                raw_date = str(item.get("trade_date") or "")
                if not re.fullmatch(r"\d{8}", raw_date):
                    continue
                trade_date = datetime.strptime(raw_date, "%Y%m%d").date()
                row = existing.get(trade_date)
                if row is None:
                    row = StockDaily(code=code, date=trade_date)
                    session.add(row)
                for key in ("open", "high", "low", "close", "pct_chg"):
                    value = item.get(key)
                    setattr(row, key, float(value) if value not in (None, "") else None)
                row.volume = float(item["vol"]) if item.get("vol") not in (None, "") else None
                row.amount = float(item["amount"]) * 1000 if item.get("amount") not in (None, "") else None
                row.data_source = source
            session.commit()

    def _price_map(self, symbols: Sequence[str], days: int) -> Dict[str, List[Dict[str, Any]]]:
        codes = {symbol.split(".")[0] for symbol in symbols}
        cutoff = datetime.now(_SH_TZ).date() - timedelta(days=max(60, days))
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily).where(StockDaily.code.in_(codes), StockDaily.date >= cutoff).order_by(StockDaily.code, StockDaily.date)
            ).scalars().all()
        result: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            result[row.code].append({"date": row.date, "open": row.open, "close": row.close, "high": row.high, "low": row.low})
        return result

    def _evaluate_event(self, event: Dict[str, Any], prices: Dict[str, List[Dict[str, Any]]], factors: Dict[str, Dict[date, float]], rule: Dict[str, Any]) -> Dict[str, Any]:
        series = prices.get(event["symbol"].split(".")[0], [])
        future = [row for row in series if row["date"] > event["event_date"] and row.get("open") not in (None, 0)]
        row = {**event, "entry_date": None, "entry_price": None, "returns": {}, "excess_returns": {}, "mature_periods": []}
        if not future:
            return row
        entry = future[0]
        entry_factor = factors.get(event["symbol"], {}).get(entry["date"], 1.0)
        entry_price = float(entry["open"]) * entry_factor
        row["entry_date"] = entry["date"].isoformat()
        row["entry_price"] = round(entry_price, 4)
        benchmark_series = prices.get(rule["benchmark_code"].split(".")[0], [])
        benchmark_future = [item for item in benchmark_series if item["date"] >= entry["date"] and item.get("close") not in (None, 0)]
        benchmark_entry = float(benchmark_future[0]["close"]) if benchmark_future else None
        for period in rule["holding_periods"]:
            if len(future) < period:
                continue
            exit_row = future[period - 1]
            exit_factor = factors.get(event["symbol"], {}).get(exit_row["date"], 1.0)
            direction = -1.0 if event.get("stance") == "bearish" else 1.0
            gross_return = (float(exit_row["close"]) * exit_factor / entry_price - 1) * 100 * direction
            stock_return = gross_return - rule["transaction_cost_bps"] / 100.0
            benchmark_exit = next((item for item in benchmark_future if item["date"] >= exit_row["date"]), None)
            benchmark_return = ((float(benchmark_exit["close"]) / benchmark_entry - 1) * 100 * direction) if benchmark_entry and benchmark_exit else 0.0
            row["returns"][str(period)] = round(stock_return, 3)
            row["excess_returns"][str(period)] = round(stock_return - benchmark_return, 3)
            row["mature_periods"].append(period)
        return row

    def _dashboard(self, rule: Dict[str, Any], events: List[Dict[str, Any]], prices: Dict[str, List[Dict[str, Any]]], factors: Dict[str, Dict[date, float]], warnings: List[str], adjusted: bool) -> Dict[str, Any]:
        periods = rule["holding_periods"]
        primary = max(periods)
        mature = [event for event in events if str(primary) in event["returns"]]

        def metric(period: int, key: str = "returns") -> Dict[str, Any]:
            values = [float(event[key][str(period)]) for event in events if str(period) in event[key]]
            return {"period": period, "sample_count": len(values), "win_rate": round(sum(value > 0 for value in values) * 100 / len(values), 1) if values else None,
                    "average_return": round(sum(values) / len(values), 3) if values else None,
                    "median_return": round(sorted(values)[len(values) // 2], 3) if values else None}

        group_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in events:
            if event["research_group"] != "其他来源":
                group_buckets[event["research_group"]].append(event)
        rankings = []
        for group, rows in group_buckets.items():
            values = [float(row["returns"][str(primary)]) for row in rows if str(primary) in row["returns"]]
            if not values:
                continue
            excess = [float(row["excess_returns"][str(primary)]) for row in rows if str(primary) in row["excess_returns"]]
            wins = sum(value > 0 for value in values)
            adjusted_win_rate = (wins + 5) / (len(values) + 10)
            shrunk_excess = sum(excess) / (len(excess) + 10) if excess else 0.0
            rankings.append({"research_group": group, "event_count": len(rows), "mature_count": len(values),
                             "rank_eligible": len(values) >= 3,
                             "win_rate": round(wins * 100 / len(values), 1),
                             "adjusted_win_rate": round(adjusted_win_rate * 100, 1),
                             "average_return": round(sum(values) / len(values), 3),
                             "average_excess_return": round(sum(excess) / len(excess), 3) if excess else None,
                             "score": round(adjusted_win_rate * 80 + max(-10, min(10, shrunk_excess)) * 2, 2)})
        rankings.sort(key=lambda item: (item["rank_eligible"], item["score"], item["mature_count"]), reverse=True)

        curve = []
        max_day = max(periods)
        for day_index in range(max_day + 1):
            stock_values, benchmark_values = [], []
            for event in events:
                if not event.get("entry_date"):
                    continue
                series = prices.get(event["symbol"].split(".")[0], [])
                future = [row for row in series if row["date"].isoformat() >= event["entry_date"]]
                if len(future) <= day_index or not future[0].get("open") or not future[day_index].get("close"):
                    continue
                entry_factor = factors.get(event["symbol"], {}).get(future[0]["date"], 1.0)
                day_factor = factors.get(event["symbol"], {}).get(future[day_index]["date"], 1.0)
                stock_values.append((float(future[day_index]["close"]) * day_factor / (float(future[0]["open"]) * entry_factor) - 1) * 100)
                benchmark = prices.get(rule["benchmark_code"].split(".")[0], [])
                bench_future = [row for row in benchmark if row["date"] >= future[0]["date"] and row.get("close")]
                if len(bench_future) > day_index:
                    benchmark_values.append((float(bench_future[day_index]["close"]) / float(bench_future[0]["close"]) - 1) * 100)
            curve.append({"day": day_index, "strategy": round(sum(stock_values) / len(stock_values), 3) if stock_values else None,
                          "benchmark": round(sum(benchmark_values) / len(benchmark_values), 3) if benchmark_values else None,
                          "sample_count": len(stock_values)})

        hype_buckets = []
        for lower, upper, label in ((0, 39, "低"), (40, 59, "中"), (60, 79, "高"), (80, 100, "极高")):
            rows = [event for event in mature if lower <= event["hype_score"] <= upper]
            values = [float(event["returns"][str(primary)]) for event in rows]
            hype_buckets.append({"level": label, "event_count": len(rows), "average_return": round(sum(values) / len(values), 3) if values else None,
                                 "win_rate": round(sum(value > 0 for value in values) * 100 / len(values), 1) if values else None})

        first_mentions = [event for event in events if event["first_mention"] and event["event_date"] >= datetime.now(_SH_TZ).date() - timedelta(days=30)]
        first_mentions.sort(key=lambda item: item["event_at"], reverse=True)
        trend_signals = self._trend_signals(events, prices, factors)
        portfolio = self._portfolio(rankings, trend_signals, prices, factors, rule)
        cutoff = max((row["date"] for series in prices.values() for row in series), default=None)
        freshness = self._price_freshness(events, prices)
        if freshness["freshness_status"] == "stale":
            warnings.append(
                f"量化行情仅 {freshness['price_freshness_ratio']:.1f}% 标的覆盖目标交易日，结果需等待自动同步"
            )
        primary_values = [float(event["excess_returns"][str(primary)]) for event in mature]
        robustness = self._robustness(primary_values, events, primary, rule)
        factor_analysis = self._factor_analysis(mature, primary)
        return {
            "generated_at": utc_naive_now().isoformat() + "Z", "rule": rule,
            "summary": {"event_count": len(events), "mature_event_count": len(mature),
                        "covered_stock_count": len({event["symbol"] for event in events}),
                        "first_mention_30d_count": len(first_mentions),
                        "metrics": [metric(period) for period in periods],
                        "excess_metrics": [metric(period, "excess_returns") for period in periods]},
            "event_curve": curve, "research_group_rankings": rankings[:30], "robustness": robustness,
            "factor_analysis": factor_analysis,
            "first_mentions_30d": [self._public_event(event) for event in first_mentions[:50]],
            "hype_analysis": hype_buckets, "trend_signals": trend_signals[:30], "portfolio": portfolio,
            "events": [self._public_event(event) for event in sorted(events, key=lambda item: item["event_at"], reverse=True)[:100]],
            "data_quality": {"essay_source": "research_notes + optional essay_analysis_records", "price_source": "stock_daily / Tushare daily",
                             **self._coverage,
                             "raw_unanalyzed_event_count": sum(event.get("analysis_status") == "raw_unanalyzed" for event in events),
                             "price_basis": "Tushare复权因子" if adjusted else "本地原始日线（未复权）",
                             "price_cutoff": cutoff.isoformat() if cutoff else None,
                             **freshness,
                             "entry_rule": "事件后首个交易日开盘", "exit_rule": "第N个交易日收盘",
                             "transaction_cost_bps": rule["transaction_cost_bps"],
                             "validation_method": rule["validation_method"],
                             "benchmark": rule["benchmark_code"], "survivorship_note": "仅统计有证券代码且具备到期行情的事件；未成熟样本不计胜率。",
                             "ranking_note": "主排名至少需要3个到期样本，并使用10个中性先验样本收缩胜率与超额收益；小样本仅展示。",
                             "warnings": warnings},
        }

    def _price_freshness(
        self,
        events: Sequence[Dict[str, Any]],
        prices: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        symbols = sorted({str(event.get("symbol") or "") for event in events if event.get("symbol")})
        latest_by_symbol = {
            symbol: max((row["date"] for row in prices.get(symbol.split(".")[0], [])), default=None)
            for symbol in symbols
        }
        observed_dates = [value for value in latest_by_symbol.values() if value is not None]
        target = self._target_price_date or (max(observed_dates) if observed_dates else None)
        current = sum(bool(target and value and value >= target) for value in latest_by_symbol.values())
        priced = len(observed_dates)
        total = len(symbols)
        ratio = current * 100 / total if total else 100.0
        status = "fresh" if ratio >= 98 else "partial" if ratio >= 80 else "stale"
        return {
            "price_target_date": target.isoformat() if target else None,
            "price_latest_date": max(observed_dates).isoformat() if observed_dates else None,
            "price_oldest_symbol_date": min(observed_dates).isoformat() if observed_dates else None,
            "current_price_symbol_count": current,
            "stale_price_symbol_count": max(0, total - current),
            "unpriced_symbol_count": max(0, total - priced),
            "price_freshness_ratio": round(ratio, 2),
            "freshness_status": status,
        }

    @staticmethod
    def _robustness(values: Sequence[float], events: Sequence[Dict[str, Any]], primary: int, rule: Dict[str, Any]) -> Dict[str, Any]:
        clean = [float(value) for value in values if math.isfinite(float(value))]
        if not clean:
            return {"sample_count": 0, "average_excess_return": None, "confidence_interval_95": [None, None],
                    "t_stat": None, "payoff_ratio": None, "distribution": [], "cohorts": [], "sensitivity": []}
        mean = statistics.fmean(clean)
        std = statistics.stdev(clean) if len(clean) > 1 else 0.0
        t_stat = mean / (std / math.sqrt(len(clean))) if std > 0 else None
        rng = random.Random(20260821)
        sample = clean if len(clean) <= 5000 else rng.sample(clean, 5000)
        boot = sorted(statistics.fmean(rng.choices(sample, k=len(sample))) for _ in range(300))
        lower = boot[max(0, int(len(boot) * .025) - 1)]
        upper = boot[min(len(boot) - 1, int(len(boot) * .975))]
        positives = [value for value in clean if value > 0]
        negatives = [abs(value) for value in clean if value < 0]
        low, high = min(clean), max(clean)
        width = max((high - low) / 10, .01)
        distribution = []
        for index in range(10):
            start = low + index * width
            end = high + .000001 if index == 9 else start + width
            distribution.append({"range": f"{start:.1f}~{end:.1f}", "midpoint": round((start + end) / 2, 3),
                                 "count": sum(start <= value < end for value in clean)})
        cohort_values: Dict[str, List[float]] = defaultdict(list)
        for event in events:
            value = event.get("excess_returns", {}).get(str(primary))
            if value is not None:
                cohort_values[str(event.get("event_at", ""))[:7]].append(float(value))
        cohorts = [{"period": key, "sample_count": len(rows), "average_excess_return": round(statistics.fmean(rows), 3),
                    "win_rate": round(sum(value > 0 for value in rows) * 100 / len(rows), 1)}
                   for key, rows in sorted(cohort_values.items())[-18:]]
        sensitivity = [{"label": f"成本 {bps}bp", "transaction_cost_bps": bps,
                        "average_excess_return": round(mean + (rule["transaction_cost_bps"] - bps) / 100.0, 3)}
                       for bps in (0, 6, 12, 20, 30)]
        ordered = sorted(
            ((str(event.get("event_at") or ""), float(event["excess_returns"][str(primary)]))
             for event in events if str(primary) in event.get("excess_returns", {})),
            key=lambda item: item[0],
        )
        split_at = max(1, min(len(ordered) - 1, int(len(ordered) * .7))) if len(ordered) > 1 else len(ordered)
        train_values = [value for _, value in ordered[:split_at]]
        test_values = [value for _, value in ordered[split_at:]]
        folds = []
        for index in range(5):
            start = len(ordered) * index // 5
            end = len(ordered) * (index + 1) // 5
            rows = ordered[start:end]
            if rows:
                folds.append({"fold": index + 1, "start_at": rows[0][0][:10], "end_at": rows[-1][0][:10],
                              "sample_count": len(rows), "average_excess_return": round(statistics.fmean(value for _, value in rows), 3)})
        validation = {"method": rule["validation_method"], "train_sample_count": len(train_values),
                      "test_sample_count": len(test_values),
                      "train_average_excess_return": round(statistics.fmean(train_values), 3) if train_values else None,
                      "test_average_excess_return": round(statistics.fmean(test_values), 3) if test_values else None,
                      "split_date": ordered[split_at][0][:10] if test_values else None, "walk_forward_folds": folds}
        return {"sample_count": len(clean), "average_excess_return": round(mean, 3),
                "confidence_interval_95": [round(lower, 3), round(upper, 3)],
                "t_stat": round(t_stat, 3) if t_stat is not None else None,
                "payoff_ratio": round(statistics.fmean(positives) / statistics.fmean(negatives), 3) if positives and negatives else None,
                "positive_rate": round(len(positives) * 100 / len(clean), 1), "distribution": distribution,
                "cohorts": cohorts, "sensitivity": sensitivity, "validation": validation,
                "out_of_sample_note": "按事件时间展示月度队列；正式策略应在样本外区间确认后再使用。"}

    @staticmethod
    def _factor_analysis(events: Sequence[Dict[str, Any]], primary: int) -> List[Dict[str, Any]]:
        """Create transparent monotonic buckets for non-structured essay factors."""
        definitions = (
            ("importance_score", "重要度"), ("confidence_score", "置信度"),
            ("novelty_score", "信息增量"), ("hype_score", "观点强度"),
        )
        result: List[Dict[str, Any]] = []
        for key, label in definitions:
            eligible = [event for event in events if str(primary) in event.get("excess_returns", {})]
            if not eligible:
                continue
            sorted_rows = sorted(eligible, key=lambda event: float(event.get(key) or 0))
            buckets = []
            for index, bucket_name in enumerate(("低", "中", "高")):
                start = len(sorted_rows) * index // 3
                end = len(sorted_rows) * (index + 1) // 3
                rows = sorted_rows[start:end]
                values = [float(event["excess_returns"][str(primary)]) for event in rows]
                buckets.append({"bucket": bucket_name, "sample_count": len(values),
                                "average_excess_return": round(statistics.fmean(values), 3) if values else None,
                                "win_rate": round(sum(value > 0 for value in values) * 100 / len(values), 1) if values else None})
            low = buckets[0]["average_excess_return"]
            high = buckets[-1]["average_excess_return"]
            result.append({"factor": key, "label": label, "buckets": buckets,
                           "high_low_spread": round(float(high) - float(low), 3) if high is not None and low is not None else None})
        return result

    def _trend_signals(self, events: Sequence[Dict[str, Any]], prices: Dict[str, List[Dict[str, Any]]], factors: Dict[str, Dict[date, float]]) -> List[Dict[str, Any]]:
        latest_by_symbol: Dict[str, Dict[str, Any]] = {}
        for event in sorted(events, key=lambda item: item["event_at"], reverse=True):
            latest_by_symbol.setdefault(event["symbol"], event)
        result = []
        for symbol, event in latest_by_symbol.items():
            series = [row for row in prices.get(symbol.split(".")[0], []) if row.get("close")]
            if len(series) < 20:
                continue
            symbol_factors = factors.get(symbol, {})
            closes = [float(row["close"]) * symbol_factors.get(row["date"], 1.0) for row in series]
            ma5, ma20 = sum(closes[-5:]) / 5, sum(closes[-20:]) / 20
            momentum = (closes[-1] / closes[-20] - 1) * 100
            strength = int(event["first_mention"]) * 2 + int(event["importance_score"] >= 75) + int(ma5 > ma20) + int(momentum > 0)
            result.append({"symbol": symbol, "stock_name": event["stock_name"], "research_group": event["research_group"],
                           "event_at": event["event_at"], "signal_strength": strength, "trend": "up" if ma5 > ma20 else "down",
                           "ma5": round(ma5, 3), "ma20": round(ma20, 3), "momentum_20d": round(momentum, 3),
                           "trigger": "首次提及 + 热度" if event["first_mention"] else "重复提及 + 趋势确认", "url": event["url"]})
        return sorted(result, key=lambda item: (item["signal_strength"], item["momentum_20d"]), reverse=True)

    def _portfolio(self, rankings: Sequence[Dict[str, Any]], signals: Sequence[Dict[str, Any]], prices: Dict[str, List[Dict[str, Any]]], factors: Dict[str, Dict[date, float]], rule: Dict[str, Any]) -> Dict[str, Any]:
        rank = {row["research_group"]: index for index, row in enumerate(rankings)}
        eligible_groups = {row["research_group"] for row in rankings if row.get("rank_eligible")}
        candidates = sorted(signals, key=lambda item: (
            0 if item["research_group"] in eligible_groups else 1 if item["research_group"] in rank else 2,
            rank.get(item["research_group"], 999),
            -item["signal_strength"],
            -item["momentum_20d"],
        ))
        selected, seen = [], set()
        for item in candidates:
            if item["symbol"] in seen:
                continue
            seen.add(item["symbol"]); selected.append(item)
            if len(selected) >= rule["portfolio_size"]:
                break
        if not selected:
            return {"components": [], "curve": [], "annualized_return": None, "max_drawdown": None, "win_rate": None}
        common_dates = sorted(set.intersection(*[
            {row["date"] for row in prices.get(item["symbol"].split(".")[0], []) if row.get("close")}
            for item in selected
        ]))[-60:]
        curves = []
        for item in selected:
            symbol_factors = factors.get(item["symbol"], {})
            mapping = {row["date"]: float(row["close"]) * symbol_factors.get(row["date"], 1.0)
                       for row in prices.get(item["symbol"].split(".")[0], []) if row.get("close")}
            if common_dates:
                base = mapping[common_dates[0]]
                curves.append([mapping[day] / base for day in common_dates])
        curve = [{"date": day.isoformat(), "value": round(sum(values) / len(values), 5)} for day, values in zip(common_dates, zip(*curves))] if curves else []
        values = [row["value"] for row in curve]
        peak, max_dd = 0.0, 0.0
        for value in values:
            peak = max(peak, value)
            max_dd = min(max_dd, value / peak - 1 if peak else 0)
        annualized = ((values[-1] / values[0]) ** (252 / max(1, len(values) - 1)) - 1) * 100 if len(values) > 1 else None
        returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values))]
        return {"components": [{**item, "weight": round(100 / len(selected), 2)} for item in selected], "curve": curve,
                "annualized_return": round(annualized, 2) if annualized is not None and math.isfinite(annualized) else None,
                "max_drawdown": round(max_dd * 100, 2),
                "win_rate": round(sum(value > 0 for value in returns) * 100 / len(returns), 1) if returns else None}

    @staticmethod
    def _public_event(event: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(event)
        result.pop("event_date", None)
        return result

    @staticmethod
    def _research_group(note: ResearchNote) -> str:
        """Prefer an explicitly named sell-side team over the ZSXQ poster account."""
        value_for = note.get if hasattr(note, "get") else lambda key, default=None: getattr(note, key, default)
        text = f"{value_for('title', '') or ''} {value_for('content', '') or ''}"[:3000]
        broker_pattern = "|".join(re.escape(name) for name in sorted(_BROKER_NAMES, key=len, reverse=True))
        sector_pattern = "|".join(re.escape(name) for name in sorted(_SELLSIDE_SECTORS, key=len, reverse=True))
        team_pattern = rf"(?:{broker_pattern})(?:证券|研究所|研究院)?(?:{sector_pattern})(?:组|团队|研究)?(?:[·\-— ].{{1,20}})?"
        bracketed = re.findall(r"【([^】]{2,40})】", text)
        for value in bracketed:
            if re.fullmatch(team_pattern, value.strip()):
                return value.strip()[:80]
        match = re.search(rf"((?:{broker_pattern})(?:证券|研究所|研究院)?(?:{sector_pattern})(?:组|团队|研究)?)", text)
        if match:
            return match.group(1).strip()[:80]
        fallback = str(value_for("author_name") or value_for("group_name") or "").strip()
        if re.fullmatch(team_pattern, fallback):
            return fallback[:80]
        return "其他来源"

    @staticmethod
    def _canonical_code(value: Any) -> Optional[str]:
        raw = str(value or "").strip().upper().replace(".SS", ".SH")
        match = re.search(r"(\d{6})(?:\.(SH|SZ|BJ))?", raw)
        if not match:
            return None
        digits, suffix = match.group(1), match.group(2)
        suffix = suffix or ("SH" if digits.startswith(("6", "9")) else "BJ" if digits.startswith(("4", "8")) else "SZ")
        return f"{digits}.{suffix}"

    @staticmethod
    def _normalize_rule(payload: Dict[str, Any]) -> Dict[str, Any]:
        periods = sorted({max(1, min(int(value), 60)) for value in (payload.get("holding_periods") or [5, 10, 20])})[:3]
        strategy_type = str(payload.get("strategy_type") or "essay_event")
        if strategy_type not in _STRATEGY_TYPES:
            strategy_type = "essay_event"
        return {
            "name": str(payload.get("name") or "小作文多头事件策略").strip()[:120],
            "source_query": str(payload.get("source_query") or "").strip()[:200],
            "signal_direction": str(payload.get("signal_direction") or "bullish") if str(payload.get("signal_direction") or "bullish") in {"bullish", "bearish", "all"} else "bullish",
            "lookback_days": max(30, min(int(payload.get("lookback_days") or 365), 1825)),
            "holding_periods": periods,
            "first_mention_only": bool(payload.get("first_mention_only", False)),
            "first_mention_window_days": max(30, min(int(payload.get("first_mention_window_days") or 180), 730)),
            "min_importance": max(0, min(int(payload.get("min_importance") or 60), 100)),
            "min_confidence": max(0.0, min(float(payload.get("min_confidence") or 0.5), 1.0)),
            "benchmark_code": EssayQuantService._canonical_code(payload.get("benchmark_code") or "000300.SH") or "000300.SH",
            "portfolio_size": max(2, min(int(payload.get("portfolio_size") or 10), 30)),
            "enabled": bool(payload.get("enabled", True)),
            "strategy_type": strategy_type,
            "raw_note_policy": "include" if str(payload.get("raw_note_policy") or "exclude") == "include" else "exclude",
            "dedupe_window_days": max(0, min(int(3 if payload.get("dedupe_window_days") is None else payload.get("dedupe_window_days")), 30)),
            "transaction_cost_bps": max(0.0, min(float(12 if payload.get("transaction_cost_bps") is None else payload.get("transaction_cost_bps")), 200.0)),
            "validation_method": str(payload.get("validation_method") or "walk_forward") if str(payload.get("validation_method") or "walk_forward") in {"walk_forward", "time_split", "none"} else "walk_forward",
        }

    @staticmethod
    def _rule_dict(row: EssayQuantRuleRecord) -> Dict[str, Any]:
        config = _loads(row.research_config_json, {})
        return {"id": row.id, "name": row.name, "source_query": row.source_query, "signal_direction": row.signal_direction,
                "lookback_days": row.lookback_days, "holding_periods": _loads(row.holding_periods_json, [5, 10, 20]),
                "first_mention_only": bool(row.first_mention_only), "first_mention_window_days": row.first_mention_window_days,
                "min_importance": row.min_importance, "min_confidence": row.min_confidence,
                "benchmark_code": row.benchmark_code, "portfolio_size": row.portfolio_size, "enabled": bool(row.enabled),
                "strategy_type": config.get("strategy_type", "essay_event"),
                "raw_note_policy": config.get("raw_note_policy", "exclude"),
                "dedupe_window_days": config.get("dedupe_window_days", 3),
                "transaction_cost_bps": config.get("transaction_cost_bps", 12),
                "validation_method": config.get("validation_method", "walk_forward"),
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None}
