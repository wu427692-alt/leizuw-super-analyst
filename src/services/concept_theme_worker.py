# -*- coding: utf-8 -*-
"""Low-impact background updater for the shared concept/theme graph."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from sqlalchemy import asc, select, update

from src.services.concept_theme_service import ConceptThemeService
from src.storage import (
    ConceptMembershipRecord,
    ConceptMembershipSyncState,
    ConceptSyncRunRecord,
    ConceptThemeRecord,
    DatabaseManager,
    UserWatchlistItem,
    utc_naive_now,
)

logger = logging.getLogger(__name__)

PROGRESSIVE_SOURCES = ("ths", "dc_board", "dc_theme", "kpl", "tdx", "sw")
REFRESH_REQUEST_TYPES = ("theme_refresh_request", "stock_refresh_request")
REFRESH_REQUEST_COOLDOWN_SECONDS = 900
REFRESH_REQUEST_LEASE_SECONDS = 7200


class ConceptThemeWorker:
    """Refresh catalogs daily and progressively fill memberships without blocking pages."""

    _instance: Optional["ConceptThemeWorker"] = None
    _instance_lock = threading.Lock()
    _request_lock = threading.Lock()

    def __init__(self):
        self.interval_seconds = max(300, min(int(os.getenv("CONCEPT_THEME_SYNC_INTERVAL_SEC", "300")), 21600))
        self.request_poll_seconds = 15
        self.batch_size = max(10, min(int(os.getenv("CONCEPT_THEME_SYNC_BATCH_SIZE", "120")), 300))
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._maintenance_requested = False
        self._last_result: Dict[str, Any] = {}
        self._last_error: Optional[str] = None
        self._last_cycle_at: Optional[float] = None
        self._last_watchlist_refresh_at: Optional[float] = None
        self._priority_watchlist_codes: set[str] = set()
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
        if os.getenv("CONCEPT_THEME_RUN_MODE", "embedded").strip().lower() == "external":
            return {**self.status(), "external": True, "deferred": True}
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
        with self._lock:
            self._maintenance_requested = True
        self.start()
        self._wake_event.set()
        return {**self.status(), "refresh_requested": True}

    def trigger_watchlist_refresh(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Wake the worker and bypass the periodic watchlist defer once.

        New symbols should not wait for the normal six-hour audit cadence. The
        actual refresh remains asynchronous so adding a watchlist item never
        blocks the user-facing request.
        """
        if os.getenv("CONCEPT_THEME_RUN_MODE", "embedded").strip().lower() == "external":
            # Production runs the expensive exposure calculator in the
            # low-priority collector process.  The external worker audits the
            # shared watchlist on its normal cadence, so an API request never
            # starts the same CPU-heavy job inside the web process.
            return {
                **self.status(),
                "external": True,
                "deferred": True,
                "watchlist_refresh_requested": True,
                "symbol": str(symbol or "").strip().upper() or None,
            }
        with self._lock:
            self._last_watchlist_refresh_at = None
            if symbol:
                self._priority_watchlist_codes.add(str(symbol).strip().upper())
        return {**self.trigger(), "watchlist_refresh_requested": True}

    def request_theme_refresh(self, theme_id: int, *, horizon_days: int = 60) -> Dict[str, Any]:
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        if int(theme_id) <= 0:
            raise ValueError("theme_id must be positive")
        return self._enqueue_refresh_request(
            "theme_refresh_request",
            {"theme_id": int(theme_id), "horizon_days": horizon},
            stage="题材详情等待后台补齐",
        )

    def request_stock_refresh(self, ts_code: str, *, horizon_days: int = 60) -> Dict[str, Any]:
        code = str(ts_code or "").strip().upper()
        horizon = min((20, 60, 120), key=lambda item: abs(item - int(horizon_days)))
        return self._enqueue_refresh_request(
            "stock_refresh_request", {"ts_code": code, "horizon_days": horizon},
            stage="个股题材等待后台补齐",
        )

    def _enqueue_refresh_request(
        self, run_type: str, payload: Dict[str, Any], *, stage: str,
    ) -> Dict[str, Any]:
        """Persist a small deduplicated request for the external/embedded worker.

        ``ConceptSyncRunRecord`` already provides a durable status ledger, so
        no process-local queue is lost when production runs the API and worker
        separately.  The class lock closes the common same-process race; the
        worker claim below is still conditional for cross-process safety.
        """
        if run_type not in REFRESH_REQUEST_TYPES:
            raise ValueError("unsupported concept refresh request")
        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = utc_naive_now()
        cooldown_cutoff = now - timedelta(seconds=REFRESH_REQUEST_COOLDOWN_SECONDS)
        with self.__class__._request_lock:
            db = DatabaseManager.get_instance()
            with db.session_scope() as session:
                active = session.execute(select(ConceptSyncRunRecord).where(
                    ConceptSyncRunRecord.run_type == run_type,
                    ConceptSyncRunRecord.source_stats_json == payload_text,
                    ConceptSyncRunRecord.status.in_(("queued", "running")),
                ).order_by(ConceptSyncRunRecord.id.desc()).limit(1)).scalar_one_or_none()
                if active is not None:
                    result = {
                        "accepted": True, "queued": active.status == "queued", "deduplicated": True,
                        "request_id": int(active.id), "status": active.status,
                    }
                else:
                    recent = session.execute(select(ConceptSyncRunRecord).where(
                        ConceptSyncRunRecord.run_type == run_type,
                        ConceptSyncRunRecord.source_stats_json == payload_text,
                        ConceptSyncRunRecord.status.in_(("completed", "failed")),
                        ConceptSyncRunRecord.updated_at >= cooldown_cutoff,
                    ).order_by(ConceptSyncRunRecord.id.desc()).limit(1)).scalar_one_or_none()
                    if recent is not None:
                        result = {
                            "accepted": True, "queued": False, "deduplicated": True,
                            "request_id": int(recent.id), "status": "cooldown",
                        }
                    else:
                        row = session.execute(select(ConceptSyncRunRecord).where(
                            ConceptSyncRunRecord.run_type == run_type,
                            ConceptSyncRunRecord.source_stats_json == payload_text,
                        ).order_by(ConceptSyncRunRecord.id.desc()).limit(1)).scalar_one_or_none()
                        if row is None:
                            row = ConceptSyncRunRecord(
                                run_type=run_type, status="queued", market_date=None, progress=0,
                                stage=stage, source_stats_json=payload_text,
                                started_at=now, updated_at=now,
                            )
                            session.add(row)
                            session.flush()
                        else:
                            row.status = "queued"
                            row.progress = 0
                            row.stage = stage
                            row.error = None
                            row.started_at = now
                            row.finished_at = None
                            row.updated_at = now
                        result = {
                            "accepted": True, "queued": True, "deduplicated": False,
                            "request_id": int(row.id), "status": "queued",
                        }
        if os.getenv("CONCEPT_THEME_RUN_MODE", "embedded").strip().lower() != "external":
            self.start()
            # This wake-up is request-only.  It must not turn a page GET into
            # an immediate full catalog/reconciliation maintenance pass.
            self._wake_event.set()
        else:
            result["external"] = True
            result["deferred"] = True
        return result

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "request_poll_seconds": self.request_poll_seconds,
                "batch_size": self.batch_size,
                "last_cycle_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_cycle_at)) if self._last_cycle_at else None,
                "last_result": self._last_result,
                "last_error": self._last_error,
                "cursor": None,
                "resume_mode": "oldest-attempt-first",
                "watchlist_interval_seconds": self.watchlist_interval_seconds,
                "priority_watchlist_codes": len(self._priority_watchlist_codes),
            }

    def _run(self) -> None:
        service = ConceptThemeService()
        startup_result: Dict[str, Any] = {}
        try:
            startup_result["legacy_ledger_rows"] = self._bootstrap_membership_state()
            startup_result["normalization"] = service.normalize_catalog_names()
            startup_result["snapshot_seed"] = service.backfill_current_snapshots()
            startup_result["provider_consensus"] = service.reconcile_exposure_provider_counts(limit=160)
            startup_result["window_alpha"] = service.reconcile_window_alpha_compounding(limit=3000)
            startup_result["rotation_history"] = service.backfill_rotation_history(days=20, dates_per_run=5)
            startup_result["multi_horizon"] = service.backfill_multi_horizon_profiles(stock_limit=2, themes_per_stock=3)
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
                if status["themes"] > 0:
                    # Page-triggered fills outrank a broad daily catalog pass
                    # once a usable catalog already exists.
                    result["refresh_requests"] = self._process_refresh_requests(service)
                if status["themes"] == 0 or last_market_date != service.latest_market_date(allow_upstream=True).isoformat():
                    result["catalog"] = service.sync_catalog()
                if status["themes"] == 0:
                    result["refresh_requests"] = self._process_refresh_requests(service)
                now = time.time()
                if self._last_watchlist_refresh_at is None or now - self._last_watchlist_refresh_at >= self.watchlist_interval_seconds:
                    result["watchlist"] = self._refresh_watchlist(service)
                    self._last_watchlist_refresh_at = time.time()
                else:
                    result["watchlist"] = {"completed": 0, "failed": 0, "deferred": 1}
                result["progressive"] = self._refresh_progressive_batch(service)
                result["provider_consensus"] = service.reconcile_exposure_provider_counts(limit=80)
                result["window_alpha"] = service.reconcile_window_alpha_compounding(limit=1000)
                result["rotation_history"] = service.backfill_rotation_history(days=20, dates_per_run=2)
                result["multi_horizon"] = service.backfill_multi_horizon_profiles(stock_limit=2, themes_per_stock=3)
                with self._lock:
                    self._last_cycle_at = time.time()
                    self._last_result = result
                    self._last_error = None
            except Exception as exc:  # noqa: BLE001 - one cycle must never stop future repair.
                logger.exception("[concept-theme] background refresh failed")
                with self._lock:
                    self._last_cycle_at = time.time()
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            self._wait_for_next_cycle(service)

    def _wait_for_next_cycle(self, service: ConceptThemeService) -> None:
        """Poll durable requests frequently without rerunning heavy maintenance."""
        deadline = time.monotonic() + self.interval_seconds
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            signalled = self._wake_event.wait(min(float(self.request_poll_seconds), remaining))
            self._wake_event.clear()
            if self._stop_event.is_set():
                return
            if signalled:
                with self._lock:
                    maintenance_requested = self._maintenance_requested
                    self._maintenance_requested = False
                if maintenance_requested:
                    # Explicit admin/watchlist triggers preserve their existing
                    # behavior and wake the complete maintenance cycle.
                    return
            try:
                request_result = self._process_refresh_requests(service)
                if request_result["attempted"]:
                    with self._lock:
                        self._last_result = {**self._last_result, "refresh_requests": request_result}
                        self._last_error = None
            except Exception as exc:  # A queue polling failure must not kill the worker.
                logger.warning("[concept-theme] refresh request poll failed: %s", exc)
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:500]}"

    def _process_refresh_requests(
        self, service: ConceptThemeService, *, limit: int = 4,
    ) -> Dict[str, int]:
        """Claim and execute a bounded number of durable page-fill requests."""
        self._recover_stale_refresh_requests()
        completed = failed = attempted = 0
        for _ in range(max(1, min(int(limit), 12))):
            claimed = self._claim_refresh_request()
            if claimed is None:
                break
            attempted += 1
            request_id, run_type, payload = claimed
            try:
                if run_type == "theme_refresh_request":
                    result = service.refresh_theme_bundle(
                        int(payload["theme_id"]), horizon_days=int(payload.get("horizon_days") or 60),
                    )
                else:
                    result = service.refresh_stock_bundle(
                        str(payload["ts_code"]), horizon_days=int(payload.get("horizon_days") or 60),
                    )
                self._finish_refresh_request(request_id, status="completed", result=result)
                completed += 1
            except Exception as exc:  # noqa: BLE001 - one request must not stop later work.
                logger.warning("[concept-theme] queued %s failed: %s", run_type, exc)
                self._finish_refresh_request(request_id, status="failed", error=exc)
                failed += 1
        return {"attempted": attempted, "completed": completed, "failed": failed}

    @staticmethod
    def _recover_stale_refresh_requests() -> int:
        """Release only genuinely stale claims left by a terminated worker."""
        db = DatabaseManager.get_instance()
        now = utc_naive_now()
        cutoff = now - timedelta(seconds=REFRESH_REQUEST_LEASE_SECONDS)
        with db.session_scope() as session:
            result = session.execute(update(ConceptSyncRunRecord).where(
                ConceptSyncRunRecord.run_type.in_(REFRESH_REQUEST_TYPES),
                ConceptSyncRunRecord.status == "running",
                ConceptSyncRunRecord.updated_at < cutoff,
            ).values(
                status="queued", progress=0, stage="后台租约超时，等待安全重试",
                error=None, updated_at=now,
            ))
            return int(result.rowcount or 0)

    @classmethod
    def _claim_refresh_request(cls) -> Optional[tuple[int, str, Dict[str, Any]]]:
        db = DatabaseManager.get_instance()
        with cls._request_lock:
            # A conditional UPDATE prevents two worker threads/processes from
            # owning the same row even if both observed it while queued.
            for _ in range(4):
                with db.session_scope() as session:
                    row = session.execute(select(ConceptSyncRunRecord).where(
                        ConceptSyncRunRecord.run_type.in_(REFRESH_REQUEST_TYPES),
                        ConceptSyncRunRecord.status == "queued",
                    ).order_by(ConceptSyncRunRecord.id).limit(1)).scalar_one_or_none()
                    if row is None:
                        return None
                    request_id = int(row.id)
                    run_type = str(row.run_type)
                    payload_text = str(row.source_stats_json or "{}")
                    now = utc_naive_now()
                    claimed = session.execute(update(ConceptSyncRunRecord).where(
                        ConceptSyncRunRecord.id == request_id,
                        ConceptSyncRunRecord.status == "queued",
                    ).values(
                        status="running", progress=5, stage="后台补齐中", updated_at=now,
                    ))
                    if int(claimed.rowcount or 0) != 1:
                        continue
                try:
                    payload = json.loads(payload_text)
                except (TypeError, ValueError) as exc:
                    cls._finish_refresh_request(request_id, status="failed", error=exc)
                    continue
                if not isinstance(payload, dict):
                    cls._finish_refresh_request(
                        request_id, status="failed", error=ValueError("invalid refresh payload"),
                    )
                    continue
                return request_id, run_type, payload
        return None

    @staticmethod
    def _finish_refresh_request(
        request_id: int, *, status: str, result: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        now = utc_naive_now()
        with DatabaseManager.get_instance().session_scope() as session:
            values: Dict[str, Any] = {
                "status": status, "progress": 100,
                "stage": "后台补齐完成" if status == "completed" else "后台补齐失败",
                "error": f"{type(error).__name__}: {str(error)[:500]}" if error else None,
                "finished_at": now, "updated_at": now,
            }
            if result is not None:
                values["memberships_seen"] = int(result.get("memberships") or result.get("saved") or 0)
                values["exposures_seen"] = int(result.get("exposures") or 0)
            session.execute(update(ConceptSyncRunRecord).where(
                ConceptSyncRunRecord.id == int(request_id),
                ConceptSyncRunRecord.status == "running",
            ).values(**values))

    def _refresh_watchlist(self, service: ConceptThemeService) -> Dict[str, int]:
        db = DatabaseManager.get_instance()
        with db.session_scope() as session:
            stored_codes = [row[0] for row in session.execute(select(UserWatchlistItem.symbol).distinct()).all()]
        with self._lock:
            priority_codes = sorted(self._priority_watchlist_codes)
            self._priority_watchlist_codes.clear()
        codes = list(dict.fromkeys([*priority_codes, *stored_codes]))
        completed = failed = 0
        for code in codes[:80]:
            try:
                service.refresh_stock(code, calculate=True)
                completed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("[concept-theme] watchlist refresh failed for %s: %s", code, exc)
        return {"completed": completed, "failed": failed, "priority": len(priority_codes)}

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
