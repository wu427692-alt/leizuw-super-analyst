# -*- coding: utf-8 -*-
"""Durable background runner for watchlist six-month backfills."""

from __future__ import annotations

import logging
from queue import Empty, Queue
import threading
from typing import Any, Dict, Optional

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.investment_monitor_service import InvestmentMonitorService

logger = logging.getLogger(__name__)


class WatchlistBackfillWorker:
    _instance: Optional["WatchlistBackfillWorker"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.queue: Queue[Dict[str, Any]] = Queue()
        self._queued: set[int] = set()
        self._queued_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="watchlist-backfill-worker", daemon=True)
        self._thread.start()
        repository = InvestmentMonitorRepository()
        for job in repository.pending_backfill_jobs():
            self.enqueue_job(job)
        service = InvestmentMonitorService(repository=repository)
        existing = {job["symbol"] for job in repository.list_backfill_jobs(service.watchlist())}
        for symbol in service.watchlist():
            if symbol not in existing:
                self.enqueue_job(service.request_backfill(symbol, days=183))

    @classmethod
    def get_instance(cls) -> "WatchlistBackfillWorker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def enqueue(self, symbol: str, *, days: int = 183) -> Dict[str, Any]:
        job = InvestmentMonitorService().request_backfill(symbol, days=days)
        self.enqueue_job(job)
        return job

    def enqueue_job(self, job: Dict[str, Any]) -> None:
        job_id = int(job["id"])
        with self._queued_lock:
            if job_id in self._queued:
                return
            self._queued.add(job_id)
        self.queue.put(job)

    def _run(self) -> None:
        while True:
            try:
                job = self.queue.get(timeout=30)
            except Empty:
                continue
            job_id = int(job["id"])
            try:
                InvestmentMonitorService().run_backfill_job(
                    job_id, str(job["symbol"]), days=int(job.get("days") or 183)
                )
            except Exception:  # pragma: no cover - service records adapter failures.
                logger.exception("watchlist backfill crashed job=%s", job_id)
            finally:
                with self._queued_lock:
                    self._queued.discard(job_id)
                self.queue.task_done()
