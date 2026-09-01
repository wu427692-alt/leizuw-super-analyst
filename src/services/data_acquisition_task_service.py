"""Persistent background tasks for one-stop data acquisition packages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from src.request_identity import current_owner_id
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService

logger = logging.getLogger(__name__)
_TASK_ID_RE = re.compile(r"^acq-[A-Za-z0-9_-]{8,80}$")
_ACTIVE_STATUSES = {"queued", "running"}


class DataAcquisitionTaskService:
    """Run acquisition jobs off-request and persist truthful phase progress."""

    _instance: Optional["DataAcquisitionTaskService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        service_factory: Callable[[], DataAcquisitionService] = DataAcquisitionService,
        task_root: Optional[Path] = None,
        owner_getter: Callable[[], Optional[str]] = current_owner_id,
    ) -> None:
        self._service_factory = service_factory
        self._owner_getter = owner_getter
        service = service_factory()
        self.task_root = Path(task_root or (service.output_root / ".tasks")).resolve()
        self.task_root.mkdir(parents=True, exist_ok=True)
        workers = max(1, min(int(os.getenv("DATA_ACQUISITION_MAX_WORKERS", "2")), 4))
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="data-acquisition")
        self._lock = threading.RLock()
        self._repair_interrupted_tasks()

    @classmethod
    def get_instance(cls) -> "DataAcquisitionTaskService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit(self, request_text: str, plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        task_id = f"acq-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
        total_tasks = min(len((plan or {}).get("tasks") or []), 12)
        task_summaries = [
            {
                "id": str(item.get("id") or f"task-{index + 1}"),
                "label": str(item.get("label") or item.get("resource") or "数据任务")[:80],
                "source": str(item.get("source") or "unknown")[:40],
            }
            for index, item in enumerate(((plan or {}).get("tasks") or [])[:12])
            if isinstance(item, dict)
        ]
        state = {
            "task_id": task_id,
            "owner_id": self._owner_getter(),
            "status": "queued",
            "progress": 0,
            "phase": "queued",
            "message": "任务已进入后台队列",
            "completed_tasks": 0,
            "total_tasks": total_tasks,
            "tasks": task_summaries,
            "current_task_id": None,
            "current_source": None,
            "job_id": None,
            "result": None,
            "error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        self._write_state(state)
        self._executor.submit(self._run, task_id, str(request_text or "").strip(), plan)
        return self._public_state(state)

    def get(self, task_id: str) -> Dict[str, Any]:
        state = self._read_state(task_id)
        if state.get("owner_id") != self._owner_getter():
            raise DataAcquisitionError("取数任务不存在或无权访问")
        return self._public_state(state)

    def list_tasks(self, limit: int = 50) -> Dict[str, Any]:
        """List the current owner's tasks without exposing another user's work."""
        owner_id = self._owner_getter()
        items = []
        for path in self.task_root.glob("acq-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if state.get("owner_id") != owner_id:
                continue
            items.append(self._public_state(state))
        items.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        bounded_limit = max(1, min(int(limit or 50), 100))
        return {"items": items[:bounded_limit], "total": len(items)}

    def _read_state(self, task_id: str) -> Dict[str, Any]:
        path = self._task_path(task_id)
        if not path.is_file():
            raise DataAcquisitionError("取数任务不存在")
        try:
            with self._lock:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DataAcquisitionError("取数任务状态暂时不可读") from exc

    def _run(self, task_id: str, request_text: str, plan: Optional[Dict[str, Any]]) -> None:
        self._update(
            task_id,
            status="running",
            progress=1,
            phase="starting",
            message="后台任务已启动",
        )
        try:
            result = self._service_factory().run(
                request_text,
                plan,
                progress_callback=lambda update: self._update(task_id, status="running", **update),
            )
            self._update(
                task_id,
                status="completed",
                progress=100,
                phase="completed",
                message="数据包已生成，可以下载",
                completed_tasks=int((result.get("summary") or {}).get("task_count") or 0),
                total_tasks=int((result.get("summary") or {}).get("task_count") or 0),
                current_task_id=None,
                current_source=None,
                job_id=result.get("job_id"),
                result=result,
                error=None,
            )
        except DataAcquisitionError as exc:
            self._fail(task_id, str(exc)[:500])
        except Exception as exc:  # noqa: BLE001 - background failure must become visible task state.
            logger.exception("Background acquisition task failed task_id=%s", task_id)
            self._fail(task_id, f"后台取数失败：{type(exc).__name__}")

    def _fail(self, task_id: str, message: str) -> None:
        current = self._read_state(task_id)
        self._update(
            task_id,
            status="failed",
            progress=min(int(current.get("progress") or 0), 99),
            phase="failed",
            message=message,
            error=message,
        )

    def _update(self, task_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            current = self._read_state(task_id)
            if "progress" in changes:
                changes["progress"] = max(0, min(int(changes["progress"]), 100))
            current.update(changes)
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_state(current)
            return self._public_state(current)

    @staticmethod
    def _public_state(state: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in state.items() if key != "owner_id"}

    def _write_state(self, state: Dict[str, Any]) -> None:
        path = self._task_path(str(state["task_id"]))
        temporary = path.with_suffix(".json.tmp")
        with self._lock:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    def _task_path(self, task_id: str) -> Path:
        if not _TASK_ID_RE.fullmatch(str(task_id or "")):
            raise DataAcquisitionError("无效的取数任务编号")
        return self.task_root / f"{task_id}.json"

    def _repair_interrupted_tasks(self) -> None:
        for path in self.task_root.glob("acq-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if state.get("status") not in _ACTIVE_STATUSES:
                continue
            state.update({
                "status": "failed",
                "phase": "interrupted",
                "message": "服务重启中断了该任务，请重新执行",
                "error": "服务重启中断了该任务，请重新执行",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            self._write_state(state)
