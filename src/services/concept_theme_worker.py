# -*- coding: utf-8 -*-
"""Low-impact background updater for the shared concept/theme graph."""

from __future__ import annotations

from datetime import datetime
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from sqlalchemy import asc, select

from src.services.concept_theme_service import ConceptThemeService
from src.storage import ConceptThemeRecord, DatabaseManager, UserWatchlistItem

logger = logging.getLogger(__name__)


class ConceptThemeWorker:
    """Refresh catalogs daily and progressively fill memberships without blocking pages."""

    _instance: Optional["ConceptThemeWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.interval_seconds = max(300, min(int(os.getenv("CONCEPT_THEME_SYNC_INTERVAL_SEC", "300")), 21600))
        self.batch_size = max(10, min(int(os.getenv("CONCEPT_THEME_SYNC_BATCH_SIZE", "120")), 300))
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._last_result: Dict[str, Any] = {}
        self._last_error: Optional[str] = None
        self._last_cycle_at: Optional[float] = None
        self._last_watchlist_refresh_at: Optional[float] = None
        self.watchlist_interval_seconds = max(
            3600,
            min(int(os.getenv("CONCEPT_THEME_WATCHLIST_INTERVAL_SEC", "21600")), 86400),
        )
        self._cursor = 0

    @classmethod
    def get_instance(cls) -> "ConceptThemeWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run, name="concept-theme-worker", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self, timeout: float = 8.0) -> Dict[str, Any]:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, timeout))
        return self.status()

    def trigger(self) -> Dict[str, Any]:
        self.start()
        self._wake_event.set()
        return {**self.status(), "refresh_requested": True}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "batch_size": self.batch_size,
                "last_cycle_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_cycle_at)) if self._last_cycle_at else None,
                "last_result": self._last_result,
                "last_error": self._last_error,
                "cursor": self._cursor,
                "watchlist_interval_seconds": self.watchlist_interval_seconds,
            }

    def _run(self) -> None:
        service = ConceptThemeService()
        while not self._stop_event.is_set():
            try:
                result: Dict[str, Any] = {}
                status = service.sync_status()
                latest = status.get("latest_run") or {}
                last_market_date = str(latest.get("market_date") or "")
                if status["themes"] == 0 or last_market_date != service.latest_market_date().isoformat():
                    result["catalog"] = service.sync_catalog()
                now = time.time()
                if self._last_watchlist_refresh_at is None or now - self._last_watchlist_refresh_at >= self.watchlist_interval_seconds:
                    result["watchlist"] = self._refresh_watchlist(service)
                    self._last_watchlist_refresh_at = time.time()
                else:
                    result["watchlist"] = {"completed": 0, "failed": 0, "deferred": 1}
                result["progressive"] = self._refresh_progressive_batch(service)
                with self._lock:
                    self._last_cycle_at = time.time()
                    self._last_result = result
                    self._last_error = None
            except Exception as exc:  # noqa: BLE001 - one cycle must never stop future repair.
                logger.exception("[concept-theme] background refresh failed")
                with self._lock:
                    self._last_cycle_at = time.time()
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            self._wake_event.wait(self.interval_seconds)
            self._wake_event.clear()

    @staticmethod
    def _refresh_watchlist(service: ConceptThemeService) -> Dict[str, int]:
        db = DatabaseManager.get_instance()
        with db.session_scope() as session:
            codes = [row[0] for row in session.execute(select(UserWatchlistItem.symbol).distinct()).all()]
        completed = failed = 0
        for code in codes[:80]:
            try:
                service.refresh_stock(code, calculate=True)
                completed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("[concept-theme] watchlist refresh failed for %s: %s", code, exc)
        return {"completed": completed, "failed": failed}

    def _refresh_progressive_batch(self, service: ConceptThemeService) -> Dict[str, int]:
        db = DatabaseManager.get_instance()
        with db.session_scope() as session:
            rows = session.execute(
                select(ConceptThemeRecord.id)
                .order_by(asc(ConceptThemeRecord.id))
                .offset(self._cursor).limit(self.batch_size)
            ).all()
        if not rows and self._cursor:
            self._cursor = 0
            return {"completed": 0, "failed": 0, "remaining_cursor": 0}
        completed = failed = 0
        for (theme_id,) in rows:
            if self._stop_event.is_set():
                break
            try:
                service.refresh_theme(int(theme_id), calculate=False)
                completed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.debug("[concept-theme] progressive theme %s failed: %s", theme_id, exc)
            # Stay below Tushare's documented per-minute component limit and
            # leave CPU/network headroom for user-facing requests.
            time.sleep(0.42)
        self._cursor += len(rows)
        return {"completed": completed, "failed": failed, "remaining_cursor": self._cursor}
