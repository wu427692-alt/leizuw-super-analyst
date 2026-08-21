# -*- coding: utf-8 -*-
"""Background worker that continuously analyzes queued research notes with DeepSeek."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from src.repositories.essay_analysis_repo import EssayAnalysisRepository
from src.services.essay_analysis_service import (
    DeepSeekEssayAnalyzer,
    ESSAY_PROMPT_VERSION,
    EssayAnalysisError,
    EssayAnalysisService,
)

logger = logging.getLogger(__name__)


class EssayAnalysisWorker:
    """Singleton lifecycle wrapper for a durable, restart-safe analysis queue."""

    _instance: Optional["EssayAnalysisWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.batch_size = max(1, min(int(os.getenv("ESSAY_ANALYSIS_BATCH_SIZE", "12")), 30))
        # DeepSeek V4 Flash supports very high API concurrency. Keep the
        # operational default conservative while allowing an explicit scale-up.
        self.concurrency = max(1, min(int(os.getenv("ESSAY_ANALYSIS_CONCURRENCY", "4")), 2000))
        self.poll_seconds = max(1.0, min(float(os.getenv("ESSAY_ANALYSIS_POLL_SEC", "5")), 60.0))
        self.max_attempts = max(1, min(int(os.getenv("ESSAY_ANALYSIS_MAX_ATTEMPTS", "4")), 10))
        self.backfill_days = max(1, min(int(os.getenv("ESSAY_ANALYSIS_BACKFILL_DAYS", "30")), 3650))
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_batch_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._processed = 0
        self._failed = 0

    @classmethod
    def get_instance(cls) -> "EssayAnalysisWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self, *, bootstrap_recent: bool = True) -> Dict[str, Any]:
        analyzer = DeepSeekEssayAnalyzer()
        if not analyzer.configured:
            raise EssayAnalysisError("DEEPSEEK_API_KEY is not configured")
        with self._state_lock:
            already_running = self._thread is not None and self._thread.is_alive()
            if not already_running:
                self._stop_event.clear()
                self._started_at = time.time()
                self._last_error = None
                self._thread = threading.Thread(
                    target=self._run,
                    args=(bootstrap_recent,),
                    name="essay-analysis-worker",
                    daemon=True,
                )
                self._thread.start()
        # Never call status() while holding _state_lock. The worker is commonly
        # already active when a user adds historical notes; the old nested lock
        # acquisition deadlocked that request until the browser timed out.
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
                "batch_size": self.batch_size,
                "concurrency": self.concurrency,
                "poll_seconds": self.poll_seconds,
                "max_attempts": self.max_attempts,
                "backfill_days": self.backfill_days,
                "started_at": self._iso_time(self._started_at),
                "last_batch_at": self._iso_time(self._last_batch_at),
                "last_error": self._last_error,
                "processed_in_process": self._processed,
                "failed_in_process": self._failed,
            }

    def _run(self, bootstrap_recent: bool = True) -> None:
        repository = EssayAnalysisRepository()
        service = EssayAnalysisService(repository=repository)
        try:
            recovered = repository.recover_stale()
            if recovered:
                logger.info("[essay-radar] recovered %s stale processing tasks", recovered)
            repaired = repository.requeue_low_quality_completed(
                model=DeepSeekEssayAnalyzer().model,
                prompt_version=ESSAY_PROMPT_VERSION,
                limit=max(1, min(int(os.getenv("ESSAY_ANALYSIS_AUTO_REPAIR_LIMIT", "5000")), 50000)),
            )
            if repaired:
                logger.warning("[essay-radar] automatically requeued %s placeholder summaries", repaired)
            if bootstrap_recent:
                service.enqueue_recent(days=self.backfill_days)
            with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="essay-deepseek") as executor:
                futures: set[Future] = set()
                while not self._stop_event.is_set():
                    # Keep every slot busy. Waiting for a whole wave means one
                    # unusually slow API request leaves the other slots idle.
                    while len(futures) < self.concurrency and not self._stop_event.is_set():
                        work = repository.claim_batch(
                            limit=self.batch_size,
                            max_attempts=self.max_attempts,
                        )
                        if not work:
                            break
                        futures.add(executor.submit(self._process_batch, work))
                    if not futures:
                        self._stop_event.wait(self.poll_seconds)
                        continue
                    completed, _ = wait(
                        futures,
                        timeout=self.poll_seconds,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in completed:
                        futures.remove(future)
                        result = future.result()
                        with self._state_lock:
                            self._processed += result["saved"]
                            self._failed += result["failed"]
                            self._last_batch_at = time.time()
                            if result.get("error"):
                                self._last_error = result["error"]
        except Exception as exc:  # noqa: BLE001 - worker must surface state instead of crashing the app.
            safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.exception("[essay-radar] worker stopped unexpectedly: %s", safe_error)
            with self._state_lock:
                self._last_error = safe_error
        finally:
            self._stop_event.set()

    def _process_batch(self, batch: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        repository = EssayAnalysisRepository()
        topic_ids = [str(item["topic_id"]) for item in batch]
        analyzer = DeepSeekEssayAnalyzer()
        try:
            response = analyzer.analyze_batch(batch)
            results = list(response["items"])
            saved = repository.save_successes(
                results,
                raw_response=response["raw_response"],
                usage=response["usage"],
            )
            successful_ids = {str(item["topic_id"]) for item in results}
            missing_ids = [topic_id for topic_id in topic_ids if topic_id not in successful_ids]
            recovered_ids: set[str] = set()
            recovery_errors: List[str] = []
            if missing_ids and len(batch) > 1:
                work_by_id = {str(item["topic_id"]): item for item in batch}
                # A large JSON response can omit or under-fill one item while
                # all other entries are valid. Retry only those topics as
                # single-note requests before waiting for the durable queue.
                for topic_id in missing_ids:
                    try:
                        single_response = analyzer.analyze_batch([work_by_id[topic_id]])
                        single_results = list(single_response["items"])
                        if not single_results:
                            continue
                        saved += repository.save_successes(
                            single_results,
                            raw_response=single_response["raw_response"],
                            usage=single_response["usage"],
                        )
                        recovered_ids.add(topic_id)
                    except Exception as exc:  # noqa: BLE001 - remaining item stays retryable.
                        recovery_errors.append(f"{topic_id}:{type(exc).__name__}")
            missing_ids = [topic_id for topic_id in missing_ids if topic_id not in recovered_ids]
            if missing_ids:
                repository.save_failures(
                    missing_ids,
                    error_message="DeepSeek omitted the topic or returned a placeholder summary",
                    retry_delay_seconds=30,
                )
            return {
                "saved": saved,
                "failed": len(missing_ids),
                "error": "; ".join(recovery_errors[:5]) or None,
            }
        except Exception as exc:  # noqa: BLE001 - persist retryable task state for every batch failure.
            delay = min(30 * (2 ** max(int(item.get("attempt_count") or 1) - 1 for item in batch)), 900)
            safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            repository.save_failures(
                topic_ids,
                error_message=safe_error,
                retry_delay_seconds=delay,
            )
            logger.warning("[essay-radar] batch failed (%s notes): %s", len(batch), safe_error)
            return {"saved": 0, "failed": len(batch), "error": safe_error}

    @staticmethod
    def _iso_time(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
