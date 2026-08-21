# -*- coding: utf-8 -*-
"""Durable one-year CNInfo backfill and high-frequency watchlist polling."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from src.config import get_config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.investment_monitor_service import InvestmentMonitorService
from src.storage import utc_naive_now

logger = logging.getLogger(__name__)
_SHANGHAI = timezone(timedelta(hours=8))


class WatchlistAnnouncementSyncWorker:
    """Keep every A-share watchlist symbol current and repair history gaps."""

    _instance: Optional["WatchlistAnnouncementSyncWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self, *, repository: Optional[InvestmentMonitorRepository] = None,
        service: Optional[InvestmentMonitorService] = None,
    ) -> None:
        self.repository = repository or InvestmentMonitorRepository()
        self.service = service or InvestmentMonitorService(repository=self.repository)
        self.poll_seconds = max(30.0, min(float(os.getenv("CNINFO_WATCHLIST_POLL_SEC", "60")), 600.0))
        self.history_days = max(365, min(int(os.getenv("CNINFO_WATCHLIST_HISTORY_DAYS", "365")), 3650))
        self.recent_days = max(2, min(int(os.getenv("CNINFO_WATCHLIST_RECENT_DAYS", "3")), 14))
        self.window_days = max(7, min(int(os.getenv("CNINFO_WATCHLIST_WINDOW_DAYS", "30")), 90))
        self.max_windows_per_cycle = max(1, min(int(os.getenv("CNINFO_WATCHLIST_WINDOWS_PER_CYCLE", "8")), 24))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_run_at: Optional[float] = None
        self._last_success_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_result: Dict[str, Any] = {}

    @classmethod
    def get_instance(cls) -> "WatchlistAnnouncementSyncWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._wake.clear()
                self._started_at = time.time()
                self._thread = threading.Thread(
                    target=self._run, name="cninfo-watchlist-sync-worker", daemon=True,
                )
                self._thread.start()
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(max(0.1, timeout))
        return self.status()

    def trigger(self) -> Dict[str, Any]:
        self.start()
        self._wake.set()
        return {**self.status(), "refresh_requested": True}

    def register_symbol(self, symbol: str) -> Dict[str, Any]:
        canonical = self.service._canonical_ts_code(symbol)
        if canonical not in self.service.equity_watchlist() and not self._is_a_share(canonical):
            raise ValueError(f"巨潮公告仅支持 A 股上市公司：{symbol}")
        state = self._ensure_state(canonical)
        self.trigger()
        return state

    def status(self) -> Dict[str, Any]:
        symbols = self._watchlist_symbols()
        states = self.repository.list_announcement_sync_states(symbols) if symbols else []
        for state in states:
            windows = self._windows(date.fromisoformat(state["target_start"]), date.fromisoformat(state["target_end"]))
            completed = set(state.get("completed_windows") or [])
            state["history_total_windows"] = len(windows)
            state["history_completed_windows"] = sum(1 for window in windows if self._window_key(window) in completed)
            state["history_progress"] = round(
                state["history_completed_windows"] * 100 / max(1, state["history_total_windows"]), 1,
            )
        with self._state_lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "poll_seconds": self.poll_seconds,
                "history_days": self.history_days,
                "recent_days": self.recent_days,
                "started_at": self._iso_timestamp(self._started_at),
                "last_run_at": self._iso_timestamp(self._last_run_at),
                "last_run_age_seconds": max(0, int(time.time() - self._last_run_at)) if self._last_run_at else None,
                "last_success_at": self._iso_timestamp(self._last_success_at),
                "last_error": self._last_error,
                "last_result": dict(self._last_result),
                "symbols": states,
            }

    def run_once(self) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"status": "running", "skipped": True}
        try:
            symbols = self._watchlist_symbols()
            states = {state["symbol"]: state for state in self._reconcile(symbols)}
            result = {"symbols": len(symbols), "incremental": [], "backfill": []}
            now = utc_naive_now()
            for symbol in symbols:
                state = states[symbol]
                if self._incremental_due(state, now):
                    result["incremental"].append(self._sync_incremental(state))
            remaining = self.max_windows_per_cycle
            for symbol in symbols:
                if remaining <= 0:
                    break
                state = self.repository.list_announcement_sync_states([symbol])[0]
                while remaining > 0:
                    window = self._next_window(state)
                    if window is None:
                        if state.get("status") != "live":
                            self.repository.update_announcement_sync_state(
                                symbol, status="live", backfill_completed_at=utc_naive_now(),
                                next_retry_at=None, last_error=None, consecutive_failures=0,
                            )
                        break
                    retry_at = self._parse_datetime(state.get("next_retry_at"))
                    if retry_at and retry_at > utc_naive_now():
                        break
                    item = self._sync_history_window(state, window)
                    result["backfill"].append(item)
                    remaining -= 1
                    state = self.repository.list_announcement_sync_states([symbol])[0]
                    if item["status"] == "failed":
                        break
            with self._state_lock:
                self._last_run_at = time.time()
                self._last_success_at = self._last_run_at
                self._last_error = None
                self._last_result = result
            return {"status": "success", **result}
        except Exception as exc:  # noqa: BLE001 - worker must survive upstream and database faults.
            safe = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.exception("[cninfo-watchlist] cycle failed: %s", safe)
            with self._state_lock:
                self._last_run_at = time.time()
                self._last_error = safe
            return {"status": "failed", "error": safe}
        finally:
            self._run_lock.release()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _watchlist_symbols(self) -> List[str]:
        try:
            get_config().refresh_stock_list()
        except Exception:  # pragma: no cover - cached config remains usable.
            pass
        return [symbol for symbol in self.service.equity_watchlist() if self._is_a_share(symbol)]

    def _reconcile(self, symbols: List[str]) -> List[Dict[str, Any]]:
        return [self._ensure_state(symbol) for symbol in symbols]

    def _ensure_state(self, symbol: str) -> Dict[str, Any]:
        today = datetime.now(_SHANGHAI).date()
        return self.repository.ensure_announcement_sync_state(
            symbol, stock_name=self.service._stock_name(symbol),
            target_start=today - timedelta(days=self.history_days), target_end=today,
        )

    def _sync_incremental(self, state: Dict[str, Any]) -> Dict[str, Any]:
        symbol = state["symbol"]
        started = utc_naive_now()
        self.repository.update_announcement_sync_state(
            symbol, status="backfilling" if not state.get("backfill_completed_at") else "live",
            last_incremental_started_at=started,
        )
        today = datetime.now(_SHANGHAI).date()
        try:
            rows = self.service.cninfo.fetch(
                start_date=today - timedelta(days=self.recent_days - 1), end_date=today,
                symbols=[symbol], page_size=100, max_pages=20,
            )
            saved = self._persist(rows)
            self._mark_success(symbol, state, saved, fetched=len(rows), incremental=True)
            return {"symbol": symbol, "status": "success", "fetched": len(rows), **saved}
        except Exception as exc:  # noqa: BLE001
            return self._mark_failure(symbol, state, exc, phase="incremental")

    def _sync_history_window(self, state: Dict[str, Any], window: Tuple[date, date]) -> Dict[str, Any]:
        symbol = state["symbol"]
        self.repository.update_announcement_sync_state(
            symbol, status="backfilling", last_backfill_started_at=utc_naive_now(),
        )
        try:
            rows = self.service.cninfo.fetch(
                start_date=window[0], end_date=window[1], symbols=[symbol],
                page_size=100, max_pages=100,
            )
            saved = self._persist(rows)
            completed = list(dict.fromkeys([*(state.get("completed_windows") or []), self._window_key(window)]))
            self._mark_success(
                symbol, state, saved, fetched=len(rows), incremental=False,
                completed_windows=completed,
            )
            return {
                "symbol": symbol, "window": self._window_key(window), "status": "success",
                "fetched": len(rows), **saved,
            }
        except Exception as exc:  # noqa: BLE001
            failed = self._mark_failure(symbol, state, exc, phase="backfill")
            return {**failed, "window": self._window_key(window)}

    def _persist(self, rows: List[Dict[str, Any]]) -> Dict[str, int]:
        source = self.repository.get_source("cninfo.announcements")
        if source is None:
            raise RuntimeError("CNInfo monitoring source is unavailable")
        saved = self.repository.upsert_events(self.service._announcement_events(source, rows))
        self.repository.update_source_status(
            "cninfo.announcements", status="success", received_count=len(rows),
            created_count=saved["created"], updated_count=saved["updated"],
            item_count=saved["created"], success_at=utc_naive_now(),
        )
        return saved

    def _mark_success(
        self, symbol: str, state: Dict[str, Any], saved: Dict[str, int], *, fetched: int, incremental: bool,
        completed_windows: Optional[List[str]] = None,
    ) -> None:
        fields: Dict[str, Any] = {
            "status": "backfilling" if not state.get("backfill_completed_at") else "live",
            "consecutive_failures": 0, "next_retry_at": None, "last_error": None,
            "total_fetched": int(state.get("total_fetched") or 0) + int(fetched),
            "total_created": int(state.get("total_created") or 0) + int(saved.get("created", 0)),
            "total_updated": int(state.get("total_updated") or 0) + int(saved.get("updated", 0)),
        }
        if incremental:
            fields["last_incremental_success_at"] = utc_naive_now()
        else:
            fields["last_backfill_success_at"] = utc_naive_now()
        if completed_windows is not None:
            fields["completed_windows"] = completed_windows
        self.repository.update_announcement_sync_state(symbol, **fields)

    def _mark_failure(self, symbol: str, state: Dict[str, Any], exc: Exception, *, phase: str) -> Dict[str, Any]:
        failures = int(state.get("consecutive_failures") or 0) + 1
        delay = min(300, 30 * (2 ** min(failures - 1, 4)))
        safe = f"{type(exc).__name__}: {str(exc)[:500]}"
        self.repository.update_announcement_sync_state(
            symbol, status="retry", consecutive_failures=failures,
            next_retry_at=utc_naive_now() + timedelta(seconds=delay), last_error=safe,
        )
        logger.warning("[cninfo-watchlist] %s failed symbol=%s retry=%ss error=%s", phase, symbol, delay, safe)
        return {"symbol": symbol, "status": "failed", "phase": phase, "retry_seconds": delay, "error": safe}

    def _incremental_due(self, state: Dict[str, Any], now: datetime) -> bool:
        retry_at = self._parse_datetime(state.get("next_retry_at"))
        if retry_at and retry_at > now:
            return False
        last = self._parse_datetime(state.get("last_incremental_success_at"))
        return last is None or (now - last).total_seconds() >= self.poll_seconds

    def _next_window(self, state: Dict[str, Any]) -> Optional[Tuple[date, date]]:
        windows = self._windows(date.fromisoformat(state["target_start"]), date.fromisoformat(state["target_end"]))
        completed = set(state.get("completed_windows") or [])
        return next((window for window in reversed(windows) if self._window_key(window) not in completed), None)

    def _windows(self, start: date, end: date) -> List[Tuple[date, date]]:
        windows: List[Tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            window_end = min(end, cursor + timedelta(days=self.window_days - 1))
            windows.append((cursor, window_end))
            cursor = window_end + timedelta(days=1)
        return windows

    @staticmethod
    def _window_key(window: Tuple[date, date]) -> str:
        return f"{window[0].isoformat()}~{window[1].isoformat()}"

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)

    @staticmethod
    def _is_a_share(symbol: str) -> bool:
        return len(symbol) == 9 and symbol[:6].isdigit() and symbol[6:] in {".SH", ".SZ", ".BJ"}

    @staticmethod
    def _iso_timestamp(value: Optional[float]) -> Optional[str]:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None
