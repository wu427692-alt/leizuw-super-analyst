"""One-second watchlist snapshots plus minute-bar fallback into SQLite."""

from __future__ import annotations

from datetime import datetime, time as clock_time
import logging
import os
import threading
import time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from data_provider.base import normalize_stock_code
from src.config import get_config
from src.services.market_data_service import MarketDataService

logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class MarketDataWorker:
    _instance: Optional["MarketDataWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        configured = os.getenv("MARKET_TICK_POLL_SECONDS", os.getenv("MARKET_DATA_POLL_SECONDS", "1"))
        self.interval = max(1.0, min(float(configured), 60.0))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._last_run_at: Optional[float] = None
        self._last_success_at: Optional[float] = None
        self._last_saved = 0
        self._last_index_saved = 0
        self._total_saved = 0
        self._total_index_saved = 0
        self._consecutive_empty_runs = 0
        self._last_error: Optional[str] = None
        self._last_minute_key: Optional[str] = None
        self._minute_thread: Optional[threading.Thread] = None
        self._bootstrap_thread: Optional[threading.Thread] = None
        self._bootstrap_running = False
        self._bootstrap_started_at: Optional[float] = None
        self._bootstrap_finished_at: Optional[float] = None
        self._bootstrap_result: Optional[Dict[str, Any]] = None
        self._bootstrap_error: Optional[str] = None
        self._bootstrap_pending: set[str] = set()
        get_config().refresh_stock_list()
        self._known_symbols = {normalize_stock_code(symbol) for symbol in get_config().stock_list}

    @classmethod
    def get_instance(cls) -> "MarketDataWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        started_new = False
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._wake.clear()
                self._thread = threading.Thread(target=self._run, name="market-data-worker", daemon=True)
                self._thread.start()
                started_new = True
        startup_bootstrap_enabled = os.getenv(
            "MARKET_STARTUP_BOOTSTRAP_ENABLED", "true",
        ).strip().lower() in {"1", "true", "yes", "on"}
        if started_new and startup_bootstrap_enabled:
            self._schedule_bootstrap(list(get_config().stock_list))
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        return self.status()

    def trigger(self) -> Dict[str, Any]:
        """Wake live sampling immediately; the worker still enforces trading hours."""
        self.start()
        self._wake.set()
        return {**self.status(), "refresh_requested": True}

    def run_now(self) -> Dict[str, Any]:
        get_config().refresh_stock_list()
        symbols = list(get_config().stock_list)
        additions = [symbol for symbol in symbols if normalize_stock_code(symbol) not in self._known_symbols]
        self._known_symbols = {normalize_stock_code(symbol) for symbol in symbols}
        if additions:
            from src.services.watchlist_backfill_worker import WatchlistBackfillWorker
            for symbol in additions:
                WatchlistBackfillWorker.get_instance().enqueue(symbol, days=183)
        service = MarketDataService()
        saved = service.refresh_ticks(symbols) if symbols else 0
        index_saved = service.refresh_index_ticks()
        minute_scheduled = False
        minute_key = datetime.now(_SHANGHAI).strftime("%Y%m%d%H%M")
        if symbols and minute_key != self._last_minute_key:
            self._last_minute_key = minute_key
            if self._minute_thread is None or not self._minute_thread.is_alive():
                self._minute_thread = threading.Thread(
                    target=self._refresh_minute, args=(symbols,), name="market-minute-fallback", daemon=True,
                )
                self._minute_thread.start()
                minute_scheduled = True
        with self._lock:
            self._last_run_at = time.time()
            self._last_saved = saved
            self._last_index_saved = index_saved
            self._total_saved += saved
            self._total_index_saved += index_saved
            if saved + index_saved > 0:
                self._last_success_at = self._last_run_at
                self._consecutive_empty_runs = 0
                self._last_error = None
            else:
                self._consecutive_empty_runs += 1
                self._last_error = "行情源本轮未返回有效快照，采集器将在下一秒自动重试"
        # Storage totals require several full-table COUNT queries.  Keep those
        # diagnostics behind the explicit admin status endpoint instead of
        # running them after every live quote collection cycle.
        return {
            "symbols": symbols,
            "saved": saved,
            "index_saved": index_saved,
            "minute_scheduled": minute_scheduled,
        }

    def register_symbol(self, symbol: str) -> int:
        """Start collecting a newly added symbol immediately."""
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return 0
        with self._lock:
            self._known_symbols.add(normalize_stock_code(normalized))
        saved = MarketDataService().refresh_ticks([normalized])
        self._schedule_bootstrap([normalized])
        return saved

    def unregister_symbol(self, symbol: str) -> bool:
        """Stop tracking a removed watchlist symbol without deleting stored history."""
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return False
        key = normalize_stock_code(normalized)
        with self._lock:
            existed = key in self._known_symbols
            self._known_symbols.discard(key)
            self._bootstrap_pending.discard(key)
        return existed

    def _schedule_bootstrap(self, symbols: list[str]) -> bool:
        with self._lock:
            if self._bootstrap_thread is not None and self._bootstrap_thread.is_alive():
                self._bootstrap_pending.update(
                    normalize_stock_code(symbol) for symbol in symbols if str(symbol or "").strip()
                )
                return False
            self._bootstrap_thread = threading.Thread(
                target=self._bootstrap_history,
                args=(list(symbols),),
                name="market-history-bootstrap",
                daemon=True,
            )
            self._bootstrap_thread.start()
            return True

    def _bootstrap_history(self, symbols: list[str]) -> None:
        delay = max(0.0, min(float(os.getenv("MARKET_BOOTSTRAP_DELAY_SECONDS", "90")), 300.0))
        # Full history repair is background maintenance.  Let health checks and
        # the first dashboard request win after login/restart.
        if delay and self._stop.wait(delay):
            return
        with self._lock:
            self._bootstrap_running = True
            self._bootstrap_started_at = time.time()
            self._bootstrap_finished_at = None
            self._bootstrap_result = None
            self._bootstrap_error = None
        try:
            batch = list(symbols)
            while batch:
                result = MarketDataService().bootstrap_universe(
                    batch,
                    intraday_sessions=max(1, min(int(os.getenv("MARKET_INTRADAY_SESSIONS", "5")), 30)),
                    daily_days=max(30, min(int(os.getenv("MARKET_DAILY_HISTORY_DAYS", "7300")), 7300)),
                )
                with self._lock:
                    self._bootstrap_result = result
                    batch = sorted(self._bootstrap_pending)
                    self._bootstrap_pending.clear()
        except Exception as exc:  # noqa: BLE001 - startup history must not stop live sampling.
            safe = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.warning("Market history bootstrap failed: %s", safe)
            with self._lock:
                self._bootstrap_error = safe
        finally:
            with self._lock:
                self._bootstrap_running = False
                self._bootstrap_finished_at = time.time()

    @staticmethod
    def _refresh_minute(symbols: list[str]) -> None:
        try:
            MarketDataService().refresh_intraday(symbols)
        except Exception as exc:  # noqa: BLE001 - minute fallback must never stop tick sampling.
            logger.info("Minute fallback refresh failed: %s", type(exc).__name__)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "poll_seconds": self.interval,
                "last_run_at": datetime.fromtimestamp(self._last_run_at).isoformat() if self._last_run_at else None,
                "last_success_at": datetime.fromtimestamp(self._last_success_at).isoformat() if self._last_success_at else None,
                "last_run_age_seconds": max(0, int(time.time() - self._last_run_at)) if self._last_run_at else None,
                "last_success_age_seconds": max(0, int(time.time() - self._last_success_at)) if self._last_success_at else None,
                "last_saved": self._last_saved,
                "last_index_saved": self._last_index_saved,
                "total_saved_since_start": self._total_saved,
                "total_index_saved_since_start": self._total_index_saved,
                "consecutive_empty_runs": self._consecutive_empty_runs,
                "watchlist_symbols": sorted(self._known_symbols),
                "last_error": self._last_error,
                "collecting_window": self._in_collecting_window(),
                "history_bootstrap": {
                    "running": self._bootstrap_running,
                    "started_at": datetime.fromtimestamp(self._bootstrap_started_at).isoformat() if self._bootstrap_started_at else None,
                    "finished_at": datetime.fromtimestamp(self._bootstrap_finished_at).isoformat() if self._bootstrap_finished_at else None,
                    "result": self._bootstrap_result,
                    "error": self._bootstrap_error,
                    "intraday_sessions": max(1, min(int(os.getenv("MARKET_INTRADAY_SESSIONS", "5")), 30)),
                    "daily_history_days": max(30, min(int(os.getenv("MARKET_DAILY_HISTORY_DAYS", "7300")), 7300)),
                    "startup_delay_seconds": max(0.0, min(float(os.getenv("MARKET_BOOTSTRAP_DELAY_SECONDS", "90")), 300.0)),
                },
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            if self._in_collecting_window():
                try:
                    self.run_now()
                except Exception as exc:  # noqa: BLE001 - worker must survive one provider failure.
                    safe = f"{type(exc).__name__}: {str(exc)[:300]}"
                    logger.warning("Market data collection failed: %s", safe)
                    with self._lock:
                        self._last_error = safe
            self._wake.wait(max(0.05, self.interval - (time.monotonic() - started)))
            self._wake.clear()

    @staticmethod
    def _in_collecting_window(now: Optional[datetime] = None) -> bool:
        current = now or datetime.now(_SHANGHAI)
        if current.weekday() >= 5:
            return False
        value = current.time().replace(tzinfo=None)
        return clock_time(9, 30) <= value <= clock_time(11, 30) or clock_time(13, 0) <= value <= clock_time(15, 0)
