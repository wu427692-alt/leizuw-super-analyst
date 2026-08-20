# -*- coding: utf-8 -*-
"""Cached market command-center aggregation for the Web home dashboard."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from src.services.financial_data_service import TushareGatewayService
from src.services.investment_monitor_service import InvestmentMonitorService

logger = logging.getLogger(__name__)
_SHANGHAI_TZ = timezone(timedelta(hours=8))

_CN_INDICES = [
    ("000001.SH", "上证指数"), ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"), ("000300.SH", "沪深300"),
]
_GLOBAL_INDICES = [
    ("HSI", "恒生指数", "中国香港"), ("SPX", "标普500", "美国"),
    ("IXIC", "纳斯达克", "美国"), ("DJI", "道琼斯", "美国"),
    ("N225", "日经225", "日本"), ("FTSE", "富时100", "英国"),
]


class HomeDashboardService:
    """Combine live market data and the local intelligence index with a five-minute cache."""

    _cache_lock = threading.Lock()
    _cache_payload: Optional[Dict[str, Any]] = None
    _cache_at = 0.0

    def __init__(self, *, tushare: Optional[TushareGatewayService] = None,
                 monitor: Optional[InvestmentMonitorService] = None,
                 cache_seconds: int = 300):
        self.tushare = tushare or TushareGatewayService()
        self.monitor = monitor or InvestmentMonitorService()
        self.cache_seconds = max(60, min(int(cache_seconds), 1800))

    def dashboard(self, *, force: bool = False) -> Dict[str, Any]:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache_payload
            if not force and cached is not None and now - self._cache_at < self.cache_seconds:
                return {**cached, "cache": {"hit": True, "ttl_seconds": self.cache_seconds,
                                             "age_seconds": round(now - self._cache_at, 1)}}
        payload = self._build()
        with self._cache_lock:
            self.__class__._cache_payload = payload
            self.__class__._cache_at = time.monotonic()
        return {**payload, "cache": {"hit": False, "ttl_seconds": self.cache_seconds, "age_seconds": 0}}

    def _build(self) -> Dict[str, Any]:
        now = datetime.now(_SHANGHAI_TZ)
        end = now.strftime("%Y%m%d")
        start = (now - timedelta(days=20)).strftime("%Y%m%d")
        warnings: List[str] = []
        index_jobs = [
            ("index_daily", code, name, "中国A股") for code, name in _CN_INDICES
        ] + [
            ("index_global", code, name, region) for code, name, region in _GLOBAL_INDICES
        ]
        # Index endpoints are independent. Keeping a small pool removes avoidable
        # network serialization without creating an unbounded upstream burst.
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="home-index") as executor:
            indices = list(executor.map(
                lambda args: self._index_item(args[0], args[1], args[2], start, end, warnings, region=args[3]),
                index_jobs,
            ))
        cn_indices = indices[:len(_CN_INDICES)]
        global_indices = indices[len(_CN_INDICES):]
        trade_date = next((item.get("trade_date") for item in cn_indices if item.get("trade_date")), end)

        def intelligence_data() -> Dict[str, Any]:
            try:
                return self.monitor.dashboard(days=7)
            except Exception as exc:  # noqa: BLE001 - dashboard should degrade per source.
                logger.warning("Home dashboard monitor aggregation failed: %s", type(exc).__name__)
                warnings.append("投资情报暂不可用")
                return {"watchlist": [], "latest_events": [], "summary": {}}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="home-summary") as executor:
            breadth_future = executor.submit(self._breadth, str(trade_date), warnings)
            northbound_future = executor.submit(self._northbound, start, end, warnings)
            intelligence_future = executor.submit(intelligence_data)
            breadth = breadth_future.result()
            northbound = northbound_future.result()
            intelligence = intelligence_future.result()
        watchlist = self._watchlist(intelligence, start, end, warnings)
        return {
            "generated_at": now.isoformat(), "market_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trade_date": str(trade_date), "cn_indices": cn_indices,
            "global_indices": global_indices, "breadth": breadth, "northbound": northbound,
            "watchlist": watchlist, "latest_events": (intelligence.get("latest_events") or [])[:12],
            "intelligence_summary": intelligence.get("summary") or {}, "warnings": warnings,
        }

    def _index_item(self, api_name: str, code: str, name: str, start: str, end: str,
                    warnings: List[str], *, region: str = "中国A股") -> Dict[str, Any]:
        try:
            result = self.tushare.query(api_name, params={"ts_code": code, "start_date": start, "end_date": end})
            rows = sorted(result["rows"], key=lambda row: str(row.get("trade_date") or ""))
            latest = rows[-1] if rows else {}
            amount = latest.get("amount")
            amount_yi = None
            if api_name == "index_daily" and amount not in (None, ""):
                # Tushare index_daily.amount is denominated in CNY thousands.
                amount_yi = round(float(amount) / 100_000, 2)
            return {"code": code, "name": name, "region": region,
                    "trade_date": latest.get("trade_date"), "close": latest.get("close"),
                    "change": latest.get("change"), "change_pct": latest.get("pct_chg"),
                    "open": latest.get("open"), "high": latest.get("high"), "low": latest.get("low"),
                    "amount": amount, "amount_yi": amount_yi,
                    "history": [{"date": row.get("trade_date"), "value": row.get("close")} for row in rows[-12:]]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard index failed api=%s code=%s error=%s", api_name, code, type(exc).__name__)
            warnings.append(f"{name}行情暂不可用")
            return {"code": code, "name": name, "region": region, "history": []}

    def _breadth(self, trade_date: str, warnings: List[str]) -> Dict[str, Any]:
        try:
            rows = self.tushare.query(
                "daily", params={"trade_date": trade_date}, fields=["ts_code", "pct_chg", "amount", "close"],
            )["rows"]
            changes = [float(row["pct_chg"]) for row in rows if row.get("pct_chg") is not None]
            buckets = [
                ("跌超7%", lambda x: x < -7), ("跌3-7%", lambda x: -7 <= x < -3),
                ("跌0-3%", lambda x: -3 <= x < 0), ("平盘", lambda x: x == 0),
                ("涨0-3%", lambda x: 0 < x <= 3), ("涨3-7%", lambda x: 3 < x <= 7),
                ("涨超7%", lambda x: x > 7),
            ]
            return {"up": sum(value > 0 for value in changes), "down": sum(value < 0 for value in changes),
                    "flat": sum(value == 0 for value in changes), "limit_up": sum(value >= 9.5 for value in changes),
                    "limit_down": sum(value <= -9.5 for value in changes), "total": len(changes),
                    "distribution": [{"label": label, "count": sum(test(value) for value in changes)}
                                     for label, test in buckets]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard breadth failed: %s", type(exc).__name__)
            warnings.append("全市场涨跌分布暂不可用")
            return {"up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0,
                    "total": 0, "distribution": []}

    def _northbound(self, start: str, end: str, warnings: List[str]) -> Dict[str, Any]:
        try:
            rows = self.tushare.query("moneyflow_hsgt", params={"start_date": start, "end_date": end})["rows"]
            rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
            latest = rows[-1] if rows else {}
            value = float(latest.get("north_money")) if latest.get("north_money") not in (None, "") else None
            return {"trade_date": latest.get("trade_date"), "north_money": value,
                    "north_money_yi": round(value / 10000, 2) if value is not None else None,
                    "south_money": latest.get("south_money")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard northbound failed: %s", type(exc).__name__)
            warnings.append("沪深港通资金暂不可用")
            return {}

    def _watchlist(self, intelligence: Dict[str, Any], start: str, end: str,
                   warnings: List[str]) -> List[Dict[str, Any]]:
        latest_events = intelligence.get("latest_events") or []
        result = []
        for card in intelligence.get("watchlist") or []:
            symbol = str(card.get("symbol") or "")
            try:
                rows = self.tushare.query(
                    "daily", params={"ts_code": symbol, "start_date": start, "end_date": end},
                    fields=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
                            "change", "pct_chg", "vol", "amount"],
                )["rows"]
                rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
                history = [{"date": row.get("trade_date"), "value": row.get("close")} for row in rows[-12:]]
                latest_daily = rows[-1] if rows else {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Home dashboard watchlist trend failed symbol=%s error=%s", symbol, type(exc).__name__)
                warnings.append(f"{card.get('name') or symbol}趋势暂不可用")
                history = []
                latest_daily = {}
            related = [event for event in latest_events if symbol in (event.get("symbols") or [])]
            try:
                detail = self.monitor.symbol_detail(symbol, days=30)
                related = detail.get("events") or related
            except (AttributeError, NotImplementedError):
                # Lightweight monitor adapters used by integrations may only expose dashboard().
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("Home dashboard symbol intelligence failed symbol=%s error=%s",
                               symbol, type(exc).__name__)
            compact = getattr(self.monitor, "compact_event", lambda event: event)
            catalyst = next((compact(event) for event in related if event.get("sentiment") == "bullish"), None)
            risk = next((compact(event) for event in related if event.get("sentiment") == "bearish"), None)
            institution = next((compact(event) for event in related if event.get("perspective") == "institution"), None)
            daily_amount = latest_daily.get("amount")
            daily_quote = {
                "current_price": latest_daily.get("close"),
                "change": latest_daily.get("change"),
                "change_percent": latest_daily.get("pct_chg"),
                "open": latest_daily.get("open"), "high": latest_daily.get("high"),
                "low": latest_daily.get("low"), "prev_close": latest_daily.get("pre_close"),
                "volume": latest_daily.get("vol"),
                # Tushare daily.amount uses CNY thousands; the UI formats yuan.
                "amount": float(daily_amount) * 1000 if daily_amount not in (None, "") else None,
                "update_time": latest_daily.get("trade_date"),
            } if latest_daily else None
            result.append({**card, "latest_quote": daily_quote or card.get("latest_quote"),
                           "history": history, "latest_catalyst": catalyst,
                           "latest_risk": risk, "latest_institution": institution,
                           "latest_events": [compact(event) for event in related[:4]]})
        return result
