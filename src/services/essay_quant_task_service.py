# -*- coding: utf-8 -*-
"""Durable, owner-scoped background queue for essay quantitative research."""

from __future__ import annotations

import json
import logging
from queue import Empty, Queue
import threading
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select

from src.request_identity import current_owner_id
from src.services.essay_quant_service import EssayQuantService
from src.services.run_diagnostics import sanitize_diagnostic_text
from src.storage import DatabaseManager, EssayQuantTaskRecord, utc_naive_now

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = ("queued", "running")
_OWNER_UNSET = object()


class EssayQuantTaskManager:
    """Run quant jobs after the HTTP request ends and persist their lifecycle."""

    _instance: Optional["EssayQuantTaskManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db: Optional[DatabaseManager] = None, worker_count: int = 2) -> None:
        self.db = db or DatabaseManager.get_instance()
        self.worker_count = max(1, min(int(worker_count), 4))
        self._queue: Queue[str] = Queue()
        self._workers: List[threading.Thread] = []
        self._scheduled: set[str] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._started = False

    @classmethod
    def get_instance(cls) -> "EssayQuantTaskManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    def start(self) -> None:
        with self._lock:
            if self._started and any(worker.is_alive() for worker in self._workers):
                return
            self._stop.clear()
            self._started = True
            self._workers = [
                threading.Thread(
                    target=self._worker_loop,
                    name=f"essay-quant-task-{index + 1}",
                    daemon=True,
                )
                for index in range(self.worker_count)
            ]
            for worker in self._workers:
                worker.start()

        # A process may have stopped while a task was running. The immutable
        # run snapshot is only linked after success, so replaying it is safe.
        with self.db.get_session() as session:
            session.query(EssayQuantTaskRecord).filter(
                EssayQuantTaskRecord.status == "running",
                EssayQuantTaskRecord.result_run_id.is_(None),
            ).update({
                EssayQuantTaskRecord.status: "queued",
                EssayQuantTaskRecord.progress: 0,
                EssayQuantTaskRecord.message: "服务重启后已恢复到后台队列",
                EssayQuantTaskRecord.started_at: None,
                EssayQuantTaskRecord.updated_at: utc_naive_now(),
            }, synchronize_session=False)
            pending = session.execute(
                select(EssayQuantTaskRecord.task_id)
                .where(EssayQuantTaskRecord.status == "queued")
                .order_by(EssayQuantTaskRecord.id)
            ).scalars().all()
            session.commit()
        for task_id in pending:
            self._enqueue(task_id)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for worker in list(self._workers):
            if worker.is_alive():
                worker.join(timeout=max(0.0, timeout / max(1, len(self._workers))))
        with self._lock:
            self._workers = []
            self._started = False

    def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        owner_id = current_owner_id()
        task_id = uuid.uuid4().hex
        request_payload = {
            "rule": dict(payload),
            "refresh_prices": bool(payload.get("refresh_prices", False)),
            "max_symbols": max(2, min(int(payload.get("max_symbols") or 30), 60)),
            "rule_id": payload.get("rule_id"),
        }
        request_payload["rule"].pop("refresh_prices", None)
        request_payload["rule"].pop("max_symbols", None)
        request_payload["rule"].pop("rule_id", None)
        with self.db.get_session() as session:
            session.add(EssayQuantTaskRecord(
                task_id=task_id,
                owner_id=owner_id,
                status="queued",
                progress=0,
                message="任务已加入后台队列，可以离开当前页面",
                request_json=json.dumps(request_payload, ensure_ascii=False),
            ))
            session.commit()
        self.start()
        self._enqueue(task_id)
        task = self.get_task(task_id, owner_id=owner_id)
        if task is None:  # pragma: no cover - the committed row must be readable.
            raise RuntimeError("量化任务创建后无法读取")
        return task

    def list_tasks(self, limit: int = 50) -> Dict[str, Any]:
        owner_id = current_owner_id()
        safe_limit = max(1, min(int(limit), 100))
        with self.db.get_session() as session:
            clause = self._owner_clause(owner_id)
            total = int(session.execute(
                select(func.count(EssayQuantTaskRecord.id)).where(clause)
            ).scalar_one() or 0)
            rows = session.execute(
                select(EssayQuantTaskRecord)
                .where(clause)
                .order_by(desc(EssayQuantTaskRecord.id))
                .limit(safe_limit)
            ).scalars().all()
        return {"items": [self._task_dict(row) for row in rows], "total": total}

    def get_task(self, task_id: str, *, owner_id: object = _OWNER_UNSET) -> Optional[Dict[str, Any]]:
        effective_owner = current_owner_id() if owner_id is _OWNER_UNSET else owner_id
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayQuantTaskRecord).where(
                    EssayQuantTaskRecord.task_id == str(task_id),
                    self._owner_clause(effective_owner),
                )
            ).scalar_one_or_none()
            return self._task_dict(row) if row is not None else None

    def _enqueue(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._scheduled:
                return
            self._scheduled.add(task_id)
            self._queue.put(task_id)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task_id = self._queue.get(timeout=0.25)
            except Empty:
                continue
            try:
                self._execute(task_id)
            finally:
                with self._lock:
                    self._scheduled.discard(task_id)
                self._queue.task_done()

    def _execute(self, task_id: str) -> None:
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayQuantTaskRecord).where(EssayQuantTaskRecord.task_id == task_id)
            ).scalar_one_or_none()
            if row is None or row.status != "queued":
                return
            row.status = "running"
            row.progress = 10
            row.message = "正在构建样本、读取行情并执行稳健性检验"
            row.started_at = utc_naive_now()
            row.updated_at = utc_naive_now()
            owner_id = row.owner_id
            request_payload = json.loads(row.request_json or "{}")
            session.commit()

        try:
            rule = request_payload.get("rule") or {}
            result = EssayQuantService(db=self.db, owner_id=owner_id).run(
                rule,
                refresh_prices=bool(request_payload.get("refresh_prices", False)),
                max_symbols=int(request_payload.get("max_symbols") or 30),
                persist=True,
                rule_id=request_payload.get("rule_id"),
            )
            run_id = int(result["run_id"])
            with self.db.get_session() as session:
                row = session.execute(
                    select(EssayQuantTaskRecord).where(EssayQuantTaskRecord.task_id == task_id)
                ).scalar_one_or_none()
                if row is None:
                    return
                row.status = "completed"
                row.progress = 100
                row.message = f"研究完成，已保存为运行 #{run_id}"
                row.result_run_id = run_id
                row.error_message = None
                row.completed_at = utc_naive_now()
                row.updated_at = utc_naive_now()
                session.commit()
        except Exception as exc:  # noqa: BLE001 - task failures are persisted for the owner.
            detail = sanitize_diagnostic_text(exc, max_length=440) or "后台研究执行失败"
            safe_error = f"{type(exc).__name__}: {detail}"
            logger.warning("[essay-quant-task] task %s failed: %s", task_id, safe_error)
            with self.db.get_session() as session:
                row = session.execute(
                    select(EssayQuantTaskRecord).where(EssayQuantTaskRecord.task_id == task_id)
                ).scalar_one_or_none()
                if row is None:
                    return
                row.status = "failed"
                row.progress = 100
                row.message = "研究任务执行失败"
                row.error_message = safe_error
                row.completed_at = utc_naive_now()
                row.updated_at = utc_naive_now()
                session.commit()

    @staticmethod
    def _owner_clause(owner_id: object):
        if owner_id:
            return EssayQuantTaskRecord.owner_id == owner_id
        return EssayQuantTaskRecord.owner_id.is_(None)

    @staticmethod
    def _task_dict(row: EssayQuantTaskRecord) -> Dict[str, Any]:
        payload = json.loads(row.request_json or "{}")
        rule = payload.get("rule") or {}
        return {
            "task_id": row.task_id,
            "status": row.status,
            "progress": int(row.progress or 0),
            "message": row.message,
            "name": rule.get("name") or "量化研究任务",
            "strategy_type": rule.get("strategy_type") or "essay_event",
            "result_run_id": row.result_run_id,
            "error": row.error_message,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        }
