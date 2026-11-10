"""Durable, owner-scoped essay exports; batching never caps the result count.

Immutable membership, source records and local checkpoints are persisted.
Only the explicitly requested focused scope performs full-text AI subject
screening. Completed judgments are retained and reused after restart; plain
title/full exports never call AI. No cookies or provider keys are persisted.
"""
from __future__ import annotations

import csv
import errno
import fcntl
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import uuid
import zipfile

from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from src.config import get_config
from src.repositories.essay_analysis_repo import EssayAnalysisRepository, feed_date_bounds
from src.services.workspace import workspace_id, workspace_owner

_TASK_ID = re.compile(r"essay-export-[0-9a-f]{32}\Z")
_BATCH_SIZE = 250
_LEASE_SECONDS = 180
_TERMINAL = {"completed", "no_results", "failed"}
_FILTERS = {"query", "query_scope", "analysis_status", "sentiment", "category", "tag", "stock",
            "min_importance", "start_date", "end_date", "cutoff"}
_CSV_FIELDS = ["topic_id", "title", "published_at", "source", "author", "content", "summary",
               "status", "sentiment", "primary_category", "importance_score", "confidence_score",
               "tags", "industries", "themes", "stock_mentions", "key_points", "catalysts", "risks",
               "evidence", "contradictions", "falsification_conditions", "monitoring_points", "model"]
_PREPARED_FILES = ("小作文.json", "小作文.csv", "清单.md")
logger = logging.getLogger(__name__)


class ExportIntegrityError(ValueError):
    """A missing frozen member must never silently become a complete export."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _normalise_item(item):
    item = dict(item)
    note = item.get("note") or {}
    for key, value in {"title": note.get("title"), "content": note.get("content"),
                       "published_at": note.get("created_at"), "source": note.get("group_name"),
                       "author": note.get("author_name")}.items():
        if item.get(key) is None:
            item[key] = value
    return item


def _csv_cell(value):
    if isinstance(value, (dict, list)):
        value = _json(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        # The JSON and Markdown retain exact originals; spreadsheet exports do
        # not execute content supplied by external, untrusted note authors.
        return "'" + value
    return value


def _is_transient_export_error(exc):
    """Retry resource/lock failures, not permanent SQL/schema mistakes."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, OSError):
        return exc.errno in {errno.ENOSPC, errno.EMFILE, errno.ENFILE, errno.EIO, errno.EAGAIN}
    if not isinstance(exc, (sqlite3.OperationalError, SQLAlchemyOperationalError)):
        return False
    source = exc.orig if isinstance(exc, SQLAlchemyOperationalError) else exc
    code = getattr(source, "sqlite_errorcode", None)
    if isinstance(code, int):
        # Extended error codes retain the primary result in their low byte.
        return (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR,
                                sqlite3.SQLITE_FULL, sqlite3.SQLITE_CANTOPEN}
    message = str(source).lower()
    # Older SQLite wrappers may not expose result codes. Match their factual
    # diagnostics, never treat every OperationalError as a recoverable lock.
    return any(fragment in message for fragment in (
        "database is locked", "database table is locked", "database schema is locked",
        "database is busy", "disk i/o error", "disk is full", "database or disk is full",
        "unable to open database file", "cannot open database file",
    ))


class EssayExportService:
    """Persistent task ledger + bounded-memory snapshot/collect/package worker."""

    def __init__(self, root=None, repo=None, clock=None, focus_service=None):
        self.root = Path(root or (Path(get_config().database_path).absolute().parent / "essay_exports")).absolute()
        if self.root.is_symlink():
            raise ValueError("无效的导出目录")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = self.root.resolve()
        self.repo = repo
        self.focus_service = focus_service
        self.clock = clock or time.time
        self.worker_id = uuid.uuid4().hex
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._worker = None
        self._worker_lock = threading.Lock()
        self._run_lock = threading.Lock()

    def _repository(self):
        if self.repo is None:
            self.repo = EssayAnalysisRepository()
        return self.repo

    def _private_path(self, path):
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError("导出路径不得使用符号链接")
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("无效的导出路径")
        return path

    @contextmanager
    def _connect(self, job):
        path = self._private_path(job / "task.sqlite3")
        connection = sqlite3.connect(str(path), timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            os.chmod(path, 0o600)
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _metadata(connection):
        row = connection.execute("SELECT value FROM metadata WHERE id=1").fetchone()
        if row is None:
            raise ExportIntegrityError("导出任务元数据缺失")
        metadata = json.loads(row[0])
        if not isinstance(metadata, dict) or not {"task_id", "status", "phase", "checkpoint"}.issubset(metadata):
            raise ExportIntegrityError("导出任务元数据结构损坏，已保留现场")
        return metadata

    @staticmethod
    def _write_metadata(connection, metadata):
        connection.execute("INSERT OR REPLACE INTO metadata(id,value) VALUES(1,?)", (_json(metadata),))

    def _save(self, job, **changes):
        with self._connect(job) as connection:
            connection.execute("BEGIN IMMEDIATE")
            metadata = self._metadata(connection)
            metadata.update(changes)
            metadata["updated_at"] = _iso(self.clock())
            self._write_metadata(connection, metadata)
        return metadata

    def _freeze_filters(self, filters):
        incoming = dict(filters or {})
        unexpected = set(incoming) - (_FILTERS | {"days", "page", "page_size", "known_total"})
        if unexpected:
            raise ValueError("不支持的导出筛选字段：" + ", ".join(sorted(unexpected)))
        clean = {key: incoming[key] for key in _FILTERS if incoming.get(key) not in (None, "")}
        for key in ("query", "sentiment", "category", "tag", "stock", "analysis_status"):
            if key in clean:
                if not isinstance(clean[key], str) or len(clean[key]) > 200:
                    raise ValueError("无效的检索条件：" + key)
                clean[key] = clean[key].strip()
        scope = clean.get("query_scope", "full")
        if scope not in {"title", "full", "focused"}:
            raise ValueError("query_scope 必须为 title、full 或 focused（全文主旨聚焦）")
        if scope == "focused" and not str(clean.get("query") or "").strip():
            raise ValueError("全文主旨聚焦必须提供明确的研究对象")
        clean["query_scope"] = scope
        clean.setdefault("analysis_status", "essay")
        if clean["analysis_status"] not in {"essay", "completed", "uncompleted", "not_queued", "pending",
                                           "processing", "failed", "media_only"}:
            raise ValueError("无效的 AI 分析状态筛选")
        if "min_importance" in clean:
            number = clean["min_importance"]
            if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 100:
                raise ValueError("min_importance 必须为 0 到 100 的整数")
        start, end = feed_date_bounds(clean.get("start_date"), clean.get("end_date"))
        if start is not None or end is not None:
            clean.pop("cutoff", None)
        elif "cutoff" in clean:
            cutoff = clean["cutoff"]
            if isinstance(cutoff, datetime):
                clean["cutoff"] = cutoff.isoformat()
            else:
                datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        else:
            days = incoming.get("days", 0)
            if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 3650:
                raise ValueError("days 必须为 0 到 3650 的整数；0 表示不限日期")
            if days:
                clean["cutoff"] = (datetime.fromtimestamp(self.clock(), timezone.utc).replace(tzinfo=None)
                                   - timedelta(days=days)).isoformat()
        return clean

    def create(self, filters, workspace=None, requested_limit=None):
        if requested_limit is not None and (isinstance(requested_limit, bool)
                or not isinstance(requested_limit, int) or requested_limit <= 0):
            raise ValueError("只有明确指定的正整数篇数才可作为导出上限")
        owner = workspace_owner()
        workspace = workspace_id(workspace)
        clean = self._freeze_filters(filters)
        digest = hashlib.sha256(workspace.encode()).hexdigest()[:32]
        job = self._private_path(self.root / owner / digest / ("essay-export-" + uuid.uuid4().hex))
        job.mkdir(mode=0o700, parents=True)
        # mkdir's mode only applies to the leaf; keep intermediate owner scope
        # private too, including when created under a permissive process umask.
        os.chmod(job.parent, 0o700)
        os.chmod(job.parent.parent, 0o700)
        timestamp = _iso(self.clock())
        metadata = {"task_id": job.name, "owner": owner, "workspace_id": workspace,
                    "filters": clean, "requested_limit": requested_limit, "status": "queued",
                    "phase": "queued", "created_at": timestamp, "updated_at": timestamp,
                    "matched_total": None, "target_total": None, "indexed_count": 0,
                    "retrieved_count": 0, "packaged_count": 0, "bytes_written": 0, "bytes_total": None,
                    "bytes_kind": "uncompressed_payload",
                    "checkpoint": {"snapshot_complete": False, "generation": 0, "collected_cursor": 0,
                                   "packaged_cursor": 0, "file_offsets": {}, "prepared_complete": False},
                    "result": None, "error": None, "resumable": True, "attempts": 0,
                    "lease_owner": None, "lease_until": 0, "retry_at": 0, "complete": False,
                    "request_complete": False, "library_complete": False}
        if clean["query_scope"] == "focused":
            metadata.update(candidate_total=None, candidate_retrieved_count=0, screened_count=0,
                            focused_count=0, mentioned_count=0, irrelevant_count=0,
                            uncertain_count=0, source_only_count=0, focus_screening_complete=False,
                            semantic_cache_hits=0, semantic_new_reviews=0)
        with self._connect(job) as connection:
            connection.execute("CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)")
            connection.execute("CREATE TABLE members(seq INTEGER PRIMARY KEY,topic_id TEXT NOT NULL UNIQUE,"
                               "note_id INTEGER NOT NULL,payload TEXT)")
            connection.execute("CREATE TABLE focus_reviews(seq INTEGER PRIMARY KEY,result TEXT NOT NULL)")
            self._write_metadata(connection, metadata)
        self._wake.set()
        return self._public(metadata)

    def _find(self, task_id, workspace=None):
        if not _TASK_ID.fullmatch(str(task_id)):
            return None
        owner = workspace_owner()
        owner_dir = self._private_path(self.root / owner)
        for job in owner_dir.glob("*/" + task_id):
            self._private_path(job)
            with self._connect(job) as connection:
                metadata = self._metadata(connection)
            if metadata.get("owner") != owner or metadata.get("task_id") != task_id:
                continue
            if workspace is not None and metadata.get("workspace_id") != workspace_id(workspace):
                return None
            return job, metadata
        return None

    def get(self, task_id, workspace=None):
        found = self._find(task_id, workspace)
        return self._public(found[1]) if found else None

    def list_tasks(self, workspace=None):
        owner = workspace_owner()
        records = []
        for db in self._private_path(self.root / owner).glob("*/essay-export-*/task.sqlite3"):
            self._private_path(db)
            with self._connect(db.parent) as connection:
                metadata = self._metadata(connection)
            if metadata.get("owner") == owner and (workspace is None
                    or metadata.get("workspace_id") == workspace_id(workspace)):
                records.append(self._public(metadata))
        return sorted(records, key=lambda value: value["created_at"], reverse=True)

    def resume(self, task_id, workspace=None):
        found = self._find(task_id, workspace)
        if not found:
            return None
        job, _snapshot = found
        with self._connect(job) as connection:
            # A second tab may have read the same failed snapshot before the
            # first resume advanced the worker. Re-read under the write lock;
            # never replace fresh classification counts with stale metadata.
            connection.execute("BEGIN IMMEDIATE")
            metadata = self._metadata(connection)
            if metadata["status"] in {"completed", "no_results"} or metadata.get("lease_until", 0) > self.clock():
                return self._public(metadata)
            policy_change = self._focus_policy_change(metadata)
            if policy_change:
                metadata.update(policy_change, updated_at=_iso(self.clock()))
                self._write_metadata(connection, metadata)
                return self._public(metadata)
            if metadata["status"] not in {"failed", "retrying"}:
                self._wake.set()
                return self._public(metadata)
            if metadata["filters"].get("query_scope") == "focused" and metadata.get("uncertain_count"):
                # Manual retry rechecks only unresolved judgments. Successful
                # judgments and complete frozen source payloads stay untouched.
                rows = connection.execute("SELECT seq,result FROM focus_reviews").fetchall()
                removed = 0
                for row in rows:
                    if json.loads(row["result"]).get("classification") == "uncertain":
                        connection.execute("DELETE FROM focus_reviews WHERE seq=?", (row["seq"],))
                        removed += 1
                if removed != metadata["uncertain_count"]:
                    raise ExportIntegrityError("待核验审阅数量与断点不一致，未改写任务状态")
                resolved_count = metadata["screened_count"] - metadata["uncertain_count"]
                metadata.update(uncertain_count=0, screened_count=resolved_count,
                                focus_screening_complete=False)
            metadata.update(status="queued", error=None, retry_at=0, updated_at=_iso(self.clock()))
            self._write_metadata(connection, metadata)
        self._wake.set()
        return self._public(metadata)

    def download_file(self, task_id, workspace=None):
        found = self._find(task_id, workspace)
        if not found or found[1]["status"] != "completed":
            raise FileNotFoundError("导出文件尚未完成或不属于当前账号")
        job, metadata = found
        filename = metadata["result"]["filename"]
        if filename != Path(filename).name:
            raise ExportIntegrityError("无效的导出文件名")
        path = self._private_path(job / filename)
        if not path.is_file():
            raise FileNotFoundError("导出文件不存在")
        return path

    @staticmethod
    def _public(metadata):
        result = {key: value for key, value in metadata.items()
                  if key not in {"owner", "lease_owner", "lease_until", "retry_at"}}
        phase = metadata["phase"]
        total = metadata.get("target_total")
        if metadata["status"] in {"completed", "no_results"}:
            progress = 100
        elif phase in {"queued", "indexing"}:
            progress = None
        elif phase == "collecting":
            if metadata["filters"].get("query_scope") == "focused":
                candidates = metadata.get("candidate_total")
                progress = round(25 * metadata.get("candidate_retrieved_count", 0) / candidates, 2) if candidates else None
            else:
                progress = round(50 * metadata["retrieved_count"] / total, 2) if total else None
        elif phase == "screening":
            candidates = metadata.get("candidate_total")
            progress = 25 + round(25 * metadata.get("screened_count", 0) / candidates, 2) if candidates else None
        elif phase == "preparing":
            progress = 50 + round(25 * metadata["packaged_count"] / total, 2) if total else None
        else:
            byte_total = metadata.get("bytes_total")
            progress = 75 + round(24 * metadata["bytes_written"] / byte_total, 2) if byte_total else None
        result["progress"] = progress
        result["poll_path"] = f"/api/v1/financial-data/essays/package/tasks/{metadata['task_id']}"
        result["resume_path"] = result["poll_path"] + "/resume"
        if metadata["filters"].get("query_scope") == "focused":
            result["focus_review_path"] = result["poll_path"] + "/focus-review"
        result["message"] = {
            "queued": "已提交后台，可离开页面", "indexing": "正在固定检索结果和真实总数",
            "collecting": "正在读取完整原文与分析标签", "preparing": "正在流式整理导出文件",
            "screening": "正在阅读全文，逐篇判断是否以研究对象为主体",
            "packaging": "正在组装并校验下载包", "completed": "全部请求内容已打包，可下载",
            "no_results": "当前条件没有匹配原文，未生成空数据包", "retrying": "暂时受阻，将从断点自动继续",
            "failed": "任务暂停，已保留断点，可重试",
        }.get(metadata["status"], "后台处理中")
        if metadata["status"] == "no_results" and metadata["filters"].get("query_scope") == "focused":
            result["message"] = "文字原文审阅后未发现以该对象为主要研究主体的内容，未生成空包；可查看审阅依据"
            if metadata.get("source_only_count", 0) and metadata["source_only_count"] == metadata.get("candidate_total"):
                result["message"] = "命中资料仅有源文件索引，没有可供主旨筛选的文字正文；附件未读取，未生成空包，可查看文件清单"
        return result

    def _claim(self, job):
        with self._connect(job) as connection:
            connection.execute("BEGIN IMMEDIATE")
            metadata = self._metadata(connection)
            now = self.clock()
            if metadata["status"] in _TERMINAL or metadata.get("retry_at", 0) > now:
                return None
            if metadata.get("lease_until", 0) > now:
                return None
            metadata.update(lease_owner=self.worker_id, lease_until=now + _LEASE_SECONDS)
            self._write_metadata(connection, metadata)
            return metadata

    def _heartbeat(self, job, **fields):
        return self._save(job, lease_until=self.clock() + _LEASE_SECONDS, **fields)

    def run_once(self):
        """Process one bounded batch (or one index/ZIP stage), for tests/worker."""
        if not self._run_lock.acquire(blocking=False):
            return False
        try:
            candidates = sorted(
                self.root.glob("*/*/essay-export-*/task.sqlite3"), key=lambda path: path.stat().st_mtime)
            for database in candidates:
                try:
                    job = self._private_path(database.parent)
                    lock_path = self._private_path(job / "runner.lock")
                    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                except (OSError, ValueError):
                    logger.warning("Cannot access essay export task %s; kept unchanged", database.parent.name,
                                   exc_info=True)
                    continue
                try:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    try:
                        metadata = self._claim(job)
                    except (sqlite3.DatabaseError, json.JSONDecodeError, ExportIntegrityError, KeyError, TypeError):
                        # Keep a damaged task intact, but do not let one corrupt
                        # private ledger prevent every other account's exports.
                        logger.warning("Cannot read essay export ledger %s; kept unchanged", job.name, exc_info=True)
                        continue
                    if metadata is None:
                        continue
                    try:
                        self._run_stage(job, metadata)
                    except Exception as exc:
                        transient = _is_transient_export_error(exc)
                        with self._connect(job) as connection:
                            current = self._metadata(connection)
                        attempts = current.get("attempts", 0) + 1
                        self._save(job, status="retrying" if transient else "failed", resumable=True,
                                   error=str(exc)[:400], attempts=attempts,
                                   retry_at=self.clock() + min(60, 2 ** min(attempts, 6)) if transient else 0)
                    finally:
                        self._save(job, lease_owner=None, lease_until=0)
                    return True
                finally:
                    os.close(descriptor)
            return False
        finally:
            self._run_lock.release()

    def _run_stage(self, job, metadata):
        policy_change = self._focus_policy_change(metadata)
        if policy_change:
            self._save(job, **policy_change)
            return
        checkpoint = metadata["checkpoint"]
        if not checkpoint["snapshot_complete"]:
            self._index(job, metadata)
        elif metadata["filters"].get("query_scope") == "focused" and metadata.get("candidate_retrieved_count", 0) < metadata["candidate_total"]:
            self._collect(job, metadata)
        elif metadata["filters"].get("query_scope") == "focused" and not metadata.get("focus_screening_complete"):
            self._screen(job, metadata)
        elif metadata["retrieved_count"] < metadata["target_total"]:
            self._collect(job, metadata)
        elif not checkpoint["prepared_complete"]:
            self._prepare(job, metadata)
        else:
            self._package(job, metadata)

    def _index(self, job, metadata):
        checkpoint = {"snapshot_complete": False, "generation": metadata["checkpoint"]["generation"] + 1,
                      "collected_cursor": 0, "packaged_cursor": 0, "file_offsets": {}, "prepared_complete": False}
        # Only this job's unfinished index is replaced. Source data is read-only.
        with self._connect(job) as connection:
            connection.execute("DELETE FROM members")
            metadata.update(status="indexing", phase="indexing", checkpoint=checkpoint,
                            indexed_count=0, retrieved_count=0, packaged_count=0,
                            bytes_written=0, bytes_total=None, error=None)
            self._write_metadata(connection, metadata)
        filters = dict(metadata["filters"])
        focused = filters.get("query_scope") == "focused"
        if focused:
            filters["query_scope"] = "full"
        if filters.get("cutoff"):
            cutoff = datetime.fromisoformat(filters["cutoff"].replace("Z", "+00:00"))
            if cutoff.tzinfo is not None:
                cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
            filters["cutoff"] = cutoff
        sequence = 0
        for batch in self._repository().iter_export_snapshot(
                requested_limit=None if focused else metadata["requested_limit"], batch_size=_BATCH_SIZE, **filters):
            if "total" in batch:
                if focused:
                    metadata.update(candidate_total=batch["total"], matched_total=None, target_total=None)
                else:
                    metadata.update(matched_total=batch["total"], target_total=batch["target_total"])
            with self._connect(job) as connection:
                for note_id, topic_id in batch["items"]:
                    sequence += 1
                    connection.execute("INSERT INTO members(seq,topic_id,note_id) VALUES(?,?,?)",
                                       (sequence, topic_id, note_id))
                metadata.update(indexed_count=sequence, lease_until=self.clock() + _LEASE_SECONDS,
                                updated_at=_iso(self.clock()))
                if batch.get("done"):
                    if sequence != (metadata["candidate_total"] if focused else metadata["target_total"]):
                        raise ExportIntegrityError("检索快照数量不一致，不能声称全部导出")
                    checkpoint["snapshot_complete"] = True
                    metadata.update(status="collecting" if sequence else "no_results",
                                    phase="collecting" if sequence else "no_results")
                    if not sequence:
                        metadata.update(resumable=False, complete=True, request_complete=True, library_complete=True)
                        if focused:
                            metadata.update(matched_total=0, target_total=0, focus_screening_complete=True)
                self._write_metadata(connection, metadata)

    def _collect(self, job, metadata):
        self._heartbeat(job, status="collecting", phase="collecting", error=None)
        checkpoint = dict(metadata["checkpoint"])
        with self._connect(job) as connection:
            batch = connection.execute("SELECT seq,topic_id FROM members WHERE seq>? ORDER BY seq LIMIT ?",
                                       (checkpoint["collected_cursor"], _BATCH_SIZE)).fetchall()
        ids = [row["topic_id"] for row in batch]
        payloads = {str(item["topic_id"]): _normalise_item(item)
                    for item in self._repository().export_payloads(ids)}
        missing = set(ids) - set(payloads)
        if missing:
            raise ExportIntegrityError(f"固定快照中有 {len(missing)} 篇原文暂不可读，保留断点等待重试")
        if not batch:
            raise ExportIntegrityError("尚未读满目标数量但快照游标已结束")
        with self._connect(job) as connection:
            for row in batch:
                connection.execute("UPDATE members SET payload=? WHERE seq=?",
                                   (_json(payloads[row["topic_id"]]), row["seq"]))
            checkpoint["collected_cursor"] = batch[-1]["seq"]
            metadata.update(checkpoint=checkpoint, retrieved_count=metadata["retrieved_count"] + len(batch),
                            status="collecting", phase="collecting", updated_at=_iso(self.clock()))
            if metadata["filters"].get("query_scope") == "focused":
                metadata["candidate_retrieved_count"] = metadata["retrieved_count"]
            self._write_metadata(connection, metadata)

    def _focus_classifier(self):
        if self.focus_service is None:
            from src.services.essay_focus_service import EssayFocusService
            self.focus_service = EssayFocusService()
        return self.focus_service

    def _focus_policy_change(self, metadata):
        previous = metadata.get("focus_policy_version")
        if metadata["filters"].get("query_scope") != "focused" or not previous:
            return None
        expected = self._focus_classifier().prompt_version
        if previous == expected:
            return None
        # Preserve the old task's original snapshot and all prior judgments.
        # Mixing successful v1 labels with v2 retries would misstate the policy
        # applied to an allegedly complete export. A fresh task gets one policy.
        return {"status": "failed", "resumable": False, "requires_new_task": True,
                "focus_policy_outdated": True, "expected_focus_policy_version": expected,
                "error": "主旨筛选规则已更新；此任务的原文快照和旧判断均已保留，请按原条件新建任务，不能混用新旧规则生成聚焦包。"}

    @staticmethod
    def _source_hash(payload):
        return hashlib.sha256((str(payload.get("title") or "") + "\0" +
                               str(payload.get("content") or "")).encode("utf-8")).hexdigest()

    @staticmethod
    def _source_only_review(payload, query, version):
        """Identify attachment indexes, never pretend to review their PDF/audio.

        A known placeholder plus actual source-file metadata proves only the
        material type. It is neither a semantic negative nor a missing text
        original. Real prose with attachments must still receive full review.
        """
        note = payload.get("note")
        files = note.get("files") if isinstance(note, dict) else None
        if not isinstance(files, list):
            return None
        files = [item for item in files if isinstance(item, dict) and
                 isinstance(item.get("name"), str) and item["name"].strip()]
        if not files:
            return None
        content = str(payload.get("content") or "")
        plain = content.strip()
        names = {item["name"].strip() for item in files}
        if plain not in {"", "文件", "「文件」", "【文件】", "[文件]"} and plain not in names:
            return None
        return {"classification": "source_only", "decision_kind": "source_type_exclusion",
                "query": query, "prompt_version": version,
                "source_sha256": EssayExportService._source_hash(payload),
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "reason": "此条仅为源文件索引，没有可供主旨分析的文字正文；附件正文未读取，单独列明，未纳入文字小作文聚焦包。",
                "primary_subjects": [], "target_role": "unclear", "substantive_analysis": False,
                "document_shape": "other", "evidence": [], "needs_review": False,
                "semantic_review_performed": False, "attachment_content_read": False,
                "scope_eligible": False, "cache_hit": False,
                "source_files": [{key: item[key] for key in ("file_id", "name", "asset_kind") if key in item}
                                 for item in files],
                "coverage": {"content_chars": len(content), "reviewed_chars": 0,
                             "chunks_total": 0, "chunks_reviewed": 0,
                             "read_complete": False, "complete": False, "required": False}}

    def _classify_focus(self, job, metadata, payload):
        classifier = self._focus_classifier()
        query = metadata["filters"]["query"]
        source_only = self._source_only_review(payload, query, classifier.prompt_version)
        if source_only is not None:
            return source_only
        source_hash = self._source_hash(payload)
        key = hashlib.sha256(_json([query, source_hash, classifier.prompt_version]).encode()).hexdigest()
        folder = self._private_path(self.root / metadata["owner"] / "focus-cache")
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._private_path(folder / (key + ".json"))
        lock = os.open(self._private_path(folder / (key + ".lock")), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TimeoutError("相同原文正在进行主体审阅，将复用结果而非重复调用AI") from exc
            cached = path.exists()
            if cached:
                try:
                    result = json.loads(path.read_text())
                except (ValueError, OSError) as exc:
                    raise ExportIntegrityError("主旨审阅缓存损坏，保留现场待核验") from exc
            else:
                from src.request_identity import set_current_user_id, reset_current_user_id
                owner = metadata["owner"]
                if owner != job.parent.parent.name or (owner != "admin" and not re.fullmatch(r"user-[1-9]\d*", owner)):
                    raise ExportIntegrityError("主体审阅任务的账号归属无效")
                token = set_current_user_id(0 if owner == "admin" else int(owner[5:]))
                heartbeat_stop = threading.Event()
                def keep_lease():
                    while not heartbeat_stop.wait(30):
                        try:
                            self._heartbeat(job)
                        except (OSError, sqlite3.Error):
                            logger.warning("Focus task lease update delayed: %s", job.name)
                heartbeat = threading.Thread(target=keep_lease, name="essay-focus-lease", daemon=True)
                heartbeat.start()
                try:
                    result = classifier.classify(query, payload, on_progress=lambda _progress:
                                                 self._heartbeat(job, status="screening", phase="screening"))
                finally:
                    heartbeat_stop.set()
                    heartbeat.join(timeout=2)
                    reset_current_user_id(token)
            if (not isinstance(result, dict) or result.get("query") != query
                    or result.get("source_sha256") != source_hash
                    or result.get("prompt_version") != classifier.prompt_version
                    or result.get("classification") not in {"focused", "mentioned", "irrelevant", "uncertain"}):
                raise ExportIntegrityError("全文主旨审阅身份或版本不一致，未发布导出结果")
            coverage = result.get("coverage") or {}
            if result["classification"] != "uncertain" and (not coverage.get("complete")
                    or coverage.get("reviewed_chars") != len(str(payload.get("content") or ""))):
                raise ExportIntegrityError("未阅读全文的主旨判断不能纳入或排除原文")
            if not path.exists() and result["classification"] != "uncertain":
                temporary = self._private_path(folder / (key + "." + uuid.uuid4().hex + ".partial"))
                descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(_json(result).encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            return {**result, "cache_hit": cached}
        finally:
            os.close(lock)

    def _screen(self, job, metadata):
        """One complete source per checkpoint; limits apply after screening."""
        self._heartbeat(job, status="screening", phase="screening", error=None)
        with self._connect(job) as connection:
            row = connection.execute(
                "SELECT m.seq,m.payload FROM members m LEFT JOIN focus_reviews f ON m.seq=f.seq "
                "WHERE f.seq IS NULL ORDER BY m.seq LIMIT 1").fetchone()
        if row is not None:
            if row["payload"] is None:
                raise ExportIntegrityError("全文主旨筛选缺少固定快照的完整原文")
            payload = json.loads(row["payload"])
            result = self._classify_focus(job, metadata, payload)
            field = {"focused": "focused_count", "mentioned": "mentioned_count",
                     "irrelevant": "irrelevant_count", "uncertain": "uncertain_count",
                     "source_only": "source_only_count"}[result["classification"]]
            with self._connect(job) as connection:
                connection.execute("INSERT INTO focus_reviews(seq,result) VALUES(?,?)", (row["seq"], _json(result)))
                metadata.update(screened_count=metadata["screened_count"] + 1,
                                status="screening", phase="screening", updated_at=_iso(self.clock()))
                metadata[field] = metadata.get(field, 0) + 1
                if result["classification"] != "source_only":
                    metadata["semantic_cache_hits" if result["cache_hit"] else "semantic_new_reviews"] += 1
                metadata["focus_policy_version"] = result["prompt_version"]
                self._write_metadata(connection, metadata)
            return
        if metadata["screened_count"] != metadata["candidate_total"]:
            raise ExportIntegrityError("全文主旨审阅数量与候选快照不一致")
        if metadata["uncertain_count"]:
            self._save(job, status="failed", phase="screening", focus_screening_complete=False,
                       error=f"仍有 {metadata['uncertain_count']} 篇原文主旨待核验，未发布不完整聚焦包；重试仅重新核验待定原文")
            return
        matched = metadata["focused_count"]
        limit = metadata["requested_limit"]
        target = min(matched, limit) if limit else matched
        self._save(job, matched_total=matched, target_total=target, retrieved_count=target,
                   focus_screening_complete=True, status="packaging" if target else "no_results",
                   phase="preparing" if target else "no_results", error=None,
                   complete=not target, request_complete=not target, library_complete=not target,
                   resumable=bool(target))

    def focus_reviews(self, task_id, workspace=None, page=1, page_size=50):
        found = self._find(task_id, workspace)
        if not found or found[1]["filters"].get("query_scope") != "focused":
            return None
        job, metadata = found
        with self._connect(job) as connection:
            rows = connection.execute("SELECT m.topic_id,m.payload,f.result FROM members m JOIN focus_reviews f "
                                      "ON m.seq=f.seq ORDER BY m.seq LIMIT ? OFFSET ?",
                                      (page_size, (page - 1) * page_size)).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row["payload"])
            items.append({"topic_id": row["topic_id"], "title": payload.get("title"),
                          "published_at": payload.get("published_at"), "review": json.loads(row["result"])})
        return {"task_id": task_id, "candidate_total": metadata.get("candidate_total"),
                "screened_count": metadata["screened_count"], "total": metadata["screened_count"],
                "page": page, "page_size": page_size, "items": items}

    @staticmethod
    def _row_buffers(payload, sequence):
        json_bytes = ((",\n" if sequence > 1 else "\n") + _json(payload)).encode("utf-8")
        csv_buffer = io.StringIO(newline="")
        csv.DictWriter(csv_buffer, fieldnames=_CSV_FIELDS, extrasaction="ignore").writerow(
            {key: _csv_cell(payload.get(key)) for key in _CSV_FIELDS})
        markdown = [f"\n## {sequence}. {payload.get('title') or '未命名'}\n",
                    " · ".join(str(payload.get(key) or "") for key in ("published_at", "source", "author")),
                    "\n### 原文全文\n", str(payload.get("content") or ""), "\n### 已有分析与标签\n",
                    _json({key: payload.get(key) for key in _CSV_FIELDS[6:]}), "\n"]
        if payload.get("focus_review"):
            markdown.extend(["\n### 全文主旨筛选依据\n", _json(payload["focus_review"]), "\n"])
        return json_bytes, csv_buffer.getvalue().encode("utf-8"), "\n".join(markdown).encode("utf-8")

    def _prepare(self, job, metadata):
        self._heartbeat(job, status="packaging", phase="preparing", error=None)
        checkpoint = dict(metadata["checkpoint"])
        offsets = dict(checkpoint["file_offsets"])
        with self._connect(job) as connection:
            focused = metadata["filters"].get("query_scope") == "focused"
            if focused:
                # Keep the original order; a user-specified limit is applied
                # only to the final focused subset, never to raw candidates.
                candidates = connection.execute("SELECT m.seq,m.payload,f.result FROM members m JOIN focus_reviews f "
                                                "ON m.seq=f.seq WHERE m.seq>? ORDER BY m.seq",
                                                (checkpoint["packaged_cursor"],))
                batch = []
                remaining = min(_BATCH_SIZE, metadata["target_total"] - metadata["packaged_count"])
                try:
                    for row in candidates:
                        if json.loads(row["result"])["classification"] == "focused":
                            batch.append(row)
                            if len(batch) == remaining:
                                break
                finally:
                    # A partially consumed SQLite cursor otherwise keeps its
                    # read lock through the subsequent checkpoint commit.
                    candidates.close()
            else:
                batch = connection.execute("SELECT seq,payload FROM members WHERE seq>? ORDER BY seq LIMIT ?",
                                           (checkpoint["packaged_cursor"], _BATCH_SIZE)).fetchall()
        handles = []
        try:
            for name in _PREPARED_FILES:
                path = self._private_path(job / (name + ".partial"))
                descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
                handle = os.fdopen(descriptor, "r+b")
                handles.append(handle)
                committed = offsets.get(name, 0)
                if os.fstat(handle.fileno()).st_size < committed:
                    raise ExportIntegrityError("导出临时文件短于已提交断点，保留现场")
                # Only task-private partial tails not covered by a committed
                # checkpoint are discarded after an interrupted write.
                handle.truncate(committed)
                handle.seek(committed)
            if checkpoint["packaged_cursor"] == 0:
                header = io.StringIO(newline="")
                csv.DictWriter(header, fieldnames=_CSV_FIELDS).writeheader()
                headers = (b"[", b"\xef\xbb\xbf" + header.getvalue().encode("utf-8"),
                           ("# 小作文原文与已有分析\n筛选条件：" + _json(metadata["filters"]) + "\n").encode("utf-8"))
                for handle, content in zip(handles, headers):
                    handle.write(content)
            for ordinal, row in enumerate(batch, start=metadata["packaged_count"] + 1):
                if row["payload"] is None:
                    raise ExportIntegrityError("导出成员缺少完整原文载荷")
                payload = json.loads(row["payload"])
                if focused:
                    payload["focus_review"] = json.loads(row["result"])
                for handle, content in zip(handles, self._row_buffers(payload, ordinal)):
                    handle.write(content)
            packaged = metadata["packaged_count"] + len(batch)
            complete = packaged == metadata["target_total"]
            if complete:
                handles[0].write(b"\n]\n")
            elif not batch:
                raise ExportIntegrityError("导出准备阶段提前结束")
            for name, handle in zip(_PREPARED_FILES, handles):
                handle.flush()
                os.fsync(handle.fileno())
                offsets[name] = handle.tell()
            checkpoint.update(file_offsets=offsets, packaged_cursor=batch[-1]["seq"] if batch else 0,
                              prepared_complete=complete)
            self._save(job, status="packaging", phase="packaging" if complete else "preparing",
                       packaged_count=packaged, checkpoint=checkpoint)
        finally:
            for handle in handles:
                handle.close()

    def _package(self, job, metadata):
        metadata = self._heartbeat(job, status="packaging", phase="packaging", bytes_written=0, error=None)
        receipt = {"task_id": metadata["task_id"], "filters": metadata["filters"],
                   "created_at": metadata["created_at"], "matched_total": metadata["matched_total"],
                   "requested_limit": metadata["requested_limit"], "target_total": metadata["target_total"],
                   "count": metadata["packaged_count"], "expected_total": metadata["matched_total"],
                   "complete": True, "request_complete": True,
                   "library_complete": metadata["target_total"] == metadata["matched_total"],
                   "user_limited": metadata["requested_limit"] is not None,
                   "snapshot": {key: metadata["checkpoint"][key] for key in ("generation", "snapshot_complete")},
                   "source_attachments_downloaded": False, "new_ai_analysis_performed": False}
        focused = metadata["filters"].get("query_scope") == "focused"
        if focused:
            receipt.update(semantic_screening_performed=metadata["semantic_cache_hits"] + metadata["semantic_new_reviews"] > 0,
                           new_ai_analysis_performed=metadata.get("semantic_new_reviews", 0) > 0,
                           ai_usage_purpose="primary_subject_classification",
                           source_record_analysis_modified=False,
                           focus_policy_version=metadata.get("focus_policy_version"),
                           candidate_total=metadata["candidate_total"], screened_count=metadata["screened_count"],
                           focused_count=metadata["focused_count"], mentioned_count=metadata["mentioned_count"],
                           irrelevant_count=metadata["irrelevant_count"], uncertain_count=metadata["uncertain_count"],
                           source_only_count=metadata.get("source_only_count", 0),
                           semantic_text_scope="available_text_originals_only",
                           source_attachment_content_read=False,
                           text_originals_reviewed=metadata["screened_count"] - metadata.get("source_only_count", 0),
                           focus_screening_complete=metadata["focus_screening_complete"],
                           semantic_cache_hits=metadata["semantic_cache_hits"],
                           semantic_new_reviews=metadata["semantic_new_reviews"])
        manifest = self._private_path(job / "检索与完整性.json.partial")
        descriptor = os.open(manifest, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json(receipt).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        files = [(name, self._private_path(job / (name + ".partial"))) for name in _PREPARED_FILES]
        if focused:
            audit = self._private_path(job / "主旨筛选依据.json.partial")
            descriptor = os.open(audit, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle, self._connect(job) as connection:
                handle.write(b"[\n")
                for ordinal, row in enumerate(connection.execute(
                        "SELECT m.topic_id,m.payload,f.result FROM members m JOIN focus_reviews f "
                        "ON m.seq=f.seq ORDER BY m.seq")):
                    payload = json.loads(row["payload"])
                    entry = {"topic_id": row["topic_id"], "title": payload.get("title"),
                             "published_at": payload.get("published_at"), "review": json.loads(row["result"])}
                    handle.write((",\n" if ordinal else "").encode() + _json(entry).encode("utf-8"))
                handle.write(b"\n]\n")
                handle.flush()
                os.fsync(handle.fileno())
            files.append(("主旨筛选依据.json", audit))
        files.append(("检索与完整性.json", manifest))
        total = sum(path.stat().st_size for _name, path in files)
        self._save(job, bytes_total=total)
        archive_path = self._private_path(job / "archive.zip.partial")
        descriptor = os.open(archive_path, os.O_CREAT | os.O_TRUNC | os.O_RDWR, 0o600)
        written = 0
        with os.fdopen(descriptor, "w+b") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1,
                                 allowZip64=True) as archive:
                for name, source in files:
                    with source.open("rb") as reader, archive.open(name, "w", force_zip64=True) as writer:
                        while True:
                            chunk = reader.read(1024 * 1024)
                            if not chunk:
                                break
                            writer.write(chunk)
                            written += len(chunk)
                            self._heartbeat(job, bytes_written=written)
            handle.flush()
            os.fsync(handle.fileno())
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None or written != total:
                raise ExportIntegrityError("下载包校验失败，未发布不完整文件")
            if [item.filename for item in archive.infolist()] != [name for name, _path in files]:
                raise ExportIntegrityError("下载包清单与准备文件不一致")
        title = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", str(metadata["filters"].get("query") or "全部匹配"))[:32].strip(" .")
        kind = "聚焦小作文" if focused else "小作文"
        filename = f"{title or '小作文'}_{kind}_{metadata['target_total']}条_{metadata['task_id'][-8:]}.zip"
        final = self._private_path(job / filename)
        os.replace(archive_path, final)
        directory_descriptor = os.open(job, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        result = {"ok": True, "filename": filename, "size": final.stat().st_size,
                  "download_url": f"/api/v1/financial-data/essays/package/tasks/{metadata['task_id']}/download",
                  "title": f"{title} 小作文（{metadata['target_total']} 条）", "count": metadata["target_total"],
                  "complete": True, "request_complete": True, "library_complete": receipt["library_complete"]}
        self._save(job, status="completed", phase="completed", resumable=False, result=result, error=None,
                   complete=True, request_complete=True, library_complete=receipt["library_complete"],
                   bytes_written=total, bytes_total=total)

    def start_worker(self):
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._loop, name="essay-export-worker", daemon=True)
            self._worker.start()

    def stop_worker(self):
        self._stop.set()
        self._wake.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            try:
                progressed = self.run_once()
            except Exception:
                # A corrupt unrelated task / unavailable disk must not kill the
                # scheduler. Each claimed task records its own visible error.
                logger.exception("Essay export scheduler iteration failed; checkpoints retained")
                progressed = False
            if not progressed:
                self._wake.wait(1)
                self._wake.clear()


_service = None
_service_lock = threading.Lock()


def get_essay_export_service():
    global _service
    with _service_lock:
        if _service is None:
            _service = EssayExportService()
        return _service
