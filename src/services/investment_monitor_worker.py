# -*- coding: utf-8 -*-
"""Lifecycle-managed polling worker for the unified investment monitor."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from src.services.investment_monitor_service import InvestmentMonitorService

logger = logging.getLogger(__name__)


class InvestmentMonitorWorker:
    """Poll due source adapters without coupling them to the request lifecycle."""

    _instance: Optional["InvestmentMonitorWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.poll_seconds = max(5.0, min(float(os.getenv("INVESTMENT_MONITOR_POLL_SEC", "10")), 300.0))
        self.max_workers = max(1, min(int(os.getenv("INVESTMENT_MONITOR_MAX_WORKERS", "16")), 24))
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._state_lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_cycle_started_at: Optional[float] = None
        self._last_cycle_finished_at: Optional[float] = None
        self._last_sync_at: Optional[float] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._consecutive_failures = 0
        self._wake_count = 0

    @classmethod
    def get_instance(cls) -> "InvestmentMonitorWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._wake_event.clear()
                self._started_at = time.time()
                self._last_error = None
                self._thread = threading.Thread(
                    target=self._run,
                    name="investment-monitor-worker",
                    daemon=True,
                )
                self._thread.start()
        return self.status()

    def stop(self, *, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, timeout))
        return self.status()

    def trigger(self) -> Dict[str, Any]:
        """Wake the shared due-source loop without starting page-local fetches."""
        self.start()
        with self._state_lock:
            self._wake_count += 1
        self._wake_event.set()
        return {**self.status(), "refresh_requested": True}

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            thread = self._thread
            return {
                "running": bool(thread and thread.is_alive()),
                "poll_seconds": self.poll_seconds,
                "max_workers": self.max_workers,
                "started_at": self._iso_time(self._started_at),
                "last_cycle_started_at": self._iso_time(self._last_cycle_started_at),
                "last_cycle_finished_at": self._iso_time(self._last_cycle_finished_at),
                "last_sync_at": self._iso_time(self._last_sync_at),
                "last_sync_age_seconds": self._age_seconds(self._last_sync_at),
                "last_error": self._last_error,
                "last_result": self._last_result,
                "consecutive_failures": self._consecutive_failures,
                "wake_count": self._wake_count,
            }

    def _run(self) -> None:
        try:
            service = InvestmentMonitorService()
            while not self._stop_event.is_set():
                with self._state_lock:
                    self._last_cycle_started_at = time.time()
                try:
                    result = service.sync_due_sources()
                    with self._state_lock:
                        self._last_sync_at = time.time()
                        self._last_result = result.get("totals") or {}
                        self._last_error = None
                        self._consecutive_failures = 0
                except Exception as exc:  # noqa: BLE001 - keep monitoring after transient failures.
                    safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                    logger.exception("[investment-monitor] polling cycle failed: %s", safe_error)
                    with self._state_lock:
                        self._last_error = safe_error
                        self._consecutive_failures += 1
                finally:
                    with self._state_lock:
                        self._last_cycle_finished_at = time.time()
                self._wake_event.wait(self.poll_seconds)
                self._wake_event.clear()
        finally:
            self._stop_event.set()

    @staticmethod
    def _iso_time(value: Optional[float]) -> Optional[str]:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None

    @staticmethod
    def _age_seconds(value: Optional[float]) -> Optional[int]:
        return max(0, int(time.time() - value)) if value else None
