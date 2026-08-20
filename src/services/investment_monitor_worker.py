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
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_sync_at: Optional[float] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

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
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, timeout))
        return self.status()

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            thread = self._thread
            return {
                "running": bool(thread and thread.is_alive()),
                "poll_seconds": self.poll_seconds,
                "started_at": self._iso_time(self._started_at),
                "last_sync_at": self._iso_time(self._last_sync_at),
                "last_error": self._last_error,
                "last_result": self._last_result,
            }

    def _run(self) -> None:
        try:
            service = InvestmentMonitorService()
            while not self._stop_event.is_set():
                try:
                    result = service.sync_due_sources()
                    with self._state_lock:
                        self._last_sync_at = time.time()
                        self._last_result = result.get("totals") or {}
                        self._last_error = None
                except Exception as exc:  # noqa: BLE001 - keep monitoring after transient failures.
                    safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                    logger.exception("[investment-monitor] polling cycle failed: %s", safe_error)
                    with self._state_lock:
                        self._last_error = safe_error
                self._stop_event.wait(self.poll_seconds)
        finally:
            self._stop_event.set()

    @staticmethod
    def _iso_time(value: Optional[float]) -> Optional[str]:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None
