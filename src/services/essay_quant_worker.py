# -*- coding: utf-8 -*-
"""Debounced background precomputation for essay institution rankings."""

from __future__ import annotations

import logging
import json
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class EssayQuantWorker:
    _instance: Optional["EssayQuantWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self.min_interval = max(300.0, min(float(os.getenv("ESSAY_QUANT_MIN_INTERVAL_SEC", "1800")), 86400.0))
        self.price_refresh_interval = max(3600.0, min(float(os.getenv("ESSAY_QUANT_PRICE_REFRESH_SEC", "21600")), 604800.0))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._dirty = True
        self._last_started_at: Optional[float] = None
        self._last_completed_at: Optional[float] = None
        self._last_price_refresh_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._reason = "startup"
        self._restore_latest_result()

    @classmethod
    def get_instance(cls) -> "EssayQuantWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="essay-quant-worker", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set(); self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        return self.status()

    def request_refresh(self, reason: str = "essay_update", *, force: bool = False) -> Dict[str, Any]:
        with self._state_lock:
            self._dirty = True
            self._reason = str(reason or "essay_update")[:80]
            if force:
                self._last_completed_at = None
        self._wake.set()
        return self.status()

    def run_now(self, *, refresh_prices: bool = True) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"status": "already_running", **self.status()}
        try:
            return self._compute(refresh_prices=refresh_prices)
        finally:
            self._run_lock.release()

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "computing": self._run_lock.locked(),
                "dirty": self._dirty,
                "reason": self._reason,
                "min_interval_seconds": self.min_interval,
                "last_started_at": self._iso(self._last_started_at),
                "last_completed_at": self._iso(self._last_completed_at),
                "last_price_refresh_at": self._iso(self._last_price_refresh_at),
                "last_error": self._last_error,
                "last_result": self._last_result,
            }

    def _loop(self) -> None:
        # Let API startup and the initial MCP cursor check settle first.
        self._stop.wait(8.0)
        while not self._stop.is_set():
            with self._state_lock:
                dirty = self._dirty
                last_completed = self._last_completed_at or 0.0
                previous = self._last_result or {}
                coverage_pending = int(previous.get("priced_symbol_count") or 0) < int(previous.get("resolved_symbol_count") or 0)
                # Materialize a complete local ranking first; network hydration
                # starts from the next throttled cycle so the page is useful
                # within seconds after application startup.
                price_due = bool(previous) and (coverage_pending or time.time() - (self._last_price_refresh_at or 0.0) >= self.price_refresh_interval)
            if dirty and time.time() - last_completed >= self.min_interval and self._run_lock.acquire(blocking=False):
                try:
                    self._compute(refresh_prices=price_due)
                finally:
                    self._run_lock.release()
            self._wake.wait(60.0); self._wake.clear()

    def _compute(self, *, refresh_prices: bool) -> Dict[str, Any]:
        started = time.time()
        with self._state_lock:
            self._last_started_at = started; self._last_error = None
        try:
            command = [sys.executable, "-m", "src.services.essay_quant_job"]
            if refresh_prices:
                command.append("--refresh-prices")
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[2],
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "background job failed").strip().splitlines()[-1]
                raise RuntimeError(detail[:400])
            output = completed.stdout.strip().splitlines()
            compact = json.loads(output[-1]) if output else {}
            if not isinstance(compact, dict):
                raise RuntimeError("background job returned invalid result")
            with self._state_lock:
                self._dirty = int(compact["priced_symbol_count"] or 0) < int(compact["resolved_symbol_count"] or 0)
                if self._dirty:
                    self._reason = "price_coverage_backfill"
                self._last_completed_at = time.time(); self._last_result = compact
                if refresh_prices:
                    self._last_price_refresh_at = self._last_completed_at
            return {"status": "completed", **compact}
        except Exception as exc:  # noqa: BLE001
            safe = f"{type(exc).__name__}: {str(exc)[:400]}"
            logger.warning("[essay-quant] background precompute failed: %s", safe)
            with self._state_lock:
                self._last_error = safe; self._last_completed_at = time.time()
            return {"status": "failed", "error": safe}

    def _restore_latest_result(self) -> None:
        """Reuse a recent baseline after app restart; MCP changes will mark it dirty."""
        try:
            from src.services.essay_quant_service import EssayQuantService

            result = EssayQuantService().latest_institution_dashboard()
            if result.get("rule", {}).get("name") != "后台全量机构预计算":
                return
            generated = str(result.get("generated_at") or "").replace("Z", "+00:00")
            generated_at = datetime.fromisoformat(generated).timestamp() if generated else None
            if not generated_at or time.time() - generated_at > self.price_refresh_interval:
                return
            summary = result.get("summary") or {}
            quality = result.get("data_quality") or {}
            self._last_result = {
                "run_id": result.get("run_id"),
                "event_count": summary.get("event_count", 0),
                "mature_event_count": summary.get("mature_event_count", 0),
                "ranked_group_count": len(result.get("research_group_rankings") or []),
                "generated_at": result.get("generated_at"),
                "resolved_symbol_count": quality.get("resolved_symbol_count", 0),
                "priced_symbol_count": quality.get("priced_symbol_count", 0),
            }
            self._last_completed_at = generated_at
            self._last_price_refresh_at = generated_at
            self._dirty = int(self._last_result["priced_symbol_count"] or 0) < int(self._last_result["resolved_symbol_count"] or 0)
            self._reason = "price_coverage_backfill" if self._dirty else "restored_recent_baseline"
        except Exception:  # noqa: BLE001 - absence of a baseline simply triggers a fresh run.
            logger.debug("[essay-quant] no recent baseline restored", exc_info=True)

    @staticmethod
    def _iso(value: Optional[float]) -> Optional[str]:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None
