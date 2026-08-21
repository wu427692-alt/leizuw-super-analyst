# -*- coding: utf-8 -*-
"""Auditable links between essay attention and locally cached daily prices."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import math
import statistics
from typing import Any, Dict, Mapping, Optional, Sequence

from sqlalchemy import select

from src.storage import DatabaseManager, StockDaily, normalize_daily_storage_code


_PERIODS = (1, 5, 20)
_BENCHMARK_CODE = "000300"


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _correlation(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) < 8 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = sum((x - left_mean) ** 2 for x in left)
    right_scale = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_scale * right_scale)
    return numerator / denominator if denominator else None


def _metric(values: Sequence[float], excess_values: Sequence[float], period: int) -> Dict[str, Any]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    excess = [float(value) for value in excess_values if math.isfinite(float(value))]
    if not clean:
        return {
            "period": period,
            "sample_count": 0,
            "average_return": None,
            "median_return": None,
            "win_rate": None,
            "average_excess_return": None,
            "excess_win_rate": None,
            "confidence_interval_95": [None, None],
        }
    mean = statistics.fmean(clean)
    if len(clean) > 1:
        margin = 1.96 * statistics.stdev(clean) / math.sqrt(len(clean))
        confidence = [_round(mean - margin), _round(mean + margin)]
    else:
        confidence = [None, None]
    return {
        "period": period,
        "sample_count": len(clean),
        "average_return": _round(mean),
        "median_return": _round(statistics.median(clean)),
        "win_rate": round(sum(value > 0 for value in clean) * 100 / len(clean), 1),
        "average_excess_return": _round(statistics.fmean(excess)) if excess else None,
        "excess_win_rate": round(sum(value > 0 for value in excess) * 100 / len(excess), 1) if excess else None,
        "confidence_interval_95": confidence,
    }


class EssayMarketInsightService:
    """Calculate descriptive event-study evidence without implying causality."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()

    def build(
        self,
        *,
        stock_buckets: Mapping[str, Mapping[str, Any]],
        mention_dates: Mapping[str, Counter[date]],
        start_date: date,
        end_date: date,
        limit: int = 8,
    ) -> Dict[str, Any]:
        ranked = sorted(
            (
                (key, bucket)
                for key, bucket in stock_buckets.items()
                if str(bucket.get("ts_code") or "").strip() and mention_dates.get(key)
            ),
            key=lambda item: (
                sum(mention_dates[item[0]].values()),
                int(item[1].get("importance_total") or 0),
            ),
            reverse=True,
        )[: max(1, min(int(limit), 12))]
        codes = {
            normalize_daily_storage_code(str(bucket.get("ts_code") or ""))
            for _, bucket in ranked
        }
        codes.discard("")
        query_codes = sorted(codes | {_BENCHMARK_CODE})
        query_start = start_date - timedelta(days=45)
        query_end = end_date + timedelta(days=45)
        with self.db.get_session() as session:
            rows = list(session.execute(
                select(StockDaily)
                .where(
                    StockDaily.code.in_(query_codes),
                    StockDaily.date >= query_start,
                    StockDaily.date <= query_end,
                )
                .order_by(StockDaily.code, StockDaily.date)
            ).scalars().all())

        prices: Dict[str, list[StockDaily]] = defaultdict(list)
        for row in rows:
            if row.close not in (None, 0):
                prices[row.code].append(row)
        benchmark = prices.get(_BENCHMARK_CODE, [])
        items = []
        covered_events = 0
        total_events = sum(len(mention_dates.get(key) or {}) for key, _ in ranked)
        for key, bucket in ranked:
            code = normalize_daily_storage_code(str(bucket.get("ts_code") or ""))
            series = prices.get(code, [])
            if len(series) < 2:
                continue
            item = self._stock_item(
                key=key,
                bucket=bucket,
                mentions=mention_dates[key],
                series=series,
                benchmark=benchmark,
                start_date=start_date,
                end_date=end_date,
            )
            covered_events += item["covered_event_days"]
            items.append(item)

        all_price_rows = [row for item in items for row in item["series"]]
        sources = sorted({str(row.data_source or "本地行情库") for row in rows if row.code in codes})
        return {
            "benchmark": "000300.SH",
            "entry_rule": "小作文出现后的下一交易日开盘",
            "exit_rule": "第1/5/20个交易日收盘",
            "price_basis": "本地 Tushare 原始日线（未复权）",
            "dedupe_rule": "同一股票同一天多篇小作文合并为一个事件，篇数作为关注强度",
            "causality_note": "相关性和事件后收益仅用于描述历史共变，不证明小作文导致股价变化。",
            "coverage": {
                "candidate_stock_count": len(ranked),
                "priced_stock_count": len(items),
                "event_day_count": total_events,
                "covered_event_day_count": covered_events,
                "event_coverage_percent": round(covered_events * 100 / total_events, 1) if total_events else 0.0,
                "benchmark_available": bool(benchmark),
                "price_start": min((row["date"] for row in all_price_rows), default=None),
                "price_end": max((row["date"] for row in all_price_rows), default=None),
                "sources": sources,
            },
            "items": items,
        }

    def _stock_item(
        self,
        *,
        key: str,
        bucket: Mapping[str, Any],
        mentions: Counter[date],
        series: Sequence[StockDaily],
        benchmark: Sequence[StockDaily],
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        in_window = [row for row in series if start_date <= row.date <= end_date]
        if not in_window:
            in_window = [row for row in series if row.date <= end_date][-30:]
        base_close = float(in_window[0].close) if in_window else float(series[0].close)
        mapped_mentions: Counter[date] = Counter()
        event_rows = []
        benchmark_by_date = {row.date: row for row in benchmark}
        for event_date, count in sorted(mentions.items()):
            future = [row for row in series if row.date > event_date and row.open not in (None, 0)]
            if not future:
                continue
            entry = future[0]
            mapped_mentions[entry.date] += count
            entry_price = float(entry.open)
            benchmark_future = [row for row in benchmark if row.date >= entry.date and row.open not in (None, 0)]
            benchmark_entry = float(benchmark_future[0].open) if benchmark_future else None
            returns: Dict[int, float] = {}
            excess: Dict[int, float] = {}
            for period in _PERIODS:
                if len(future) < period:
                    continue
                exit_row = future[period - 1]
                stock_return = (float(exit_row.close) / entry_price - 1) * 100
                returns[period] = stock_return
                benchmark_exit = next((row for row in benchmark_future if row.date >= exit_row.date), None)
                if benchmark_entry and benchmark_exit and benchmark_exit.close not in (None, 0):
                    excess[period] = stock_return - (float(benchmark_exit.close) / benchmark_entry - 1) * 100
            event_rows.append({
                "event_date": event_date.isoformat(),
                "signal_date": entry.date.isoformat(),
                "mention_count": int(count),
                "returns": returns,
                "excess_returns": excess,
            })

        chart_rows = []
        daily_returns = []
        prior_close = next((float(row.close) for row in reversed(series) if row.date < (in_window[0].date if in_window else start_date)), None)
        for row in in_window:
            close = float(row.close)
            daily_return = (close / prior_close - 1) * 100 if prior_close else 0.0
            daily_returns.append(daily_return)
            chart_rows.append({
                "date": row.date.isoformat(),
                "close": _round(close, 4),
                "price_return": _round((close / base_close - 1) * 100),
                "daily_return": _round(daily_return),
                "mention_count": int(mapped_mentions.get(row.date, 0)),
            })
            prior_close = close

        lead_lag = []
        mention_vector = [float(row["mention_count"]) for row in chart_rows]
        for lag in (0, 1, 3, 5):
            usable = len(chart_rows) - lag
            correlation = _correlation(mention_vector[:usable], daily_returns[lag:]) if usable > 0 else None
            lead_lag.append({
                "lag_sessions": lag,
                "correlation": _round(correlation),
                "sample_count": usable,
            })

        metrics = []
        for period in _PERIODS:
            metrics.append(_metric(
                [event["returns"][period] for event in event_rows if period in event["returns"]],
                [event["excess_returns"][period] for event in event_rows if period in event["excess_returns"]],
                period,
            ))
        primary = next((metric for metric in metrics if metric["period"] == 5), metrics[0])
        positive_counts = sorted(event["mention_count"] for event in event_rows)
        attention_threshold = statistics.median(positive_counts) if positive_counts else None
        attention_comparison = []
        if attention_threshold is not None:
            for level, predicate in (
                ("集中提及", lambda value: value > attention_threshold),
                ("一般提及", lambda value: value <= attention_threshold),
            ):
                values = [
                    float(event["returns"][5])
                    for event in event_rows
                    if 5 in event["returns"] and predicate(event["mention_count"])
                ]
                attention_comparison.append({
                    "level": level,
                    "threshold": int(attention_threshold),
                    "sample_count": len(values),
                    "average_return_5d": _round(statistics.fmean(values)) if values else None,
                    "win_rate_5d": round(sum(value > 0 for value in values) * 100 / len(values), 1) if values else None,
                })
        if primary["sample_count"]:
            direction = "偏正" if float(primary["average_return"] or 0) > 0 else "偏负" if float(primary["average_return"] or 0) < 0 else "中性"
            insight = (
                f"事件后5日平均收益{float(primary['average_return'] or 0):+.2f}%，"
                f"胜率{float(primary['win_rate'] or 0):.1f}%，样本{primary['sample_count']}个，历史表现{direction}。"
            )
        else:
            insight = "当前窗口缺少已经走完5个交易日的事件样本，暂不判断历史方向。"
        return {
            "key": key,
            "ts_code": str(bucket.get("ts_code") or ""),
            "name": str(bucket.get("name") or key),
            "mention_count": int(sum(mentions.values())),
            "event_day_count": len(mentions),
            "covered_event_days": len(event_rows),
            "metrics": metrics,
            "lead_lag": lead_lag,
            "attention_comparison": attention_comparison,
            "series": chart_rows,
            "insight": insight,
            "latest_price_date": chart_rows[-1]["date"] if chart_rows else None,
            "latest_close": chart_rows[-1]["close"] if chart_rows else None,
            "data_source": next((row.data_source for row in reversed(series) if row.data_source), "本地行情库"),
        }
