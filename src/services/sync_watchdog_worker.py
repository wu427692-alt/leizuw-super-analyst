# -*- coding: utf-8 -*-
"""Supervise background data synchronizers and wake stalled incremental work."""

from __future__ import annotations

from datetime import datetime
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository

logger = logging.getLogger(__name__)


def _enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class SyncWatchdogWorker:
    """Keep configured sync workers alive and repair overdue synchronization.

    The watchdog does not fetch or persist data itself. It only inspects worker
    heartbeats and the shared source schedule, then restarts or wakes the
    existing idempotent incremental workers. This keeps one source of truth for
    cursor handling, deduplication and storage.
    """

    _instance: Optional["SyncWatchdogWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        investment_worker: Any = None,
        zsxq_worker: Any = None,
        market_worker: Any = None,
        repository_factory: Callable[[], InvestmentMonitorRepository] = InvestmentMonitorRepository,
        clock: Callable[[], float] = time.time,
    ):
        self.interval = max(10.0, min(float(os.getenv("SYNC_WATCHDOG_INTERVAL_SEC", "30")), 300.0))
        self.stale_multiplier = max(2.0, min(float(os.getenv("SYNC_WATCHDOG_STALE_MULTIPLIER", "4")), 20.0))
        self._investment_worker = investment_worker
        self._zsxq_worker = zsxq_worker
        self._market_worker = market_worker
        self._repository_factory = repository_factory
        self._clock = clock
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_audit_at: Optional[float] = None
        self._last_healthy_at: Optional[float] = None
        self._last_result: Dict[str, Any] = {}
        self._last_error: Optional[str] = None
        self._audit_count = 0
        self._repair_count = 0

    @classmethod
    def get_instance(cls) -> "SyncWatchdogWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._wake.clear()
                self._started_at = self._clock()
                self._thread = threading.Thread(
                    target=self._run,
                    name="sync-watchdog-worker",
                    daemon=True,
                )
                self._thread.start()
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(max(0.1, timeout))
        return self.status()

    def trigger(self) -> Dict[str, Any]:
        self.start()
        self._wake.set()
        return {**self.status(), "audit_requested": True}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval,
                "stale_multiplier": self.stale_multiplier,
                "started_at": self._iso(self._started_at),
                "last_audit_at": self._iso(self._last_audit_at),
                "last_healthy_at": self._iso(self._last_healthy_at),
                "audit_count": self._audit_count,
                "repair_count": self._repair_count,
                "last_error": self._last_error,
                "last_result": dict(self._last_result),
            }

    def audit_once(self) -> Dict[str, Any]:
        """Run one deterministic supervision pass; public for health APIs/tests."""
        started = self._clock()
        repairs = []
        workers: Dict[str, Dict[str, Any]] = {}
        errors = []

        if _enabled("INVESTMENT_MONITOR_AUTO_START"):
            worker = self._get_investment_worker()
            state = worker.status()
            workers["investment_monitor"] = state
            overdue = []
            try:
                overdue = [item["source_key"] for item in self._repository_factory().due_sources()]
            except Exception as exc:  # noqa: BLE001 - worker liveness repair should still proceed.
                errors.append(f"source_schedule:{type(exc).__name__}")
            if not state.get("running"):
                worker.start()
                repairs.append({"worker": "investment_monitor", "action": "restart"})
            elif overdue or self._heartbeat_stale(state, "last_sync_age_seconds", "poll_seconds", 60):
                worker.trigger()
                repairs.append({
                    "worker": "investment_monitor",
                    "action": "wake",
                    "overdue_sources": overdue,
                })
            workers["investment_monitor"] = worker.status()

        if _enabled("ZSXQ_MCP_AUTO_START", "false"):
            worker = self._get_zsxq_worker()
            state = worker.status()
            workers["zsxq_mcp"] = state
            if state.get("available"):
                if not state.get("running"):
                    worker.start()
                    repairs.append({"worker": "zsxq_mcp", "action": "restart"})
                elif not state.get("syncing") and self._heartbeat_stale(
                    state, "last_sync_age_seconds", "poll_seconds", 120,
                ):
                    worker.trigger()
                    repairs.append({"worker": "zsxq_mcp", "action": "wake"})
                workers["zsxq_mcp"] = worker.status()

        if _enabled("MARKET_DATA_AUTO_START"):
            worker = self._get_market_worker()
            state = worker.status()
            workers["market_data"] = state
            if not state.get("running"):
                worker.start()
                repairs.append({"worker": "market_data", "action": "restart"})
            elif state.get("collecting_window") and self._heartbeat_stale(
                state, "last_run_age_seconds", "poll_seconds", 5,
            ):
                worker.trigger()
                repairs.append({"worker": "market_data", "action": "wake"})
            workers["market_data"] = worker.status()

        result = {
            "status": "degraded" if errors else "repaired" if repairs else "healthy",
            "checked_at": self._iso(started),
            "duration_ms": max(0, round((self._clock() - started) * 1000)),
            "repairs": repairs,
            "workers": workers,
            "errors": errors,
        }
        with self._lock:
            self._last_audit_at = self._clock()
            self._audit_count += 1
            self._repair_count += len(repairs)
            self._last_result = result
            self._last_error = "; ".join(errors) or None
            if not errors and all(bool(item.get("running")) for item in workers.values()):
                self._last_healthy_at = self._last_audit_at
        if repairs:
            logger.warning("[sync-watchdog] repaired background synchronization: %s", repairs)
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.audit_once()
            except Exception as exc:  # noqa: BLE001 - supervisor must survive one broken audit.
                safe = f"{type(exc).__name__}: {str(exc)[:500]}"
                logger.exception("[sync-watchdog] audit failed: %s", safe)
                with self._lock:
                    self._last_audit_at = self._clock()
                    self._audit_count += 1
                    self._last_error = safe
            self._wake.wait(self.interval)
            self._wake.clear()

    def _heartbeat_stale(
        self,
        state: Dict[str, Any],
        age_key: str,
        interval_key: str,
        minimum_seconds: int,
    ) -> bool:
        age = state.get(age_key)
        if age is None:
            return True
        cadence = max(1.0, float(state.get(interval_key) or minimum_seconds))
        return float(age) > max(float(minimum_seconds), cadence * self.stale_multiplier)

    def _get_investment_worker(self) -> Any:
        if self._investment_worker is None:
            from src.services.investment_monitor_worker import InvestmentMonitorWorker

            self._investment_worker = InvestmentMonitorWorker.get_instance()
        return self._investment_worker

    def _get_zsxq_worker(self) -> Any:
        if self._zsxq_worker is None:
            from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncWorker

            self._zsxq_worker = ZsxqMcpSyncWorker.get_instance()
        return self._zsxq_worker

    def _get_market_worker(self) -> Any:
        if self._market_worker is None:
            from src.services.market_data_worker import MarketDataWorker

            self._market_worker = MarketDataWorker.get_instance()
        return self._market_worker

    @staticmethod
    def _iso(value: Optional[float]) -> Optional[str]:
        return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds") if value else None
