# -*- coding: utf-8 -*-
"""Event studies and reproducible strategies built from analyzed ZSXQ essays."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import desc, select

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
            for key, value in fields.items():
                if key != "holding_periods":
                    setattr(row, key, value)
            row.holding_periods_json = json.dumps(fields["holding_periods"])
            row.updated_at = utc_naive_now()
            session.commit()
            session.refresh(row)
            return self._rule_dict(row)

    def latest_dashboard(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(select(EssayQuantRunRecord).order_by(desc(EssayQuantRunRecord.id)).limit(1)).scalar_one_or_none()
        if row:
            result = _loads(row.result_json, {})
            result["run_id"] = row.id
            result["rule_id"] = row.rule_id
            return result
        return self.run(self._normalize_rule({}), refresh_prices=False, max_symbols=30, persist=False)

    def run(
        self,
        payload: Dict[str, Any],
        *,
        refresh_prices: bool = True,
        max_symbols: int = 30,
        persist: bool = True,
        rule_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        rule = self._normalize_rule(payload)
        events = self._essay_events(rule)
        symbol_counts = Counter(event["symbol"] for event in events)
        selected_symbols = [symbol for symbol, _ in symbol_counts.most_common(max(2, min(int(max_symbols), 60)))]
        events = [event for event in events if event["symbol"] in selected_symbols]
        factor_map: Dict[str, Dict[date, float]] = {}
        warnings: List[str] = []
        if refresh_prices and selected_symbols:
            factor_map, refresh_warnings = self._hydrate_prices(
                selected_symbols, rule["benchmark_code"], rule["lookback_days"] + max(rule["holding_periods"]) + 45,
            )
            warnings.extend(refresh_warnings)
        price_map = self._price_map(selected_symbols + [rule["benchmark_code"]], rule["lookback_days"] + max(rule["holding_periods"]) + 60)
        evaluated = [self._evaluate_event(event, price_map, factor_map, rule) for event in events]
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
                select(EssayAnalysisRecord, ResearchNote)
                .join(ResearchNote, ResearchNote.topic_id == EssayAnalysisRecord.topic_id)
                .where(EssayAnalysisRecord.status == "completed", ResearchNote.created_at >= cutoff)
                .order_by(ResearchNote.created_at)
            ).all()
        raw_events: List[Dict[str, Any]] = []
        query = rule["source_query"].lower()
        for analysis, note in rows:
            searchable = " ".join((note.group_name or "", note.author_name or "", note.title or "", note.content or "")).lower()
            if query and query not in searchable:
                continue
            if int(analysis.importance_score or 0) < rule["min_importance"] or float(analysis.confidence_score or 0) < rule["min_confidence"]:
                continue
            raw = _loads(analysis.raw_response, {})
            novelty = int(raw.get("novelty_score") or 0)
            for mention in _loads(analysis.stock_mentions_json, []):
                symbol = self._canonical_code(mention.get("ts_code"))
                if not symbol:
                    continue
                stance = str(mention.get("stance") or "neutral").lower()
                if rule["signal_direction"] == "bullish" and stance != "bullish":
                    continue
                if rule["signal_direction"] == "bearish" and stance != "bearish":
                    continue
                text = f"{note.title or ''} {analysis.summary or ''} {note.content or ''}"
                hype = min(100, int(analysis.importance_score or 0) // 2 + novelty // 4 + sum(word in text for word in _HYPE_WORDS) * 6)
                local_time = note.created_at.replace(tzinfo=timezone.utc).astimezone(_SH_TZ)
                raw_events.append({
                    "topic_id": note.topic_id, "symbol": symbol,
                    "stock_name": str(mention.get("name") or symbol), "stance": stance,
                    "event_at": local_time.isoformat(), "event_date": local_time.date(),
                    "title": note.title, "summary": analysis.summary,
                    "source_group": note.group_name, "research_group": self._research_group(note),
                    "importance_score": int(analysis.importance_score or 0),
                    "confidence_score": float(analysis.confidence_score or 0),
                    "novelty_score": novelty, "hype_score": hype,
                    "rationale": str(mention.get("rationale") or ""),
                    "url": f"https://wx.zsxq.com/group/{note.group_id}/topic/{note.topic_id}",
                })
        first_seen: Dict[str, date] = {}
        eligible_cutoff = datetime.now(_SH_TZ).date() - timedelta(days=rule["lookback_days"])
        result = []
        for event in raw_events:
            previous = first_seen.get(event["symbol"])
            event["first_mention"] = previous is None or (event["event_date"] - previous).days > rule["first_mention_window_days"]
            first_seen[event["symbol"]] = event["event_date"]
            if event["event_date"] < eligible_cutoff:
                continue
            if rule["first_mention_only"] and not event["first_mention"]:
                continue
            result.append(event)
        return result

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
            stock_return = (float(exit_row["close"]) * exit_factor / entry_price - 1) * 100
            benchmark_exit = next((item for item in benchmark_future if item["date"] >= exit_row["date"]), None)
            benchmark_return = ((float(benchmark_exit["close"]) / benchmark_entry - 1) * 100) if benchmark_entry and benchmark_exit else 0.0
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
        return {
            "generated_at": utc_naive_now().isoformat() + "Z", "rule": rule,
            "summary": {"event_count": len(events), "mature_event_count": len(mature),
                        "covered_stock_count": len({event["symbol"] for event in events}),
                        "first_mention_30d_count": len(first_mentions),
                        "metrics": [metric(period) for period in periods],
                        "excess_metrics": [metric(period, "excess_returns") for period in periods]},
            "event_curve": curve, "research_group_rankings": rankings[:30],
            "first_mentions_30d": [self._public_event(event) for event in first_mentions[:50]],
            "hype_analysis": hype_buckets, "trend_signals": trend_signals[:30], "portfolio": portfolio,
            "events": [self._public_event(event) for event in sorted(events, key=lambda item: item["event_at"], reverse=True)[:100]],
            "data_quality": {"essay_source": "research_notes + essay_analysis_records", "price_source": "stock_daily / Tushare daily",
                             "price_basis": "Tushare复权因子" if adjusted else "本地原始日线（未复权）",
                             "price_cutoff": cutoff.isoformat() if cutoff else None,
                             "entry_rule": "事件后首个交易日开盘", "exit_rule": "第N个交易日收盘",
                             "benchmark": rule["benchmark_code"], "survivorship_note": "仅统计有证券代码且具备到期行情的事件；未成熟样本不计胜率。",
                             "ranking_note": "主排名至少需要3个到期样本，并使用10个中性先验样本收缩胜率与超额收益；小样本仅展示。",
                             "warnings": warnings},
        }

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
        }

    @staticmethod
    def _rule_dict(row: EssayQuantRuleRecord) -> Dict[str, Any]:
        return {"id": row.id, "name": row.name, "source_query": row.source_query, "signal_direction": row.signal_direction,
                "lookback_days": row.lookback_days, "holding_periods": _loads(row.holding_periods_json, [5, 10, 20]),
                "first_mention_only": bool(row.first_mention_only), "first_mention_window_days": row.first_mention_window_days,
                "min_importance": row.min_importance, "min_confidence": row.min_confidence,
                "benchmark_code": row.benchmark_code, "portfolio_size": row.portfolio_size, "enabled": bool(row.enabled),
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None}
