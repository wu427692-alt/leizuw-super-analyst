"""Persistent background packaging for selected Knowledge Planet audio files."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4
import zipfile

import requests

from src.request_identity import current_owner_id
from src.services.financial_data_service import (
    FinancialDataValidationError,
    ResearchNoteNotFoundError,
    ResearchNoteService,
)
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncError, ZsxqMcpSyncService


logger = logging.getLogger(__name__)
_TASK_ID_RE = re.compile(r"^audio-[A-Za-z0-9_-]{8,80}$")
_ACTIVE_STATUSES = {"queued", "running"}
_RETENTION_HOURS = 48


class ResearchNoteMediaTaskError(RuntimeError):
    """A selected-audio package task cannot be created or read."""


class ResearchNoteMediaTaskService:
    """Download and package selected audio outside the request lifecycle."""

    _instance: Optional["ResearchNoteMediaTaskService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        service_factory: Callable[[], ResearchNoteService] = ResearchNoteService,
        resolver: Optional[Callable[[str, str], str]] = None,
        http_get: Callable[..., Any] = requests.get,
        task_root: Optional[Path] = None,
        workers: Optional[int] = None,
        owner_getter: Callable[[], Optional[str]] = current_owner_id,
    ) -> None:
        self._service_factory = service_factory
        self._resolver = resolver or self._resolve_source_url
        self._http_get = http_get
        self._owner_getter = owner_getter
        database_path = Path(os.getenv("DATABASE_PATH", "./data/stock_analysis.db")).resolve()
        configured_root = os.getenv("RESEARCH_NOTE_EXPORT_ROOT", "").strip()
        self.task_root = Path(
            task_root or configured_root or database_path.parent / "research_note_audio_exports"
        ).resolve()
        self.archive_root = self.task_root / "archives"
        self.task_root.mkdir(parents=True, exist_ok=True)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        worker_count = workers if workers is not None else int(os.getenv("RESEARCH_NOTE_EXPORT_WORKERS", "2"))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(worker_count, 3)),
            thread_name_prefix="research-note-audio",
        )
        self._lock = threading.RLock()
        self._repair_interrupted_tasks()
        self._cleanup_expired_tasks()

    @classmethod
    def get_instance(cls) -> "ResearchNoteMediaTaskService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit(self, items: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
        selected = list(dict.fromkeys(
            (str(topic_id).strip(), str(file_id).strip()) for topic_id, file_id in items
        ))
        if not selected:
            raise ResearchNoteMediaTaskError("至少选择一个录音文件")
        if len(selected) > 100:
            raise ResearchNoteMediaTaskError("单次最多打包 100 个录音文件")
        if any(not topic_id or not file_id for topic_id, file_id in selected):
            raise ResearchNoteMediaTaskError("录音文件编号不能为空")

        self._cleanup_expired_tasks()
        now = datetime.now(timezone.utc)
        task_id = f"audio-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
        state = {
            "task_id": task_id,
            "owner_id": self._owner_getter(),
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "message": "已提交后台打包，可以离开当前页面",
            "total_files": len(selected),
            "completed_files": 0,
            "current_filename": None,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "archive_bytes": 0,
            "download_url": None,
            "download_name": None,
            "error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=_RETENTION_HOURS)).isoformat(),
        }
        self._write_state(state)
        self._executor.submit(self._run, task_id, selected)
        return self._public_state(state)

    def get(self, task_id: str) -> Dict[str, Any]:
        state = self._read_state(task_id)
        if state.get("owner_id") != self._owner_getter():
            raise ResearchNoteMediaTaskError("录音打包任务不存在或无权访问")
        return self._public_state(state)

    def list_tasks(self, limit: int = 20) -> Dict[str, Any]:
        """Return retained package tasks for the active user."""
        self._cleanup_expired_tasks()
        owner_id = self._owner_getter()
        items = []
        for path in self.task_root.glob("audio-*.json"):
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
        bounded_limit = max(1, min(int(limit or 20), 50))
        return {"items": items[:bounded_limit], "total": len(items)}

    def _read_state(self, task_id: str) -> Dict[str, Any]:
        path = self._task_path(task_id)
        if not path.is_file():
            raise ResearchNoteMediaTaskError("录音打包任务不存在或已过期")
        try:
            with self._lock:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResearchNoteMediaTaskError("录音打包任务状态暂时不可读") from exc

    def download(self, task_id: str) -> Tuple[Path, str]:
        state = self.get(task_id)
        if state.get("status") != "completed":
            raise ResearchNoteMediaTaskError("录音文件仍在后台打包")
        archive_path = self._archive_path(task_id)
        if not archive_path.is_file():
            raise ResearchNoteMediaTaskError("录音压缩包不存在或已过期")
        return archive_path, str(state.get("download_name") or f"知识星球录音_{task_id}.zip")

    def _run(self, task_id: str, selected: List[Tuple[str, str]]) -> None:
        part_path = self._archive_path(task_id).with_suffix(".zip.part")
        archive_path = self._archive_path(task_id)
        part_path.unlink(missing_ok=True)
        archive_path.unlink(missing_ok=True)
        self._update(
            task_id,
            status="running",
            phase="preparing",
            progress=2,
            message="正在核对所选录音与源文件信息",
        )
        try:
            service = self._service_factory()
            prepared: List[Dict[str, Any]] = []
            total_bytes = 0
            for topic_id, file_id in selected:
                note = service.get_note(topic_id)
                asset = next(
                    (item for item in note.get("files", []) if str(item.get("file_id") or "") == file_id),
                    None,
                )
                if asset is None or asset.get("asset_kind") != "audio":
                    raise FinancialDataValidationError(f"录音附件不存在：{topic_id}/{file_id}")
                size = max(0, int(asset.get("size") or 0))
                total_bytes += size
                prepared.append({
                    "topic_id": topic_id,
                    "file_id": file_id,
                    "asset": asset,
                    "note": note,
                    "size": size,
                })
            self._update(task_id, total_bytes=total_bytes, message="录音清单已核对，开始下载源文件")

            manifest: List[Dict[str, Any]] = []
            used_names: set[str] = set()
            downloaded_bytes = 0
            total_files = len(prepared)
            with zipfile.ZipFile(part_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for index, item in enumerate(prepared):
                    asset = item["asset"]
                    raw_name = Path(str(asset.get("name") or f"录音-{item['file_id']}")).name
                    safe_name = raw_name.replace("\r", "_").replace("\n", "_") or f"录音-{item['file_id']}"
                    candidate = safe_name
                    suffix = Path(safe_name).suffix
                    stem = Path(safe_name).stem
                    counter = 2
                    while candidate.lower() in used_names:
                        candidate = f"{stem}_{counter}{suffix}"
                        counter += 1
                    used_names.add(candidate.lower())
                    self._update(
                        task_id,
                        phase="downloading",
                        progress=self._file_progress(index, 0.0, total_files),
                        current_filename=candidate,
                        message=f"正在下载 {index + 1}/{total_files} · {candidate}",
                    )
                    source_url = self._resolver(item["topic_id"], item["file_id"])
                    upstream = self._http_get(source_url, stream=True, timeout=(10, 300))
                    try:
                        upstream.raise_for_status()
                        response_size = int((getattr(upstream, "headers", {}) or {}).get("Content-Length") or 0)
                        current_size = item["size"] or response_size
                        current_loaded = 0
                        last_update = time.monotonic()
                        with archive.open(f"录音文件/{candidate}", "w") as target:
                            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                                if not chunk:
                                    continue
                                target.write(chunk)
                                current_loaded += len(chunk)
                                now_monotonic = time.monotonic()
                                if now_monotonic - last_update >= 0.8:
                                    ratio = min(current_loaded / current_size, 0.99) if current_size else 0.0
                                    self._update(
                                        task_id,
                                        progress=self._file_progress(index, ratio, total_files),
                                        downloaded_bytes=downloaded_bytes + current_loaded,
                                        archive_bytes=part_path.stat().st_size if part_path.exists() else 0,
                                    )
                                    last_update = now_monotonic
                        downloaded_bytes += current_loaded
                    finally:
                        upstream.close()
                    note = item["note"]
                    manifest.append({
                        "topic_id": item["topic_id"],
                        "file_id": item["file_id"],
                        "filename": candidate,
                        "size": current_loaded,
                        "note_title": note.get("title"),
                        "group_name": note.get("group_name"),
                        "created_at": note.get("created_at"),
                    })
                    self._update(
                        task_id,
                        progress=self._file_progress(index + 1, 0.0, total_files),
                        completed_files=index + 1,
                        downloaded_bytes=downloaded_bytes,
                        archive_bytes=part_path.stat().st_size if part_path.exists() else 0,
                    )
                self._update(
                    task_id,
                    phase="packaging",
                    progress=97,
                    current_filename=None,
                    message="源文件已下载，正在写入下载清单并完成 ZIP",
                )
                archive.writestr("下载清单.json", json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
            part_path.replace(archive_path)
            now = datetime.now(timezone.utc)
            download_name = f"知识星球录音_已选{total_files}个_{now.strftime('%Y%m%d_%H%M%S')}.zip"
            self._update(
                task_id,
                status="completed",
                phase="completed",
                progress=100,
                message="后台打包完成，可以下载 ZIP",
                completed_files=total_files,
                current_filename=None,
                downloaded_bytes=downloaded_bytes,
                archive_bytes=archive_path.stat().st_size,
                download_url=f"/api/v1/financial-data/research-notes/audio-files/batch-download-tasks/{task_id}/download",
                download_name=download_name,
                error=None,
            )
        except (FinancialDataValidationError, ResearchNoteNotFoundError, ZsxqMcpSyncError, requests.RequestException) as exc:
            part_path.unlink(missing_ok=True)
            logger.warning("Selected audio packaging failed task_id=%s: %s", task_id, exc)
            self._fail(task_id, str(exc)[:500])
        except Exception as exc:  # noqa: BLE001 - task failures must remain visible to the user.
            part_path.unlink(missing_ok=True)
            logger.exception("Selected audio packaging failed task_id=%s", task_id)
            self._fail(task_id, f"后台打包失败：{type(exc).__name__}")

    @staticmethod
    def _file_progress(completed: int, current_ratio: float, total: int) -> int:
        ratio = (completed + max(0.0, min(current_ratio, 1.0))) / max(total, 1)
        return max(3, min(95, int(3 + ratio * 92)))

    @staticmethod
    def _resolve_source_url(topic_id: str, file_id: str) -> str:
        return ZsxqMcpSyncService().resolve_media_url_sync(topic_id, "files", file_id)

    def _fail(self, task_id: str, message: str) -> None:
        try:
            current = self._read_state(task_id)
        except ResearchNoteMediaTaskError:
            return
        self._update(
            task_id,
            status="failed",
            phase="failed",
            progress=min(int(current.get("progress") or 0), 99),
            message=message,
            current_filename=None,
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
            raise ResearchNoteMediaTaskError("无效的录音打包任务编号")
        return self.task_root / f"{task_id}.json"

    def _archive_path(self, task_id: str) -> Path:
        if not _TASK_ID_RE.fullmatch(str(task_id or "")):
            raise ResearchNoteMediaTaskError("无效的录音打包任务编号")
        return self.archive_root / f"{task_id}.zip"

    def _repair_interrupted_tasks(self) -> None:
        for path in self.task_root.glob("audio-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if state.get("status") not in _ACTIVE_STATUSES:
                continue
            state.update({
                "status": "failed",
                "phase": "interrupted",
                "message": "服务重启中断了打包任务，请重新提交",
                "error": "服务重启中断了打包任务，请重新提交",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            self._write_state(state)
            self._archive_path(str(state.get("task_id"))).with_suffix(".zip.part").unlink(missing_ok=True)

    def _cleanup_expired_tasks(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_RETENTION_HOURS)
        for path in self.task_root.glob("audio-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                updated_at = datetime.fromisoformat(str(state.get("updated_at") or ""))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
            except (OSError, ValueError):
                continue
            if state.get("status") in _ACTIVE_STATUSES or updated_at >= cutoff:
                continue
            task_id = str(state.get("task_id") or "")
            if _TASK_ID_RE.fullmatch(task_id):
                self._archive_path(task_id).unlink(missing_ok=True)
                self._archive_path(task_id).with_suffix(".zip.part").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
