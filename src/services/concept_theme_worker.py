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
from src.storage import (
    ConceptMembershipRecord,
    ConceptMembershipSyncState,
    ConceptThemeRecord,
    DatabaseManager,
    UserWatchlistItem,
    utc_naive_now,
)

logger = logging.getLogger(__name__)

PROGRESSIVE_SOURCES = ("ths", "dc_board", "dc_theme", "kpl", "tdx", "sw")


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
                "cursor": None,
                "resume_mode": "oldest-attempt-first",
                "watchlist_interval_seconds": self.watchlist_interval_seconds,
            }

    def _run(self) -> None:
        service = ConceptThemeService()
        startup_result: Dict[str, Any] = {}
        try:
            startup_result["legacy_ledger_rows"] = self._bootstrap_membership_state()
            startup_result["normalization"] = service.normalize_catalog_names()
            startup_result["snapshot_seed"] = service.backfill_current_snapshots()
        except Exception as exc:  # noqa: BLE001 - startup repair must not kill recurring updates.
            logger.exception("[concept-theme] startup normalization failed")
            startup_result["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        while not self._stop_event.is_set():
            try:
                result: Dict[str, Any] = {"startup": startup_result} if startup_result else {}
                startup_result = {}
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

    @staticmethod
    def _bootstrap_membership_state() -> int:
        """Mark legacy membered themes as known so deployments prioritize genuinely unseen nodes."""
        db = DatabaseManager.get_instance()
        now = utc_naive_now()
        with db.session_scope() as session:
            existing = set(session.execute(select(ConceptMembershipSyncState.theme_id)).scalars().all())
            membered = set(session.execute(select(ConceptMembershipRecord.theme_id).distinct()).scalars().all())
            missing = sorted(membered - existing)
            session.add_all([
                ConceptMembershipSyncState(
                    theme_id=int(theme_id), status="completed", members_received=0, members_saved=0,
                    attempts=1, last_attempt_at=now, last_success_at=now, updated_at=now,
                )
                for theme_id in missing
            ])
        return len(missing)

    def _refresh_progressive_batch(self, service: ConceptThemeService) -> Dict[str, int]:
        db = DatabaseManager.get_instance()
        rows = self._next_progressive_theme_ids(db, self.batch_size)
        if not rows:
            return {"completed": 0, "failed": 0, "attempted_themes": 0, "sources": {}}
        completed = failed = 0
        source_breakdown: Dict[str, int] = {}
        for theme_id, source in rows:
            if self._stop_event.is_set():
                break
            try:
                result = service.refresh_theme(int(theme_id), calculate=False)
                completed += 1
                source_breakdown[source] = source_breakdown.get(source, 0) + 1
                self._record_membership_attempt(
                    db, int(theme_id), status="completed",
                    received=int(result.get("received") or 0), saved=int(result.get("saved") or 0),
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                source_breakdown[source] = source_breakdown.get(source, 0) + 1
                self._record_membership_attempt(db, int(theme_id), status="failed", error=exc)
                logger.debug("[concept-theme] progressive theme %s failed: %s", theme_id, exc)
            # Stay below Tushare's documented per-minute component limit and
            # leave CPU/network headroom for user-facing requests.
            time.sleep(0.42)
        with db.session_scope() as session:
            attempted = len(session.execute(select(ConceptMembershipSyncState.theme_id)).all())
        return {"completed": completed, "failed": failed, "attempted_themes": attempted, "sources": source_breakdown}

    @staticmethod
    def _next_progressive_theme_ids(db: DatabaseManager, batch_size: int) -> list[tuple[int, str]]:
        """Reserve a fair share for every source before filling spare batch slots."""
        limit = max(1, int(batch_size))
        per_source = max(1, limit // len(PROGRESSIVE_SOURCES))
        selected: list[tuple[int, str]] = []
        selected_ids: set[int] = set()
        with db.session_scope() as session:
            for source in PROGRESSIVE_SOURCES:
                if len(selected) >= limit:
                    break
                source_rows = session.execute(select(
                    ConceptThemeRecord.id, ConceptThemeRecord.source,
                ).outerjoin(
                    ConceptMembershipSyncState, ConceptMembershipSyncState.theme_id == ConceptThemeRecord.id,
                ).where(
                    ConceptThemeRecord.source == source,
                ).order_by(
                    asc(ConceptMembershipSyncState.last_attempt_at), asc(ConceptThemeRecord.id),
                ).limit(min(per_source, limit - len(selected)))).all()
                for theme_id, source_name in source_rows:
                    numeric_id = int(theme_id)
                    if numeric_id not in selected_ids:
                        selected.append((numeric_id, str(source_name)))
                        selected_ids.add(numeric_id)
            if len(selected) < limit:
                fill_rows = session.execute(select(
                    ConceptThemeRecord.id, ConceptThemeRecord.source,
                ).outerjoin(
                    ConceptMembershipSyncState, ConceptMembershipSyncState.theme_id == ConceptThemeRecord.id,
                ).where(
                    ConceptThemeRecord.id.notin_(selected_ids) if selected_ids else ConceptThemeRecord.id > 0,
                ).order_by(
                    asc(ConceptMembershipSyncState.last_attempt_at), asc(ConceptThemeRecord.id),
                ).limit(limit - len(selected))).all()
                selected.extend((int(theme_id), str(source_name)) for theme_id, source_name in fill_rows)
        return selected

    @staticmethod
    def _record_membership_attempt(
        db: DatabaseManager, theme_id: int, *, status: str,
        received: int = 0, saved: int = 0, error: Optional[Exception] = None,
    ) -> None:
        now = utc_naive_now()
        with db.session_scope() as session:
            state = session.execute(select(ConceptMembershipSyncState).where(
                ConceptMembershipSyncState.theme_id == theme_id,
            )).scalar_one_or_none()
            if state is None:
                state = ConceptMembershipSyncState(theme_id=theme_id)
                session.add(state)
            state.status = status
            state.members_received = received
            state.members_saved = saved
            state.attempts = int(state.attempts or 0) + 1
            state.error = f"{type(error).__name__}: {str(error)[:500]}" if error else None
            state.last_attempt_at = now
            if status == "completed":
                state.last_success_at = now
            state.updated_at = now
