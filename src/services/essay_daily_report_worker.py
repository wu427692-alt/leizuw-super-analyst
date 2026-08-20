# -*- coding: utf-8 -*-
"""Background scheduler for previous-day, per-model essay reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from src.services.essay_analysis_service import EssayDailyReportService

logger = logging.getLogger(__name__)


class EssayDailyReportWorker:
    _instance: Optional["EssayDailyReportWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.poll_seconds = max(60, min(int(os.getenv("ESSAY_DAILY_REPORT_POLL_SEC", "300")), 86400))
        self.run_hour = max(0, min(int(os.getenv("ESSAY_DAILY_REPORT_HOUR", "7")), 23))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_run_at: Optional[float] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "EssayDailyReportWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="essay-daily-report-worker", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        return self.status()

    def run_now(self, report_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        result = EssayDailyReportService().generate(report_date=report_date, force=force)
        with self._lock:
            self._last_run_at = time.time()
            self._last_result = result
            self._last_error = None
        return result

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "poll_seconds": self.poll_seconds,
                "run_hour_shanghai": self.run_hour,
                "models": EssayDailyReportService.configured_models(),
                "last_run_at": self._iso(self._last_run_at),
                # Status is polled by the UI. Never resend the complete report body
                # every few seconds; the report endpoint remains the source of truth.
                "last_result": self._result_summary(self._last_result),
                "last_error": self._last_error,
            }

    @staticmethod
    def _result_summary(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not result:
            return None
        models = result.get("models") or result.get("items") or []
        if isinstance(models, dict):
            model_rows = [
                {"model": str(name), "status": (value or {}).get("status") if isinstance(value, dict) else None}
                for name, value in models.items()
            ]
        elif isinstance(models, list):
            model_rows = [
                {key: row.get(key) for key in ("model", "model_name", "status", "report_id") if key in row}
                for row in models if isinstance(row, dict)
            ]
        else:
            model_rows = []
        return {
            key: result.get(key) for key in ("report_date", "source_count", "generated_at", "status")
            if key in result
        } | {"models": model_rows}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = datetime.now(timezone(timedelta(hours=8)))
                target = (now - timedelta(days=1)).date().isoformat()
                # The service compares source hashes, so a report is regenerated
                # when late analysis results arrive and remains free when unchanged.
                if now.hour >= self.run_hour:
                    self.run_now(target)
            except Exception as exc:  # noqa: BLE001
                safe = f"{type(exc).__name__}: {str(exc)[:500]}"
                logger.warning("[essay-daily-report] run failed: %s", safe)
                with self._lock:
                    self._last_error = safe
            self._stop.wait(self.poll_seconds)

    @staticmethod
    def _iso(value: Optional[float]) -> Optional[str]:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None
