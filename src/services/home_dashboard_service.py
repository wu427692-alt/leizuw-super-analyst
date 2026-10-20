# -*- coding: utf-8 -*-
"""Cached market command-center aggregation for the Web home dashboard."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, time as clock_time, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional

from data_provider.base import normalize_stock_code
from src.data.stock_index_loader import get_index_stock_name
from src.services.financial_data_service import TushareGatewayService
from src.services.investment_monitor_service import InvestmentMonitorService
from src.services.market_data_service import INDEX_NAMES, configured_index_symbols
from src.services.stock_code_utils import _infer_cn_exchange

logger = logging.getLogger(__name__)
_SHANGHAI_TZ = timezone(timedelta(hours=8))

def _cn_indices() -> List[tuple[str, str]]:
    return [(code, INDEX_NAMES.get(code, code)) for code in configured_index_symbols()]
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
    _refresh_thread: Optional[threading.Thread] = None
    # Public per-symbol evidence is reusable across requests; membership and
    # live prices are always read separately for the current request.
    _watchlist_facts_lock = threading.Lock()
    _watchlist_facts: Dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}
    _watchlist_facts_pending: Dict[tuple[str, str], Future] = {}
    _watchlist_facts_ttl_seconds = 15
    _watchlist_facts_limit = 256

    def __init__(self, *, tushare: Optional[TushareGatewayService] = None,
                 monitor: Optional[InvestmentMonitorService] = None,
                 cache_seconds: int = 300,
                 background_refresh: Optional[bool] = None):
        injected_dependencies = tushare is not None or monitor is not None
        self.tushare = tushare or TushareGatewayService()
        # This service only reads monitor facts; source registration belongs to
        # collection/worker startup, not every homepage request.
        self.monitor = monitor or InvestmentMonitorService(initialize_sources=False)
        self.cache_seconds = max(60, min(int(cache_seconds), 1800))
        self.background_refresh = (
            not injected_dependencies if background_refresh is None else bool(background_refresh)
        )

    def dashboard(self, *, force: bool = False, refresh: bool = False) -> Dict[str, Any]:
        now = time.monotonic()
        cached_response: Optional[Dict[str, Any]] = None
        with self._cache_lock:
            cached = self._cache_payload
            if not force and cached is not None and now - self._cache_at < self.cache_seconds:
                cached_response = {**cached, "cache": {"hit": True, "refreshing": refresh,
                                                        "ttl_seconds": self.cache_seconds,
                                                        "age_seconds": round(now - self._cache_at, 1)}}
        if cached_response is not None:
            if self.background_refresh:
                cached_response = self._overlay_local_index_quotes(cached_response)
            if refresh:
                self._schedule_refresh()
            return cached_response
        if not force and self.background_refresh:
            persisted = self._load_persistent_cache()
            if persisted is not None:
                persisted = self._overlay_local_index_quotes(persisted)
                with self._cache_lock:
                    self.__class__._cache_payload = persisted
                    self.__class__._cache_at = time.monotonic()
                self._schedule_refresh()
                return {**persisted, "cache": {
                    "hit": True, "persistent": True, "refreshing": True,
                    "ttl_seconds": self.cache_seconds, "age_seconds": None,
                }}
            payload = self._local_snapshot()
            with self._cache_lock:
                self.__class__._cache_payload = payload
                self.__class__._cache_at = time.monotonic()
            self._schedule_refresh()
            return {**payload, "cache": {
                "hit": False, "local_snapshot": True, "refreshing": True,
                "ttl_seconds": self.cache_seconds, "age_seconds": 0,
            }}
        payload = self._build()
        self._save_cache(payload)
        return {**payload, "cache": {"hit": False, "ttl_seconds": self.cache_seconds, "age_seconds": 0}}

    def watchlist_for_symbols(
        self, symbols: List[str], *, cached_cards: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Build a request-owned watchlist from local data, never the global stock list.

        Public per-symbol price/history cards may be reused, but this result must
        not enter the shared dashboard cache. Page entry does not fetch missing
        quotes, perform stock-name lookups upstream, or start analysis jobs.
        """
        from src.services.market_data_service import MarketDataService

        codes = list(dict.fromkeys(
            normalize_stock_code(symbol) for symbol in symbols if str(symbol or "").strip()
        ))
        if not codes:
            return []
        cached = {
            normalize_stock_code(str(card.get("symbol") or "")): card
            for card in (cached_cards or [])
        }
        quotes = {
            normalize_stock_code(str(row.get("stock_code") or "")): row
            for row in MarketDataService().latest_quotes(codes, refresh_missing=False)
        }
        result = []
        for code in codes:
            exchange = _infer_cn_exchange(code)
            symbol = f"{code}.{exchange}" if exchange else code
            previous = cached.get(code) or {}
            quote = self._newest_watchlist_quote(quotes.get(code), previous.get("latest_quote"))
            name = (
                (quote or {}).get("stock_name") or get_index_stock_name(code)
                or previous.get("name") or symbol
            )
            facts = self._watchlist_symbol_facts(symbol)
            card = InvestmentMonitorService._symbol_card_from_stats(symbol, name, facts.pop("stats"))
            result.append({
                **card,
                **facts,
                "latest_quote": quote,
                "history": list(previous.get("history") or []),
            })
        return result

    def _watchlist_symbol_facts(self, symbol: str) -> Dict[str, Any]:
        """Share bounded local evidence reads without caching a user's list.

        A homepage cache hit previously still hydrated 200 events per stock on
        every request. Coalesce concurrent misses too, so several visitors do
        not all repeat that SQL/JSON work. Failed reads never become cache hits.
        """
        database_key = str(getattr(self.monitor.repo.db, "_db_url", id(self.monitor.repo.db)))
        key = (database_key, symbol)
        with self._watchlist_facts_lock:
            cached = self._watchlist_facts.get(key)
            if cached and time.monotonic() - cached[0] < self._watchlist_facts_ttl_seconds:
                return deepcopy(cached[1])
            pending = self._watchlist_facts_pending.get(key)
            owns_read = pending is None
            if owns_read:
                pending = Future()
                self._watchlist_facts_pending[key] = pending
        if not owns_read:
            return deepcopy(pending.result())
        try:
            stats = self.monitor.repo.symbol_dashboard_stats(symbol=symbol, days=7)
            events = self.monitor.list_events(days=30, symbol=symbol, page=1, page_size=200)["items"]
            compact = self.monitor.compact_event
            facts = {
                "stats": stats,
                "latest_catalyst": next((compact(event) for event in events
                                         if event.get("sentiment") == "bullish"), None),
                "latest_risk": next((compact(event) for event in events
                                     if event.get("sentiment") == "bearish"), None),
                "latest_institution": next((compact(event) for event in events
                                            if event.get("perspective") == "institution"), None),
                "latest_events": [compact(event) for event in events[:4]],
            }
            with self._watchlist_facts_lock:
                self._watchlist_facts[key] = (time.monotonic(), facts)
                if len(self._watchlist_facts) > self._watchlist_facts_limit:
                    oldest = min(self._watchlist_facts, key=lambda item: self._watchlist_facts[item][0])
                    self._watchlist_facts.pop(oldest, None)
            pending.set_result(facts)
            return deepcopy(facts)
        except BaseException as exc:
            # Wake every waiting request even when the owning read is cancelled.
            pending.set_exception(exc)
            raise
        finally:
            with self._watchlist_facts_lock:
                self._watchlist_facts_pending.pop(key, None)

    @staticmethod
    def _newest_watchlist_quote(*quotes: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Choose a whole dated quote, not a mixture of two sessions' fields.

        The local DB may lag behind a completed daily row in the shared cache.
        Polling staleness is not chronological order: today's closing quote is
        newer than today's afternoon snapshot even when both polls have aged.
        """
        selected: Optional[Dict[str, Any]] = None
        selected_at: Optional[datetime] = None
        for quote in quotes:
            if not isinstance(quote, dict):
                continue
            price = quote.get("current_price")
            if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                continue
            quote_at = HomeDashboardService._parse_index_card_time(quote)
            if (selected is None
                or (quote_at is not None and (selected_at is None or quote_at > selected_at))
                or (quote_at == selected_at and selected.get("is_stale") and not quote.get("is_stale"))):
                selected, selected_at = quote, quote_at
        # A response consumer must not mutate the shared dashboard's card.
        return dict(selected) if selected is not None else None

    @staticmethod
    def _overlay_local_index_quotes(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Overlay cached daily cards with newer SQLite realtime snapshots.

        The full dashboard cache intentionally survives process restarts, but
        its index cards must not show the prior close while today's minute K
        line is already live.  This reconciliation is local-only and therefore
        adds no upstream latency to page entry.
        """
        items = payload.get("cn_indices")
        if not isinstance(items, list) or not items:
            return payload
        try:
            from src.repositories.market_data_repo import MarketDataRepository

            codes = [str(item.get("code") or "").upper() for item in items if isinstance(item, dict)]
            latest = MarketDataRepository().latest_index_bars(codes, frequency="1SEC")
        except Exception as exc:  # noqa: BLE001 - cached payload remains valid.
            logger.info("Home dashboard local index overlay unavailable: %s", type(exc).__name__)
            return payload

        changed = False
        merged_items: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                merged_items.append(item)
                continue
            code = str(item.get("code") or "").upper()
            row = latest.get(code)
            if row is None:
                merged_items.append(item)
                continue
            cached_time = HomeDashboardService._parse_index_card_time(item)
            if cached_time is not None and row.timestamp <= cached_time:
                merged_items.append(item)
                continue
            history = list(item.get("history") or [])
            history.append({"date": row.timestamp.isoformat(timespec="seconds"), "value": row.close})
            merged_items.append({
                **item,
                "trade_date": row.timestamp.strftime("%Y%m%d"),
                "close": row.close,
                "change_pct": row.pct_chg,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "amount": row.amount,
                "source": row.data_source or "local.sqlite",
                "update_time": row.timestamp.isoformat(timespec="seconds"),
                "history": history[-12:],
            })
            changed = True
        return {**payload, "cn_indices": merged_items} if changed else payload

    @staticmethod
    def _parse_index_card_time(item: Dict[str, Any]) -> Optional[datetime]:
        raw = str(item.get("update_time") or item.get("trade_date") or "").strip()
        if not raw:
            return None
        if len(raw) == 8 and raw.isdigit():
            try:
                return datetime.strptime(raw, "%Y%m%d").replace(hour=15)
            except ValueError:
                return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if len(raw) == 10:
                # Date-only index cards are completed daily bars, not midnight
                # quotes: a 14:53 snapshot must not replace their final close.
                return parsed.replace(hour=15)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone(timedelta(hours=8)))
            return parsed.replace(tzinfo=None)
        except ValueError:
            return None

    def _schedule_refresh(self) -> None:
        with self._cache_lock:
            current = self.__class__._refresh_thread
            if current is not None and current.is_alive():
                return
            thread = threading.Thread(
                target=self._refresh_cache,
                name="home-dashboard-refresh",
                daemon=True,
            )
            self.__class__._refresh_thread = thread
            thread.start()

    @staticmethod
    def _newest_index_card_row(live: Any, daily: Any) -> Any:
        if live is None:
            return daily
        if daily is None:
            return live
        daily_close_at = daily.timestamp.replace(hour=15, minute=0, second=0, microsecond=0)
        return live if live.timestamp > daily_close_at else daily

    def _refresh_cache(self) -> None:
        try:
            self._save_cache(self._build())
        except Exception as exc:  # noqa: BLE001 - the persisted/local snapshot remains usable.
            logger.warning("Home dashboard background refresh failed: %s", type(exc).__name__)

    def _save_cache(self, payload: Dict[str, Any]) -> None:
        with self._cache_lock:
            self.__class__._cache_payload = payload
            self.__class__._cache_at = time.monotonic()
        try:
            path = self._cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Home dashboard cache persistence failed: %s", type(exc).__name__)

    @staticmethod
    def _cache_path() -> Path:
        configured = str(os.getenv("HOME_DASHBOARD_CACHE_PATH") or "").strip()
        if configured:
            return Path(configured).expanduser()
        database_path = Path(os.getenv("DATABASE_PATH") or "data/stock_analysis.db").expanduser()
        return database_path.parent / "cache" / "home_dashboard.json"

    def _load_persistent_cache(self) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(self._cache_path().read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) and payload.get("generated_at") else None
        except (OSError, TypeError, ValueError):
            return None

    def _local_snapshot(self) -> Dict[str, Any]:
        """Return only already persisted facts while remote refresh runs."""
        now = datetime.now(_SHANGHAI_TZ)
        warnings = ["完整市场数据正在后台刷新，当前先展示本地事实快照"]
        cn_indices: List[Dict[str, Any]] = []
        try:
            from src.repositories.market_data_repo import MarketDataRepository

            repo = MarketDataRepository()
            end = now.replace(tzinfo=None)
            start = end - timedelta(days=20)
            latest_ticks = repo.latest_index_bars(configured_index_symbols(), frequency="1SEC")
            for code, name in _cn_indices():
                rows = repo.index_range(code, start, end, frequency="1D")
                daily = rows[-1] if rows else None
                live = latest_ticks.get(code)
                latest = self._newest_index_card_row(live, daily)
                cn_indices.append({
                    "code": code, "name": name, "region": "中国A股",
                    "trade_date": latest.timestamp.strftime("%Y%m%d") if latest else None,
                    "close": latest.close if latest else None,
                    "change_pct": latest.pct_chg if latest else None,
                    "open": latest.open if latest else None,
                    "high": latest.high if latest else None,
                    "low": latest.low if latest else None,
                    "amount": latest.amount if latest else None,
                    "source": latest.data_source if latest else None,
                    "update_time": (
                        (latest.timestamp.replace(hour=15, minute=0, second=0, microsecond=0)
                         if latest is daily else latest.timestamp).isoformat(timespec="seconds")
                        if latest else None
                    ),
                    "history": [{"date": row.timestamp.strftime("%Y%m%d"), "value": row.close}
                                for row in rows[-12:]],
                })
        except Exception as exc:  # noqa: BLE001 - cold-start fallback remains valid without market DB.
            logger.warning("Home dashboard local index snapshot failed: %s", type(exc).__name__)
            cn_indices = [{"code": code, "name": name, "region": "中国A股", "history": []}
                          for code, name in _cn_indices()]

        try:
            intelligence = self.monitor.dashboard(days=7)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard local intelligence snapshot failed: %s", type(exc).__name__)
            intelligence = {"watchlist": [], "latest_events": [], "summary": {}}
        trade_date = next((item.get("trade_date") for item in cn_indices if item.get("trade_date")), now.strftime("%Y%m%d"))
        breadth = self._empty_distribution(str(trade_date), "当日全市场事实正在后台刷新")
        sectors = self._empty_distribution(str(trade_date), "当日行业板块事实正在后台刷新")
        sectors.update({"leaders": [], "laggards": []})
        return {
            "generated_at": now.isoformat(), "market_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trade_date": str(trade_date), "cn_indices": cn_indices, "global_indices": [],
            "breadth": breadth, "sector_distribution": sectors, "northbound": {},
            "watchlist": intelligence.get("watchlist") or [],
            "latest_events": (intelligence.get("latest_events") or [])[:12],
            "latest_news": (intelligence.get("latest_news") or [])[:12],
            "pinned_news": (intelligence.get("pinned_news") or [])[:2],
            "intelligence_summary": intelligence.get("summary") or {}, "warnings": warnings,
        }

    def _build(self) -> Dict[str, Any]:
        now = datetime.now(_SHANGHAI_TZ)
        end = now.strftime("%Y%m%d")
        start = (now - timedelta(days=20)).strftime("%Y%m%d")
        warnings: List[str] = []
        cn_index_list = _cn_indices()
        index_jobs = [
            ("index_daily", code, name, "中国A股") for code, name in cn_index_list
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
        cn_indices = indices[:len(cn_index_list)]
        global_indices = indices[len(cn_index_list):]
        trade_date = next((item.get("trade_date") for item in cn_indices if item.get("trade_date")), end)
        is_open_today = self._is_trading_day(end)
        # On weekends and exchange holidays the latest index session is the
        # authoritative market date. Reuse it for breadth and sector queries
        # instead of asking upstream providers for a non-existent calendar day.
        market_summary_date = end if is_open_today else str(trade_date)

        def intelligence_data() -> Dict[str, Any]:
            try:
                return self.monitor.dashboard(days=7)
            except Exception as exc:  # noqa: BLE001 - dashboard should degrade per source.
                logger.warning("Home dashboard monitor aggregation failed: %s", type(exc).__name__)
                warnings.append("投资情报暂不可用")
                return {"watchlist": [], "latest_events": [], "summary": {}}

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="home-summary") as executor:
            breadth_future = executor.submit(self._breadth, market_summary_date, now, is_open_today, warnings)
            sectors_future = executor.submit(self._sector_distribution, market_summary_date, now, is_open_today, warnings)
            northbound_future = executor.submit(self._northbound, start, end, warnings)
            intelligence_future = executor.submit(intelligence_data)
            breadth = breadth_future.result()
            sector_distribution = sectors_future.result()
            northbound = northbound_future.result()
            intelligence = intelligence_future.result()
        watchlist = self._watchlist(intelligence, start, end, warnings)
        return {
            "generated_at": now.isoformat(), "market_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trade_date": str(trade_date), "cn_indices": cn_indices,
            "global_indices": global_indices, "breadth": breadth,
            "sector_distribution": sector_distribution, "northbound": northbound,
            "watchlist": watchlist, "latest_events": (intelligence.get("latest_events") or [])[:12],
            "latest_news": (intelligence.get("latest_news") or [])[:12],
            "pinned_news": (intelligence.get("pinned_news") or [])[:2],
            "intelligence_summary": intelligence.get("summary") or {}, "warnings": warnings,
        }

    def _index_item(self, api_name: str, code: str, name: str, start: str, end: str,
                    warnings: List[str], *, region: str = "中国A股") -> Dict[str, Any]:
        try:
            result = self.tushare.query(api_name, params={"ts_code": code, "start_date": start, "end_date": end})
            rows = sorted(result["rows"], key=lambda row: str(row.get("trade_date") or ""))
            if api_name == "index_daily" and rows:
                try:
                    from src.repositories.market_data_repo import MarketDataRepository
                    persisted = []
                    for row in rows:
                        raw_date = str(row.get("trade_date") or "")
                        if len(raw_date) != 8:
                            continue
                        persisted.append({
                            "timestamp": datetime.strptime(raw_date, "%Y%m%d"),
                            "open": row.get("open"), "high": row.get("high"),
                            "low": row.get("low"), "close": row.get("close"),
                            "volume": row.get("vol"), "amount": row.get("amount"),
                            "change_percent": row.get("pct_chg"),
                        })
                    MarketDataRepository().upsert_index(
                        code, persisted, frequency="1D", source="tushare.index_daily",
                    )
                except Exception as exc:  # noqa: BLE001 - dashboard facts remain usable if cache write fails.
                    logger.info("Home dashboard index cache write failed code=%s error=%s", code, type(exc).__name__)
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
                    "source": f"tushare.{api_name}", "update_time": latest.get("trade_date"),
                    "history": [{"date": row.get("trade_date"), "value": row.get("close")} for row in rows[-12:]]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard index failed api=%s code=%s error=%s", api_name, code, type(exc).__name__)
            warnings.append(f"{name}行情暂不可用")
            return {"code": code, "name": name, "region": region, "history": []}

    def _is_trading_day(self, trade_date: str) -> bool:
        """Use the exchange calendar so a holiday never inherits the prior session snapshot."""
        try:
            rows = self.tushare.query(
                "trade_cal",
                params={"exchange": "SSE", "start_date": trade_date, "end_date": trade_date},
                fields=["cal_date", "is_open"],
            )["rows"]
            return any(str(row.get("cal_date") or "") == trade_date and int(row.get("is_open") or 0) == 1
                       for row in rows)
        except Exception as exc:  # noqa: BLE001 - conservative failure is intentional.
            logger.warning("Home dashboard trading-day check failed: %s", type(exc).__name__)
            return False

    @staticmethod
    def _empty_distribution(trade_date: str, reason: str) -> Dict[str, Any]:
        return {
            "available": False, "trade_date": trade_date, "updated_at": None,
            "source": None, "reason": reason, "up": 0, "down": 0, "flat": 0,
            "limit_up": 0, "limit_down": 0, "total": 0, "distribution": [],
        }

    @staticmethod
    def _change_distribution(changes: List[float]) -> List[Dict[str, Any]]:
        buckets = [
            ("跌超7%", lambda x: x < -7), ("跌3-7%", lambda x: -7 <= x < -3),
            ("跌0-3%", lambda x: -3 <= x < 0), ("平盘", lambda x: x == 0),
            ("涨0-3%", lambda x: 0 < x <= 3), ("涨3-7%", lambda x: 3 < x <= 7),
            ("涨超7%", lambda x: x > 7),
        ]
        return [{"label": label, "count": sum(test(value) for value in changes)}
                for label, test in buckets]

    @staticmethod
    def _sector_change_distribution(changes: List[float]) -> List[Dict[str, Any]]:
        buckets = [
            ("跌超3%", lambda x: x < -3), ("跌1-3%", lambda x: -3 <= x < -1),
            ("跌0-1%", lambda x: -1 <= x < 0), ("平盘", lambda x: x == 0),
            ("涨0-1%", lambda x: 0 < x <= 1), ("涨1-3%", lambda x: 1 < x <= 3),
            ("涨超3%", lambda x: x > 3),
        ]
        return [{"label": label, "count": sum(test(value) for value in changes)}
                for label, test in buckets]

    @staticmethod
    def _live_snapshot_allowed(now: datetime, is_open_today: bool) -> bool:
        # Before call auction there is no factual current-day breadth. After the close the
        # same-day closing snapshot remains valid for the rest of the trading date.
        return is_open_today and now.time().replace(tzinfo=None) >= clock_time(9, 15)

    @staticmethod
    def _sina_stock_snapshot(trade_date: str, now: datetime) -> Dict[str, Any]:
        import akshare as ak
        import pandas as pd

        frame = ak.stock_zh_a_spot()
        required = {"代码", "名称", "最新价", "昨收", "涨跌幅", "成交额"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            raise ValueError("Sina A-share snapshot is empty or missing required fields")
        df = frame.copy()
        for column in ("最新价", "昨收", "涨跌幅", "成交额"):
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.dropna(subset=["最新价", "昨收", "涨跌幅"])
        df = df[(df["最新价"] > 0) & (df["昨收"] > 0) & (df["成交额"] > 0)]
        changes = [float(value) for value in df["涨跌幅"].tolist()]

        limit_up = 0
        limit_down = 0
        for row in df[["代码", "名称", "最新价", "昨收"]].itertuples(index=False, name=None):
            code, name, current_price, pre_close = row
            pure_code = str(code or "").lower().removeprefix("sh").removeprefix("sz").removeprefix("bj")
            if str(code or "").lower().startswith("bj"):
                ratio = 0.30
            elif pure_code.startswith(("688", "300")):
                ratio = 0.20
            elif "ST" in str(name or "").upper():
                ratio = 0.05
            else:
                ratio = 0.10
            upper = math.floor(float(pre_close) * (1 + ratio) * 100 + 0.5) / 100
            lower = math.floor(float(pre_close) * (1 - ratio) * 100 + 0.5) / 100
            limit_up += abs(float(current_price) - upper) <= 0.005
            limit_down += abs(float(current_price) - lower) <= 0.005

        latest_clock = ""
        if "时间戳" in df.columns:
            latest_clock = max((str(value) for value in df["时间戳"].dropna().tolist()), default="")
        date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        updated_at = f"{date_text}T{latest_clock}" if latest_clock else now.isoformat()
        return {
            "available": True, "trade_date": trade_date, "updated_at": updated_at,
            "source": "akshare.sina_a_spot", "reason": None,
            "up": sum(value > 0 for value in changes),
            "down": sum(value < 0 for value in changes),
            "flat": sum(value == 0 for value in changes),
            "limit_up": int(limit_up), "limit_down": int(limit_down), "total": len(changes),
            "distribution": HomeDashboardService._change_distribution(changes),
        }

    def _breadth(self, trade_date: str, now: datetime, is_open_today: bool,
                 warnings: List[str]) -> Dict[str, Any]:
        if self._live_snapshot_allowed(now, is_open_today):
            try:
                return self._sina_stock_snapshot(trade_date, now)
            except Exception as exc:  # noqa: BLE001 - Tushare current-day close remains a fallback.
                logger.warning("Home dashboard Sina breadth failed: %s", type(exc).__name__)
        try:
            rows = self.tushare.query(
                "daily", params={"trade_date": trade_date},
                fields=["ts_code", "trade_date", "pct_chg", "amount", "close"],
            )["rows"]
            rows = [row for row in rows if str(row.get("trade_date") or "") == trade_date]
            changes = [float(row["pct_chg"]) for row in rows if row.get("pct_chg") is not None]
            if not changes:
                raise ValueError("Tushare has not published current-day daily rows")
            return {"available": True, "up": sum(value > 0 for value in changes), "down": sum(value < 0 for value in changes),
                    "flat": sum(value == 0 for value in changes), "limit_up": sum(value >= 9.5 for value in changes),
                    "limit_down": sum(value <= -9.5 for value in changes), "total": len(changes),
                    "trade_date": trade_date, "updated_at": f"{trade_date}T15:00:00",
                    "source": "tushare.daily", "reason": None,
                    "distribution": self._change_distribution(changes)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard breadth failed: %s", type(exc).__name__)
            warnings.append("当日市场广度尚未取得，已停止展示旧交易日数据")
            return self._empty_distribution(trade_date, "当日全市场行情尚未发布或实时源暂不可用")

    @staticmethod
    def _sina_sector_snapshot(trade_date: str, now: datetime) -> Dict[str, Any]:
        import akshare as ak
        import pandas as pd

        frame = ak.stock_sector_spot(indicator="行业")
        required = {"板块", "涨跌幅"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            raise ValueError("Sina industry snapshot is empty or missing required fields")
        df = frame.copy()
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
        if "公司家数" in df.columns:
            df["公司家数"] = pd.to_numeric(df["公司家数"], errors="coerce")
        df = df.dropna(subset=["涨跌幅"])
        rows = [{
            "name": str(row.get("板块") or "").strip(),
            "change_pct": float(row.get("涨跌幅")),
            "company_count": int(row.get("公司家数")) if pd.notna(row.get("公司家数")) else None,
            "leader": str(row.get("股票名称") or "").strip() or None,
        } for _, row in df.iterrows() if str(row.get("板块") or "").strip()]
        if not rows:
            raise ValueError("Sina industry snapshot has no normalized rows")
        ordered = sorted(rows, key=lambda item: item["change_pct"], reverse=True)
        changes = [item["change_pct"] for item in rows]
        return {
            "available": True, "trade_date": trade_date, "updated_at": now.isoformat(),
            "source": "akshare.sina_sector_spot", "reason": None,
            "up": sum(value > 0 for value in changes),
            "down": sum(value < 0 for value in changes),
            "flat": sum(value == 0 for value in changes), "total": len(changes),
            "distribution": HomeDashboardService._sector_change_distribution(changes),
            "leaders": ordered[:6], "laggards": list(reversed(ordered[-6:])),
        }

    def _sector_distribution(self, trade_date: str, now: datetime, is_open_today: bool,
                             warnings: List[str]) -> Dict[str, Any]:
        if self._live_snapshot_allowed(now, is_open_today):
            try:
                return self._sina_sector_snapshot(trade_date, now)
            except Exception as exc:  # noqa: BLE001 - current-day Tushare data remains a fallback.
                logger.warning("Home dashboard Sina sector snapshot failed: %s", type(exc).__name__)
        for api_name, fields in (
            ("moneyflow_ind_ths", ["trade_date", "industry", "pct_change"]),
            ("moneyflow_ind_dc", ["trade_date", "content_type", "name", "pct_change"]),
        ):
            try:
                rows = self.tushare.query(api_name, params={"trade_date": trade_date}, fields=fields)["rows"]
                if api_name == "moneyflow_ind_dc":
                    rows = [row for row in rows if str(row.get("content_type") or "") == "行业"]
                normalized = [{
                    "name": str(row.get("industry") or row.get("name") or "").strip(),
                    "change_pct": float(row.get("pct_change")),
                    "company_count": None, "leader": None,
                } for row in rows
                    if str(row.get("trade_date") or "") == trade_date
                    and row.get("pct_change") is not None
                    and str(row.get("industry") or row.get("name") or "").strip()]
                if not normalized:
                    continue
                ordered = sorted(normalized, key=lambda item: item["change_pct"], reverse=True)
                changes = [item["change_pct"] for item in normalized]
                return {
                    "available": True, "trade_date": trade_date,
                    "updated_at": f"{trade_date}T15:00:00", "source": f"tushare.{api_name}",
                    "reason": None, "up": sum(value > 0 for value in changes),
                    "down": sum(value < 0 for value in changes),
                    "flat": sum(value == 0 for value in changes), "total": len(changes),
                    "distribution": self._sector_change_distribution(changes),
                    "leaders": ordered[:6], "laggards": list(reversed(ordered[-6:])),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Home dashboard sector fallback failed api=%s error=%s",
                               api_name, type(exc).__name__)
        warnings.append("当日板块涨跌分布尚未取得，已停止展示旧交易日数据")
        empty = self._empty_distribution(trade_date, "当日行业板块行情尚未发布或实时源暂不可用")
        empty.update({"leaders": [], "laggards": []})
        return empty

    def _northbound(self, start: str, end: str, warnings: List[str]) -> Dict[str, Any]:
        try:
            rows = self.tushare.query("moneyflow_hsgt", params={"start_date": start, "end_date": end})["rows"]
            rows = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
            latest = rows[-1] if rows else {}
            value = float(latest.get("north_money")) if latest.get("north_money") not in (None, "") else None
            return {"trade_date": latest.get("trade_date"), "north_money": value,
                    "north_money_yi": round(value / 10000, 2) if value is not None else None,
                    "south_money": latest.get("south_money"), "source": "tushare.moneyflow_hsgt"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard northbound failed: %s", type(exc).__name__)
            warnings.append("沪深港通资金暂不可用")
            return {}

    def _watchlist(self, intelligence: Dict[str, Any], start: str, end: str,
                   warnings: List[str]) -> List[Dict[str, Any]]:
        latest_events = intelligence.get("latest_events") or []
        cards = intelligence.get("watchlist") or []
        try:
            from src.services.market_data_service import MarketDataService
            live_rows = MarketDataService().latest_quotes(
                [str(card.get("symbol") or "") for card in cards], refresh_missing=True,
            )
            live_by_code = {str(row.get("stock_code") or "").split(".")[0]: row for row in live_rows}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Home dashboard realtime overlay failed: %s", type(exc).__name__)
            live_by_code = {}
        result = []
        for card in cards:
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
                "update_time": latest_daily.get("trade_date"), "source": "tushare.daily",
            } if latest_daily else None
            live_quote = live_by_code.get(symbol.split(".")[0])
            result.append({**card, "latest_quote": self._newest_watchlist_quote(live_quote, daily_quote, card.get("latest_quote")),
                           "history": history, "latest_catalyst": catalyst,
                           "latest_risk": risk, "latest_institution": institution,
                           "latest_events": [compact(event) for event in related[:4]]})
        return result
