"""Unified local-first intraday/daily/weekly/monthly/yearly market series."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import os
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from data_provider.base import DataFetcherManager, normalize_stock_code
from src.data.stock_index_loader import get_index_stock_name
from src.repositories.market_data_repo import MarketDataRepository
from src.repositories.stock_repo import StockRepository
from src.services.financial_data_service import FinancialDataUpstreamError, TushareGatewayService

logger = logging.getLogger(__name__)

PERIODS = {"intraday", "daily", "weekly", "monthly", "yearly"}
RANGE_DAYS = {
    "1d": 1, "5d": 5, "1m": 31, "3m": 93, "6m": 186,
    "1y": 366, "2y": 732, "3y": 1098, "5y": 1830,
    "10y": 3660, "max": 7300,
}
DEFAULT_RANGE = {"intraday": "1d", "daily": "6m", "weekly": "2y", "monthly": "5y", "yearly": "max"}
INDEX_NAMES = {
    "000001.SH": "上证指数", "000016.SH": "上证50", "000300.SH": "沪深300",
    "000688.SH": "科创50", "000905.SH": "中证500", "000852.SH": "中证1000",
    "399001.SZ": "深证成指", "399006.SZ": "创业板指",
}
DEFAULT_INDEX_SYMBOLS = tuple(INDEX_NAMES.keys())


def configured_index_symbols() -> List[str]:
    """Return an extensible A-share index universe from the environment."""
    raw = os.getenv("MARKET_INDEX_LIST", "")
    values = [item.strip().upper().replace(".SS", ".SH") for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(values or DEFAULT_INDEX_SYMBOLS))


class MarketDataService:
    def __init__(
        self,
        *,
        repository: Optional[MarketDataRepository] = None,
        stock_repository: Optional[StockRepository] = None,
        tushare: Optional[TushareGatewayService] = None,
        fetcher: Optional[DataFetcherManager] = None,
        realtime_batch_fetcher: Optional[Callable[[List[str]], List[Dict[str, Any]]]] = None,
        minute_history_fetcher: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    ):
        self.repo = repository or MarketDataRepository()
        self.stock_repo = stock_repository or StockRepository(self.repo.db)
        self.tushare = tushare or TushareGatewayService()
        self.fetcher = fetcher
        self.realtime_batch_fetcher = realtime_batch_fetcher
        self.minute_history_fetcher = minute_history_fetcher

    def get_series(
        self,
        stock_code: str,
        *,
        period: str = "daily",
        range_key: Optional[str] = None,
        days: Optional[int] = None,
        refresh: bool = False,
        max_points: int = 2000,
    ) -> Dict[str, Any]:
        normalized_period = str(period or "daily").lower()
        if normalized_period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(sorted(PERIODS))}")
        selected_range = str(range_key or DEFAULT_RANGE[normalized_period]).lower()
        if days is not None:
            lookback_days = max(1, min(int(days), 7300))
            selected_range = f"{lookback_days}d"
        else:
            if selected_range not in RANGE_DAYS:
                raise ValueError(f"unsupported range: {selected_range}")
            lookback_days = RANGE_DAYS[selected_range]

        code = normalize_stock_code(stock_code)
        refreshed = False
        if normalized_period == "intraday":
            refreshed = self.refresh_ticks([code]) > 0 if refresh else False
            session_count = 5 if selected_range == "5d" else 1
            storage_days = 14 if session_count == 5 else 7

            def local_rows(*, allow_tick_history: bool = False) -> tuple[List[Any], List[Any], List[Any]]:
                minutes = self._regular_session_rows(
                    self._intraday_rows(code, storage_days, minimum_sessions=session_count), code,
                )
                minute_sessions = {row.timestamp.date() for row in minutes}
                raw_ticks = (
                    self._tick_rows(code, storage_days, minimum_sessions=session_count)
                    if allow_tick_history or len(minute_sessions) < session_count
                    else self.repo.latest_tick_minute(code)
                )
                ticks = self._regular_session_rows(raw_ticks, code)
                return raw_ticks, ticks, minutes

            # With normal 1MIN history present, second snapshots are needed
            # only for the newest live minute—not for the entire 7/14-day DB.
            raw_tick_rows, tick_rows, minute_rows = local_rows()
            available_dates = {row.timestamp.date() for row in [*tick_rows, *minute_rows]}
            if refresh or len(available_dates) < session_count:
                refreshed = self.refresh_historical_intraday([code], sessions=session_count) > 0 or refreshed
                raw_tick_rows, tick_rows, minute_rows = local_rows()
            if not tick_rows and not minute_rows:
                refreshed = self.refresh_ticks([code]) > 0 or refreshed
                raw_tick_rows, tick_rows, minute_rows = local_rows(allow_tick_history=True)
            rows = _merge_second_and_minute_rows(tick_rows, minute_rows, sessions=session_count)
            completed_session = _latest_completed_a_share_session()
            rows = _append_completed_stock_close(
                rows,
                self.repo.latest_daily([code]).get(code),
                completed_session,
            )
            # A user-scoped watchlist can receive the final Tencent snapshot
            # before its Tushare daily bar has been backfilled.  Off-session
            # snapshots are deliberately excluded from the plotted stream, but
            # a same-day snapshot received after 15:00 is still authoritative
            # closing evidence and must terminate an otherwise partial line.
            rows = _append_completed_stock_snapshot_close(
                rows,
                max(raw_tick_rows, key=lambda row: row.timestamp) if raw_tick_rows else None,
                completed_session,
            )
            pre_close = _intraday_pre_close(
                rows,
                fallback=lambda session_date: self.repo.previous_daily_close(code, session_date),
            )
            data = [self._market_row_item(row) for row in _sample_rows(rows, max_points)]
            sources = sorted({str(row.data_source or "local") for row in rows})
        else:
            daily_rows, fetched = self._daily_rows(code, lookback_days, refresh=refresh)
            refreshed = fetched
            data = self._aggregate_daily(daily_rows, normalized_period)
            sources = sorted({str(row.data_source or "local") for row in daily_rows})

        return {
            "stock_code": code,
            "stock_name": self._stock_name(code),
            "period": normalized_period,
            "range": selected_range,
            "data": data,
            "source": "+".join(sources) if sources else "local",
            "stored_count": len(rows) if normalized_period == "intraday" else len(data),
            "latest_at": data[-1]["date"] if data else None,
            "pre_close": pre_close if normalized_period == "intraday" else None,
            "refreshed": refreshed,
            "storage": "sqlite",
        }

    def refresh_ticks(self, stock_codes: Iterable[str]) -> int:
        codes = list(dict.fromkeys(
            normalize_stock_code(value) for value in stock_codes if str(value or "").strip()
        ))
        if not codes:
            return 0
        rows: List[Dict[str, Any]] = []
        try:
            rows = list(
                self.realtime_batch_fetcher(codes)
                if self.realtime_batch_fetcher is not None
                else _tencent_realtime_stock_snapshots(codes)
            )
        except Exception as exc:  # noqa: BLE001 - high-frequency worker must degrade cleanly.
            logger.info("Tencent realtime quote batch unavailable: %s", type(exc).__name__)
        now = datetime.now().replace(microsecond=0)
        current_codes = {
            normalize_stock_code(str(row.get("code") or ""))
            for row in rows
            if _snapshot_is_current(row.get("timestamp"), now)
        }
        fallback_codes = [code for code in codes if code not in current_codes]
        if fallback_codes:
            manager = self.fetcher or DataFetcherManager()
            for code in fallback_codes:
                try:
                    quote = manager.get_realtime_quote(code, log_final_failure=False)
                except Exception as exc:  # noqa: BLE001 - one failed symbol must not block the batch.
                    logger.info("Realtime quote fallback unavailable code=%s: %s", code, type(exc).__name__)
                    continue
                fallback = _unified_quote_tick(code, quote, now)
                if fallback is not None:
                    rows.append(fallback)
        return self.repo.upsert_ticks(rows, source="tushare.legacy_snapshot")

    def latest_quotes(self, stock_codes: Iterable[str], *, refresh_missing: bool = False) -> List[Dict[str, Any]]:
        """Read the shared one-second quote cache; optionally seed missing symbols once."""
        codes = list(dict.fromkeys(
            normalize_stock_code(value) for value in stock_codes if str(value or "").strip()
        ))
        if not codes:
            return []
        rows = self.repo.latest_ticks(codes)
        now = datetime.now()
        refresh_codes = [
            code for code in codes
            if code not in rows or _tick_needs_refresh(rows.get(code), now)
        ]
        if refresh_missing and refresh_codes:
            self.refresh_ticks(refresh_codes)
            rows = self.repo.latest_ticks(codes)
        daily_rows = self.repo.latest_daily(codes)
        completed_session = _latest_completed_a_share_session(now)
        result: List[Dict[str, Any]] = []
        for code in codes:
            row = rows.get(code)
            daily = daily_rows.get(code)
            use_daily_close = daily is not None and (
                row is None
                or daily.date > row.timestamp.date()
                or (
                    daily.date == row.timestamp.date()
                    and daily.date <= completed_session
                    and row.timestamp.time() < datetime.strptime("15:00", "%H:%M").time()
                )
            )
            if use_daily_close:
                update_at = datetime.combine(daily.date, datetime.min.time()).replace(hour=15)
                previous_close = self.repo.previous_daily_close(code, daily.date)
                change = daily.close - previous_close if daily.close is not None and previous_close is not None else None
                change_percent = change / previous_close * 100 if change is not None and previous_close else None
                age = max(0.0, (now - update_at).total_seconds())
                result.append({
                    "stock_code": code,
                    "stock_name": None,
                    "current_price": daily.close,
                    "change": change,
                    "change_percent": change_percent,
                    "open": daily.open,
                    "high": daily.high,
                    "low": daily.low,
                    "prev_close": previous_close,
                    "volume": daily.volume * 100 if daily.volume is not None else None,
                    "amount": daily.amount,
                    "second_volume": None,
                    "second_amount": None,
                    "update_time": update_at.isoformat(timespec="seconds"),
                    "source": daily.data_source or "local.sqlite.daily",
                    "stale_seconds": round(age, 1),
                    "is_stale": True,
                })
                continue
            if row is None:
                continue
            age = max(0.0, (now - row.timestamp).total_seconds())
            result.append({
                "stock_code": code,
                "stock_name": None,
                "current_price": row.price,
                "change": row.change,
                "change_percent": row.pct_chg,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "prev_close": row.pre_close,
                "volume": row.volume,
                "amount": row.amount,
                "second_volume": row.volume_delta,
                "second_amount": row.amount_delta,
                "update_time": row.timestamp.isoformat(timespec="seconds"),
                "source": row.data_source or "local.sqlite",
                "stale_seconds": round(age, 1),
                "is_stale": _tick_is_stale(row.timestamp, now),
            })
        return result

    def refresh_index_ticks(self, symbols: Optional[Iterable[str]] = None) -> int:
        requested = list(symbols or configured_index_symbols())
        legacy = [f"{'sh' if symbol.endswith('.SH') else 'sz'}{symbol.split('.')[0]}" for symbol in requested]
        saved_symbols: set[str] = set()
        saved = 0
        try:
            import tushare as ts
            frame = ts.get_realtime_quotes(legacy)
            if frame is None or frame.empty:
                raise ValueError("empty realtime index snapshot")
            collected_at = datetime.now().replace(microsecond=0)
            exchange_fallback = _latest_a_share_close_timestamp(collected_at)
            rows_by_code = {str(row.get("code") or "").zfill(6): row for _, row in frame.iterrows()}
            for symbol in requested:
                row = rows_by_code.get(symbol.split(".")[0])
                if row is None:
                    continue
                price = _float_or_none(row.get("price")); pre_close = _float_or_none(row.get("pre_close"))
                change_pct = (price - pre_close) / pre_close * 100 if price is not None and pre_close else None
                timestamp = _legacy_snapshot_timestamp(row, exchange_fallback)
                saved += self.repo.upsert_index(symbol, [{
                    "timestamp": timestamp,
                    "open": row.get("open"), "high": row.get("high"),
                    "low": row.get("low"), "close": price, "volume": row.get("volume"),
                    "amount": row.get("amount"), "change_percent": change_pct,
                }], frequency="1SEC", source="tushare.legacy_snapshot")
                if _snapshot_is_current(timestamp, collected_at):
                    saved_symbols.add(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.info("One-second index batch unavailable: %s", type(exc).__name__)
        fallback_symbols = [symbol for symbol in requested if symbol not in saved_symbols]
        if fallback_symbols:
            try:
                fallback_rows = _tencent_realtime_index_snapshots(fallback_symbols)
                for symbol, row in fallback_rows.items():
                    saved += self.repo.upsert_index(
                        symbol, [row], frequency="1SEC", source="tencent.snapshot",
                    )
            except Exception as exc:  # noqa: BLE001
                logger.info("Tencent realtime index fallback unavailable: %s", type(exc).__name__)
        return saved

    def latest_index_quotes(self, symbols: Optional[Iterable[str]] = None, *, refresh_missing: bool = False) -> List[Dict[str, Any]]:
        requested = [str(symbol).upper().replace(".SS", ".SH") for symbol in (symbols or configured_index_symbols())]
        rows = self.repo.latest_index_bars(requested)
        now = datetime.now()
        refresh_symbols = [
            symbol for symbol in requested
            if symbol not in rows or _tick_needs_refresh(rows.get(symbol), now)
        ]
        if refresh_missing and refresh_symbols:
            self.refresh_index_ticks(refresh_symbols); rows = self.repo.latest_index_bars(requested)
        result = []
        for symbol in requested:
            row = rows.get(symbol)
            if row is None: continue
            age = max(0.0, (now - row.timestamp).total_seconds())
            result.append({"code": symbol, "name": INDEX_NAMES.get(symbol), "close": row.close,
                           "change_pct": row.pct_chg, "open": row.open, "high": row.high, "low": row.low,
                           "volume": row.volume, "amount": row.amount,
                           "second_volume": row.volume_delta, "second_amount": row.amount_delta,
                           "update_time": row.timestamp.isoformat(timespec="seconds"),
                           "stale_seconds": round(age, 1),
                           "is_stale": _tick_is_stale(row.timestamp, now), "source": row.data_source})
        return result

    def refresh_intraday(self, stock_codes: Iterable[str]) -> int:
        codes = [normalize_stock_code(value) for value in stock_codes if str(value or "").strip()]
        if not codes:
            return 0
        saved = 0
        if self.tushare.available:
            try:
                response = self.tushare.query(
                    "rt_min",
                    params={"ts_code": ",".join(_ts_code(code) for code in codes), "freq": "1MIN"},
                )
                grouped: Dict[str, List[Dict[str, Any]]] = {}
                for row in response.get("rows") or []:
                    raw_code = normalize_stock_code(str(row.get("ts_code") or row.get("code") or ""))
                    timestamp = _parse_minute_time(row.get("time") or row.get("trade_time"))
                    if not raw_code or timestamp is None:
                        continue
                    grouped.setdefault(raw_code, []).append({
                        "timestamp": timestamp,
                        "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
                        "close": row.get("close"), "volume": row.get("vol"), "amount": row.get("amount"),
                    })
                for code, rows in grouped.items():
                    saved += self.repo.upsert_intraday(code, rows, source="tushare.rt_min")
                if saved:
                    return saved
            except (FinancialDataUpstreamError, ValueError) as exc:
                logger.info("Tushare rt_min unavailable, using quote snapshots: %s", exc)

        manager = self.fetcher or DataFetcherManager()
        minute = datetime.now().replace(second=0, microsecond=0)
        for code in codes:
            quote = manager.get_realtime_quote(code, log_final_failure=False)
            price = getattr(quote, "price", None) if quote is not None else None
            if price is None:
                continue
            saved += self.repo.upsert_intraday(code, [{
                "timestamp": minute,
                "open": price, "high": price, "low": price, "close": price,
                "volume": getattr(quote, "volume", None), "amount": getattr(quote, "amount", None),
            }], source=f"{getattr(getattr(quote, 'source', None), 'value', None) or 'realtime'}.snapshot")
        return saved

    def refresh_historical_intraday(self, stock_codes: Iterable[str], *, sessions: int = 5) -> int:
        """Backfill the latest trading sessions at one-minute fidelity.

        Historical one-second snapshots do not exist in the standard Tushare API; true
        one-second rows are accumulated locally while the worker is running.
        """
        codes = list(dict.fromkeys(
            normalize_stock_code(value) for value in stock_codes if str(value or "").strip()
        ))
        saved = 0
        start = datetime.now() - timedelta(days=14 if sessions >= 5 else 7)
        end = datetime.now()
        for code in codes:
            parsed: List[Dict[str, Any]] = []
            source = "tushare.stk_mins"
            if self.tushare.available:
                try:
                    response = self.tushare.query("stk_mins", params={
                        "ts_code": _ts_code(code),
                        "freq": "1min",
                        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
                        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    parsed = _parse_historical_minute_rows(response.get("rows") or [])
                except (FinancialDataUpstreamError, ValueError) as exc:
                    logger.info("Tushare historical minute unavailable code=%s: %s", code, exc)
            if not parsed:
                parsed = self._fetch_five_day_minutes(_ts_code(code))
                source = "tencent.5day_minute"
            parsed = _keep_latest_sessions(parsed, sessions)
            if parsed:
                saved += self.repo.upsert_intraday(code, parsed, source=source)
        return saved

    def refresh_historical_index_intraday(
        self,
        symbols: Optional[Iterable[str]] = None,
        *,
        sessions: int = 5,
    ) -> int:
        requested = [str(symbol).upper().replace(".SS", ".SH") for symbol in (symbols or configured_index_symbols())]
        saved = 0
        start = datetime.now() - timedelta(days=14 if sessions >= 5 else 7)
        end = datetime.now()
        for symbol in requested:
            parsed: List[Dict[str, Any]] = []
            source = "tushare.idx_mins"
            if self.tushare.available:
                try:
                    response = self.tushare.query("idx_mins", params={
                        "ts_code": symbol,
                        "freq": "1min",
                        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
                        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    parsed = _parse_historical_minute_rows(response.get("rows") or [])
                except (FinancialDataUpstreamError, ValueError) as exc:
                    logger.info("Tushare index minute unavailable symbol=%s: %s", symbol, exc)
            if not parsed:
                parsed = self._fetch_five_day_minutes(symbol)
                source = "tencent.5day_minute"
            parsed = _keep_latest_sessions(parsed, sessions)
            if parsed:
                saved += self.repo.upsert_index(symbol, parsed, frequency="1MIN", source=source)
        return saved

    def bootstrap_universe(
        self,
        stock_codes: Iterable[str],
        *,
        index_symbols: Optional[Iterable[str]] = None,
        intraday_sessions: int = 5,
        daily_days: int = 7300,
    ) -> Dict[str, Any]:
        codes = list(dict.fromkeys(
            normalize_stock_code(value) for value in stock_codes if str(value or "").strip()
        ))
        indices = list(index_symbols or configured_index_symbols())
        result = {
            "stocks": codes,
            "indices": indices,
            "stock_minute_rows": self.refresh_historical_intraday(codes, sessions=intraday_sessions),
            "index_minute_rows": self.refresh_historical_index_intraday(indices, sessions=intraday_sessions),
            "stock_daily_rows": 0,
            "index_daily_rows": 0,
        }
        for code in codes:
            try:
                rows, fetched = self._daily_rows(
                    code, min(max(int(daily_days), 30), 7300), refresh=False
                )
                if fetched or rows:
                    result["stock_daily_rows"] += len(rows)
            except Exception as exc:  # noqa: BLE001 - one symbol must not block the default universe.
                logger.info("Stock daily bootstrap unavailable code=%s: %s", code, type(exc).__name__)
        for symbol in indices:
            series = self.get_index_series(
                symbol,
                period="daily",
                days=min(max(int(daily_days), 30), 7300),
                refresh=False,
            )
            result["index_daily_rows"] += int(series.get("stored_count") or 0)
        result["pruned"] = self.repo.prune_realtime_sessions(keep_sessions=intraday_sessions)
        result["storage"] = self.status()
        return result

    def _fetch_five_day_minutes(self, symbol: str) -> List[Dict[str, Any]]:
        if self.minute_history_fetcher is not None:
            return self.minute_history_fetcher(symbol)
        return _tencent_five_day_minutes(symbol)

    def get_index_series(
        self, symbol: str, *, period: str = "daily", range_key: Optional[str] = None,
        days: Optional[int] = None, refresh: bool = False, max_points: int = 2000,
    ) -> Dict[str, Any]:
        normalized_period, selected_range, lookback_days = self._series_options(period, range_key, days)
        qualified = str(symbol or "").strip().upper().replace(".SS", ".SH")
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        frequency = "1MIN" if normalized_period == "intraday" else "1D"
        rows = self.repo.index_range(qualified, start, end, frequency=frequency)
        expected = max(1, int(lookback_days * .45))
        latest_date = rows[-1].timestamp.date() if rows else None
        stale = latest_date is not None and latest_date < _latest_completed_a_share_session()
        # A newly listed symbol cannot fill a multi-year lookback.  A fresh
        # local tail is sufficient and must not trigger a full download on
        # every process restart.
        incomplete_short_range = frequency == "1D" and lookback_days <= 365 and len(rows) < expected
        should_refresh = refresh or not rows or stale or incomplete_short_range
        refreshed = self._refresh_index(qualified, frequency, lookback_days) > 0 if should_refresh else False
        if refreshed:
            rows = self.repo.index_range(qualified, start, end, frequency=frequency)
        if normalized_period == "intraday":
            session_count = 5 if selected_range == "5d" else 1
            storage_days = 14 if session_count == 5 else 7
            broad_start = end - timedelta(days=storage_days)
            second_rows = self._regular_session_rows(
                self.repo.index_range(qualified, broad_start, end, frequency="1SEC"), qualified,
            )
            minute_rows = self._regular_session_rows(
                self.repo.index_range(qualified, broad_start, end, frequency="1MIN"), qualified,
            )
            available_dates = {row.timestamp.date() for row in [*second_rows, *minute_rows]}
            if refresh or len(available_dates) < session_count:
                self.refresh_historical_index_intraday([qualified], sessions=session_count)
                second_rows = self._regular_session_rows(
                    self.repo.index_range(qualified, broad_start, end, frequency="1SEC"), qualified,
                )
                minute_rows = self._regular_session_rows(
                    self.repo.index_range(qualified, broad_start, end, frequency="1MIN"), qualified,
                )
            rows = _merge_second_and_minute_rows(second_rows, minute_rows, sessions=session_count)
            completed_session = _latest_completed_a_share_session()
            daily_tail = self.repo.index_range(
                qualified,
                datetime.combine(completed_session, datetime.min.time()),
                datetime.combine(completed_session, datetime.max.time()),
                frequency="1D",
            )
            rows = _append_completed_index_close(
                rows,
                daily_tail[-1] if daily_tail else None,
                completed_session,
            )
            pre_close = _intraday_pre_close(
                rows,
                fallback=lambda session_date: self.repo.previous_index_close(
                    qualified, datetime.combine(session_date, datetime.min.time()),
                ),
            )
        stored_count = len(rows)
        render_rows = _sample_rows(rows, max_points) if normalized_period == "intraday" else rows
        raw = []
        for row in render_rows:
            is_second = row.frequency == "1SEC"
            raw.append({
                "date": row.timestamp.date() if frequency == "1D" else row.timestamp,
                "open": row.open, "high": row.high, "low": row.low, "close": row.close,
                "volume": row.volume_delta if is_second else row.volume,
                "amount": row.amount_delta if is_second else row.amount,
                "cumulative_volume": row.volume if is_second else None,
                "cumulative_amount": row.amount if is_second else None,
                "change_percent": row.pct_chg,
            })
        data = self._aggregate_records(raw, normalized_period)
        sources = sorted({str(row.data_source or "local") for row in rows})
        return {
            "stock_code": qualified, "stock_name": INDEX_NAMES.get(qualified),
            "period": normalized_period, "range": selected_range, "data": data,
            "source": "+".join(sources) if sources else "local", "stored_count": stored_count,
            "latest_at": data[-1]["date"] if data else None, "refreshed": refreshed, "storage": "sqlite",
            "pre_close": pre_close if normalized_period == "intraday" else None,
        }

    def _refresh_index(self, symbol: str, frequency: str, lookback_days: int) -> int:
        if not self.tushare.available:
            return 0
        try:
            if frequency == "1MIN":
                response = self.tushare.query("rt_min", params={"ts_code": symbol, "freq": "1MIN"})
                parsed = []
                for row in response.get("rows") or []:
                    timestamp = _parse_minute_time(row.get("time") or row.get("trade_time"))
                    if timestamp is None:
                        continue
                    parsed.append({
                        "timestamp": timestamp, "open": row.get("open"), "high": row.get("high"),
                        "low": row.get("low"), "close": row.get("close"), "volume": row.get("vol"),
                        "amount": row.get("amount"), "change_percent": row.get("pct_chg"),
                    })
                return self.repo.upsert_index(symbol, parsed, frequency="1MIN", source="tushare.rt_min")
            end = date.today()
            start = end - timedelta(days=min(lookback_days + 10, 7300))
            response = self.tushare.query("index_daily", params={
                "ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
            })
            parsed = []
            for row in response.get("rows") or []:
                raw_date = str(row.get("trade_date") or "")
                if len(raw_date) != 8:
                    continue
                parsed.append({
                    "timestamp": datetime.strptime(raw_date, "%Y%m%d"),
                    "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
                    "close": row.get("close"), "volume": row.get("vol"), "amount": row.get("amount"),
                    "change_percent": row.get("pct_chg"),
                })
            return self.repo.upsert_index(symbol, parsed, frequency="1D", source="tushare.index_daily")
        except (FinancialDataUpstreamError, ValueError) as exc:
            logger.info("Index refresh unavailable symbol=%s frequency=%s: %s", symbol, frequency, exc)
            return 0

    def status(self) -> Dict[str, Any]:
        return self.repo.status()

    @staticmethod
    def _series_options(period: str, range_key: Optional[str], days: Optional[int]) -> tuple[str, str, int]:
        normalized_period = str(period or "daily").lower()
        if normalized_period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(sorted(PERIODS))}")
        selected_range = str(range_key or DEFAULT_RANGE[normalized_period]).lower()
        if days is not None:
            lookback_days = max(1, min(int(days), 7300))
            return normalized_period, f"{lookback_days}d", lookback_days
        if selected_range not in RANGE_DAYS:
            raise ValueError(f"unsupported range: {selected_range}")
        return normalized_period, selected_range, RANGE_DAYS[selected_range]

    def _daily_rows(self, code: str, lookback_days: int, *, refresh: bool) -> tuple[List[Any], bool]:
        end = date.today()
        start = end - timedelta(days=lookback_days)
        rows = self.repo.daily_range(code, start, end)
        expected_minimum = max(1, int(lookback_days * 0.45))
        latest_date = rows[-1].date if rows else None
        stale = latest_date is not None and latest_date < _latest_completed_a_share_session()
        incomplete_short_range = lookback_days <= 365 and len(rows) < expected_minimum
        should_fetch = refresh or not rows or stale or incomplete_short_range
        fetched = False
        if should_fetch:
            manager = self.fetcher or DataFetcherManager()
            frame, source = manager.get_daily_data(code, days=min(lookback_days + 10, 7300))
            if frame is not None and not frame.empty:
                self.stock_repo.save_dataframe(frame, code, str(source or "market_data"))
                rows = self.repo.daily_range(code, start, end)
                fetched = True
        return rows, fetched

    def _intraday_rows(self, code: str, lookback_days: int, *, minimum_sessions: int = 1) -> List[Any]:
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        rows = self.repo.intraday_range(code, start, end)
        if len({row.timestamp.date() for row in rows}) >= max(1, minimum_sessions):
            return rows
        recent = self.repo.recent_intraday(code, lookback_days)
        return recent if len(recent) > len(rows) else rows

    @staticmethod
    def _regular_session_rows(rows: List[Any], symbol: str) -> List[Any]:
        """Discard impossible off-session A-share intraday snapshots.

        Legacy quote endpoints keep returning the last close overnight. Such
        responses are useful as close values, but stamping them with the poll
        time creates a fake next-day intraday point and a false date label.
        """
        normalized = str(symbol or "").upper().replace(".SS", ".SH")
        pure = normalized.split(".", 1)[0]
        mainland = pure.isdigit() and len(pure) == 6 and (
            "." not in normalized or normalized.endswith((".SH", ".SZ", ".BJ"))
        )
        if not mainland:
            return rows
        return [row for row in rows if _is_a_share_intraday_timestamp(row.timestamp)]

    def _tick_rows(self, code: str, lookback_days: int, *, minimum_sessions: int = 1) -> List[Any]:
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        rows = self.repo.tick_range(code, start, end)
        if len({row.timestamp.date() for row in rows}) >= max(1, minimum_sessions):
            return rows
        recent = self.repo.recent_ticks(code, lookback_days)
        return recent if len(recent) > len(rows) else rows

    def _stock_name(self, code: str) -> Optional[str]:
        # Normal page reads must stay local. Constructing DataFetcherManager
        # initializes every configured market provider and used to happen for
        # each 15-second chart poll merely to resolve a display label.
        if self.fetcher is None:
            return get_index_stock_name(code)
        try:
            return self.fetcher.get_stock_name(code, allow_realtime=False)
        except Exception:
            return get_index_stock_name(code)

    @staticmethod
    def _intraday_item(row: Any) -> Dict[str, Any]:
        return {
            "date": row.timestamp.isoformat(timespec="minutes"),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close,
            "volume": row.volume, "amount": row.amount, "change_percent": None,
        }

    @staticmethod
    def _tick_item(row: Any) -> Dict[str, Any]:
        return {
            "date": row.timestamp.isoformat(timespec="seconds"),
            "open": row.open, "high": row.high, "low": row.low, "close": row.price,
            "volume": row.volume_delta, "amount": row.amount_delta,
            "cumulative_volume": row.volume, "cumulative_amount": row.amount,
            "change_percent": row.pct_chg,
        }

    @staticmethod
    def _market_row_item(row: Any) -> Dict[str, Any]:
        return MarketDataService._tick_item(row) if hasattr(row, "price") else MarketDataService._intraday_item(row)

    @staticmethod
    def _aggregate_daily(rows: List[Any], period: str) -> List[Dict[str, Any]]:
        raw = [{
            "date": row.date, "open": row.open, "high": row.high, "low": row.low,
            "close": row.close, "volume": row.volume, "amount": row.amount, "change_percent": row.pct_chg,
        } for row in rows]
        return MarketDataService._aggregate_records(raw, period)

    @staticmethod
    def _aggregate_records(raw: List[Dict[str, Any]], period: str) -> List[Dict[str, Any]]:
        if period == "daily":
            return [{**item, "date": item["date"].isoformat(timespec="minutes") if isinstance(item["date"], datetime) else item["date"].isoformat()} for item in raw]
        if period == "intraday":
            return [{**item, "date": item["date"].isoformat(timespec="seconds")} for item in raw]
        if not raw:
            return []
        frame = pd.DataFrame(raw)
        frame["actual_date"] = pd.to_datetime([item["date"] for item in raw])
        frame = frame.set_index("actual_date", drop=False)
        rule = {"weekly": "W-FRI", "monthly": "ME", "yearly": "YE"}[period]
        grouped = frame.resample(rule).agg({
            "open": "first", "high": "max", "low": "min", "close": "last",
            "volume": "sum", "amount": "sum", "actual_date": "max",
        }).dropna(subset=["close"])
        grouped["change_percent"] = grouped["close"].pct_change() * 100
        return [{
            "date": row["actual_date"].date().isoformat(),
            "open": _float_or_none(row["open"]), "high": _float_or_none(row["high"]),
            "low": _float_or_none(row["low"]), "close": _float_or_none(row["close"]),
            "volume": _float_or_none(row["volume"]), "amount": _float_or_none(row["amount"]),
            "change_percent": _float_or_none(row["change_percent"]),
        } for _index, row in grouped.iterrows()]


def _ts_code(code: str) -> str:
    normalized = normalize_stock_code(code)
    if "." in normalized:
        return normalized.replace(".SS", ".SH")
    if normalized.startswith(("4", "8", "9")):
        return f"{normalized}.BJ"
    return f"{normalized}.SH" if normalized.startswith("6") else f"{normalized}.SZ"


def _parse_minute_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _append_completed_close(
    rows: List[Any],
    daily: Any,
    completed_session: date,
    *,
    daily_date: Optional[date],
) -> List[Any]:
    """End a partial intraday line at the official same-day daily close."""
    if not rows or daily is None or daily_date != completed_session:
        return rows
    session_rows = [
        row for row in rows
        if callable(getattr(getattr(row, "timestamp", None), "date", None))
        and row.timestamp.date() == completed_session
    ]
    if not session_rows:
        return rows
    latest = max(session_rows, key=lambda row: row.timestamp)
    if latest.timestamp.time() >= datetime.strptime("15:00", "%H:%M").time():
        return rows
    close = _float_or_none(getattr(daily, "close", None))
    if close is None or close <= 0:
        return rows
    # A daily volume is cumulative, not a factual 15:00 minute volume.  Keep
    # the closing point at zero volume instead of drawing a false final spike.
    daily_open = _float_or_none(getattr(daily, "open", None))
    daily_high = _float_or_none(getattr(daily, "high", None))
    daily_low = _float_or_none(getattr(daily, "low", None))
    closing_row = SimpleNamespace(
        timestamp=datetime.combine(completed_session, datetime.min.time()).replace(hour=15),
        open=daily_open if daily_open is not None else close,
        high=daily_high if daily_high is not None else close,
        low=daily_low if daily_low is not None else close,
        close=close,
        volume=0.0, amount=0.0, volume_delta=0.0, amount_delta=0.0,
        pct_chg=_float_or_none(getattr(daily, "pct_chg", None)),
        frequency="1MIN",
        data_source=f"{getattr(daily, 'data_source', None) or 'local.daily'}:official_close",
    )
    return sorted([*rows, closing_row], key=lambda row: row.timestamp)


def _append_completed_stock_close(rows: List[Any], daily: Any, completed_session: date) -> List[Any]:
    return _append_completed_close(
        rows,
        daily,
        completed_session,
        daily_date=getattr(daily, "date", None),
    )


def _append_completed_stock_snapshot_close(
    rows: List[Any], snapshot: Any, completed_session: date,
) -> List[Any]:
    """Use a post-close exchange snapshot when the same-day daily bar is absent."""
    timestamp = getattr(snapshot, "timestamp", None)
    if (
        not rows
        or snapshot is None
        or not callable(getattr(timestamp, "date", None))
        or timestamp.date() != completed_session
        or timestamp.time() < datetime.strptime("15:00", "%H:%M").time()
    ):
        return rows
    session_rows = [
        row for row in rows
        if callable(getattr(getattr(row, "timestamp", None), "date", None))
        and row.timestamp.date() == completed_session
    ]
    session_close_time = datetime.strptime("15:00", "%H:%M").time()
    if not session_rows or max(row.timestamp for row in session_rows).time() >= session_close_time:
        return rows
    close = _float_or_none(getattr(snapshot, "price", None))
    if close is None or close <= 0:
        return rows
    high = _float_or_none(getattr(snapshot, "high", None))
    low = _float_or_none(getattr(snapshot, "low", None))
    opening = _float_or_none(getattr(snapshot, "open", None))
    closing_row = SimpleNamespace(
        timestamp=datetime.combine(completed_session, datetime.min.time()).replace(hour=15),
        open=opening if opening is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=0.0, amount=0.0, volume_delta=0.0, amount_delta=0.0,
        pct_chg=_float_or_none(getattr(snapshot, "pct_chg", None)),
        frequency="1MIN",
        data_source=f"{getattr(snapshot, 'data_source', None) or 'local.snapshot'}:official_close",
    )
    return sorted([*rows, closing_row], key=lambda row: row.timestamp)


def _append_completed_index_close(rows: List[Any], daily: Any, completed_session: date) -> List[Any]:
    timestamp = getattr(daily, "timestamp", None)
    return _append_completed_close(
        rows,
        daily,
        completed_session,
        daily_date=timestamp.date() if callable(getattr(timestamp, "date", None)) else None,
    )


def _latest_completed_a_share_session(now: Optional[datetime] = None) -> date:
    """Return the newest weekday whose A-share close should be available."""
    current = now or datetime.now()
    candidate = current.date()
    if candidate.weekday() < 5 and current.time() < datetime.strptime("15:10", "%H:%M").time():
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _latest_a_share_close_timestamp(now: Optional[datetime] = None) -> datetime:
    """Fallback for legacy quotes that omit exchange date/time after close."""
    current = now or datetime.now()
    return datetime.combine(_latest_completed_a_share_session(current), datetime.min.time()).replace(hour=15)


def _legacy_snapshot_timestamp(row: Any, fallback: datetime) -> datetime:
    """Use the exchange timestamp returned by the legacy quote endpoint."""
    raw_date = str(row.get("date") or "").strip()
    raw_time = str(row.get("time") or "").strip()
    candidates = [f"{raw_date} {raw_time}".strip(), raw_date]
    formats = (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M", "%Y%m%d",
    )
    for candidate in candidates:
        if not candidate:
            continue
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return fallback


def _is_a_share_intraday_timestamp(value: datetime) -> bool:
    minutes = value.hour * 60 + value.minute
    return 9 * 60 + 15 <= minutes <= 11 * 60 + 30 or 13 * 60 <= minutes <= 15 * 60 + 5


def _a_share_refresh_window(now: datetime) -> bool:
    """Whether a weekday can have a newer same-day exchange snapshot."""
    local = now.replace(tzinfo=None)
    opening = datetime.strptime("09:15", "%H:%M").time()
    closing = datetime.strptime("15:05", "%H:%M").time()
    return local.weekday() < 5 and opening <= local.time() <= closing


def _snapshot_is_current(timestamp: Any, now: datetime) -> bool:
    if not isinstance(timestamp, datetime):
        return False
    local_timestamp = timestamp.astimezone().replace(tzinfo=None) if timestamp.tzinfo else timestamp
    if _a_share_refresh_window(now):
        return local_timestamp.date() == now.date() and max(0.0, (now - local_timestamp).total_seconds()) <= 90
    return local_timestamp >= _latest_a_share_close_timestamp(now)


def _tick_needs_refresh(row: Any, now: datetime) -> bool:
    if row is None:
        return True
    timestamp = getattr(row, "timestamp", None)
    if not isinstance(timestamp, datetime):
        return True
    if not _a_share_refresh_window(now):
        # A worker may miss the closing minutes (for example during a deploy or
        # upstream timeout).  Do not freeze that partial intraday quote for the
        # rest of the day merely because the polling window has ended.  One
        # post-close refresh can still obtain the exchange's final snapshot.
        return timestamp < _latest_a_share_close_timestamp(now)
    age = max(0.0, (now - timestamp).total_seconds())
    return timestamp.date() != now.date() or age > 15


def _tick_is_stale(timestamp: datetime, now: datetime) -> bool:
    """A 15-second polling UI may safely display the current minute for 90 seconds."""
    if _a_share_refresh_window(now):
        return timestamp.date() != now.date() or max(0.0, (now - timestamp).total_seconds()) > 90
    return timestamp < _latest_a_share_close_timestamp(now)


def _provider_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _unified_quote_tick(code: str, quote: Any, now: datetime) -> Optional[Dict[str, Any]]:
    price = _float_or_none(getattr(quote, "price", None)) if quote is not None else None
    if price is None or price <= 0:
        return None
    timestamp = _provider_timestamp(getattr(quote, "provider_timestamp", None))
    if timestamp is None:
        timestamp = now if _a_share_refresh_window(now) else _latest_a_share_close_timestamp(now)
    source = getattr(getattr(quote, "source", None), "value", None) or "realtime"
    return {
        "code": code,
        "timestamp": timestamp,
        "price": price,
        "open": getattr(quote, "open_price", None),
        "high": getattr(quote, "high", None),
        "low": getattr(quote, "low", None),
        "pre_close": getattr(quote, "pre_close", None),
        "volume": getattr(quote, "volume", None),
        "amount": getattr(quote, "amount", None),
        "change": getattr(quote, "change_amount", None),
        "change_percent": getattr(quote, "change_pct", None),
        "source": f"{source}.snapshot",
    }


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_rows(rows: List[Any], max_points: int) -> List[Any]:
    limit = max(100, min(int(max_points or 2000), 5000))
    if len(rows) <= limit:
        return rows
    step = max(1, (len(rows) - 1) // (limit - 1))
    sampled = rows[::step]
    if sampled[-1] is not rows[-1]:
        sampled.append(rows[-1])
    return sampled[-limit:]


def _intraday_pre_close(
    rows: List[Any],
    *,
    fallback: Callable[[date], Optional[float]],
) -> Optional[float]:
    """Resolve the zero-axis price for the newest displayed trading session.

    A live exchange snapshot is authoritative because its ``pre_close`` also
    reflects ex-right adjustments.  Historical minute bars do not carry that
    field, so their fallback must be queried relative to the bar's session
    date—not the wall-clock date.  Otherwise today's daily close becomes
    "yesterday's close" as soon as the process runs after midnight or a closed
    session is displayed.
    """
    dated_rows = [
        row for row in rows
        if callable(getattr(getattr(row, "timestamp", None), "date", None))
    ]
    if not dated_rows:
        return None
    session_date = max(row.timestamp.date() for row in dated_rows)
    for row in reversed(dated_rows):
        if row.timestamp.date() != session_date:
            continue
        value = _float_or_none(getattr(row, "pre_close", None))
        if value is not None and value > 0:
            return value
    value = _float_or_none(fallback(session_date))
    return value if value is not None and value > 0 else None


def _parse_historical_minute_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        timestamp = _parse_minute_time(row.get("trade_time") or row.get("time"))
        if timestamp is None:
            continue
        parsed.append({
            "timestamp": timestamp,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("vol") if row.get("vol") is not None else row.get("volume"),
            "amount": row.get("amount"),
            "change_percent": row.get("pct_chg"),
        })
    return parsed


def _keep_latest_sessions(rows: List[Dict[str, Any]], sessions: int) -> List[Dict[str, Any]]:
    safe_sessions = max(1, min(int(sessions), 30))
    dates = sorted({row["timestamp"].date() for row in rows if isinstance(row.get("timestamp"), datetime)})
    allowed = set(dates[-safe_sessions:])
    return sorted(
        (row for row in rows if isinstance(row.get("timestamp"), datetime) and row["timestamp"].date() in allowed),
        key=lambda row: row["timestamp"],
    )


def _merge_second_and_minute_rows(second_rows: List[Any], minute_rows: List[Any], *, sessions: int) -> List[Any]:
    """Return minute history plus one live point for the current minute.

    Seconds are an ingestion detail.  Completed chart history remains one point
    per minute; only the newest minute is rebuilt from its available snapshots
    so the visible last point can keep moving in real time.
    """
    def minute_bucket(row: Any) -> Optional[datetime]:
        timestamp = getattr(row, "timestamp", None)
        return timestamp.replace(second=0, microsecond=0) if hasattr(timestamp, "replace") else None

    available = [row for row in [*minute_rows, *second_rows] if minute_bucket(row) is not None]
    dates = sorted({row.timestamp.date() for row in available})
    allowed = set(dates[-max(1, min(int(sessions), 30)):])
    minutes = {
        minute_bucket(row): row
        for row in minute_rows
        if minute_bucket(row) is not None and row.timestamp.date() in allowed
    }
    seconds_by_minute: Dict[datetime, List[Any]] = {}
    for row in second_rows:
        bucket = minute_bucket(row)
        if bucket is not None and row.timestamp.date() in allowed:
            seconds_by_minute.setdefault(bucket, []).append(row)

    if not seconds_by_minute:
        return sorted(minutes.values(), key=lambda row: row.timestamp)

    latest_second_bucket = max(seconds_by_minute)
    latest_minute_bucket = max(minutes) if minutes else None
    if minutes:
        # An older cached tick must never replace a newer completed minute bar.
        if latest_minute_bucket is None or latest_second_bucket >= latest_minute_bucket:
            minutes[latest_second_bucket] = _minute_snapshot_from_seconds(
                seconds_by_minute[latest_second_bucket], live=True,
            )
    else:
        # Graceful fallback while minute history is being seeded: collapse the
        # stored snapshots to minute points instead of returning a second chart.
        for bucket, rows in seconds_by_minute.items():
            minutes[bucket] = _minute_snapshot_from_seconds(
                rows, live=bucket == latest_second_bucket,
            )
    return sorted(minutes.values(), key=lambda row: row.timestamp)


def _minute_snapshot_from_seconds(rows: List[Any], *, live: bool) -> Any:
    """Build a factual one-minute OHLC point from stored quote snapshots."""
    ordered = sorted(rows, key=lambda row: row.timestamp)
    first, latest = ordered[0], ordered[-1]
    prices = [
        value
        for row in ordered
        if (value := _float_or_none(getattr(row, "price", getattr(row, "close", None)))) is not None
    ]
    volume_deltas = [
        value
        for row in ordered
        if (value := _float_or_none(getattr(row, "volume_delta", None))) is not None
    ]
    amount_deltas = [
        value
        for row in ordered
        if (value := _float_or_none(getattr(row, "amount_delta", None))) is not None
    ]
    latest_price = prices[-1] if prices else None
    timestamp = latest.timestamp if live else latest.timestamp.replace(second=0, microsecond=0)
    common = {
        "timestamp": timestamp,
        "frequency": "1SEC" if live else "1MIN",
        "open": prices[0] if prices else _float_or_none(getattr(first, "open", None)),
        "high": max(prices) if prices else _float_or_none(getattr(latest, "high", None)),
        "low": min(prices) if prices else _float_or_none(getattr(latest, "low", None)),
        "close": latest_price,
        "volume": _float_or_none(getattr(latest, "volume", None)) if live else (sum(volume_deltas) if volume_deltas else None),
        "amount": _float_or_none(getattr(latest, "amount", None)) if live else (sum(amount_deltas) if amount_deltas else None),
        "volume_delta": sum(volume_deltas) if volume_deltas else None,
        "amount_delta": sum(amount_deltas) if amount_deltas else None,
        "pct_chg": _float_or_none(getattr(latest, "pct_chg", None)),
        "pre_close": _float_or_none(getattr(latest, "pre_close", None)),
        "data_source": getattr(latest, "data_source", None),
    }
    if live:
        common["price"] = latest_price
    return SimpleNamespace(**common)


def _tencent_quote_payload(legacy_symbols: Iterable[str]) -> Dict[str, List[str]]:
    import requests

    symbols = list(dict.fromkeys(str(value).strip().lower() for value in legacy_symbols if str(value).strip()))
    if not symbols:
        return {}
    response = requests.get(
        "https://qt.gtimg.cn/q=" + ",".join(symbols),
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"},
    )
    response.raise_for_status()
    response.encoding = "gbk"
    result: Dict[str, List[str]] = {}
    for line in response.text.splitlines():
        prefix, separator, quoted = line.partition("=")
        if not separator:
            continue
        legacy = prefix.removeprefix("v_").strip().lower()
        body = quoted.strip().strip(";\r\n").strip('"')
        fields = body.split("~")
        if len(fields) >= 35:
            result[legacy] = fields
    return result


def _tencent_exchange_timestamp(fields: List[str]) -> Optional[datetime]:
    raw_timestamp = str(fields[30] or "").strip() if len(fields) > 30 else ""
    if len(raw_timestamp) < 14:
        return None
    try:
        return datetime.strptime(raw_timestamp[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _tencent_realtime_stock_snapshots(codes: Iterable[str]) -> List[Dict[str, Any]]:
    """Fetch the whole watchlist in one low-latency Tencent request."""
    from data_provider.akshare_fetcher import _normalize_tencent_volume, _parse_tencent_amount

    normalized = [normalize_stock_code(value) for value in codes if str(value or "").strip()]
    legacy_by_code = {
        code: f"{'sh' if code.startswith('6') else 'bj' if code.startswith(('4', '8', '9')) else 'sz'}{code}"
        for code in normalized
    }
    payload = _tencent_quote_payload(legacy_by_code.values())
    result: List[Dict[str, Any]] = []
    for code, legacy in legacy_by_code.items():
        fields = payload.get(legacy)
        if not fields:
            continue
        price = _float_or_none(fields[3])
        if price is None or price <= 0:
            continue
        result.append({
            "code": code,
            "timestamp": _tencent_exchange_timestamp(fields) or _latest_a_share_close_timestamp(),
            "price": price,
            "open": _float_or_none(fields[5]),
            "high": _float_or_none(fields[33]),
            "low": _float_or_none(fields[34]),
            "pre_close": _float_or_none(fields[4]),
            "volume": _normalize_tencent_volume(fields),
            "amount": _parse_tencent_amount(fields),
            "change": _float_or_none(fields[31]),
            "change_percent": _float_or_none(fields[32]),
            "source": "tencent.snapshot",
        })
    return result


def _tencent_realtime_index_snapshots(symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch one factual Tencent snapshot for each configured A-share index."""
    requested = [str(symbol).upper().replace(".SS", ".SH") for symbol in symbols]
    legacy_by_symbol = {
        symbol: f"{'sh' if symbol.endswith('.SH') else 'sz'}{symbol.split('.')[0]}"
        for symbol in requested
    }
    if not legacy_by_symbol:
        return {}
    payload_by_legacy = _tencent_quote_payload(legacy_by_symbol.values())

    result: Dict[str, Dict[str, Any]] = {}
    for symbol, legacy in legacy_by_symbol.items():
        fields = payload_by_legacy.get(legacy)
        if not fields:
            continue
        price = _float_or_none(fields[3])
        if price is None or price <= 0:
            continue
        timestamp = _tencent_exchange_timestamp(fields)
        raw_amount = _float_or_none(fields[37]) if len(fields) > 37 else None
        result[symbol] = {
            "timestamp": timestamp or _latest_a_share_close_timestamp(),
            "open": _float_or_none(fields[5]),
            "high": _float_or_none(fields[33]),
            "low": _float_or_none(fields[34]),
            "close": price,
            "volume": _float_or_none(fields[36]) if len(fields) > 36 else None,
            # Tencent field 37 is CNY ten-thousands.
            "amount": raw_amount * 10_000 if raw_amount is not None else None,
            "change_percent": _float_or_none(fields[32]),
        }
    return result


def _tencent_five_day_minutes(symbol: str) -> List[Dict[str, Any]]:
    """Fetch Tencent's public five-trading-day minute history for stocks or indices."""
    import requests

    qualified = str(symbol or "").strip().upper().replace(".SS", ".SH")
    code = qualified.split(".")[0]
    exchange = "sh" if qualified.endswith(".SH") or ("." not in qualified and code.startswith("6")) else "sz"
    legacy = f"{exchange}{code}"
    response = requests.get(
        "https://web.ifzq.gtimg.cn/appstock/app/day/query",
        params={"code": legacy},
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
    )
    response.raise_for_status()
    payload = response.json()
    days = (((payload.get("data") or {}).get(legacy) or {}).get("data") or [])
    parsed: List[Dict[str, Any]] = []
    for day in days:
        raw_date = str(day.get("date") or "")
        parsed.extend(_parse_tencent_day_minutes(raw_date, day.get("data") or []))
    return parsed


def _parse_tencent_day_minutes(raw_date: str, lines: Iterable[Any]) -> List[Dict[str, Any]]:
    """Convert Tencent cumulative minute totals into factual interval deltas."""
    if len(str(raw_date or "")) != 8:
        return []
    parsed: List[Dict[str, Any]] = []
    previous_volume: Optional[float] = None
    previous_amount: Optional[float] = None
    for line in lines:
        parts = str(line).split()
        if len(parts) < 2 or len(parts[0]) != 4:
            continue
        try:
            timestamp = datetime.strptime(f"{raw_date}{parts[0]}", "%Y%m%d%H%M")
            price = float(parts[1])
        except (TypeError, ValueError):
            continue
        cumulative_volume = _float_or_none(parts[2]) if len(parts) > 2 else None
        cumulative_amount = _float_or_none(parts[3]) if len(parts) > 3 else None
        minute_volume = _non_negative_cumulative_delta(cumulative_volume, previous_volume)
        minute_amount = _non_negative_cumulative_delta(cumulative_amount, previous_amount)
        if cumulative_volume is not None:
            previous_volume = cumulative_volume
        if cumulative_amount is not None:
            previous_amount = cumulative_amount
        parsed.append({
            "timestamp": timestamp,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": minute_volume,
            "amount": minute_amount,
            "change_percent": None,
        })
    return parsed


def _non_negative_cumulative_delta(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None:
        return None
    if previous is None or current < previous:
        return current
    return current - previous


def _legacy_realtime_snapshots(codes: List[str]) -> List[Dict[str, Any]]:
    """Fetch the watchlist in one legacy Tushare snapshot request."""
    import tushare as ts

    frame = ts.get_realtime_quotes(codes)
    if frame is None or frame.empty:
        return []
    result: List[Dict[str, Any]] = []
    collected_at = datetime.now().replace(microsecond=0)
    exchange_fallback = _latest_a_share_close_timestamp(collected_at)
    for _, row in frame.iterrows():
        price = _float_or_none(row.get("price"))
        pre_close = _float_or_none(row.get("pre_close"))
        change = price - pre_close if price is not None and pre_close is not None else None
        change_percent = change / pre_close * 100 if change is not None and pre_close else None
        result.append({
            "code": normalize_stock_code(str(row.get("code") or "")),
            "timestamp": _legacy_snapshot_timestamp(row, exchange_fallback),
            "price": price, "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
            "pre_close": pre_close, "volume": row.get("volume"), "amount": row.get("amount"),
            "change": change, "change_percent": change_percent,
        })
    return result
