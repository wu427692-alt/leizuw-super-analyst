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

from sqlalchemy import desc, select, text

from src.data.stock_index_loader import get_index_stock_name, get_stock_name_index_map
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


class EssayQuantError(ValueError):
    pass


def _loads(value: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


class EssayQuantService:
    """Turn essay mentions into bounded, auditable daily-bar event studies."""

    def __init__(self, db: Optional[DatabaseManager] = None, tushare: Optional[TushareGatewayService] = None):
        self.db = db or DatabaseManager.get_instance()
        self.tushare = tushare or TushareGatewayService()
        self._coverage: Dict[str, Any] = {}

    def list_rules(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            rows = session.execute(select(EssayQuantRuleRecord).order_by(desc(EssayQuantRuleRecord.updated_at))).scalars().all()
        return {"items": [self._rule_dict(row) for row in rows], "total": len(rows)}

    def save_rule(self, payload: Dict[str, Any], rule_id: Optional[int] = None) -> Dict[str, Any]:
        fields = self._normalize_rule(payload)
        with self.db.get_session() as session:
            row = session.get(EssayQuantRuleRecord, int(rule_id)) if rule_id else None
            if rule_id and row is None:
                raise EssayQuantError("量化规则不存在")
            if row is None:
                row = EssayQuantRuleRecord()
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
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayQuantRunRecord).order_by(desc(EssayQuantRunRecord.id)).limit(max(1, min(limit, 100)))
            ).scalars().all()
        items = []
        for row in rows:
            rule = _loads(row.rule_json, {})
            result = _loads(row.result_json, {})
            robustness = result.get("robustness") or {}
            items.append({
                "id": row.id, "name": rule.get("name") or f"研究 #{row.id}",
                "strategy_type": rule.get("strategy_type", "essay_event"),
                "event_count": row.event_count, "mature_event_count": row.mature_event_count,
                "price_cutoff": row.price_cutoff,
                "primary_average_excess": robustness.get("average_excess_return"),
                "confidence_interval": robustness.get("confidence_interval_95"),
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            })
        return {"items": items, "total": len(items)}

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
        methods = [
            {"key": "event_study", "name": "事件研究", "purpose": "检验公告、研报、小作文或新闻发生后的超额收益", "output": "事件窗、置信区间、分组收益"},
            {"key": "multi_factor", "name": "多因子选股", "purpose": "组合行情、财务、筹码、资金与情报因子进行截面排序", "output": "因子分层、强弱差、交互信号"},
            {"key": "hybrid_intelligence", "name": "情报混合策略", "purpose": "将非结构化观点与结构化市场因子联合验证", "output": "交互效应、因子贡献、组合曲线"},
            {"key": "institution_track", "name": "机构胜率追踪", "purpose": "按研究团队、行业与市场状态评估观点有效性", "output": "收缩胜率、样本外稳定性、排名"},
            {"key": "portfolio", "name": "组合与持仓", "purpose": "把研究信号转为有成本和仓位约束的组合", "output": "收益、回撤、风险归因、交易明细"},
        ]
        return {"generated_at": utc_naive_now().isoformat() + "Z", "assets": assets, "methods": methods,
                "safeguards": ["时间顺序切分", "重复信号聚类", "交易成本", "置信区间", "参数敏感性", "样本外验证"]}

    def latest_dashboard(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(select(EssayQuantRunRecord).order_by(desc(EssayQuantRunRecord.id)).limit(1)).scalar_one_or_none()
        if row:
            result = _loads(row.result_json, {})
            result["run_id"] = row.id
            result["rule_id"] = row.rule_id
            return result
        return self.run(self._normalize_rule({}), refresh_prices=False, max_symbols=30, persist=False)

    def latest_institution_dashboard(self) -> Dict[str, Any]:
        """Return the durable all-institution baseline, never a user's custom rule."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(EssayQuantRunRecord).order_by(desc(EssayQuantRunRecord.id)).limit(100)
            ).scalars().all()
        for row in rows:
            rule = _loads(row.rule_json, {})
            if rule.get("name") != "后台全量机构预计算" or rule.get("source_query"):
                continue
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
        apply_adjustment: bool = True,
    ) -> Dict[str, Any]:
        rule = self._normalize_rule(payload)
        events = self._essay_events(rule)
        symbol_counts = Counter(event["symbol"] for event in events)
        all_symbols = list(symbol_counts)
        refresh_limit = max(2, min(int(max_symbols), 60))
        existing_prices = self._price_map(all_symbols, rule["lookback_days"] + max(rule["holding_periods"]) + 60)
        unpriced = [symbol for symbol, _ in symbol_counts.most_common() if not existing_prices.get(symbol.split(".")[0])]
        priced = [symbol for symbol, _ in symbol_counts.most_common() if existing_prices.get(symbol.split(".")[0])]
        refresh_symbols = (unpriced + priced)[:refresh_limit]
        factor_map: Dict[str, Dict[date, float]] = {}
        warnings: List[str] = []
        if refresh_prices and refresh_symbols:
            factor_map, refresh_warnings = self._hydrate_prices(
                refresh_symbols, rule["benchmark_code"], rule["lookback_days"] + max(rule["holding_periods"]) + 45,
            )
            warnings.extend(refresh_warnings)
            if not apply_adjustment:
                factor_map = {}
        price_map = self._price_map(all_symbols + [rule["benchmark_code"]], rule["lookback_days"] + max(rule["holding_periods"]) + 60)
        evaluated = [self._evaluate_event(event, price_map, factor_map, rule) for event in events]
        self._coverage.update({
            "resolved_symbol_count": len(all_symbols),
            "priced_symbol_count": sum(bool(price_map.get(symbol.split(".")[0])) for symbol in all_symbols),
            "price_refresh_symbol_count": len(refresh_symbols) if refresh_prices else 0,
        })
        dashboard = self._dashboard(rule, evaluated, price_map, factor_map, warnings, bool(factor_map))
        if persist:
            source_hash = hashlib.sha256(json.dumps([
                (row["topic_id"], row["symbol"], row["event_at"], row.get("entry_date"), row.get("returns"))
                for row in evaluated
            ], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            with self.db.get_session() as session:
                run = EssayQuantRunRecord(
                    rule_id=rule_id,
                    rule_json=json.dumps(rule, ensure_ascii=False),
                    result_json=json.dumps(dashboard, ensure_ascii=False, default=str),
                    source_hash=source_hash,
                    price_cutoff=dashboard["data_quality"].get("price_cutoff"),
                    event_count=len(evaluated),
                    mature_event_count=dashboard["summary"]["mature_event_count"],
                )
                session.add(run)
                session.commit()
                session.refresh(run)
                dashboard["run_id"] = run.id
                dashboard["rule_id"] = rule_id
        return dashboard

    def _essay_events(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        now = utc_naive_now()
        cutoff = now - timedelta(days=rule["lookback_days"] + rule["first_mention_window_days"])
        with self.db.get_session() as session:
            rows = session.execute(
                select(ResearchNote, EssayAnalysisRecord)
                .outerjoin(EssayAnalysisRecord, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(ResearchNote.created_at >= cutoff)
                .order_by(ResearchNote.created_at)
            ).all()
        raw_events: List[Dict[str, Any]] = []
        note_ids: set[str] = set()
        analyzed_note_ids: set[str] = set()
        resolved_note_ids: set[str] = set()
        invalid_symbol_mentions = 0
        query = rule["source_query"].lower()
        for note, analysis in rows:
            note_ids.add(note.topic_id)
            searchable = " ".join((note.group_name or "", note.author_name or "", note.title or "", note.content or "")).lower()
            if query and query not in searchable:
                continue
            completed = analysis is not None and analysis.status == "completed"
            if completed:
                analyzed_note_ids.add(note.topic_id)
            if completed and (
                int(analysis.importance_score or 0) < rule["min_importance"]
                or float(analysis.confidence_score or 0) < rule["min_confidence"]
            ):
                continue
            raw = _loads(analysis.raw_response, {}) if completed else {}
            novelty = int(raw.get("novelty_score") or 0)
            if not completed and rule["raw_note_policy"] == "exclude":
                continue
            mentions = _loads(analysis.stock_mentions_json, []) if completed else []
            if not mentions:
                stance = self._raw_note_stance(searchable)
                mentions = self._raw_note_mentions(note, stance)
            for mention in mentions:
                symbol = self._canonical_code(mention.get("ts_code"))
                if not symbol or not get_index_stock_name(symbol):
                    if symbol:
                        invalid_symbol_mentions += 1
                    continue
                resolved_note_ids.add(note.topic_id)
                stance = str(mention.get("stance") or "neutral").lower()
                if rule["signal_direction"] == "bullish" and stance != "bullish":
                    continue
                if rule["signal_direction"] == "bearish" and stance != "bearish":
                    continue
                summary = str(analysis.summary or "") if completed else str(note.title or note.content or "")[:180]
                importance = int(analysis.importance_score or 0) if completed else 0
                confidence = float(analysis.confidence_score or 0) if completed else 0.0
                text = f"{note.title or ''} {summary} {note.content or ''}"
                hype = min(100, importance // 2 + novelty // 4 + sum(word in text for word in _HYPE_WORDS) * 6)
                local_time = note.created_at.replace(tzinfo=timezone.utc).astimezone(_SH_TZ)
                raw_events.append({
                    "topic_id": note.topic_id, "symbol": symbol,
                    "stock_name": str(mention.get("name") or symbol), "stance": stance,
                    "event_at": local_time.isoformat(), "event_date": local_time.date(),
                    "title": note.title, "summary": summary,
                    "source_group": note.group_name, "research_group": self._research_group(note),
                    "importance_score": importance,
                    "confidence_score": confidence,
                    "novelty_score": novelty, "hype_score": hype,
                    "rationale": str(mention.get("rationale") or ""),
                    "analysis_status": "completed" if completed else "raw_unanalyzed",
                    "url": f"https://wx.zsxq.com/group/{note.group_id}/topic/{note.topic_id}",
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
        for value in (note.symbol_codes or "").split(","):
            symbol = EssayQuantService._canonical_code(value)
            name = get_index_stock_name(symbol) if symbol else None
            if symbol and name:
                matches[symbol] = name
        text = f"{note.title or ''} {note.content or ''}"
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

    def _hydrate_prices(self, symbols: Sequence[str], benchmark: str, days: int) -> Tuple[Dict[str, Dict[date, float]], List[str]]:
        if not self.tushare.available:
            return {}, ["Tushare 未配置，本次仅使用本地日线且无法进行复权"]
        start = (datetime.now(_SH_TZ) - timedelta(days=max(60, days))).strftime("%Y%m%d")
        end = datetime.now(_SH_TZ).strftime("%Y%m%d")
        factors: Dict[str, Dict[date, float]] = {}
        warnings: List[str] = []

        def fetch(symbol: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
            daily = self.tushare.query("daily", params={"ts_code": symbol, "start_date": start, "end_date": end})["rows"]
            adj = self.tushare.query("adj_factor", params={"ts_code": symbol, "start_date": start, "end_date": end})["rows"]
            return symbol, daily, adj

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="essay-quant-price") as executor:
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
                             "entry_rule": "事件后首个交易日开盘", "exit_rule": "第N个交易日收盘",
                             "transaction_cost_bps": rule["transaction_cost_bps"],
                             "validation_method": rule["validation_method"],
                             "benchmark": rule["benchmark_code"], "survivorship_note": "仅统计有证券代码且具备到期行情的事件；未成熟样本不计胜率。",
                             "ranking_note": "主排名至少需要3个到期样本，并使用10个中性先验样本收缩胜率与超额收益；小样本仅展示。",
                             "warnings": warnings},
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
        text = f"{note.title or ''} {note.content or ''}"[:3000]
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
        fallback = str(note.author_name or note.group_name or "").strip()
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
            "strategy_type": str(payload.get("strategy_type") or "essay_event")[:40],
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
