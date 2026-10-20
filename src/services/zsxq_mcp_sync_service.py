# -*- coding: utf-8 -*-
"""Direct, cursor-based ZSXQ MCP synchronization with remote-only media links."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
import tomllib
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from src.repositories.research_note_repo import ResearchNoteRepository
from src.services.financial_data_service import ResearchNoteService, _parse_datetime
from src.storage import utc_naive_now

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

logger = logging.getLogger(__name__)
# The configured MCP URL may carry a query credential. Transport-level INFO logs
# include the full URL, so keep them above INFO and expose sanitized worker state instead.
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)


class ZsxqMcpSyncError(RuntimeError):
    """Safe MCP synchronization failure without credential disclosure."""


class ZsxqMcpSyncService:
    # Serialize the short insert/checkpoint section, never a network call or retry.
    _page_write_lock = threading.RLock()

    def __init__(self, repository: Optional[ResearchNoteRepository] = None):
        self.repo = repository or ResearchNoteRepository()
        self.notes = ResearchNoteService(self.repo)
        self.max_pages = max(1, min(int(os.getenv("ZSXQ_MCP_MAX_PAGES_PER_SYNC", "10")), 100))
        self.timeout = max(5, min(int(os.getenv("ZSXQ_MCP_TIMEOUT_SEC", "45")), 300))
        self.history_request_interval = max(
            0.25,
            min(float(os.getenv("ZSXQ_MCP_HISTORY_REQUEST_INTERVAL_SEC", "0.75")), 10.0),
        )
        self.history_retry_attempts = max(
            1,
            min(int(os.getenv("ZSXQ_MCP_HISTORY_RETRY_ATTEMPTS", "6")), 10),
        )

    @property
    def available(self) -> bool:
        return bool(self._mcp_url())

    def status(self) -> Dict[str, Any]:
        return {"available": self.available, "groups": self.repo.list_sync_states(),
                "media_storage": "remote_only", "mode": "direct_mcp_incremental"}

    def sync_once(self) -> Dict[str, Any]:
        url = self._mcp_url()
        if not url:
            raise ZsxqMcpSyncError("未配置 ZSXQ MCP；请设置 ZSXQ_MCP_URL 或 Codex mcp_servers.zsxq")
        try:
            return asyncio.run(self._sync_async(url))
        except ZsxqMcpSyncError:
            raise
        except Exception as exc:
            raise ZsxqMcpSyncError(f"知识星球 MCP 增量同步失败：{type(exc).__name__}") from exc

    def sync_history(
        self,
        *,
        days: int,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Backfill raw notes for research without automatically spending AI tokens."""
        safe_days = max(1, min(int(days), 730))
        url = self._mcp_url()
        if not url:
            raise ZsxqMcpSyncError("未配置 ZSXQ MCP；请设置 ZSXQ_MCP_URL 或 Codex mcp_servers.zsxq")
        try:
            configured_max_pages = os.getenv("ZSXQ_MCP_HISTORY_MAX_PAGES")
            history_max_pages = (
                int(configured_max_pages)
                if configured_max_pages
                else max(500, safe_days * 20)
            )
            return asyncio.run(self._sync_async(
                url,
                history_days=safe_days,
                history_cutoff=start_at,
                history_end=end_at,
                should_stop=should_stop,
                enqueue_analysis=False,
                max_pages=max(1, min(history_max_pages, 50000)),
                progress_callback=progress_callback,
            ))
        except ZsxqMcpSyncError:
            raise
        except Exception as exc:
            raise ZsxqMcpSyncError(f"知识星球 MCP 历史同步失败：{type(exc).__name__}") from exc

    async def _sync_async(
        self,
        url: str,
        *,
        history_days: Optional[int] = None,
        history_cutoff: Optional[datetime] = None,
        history_end: Optional[datetime] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        enqueue_analysis: bool = True,
        max_pages: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        from mcp import Client

        latest = self.repo.latest_created_by_group()
        totals = {"groups": 0, "received": 0, "created": 0, "updated": 0, "unchanged": 0,
                  "media_downloaded": 0, "failed_groups": 0, "incomplete_groups": 0}
        if history_days:
            history_end = history_end or utc_naive_now()
            history_cutoff = history_cutoff or history_end - timedelta(days=history_days)
        self._emit_progress(progress_callback, phase="connecting", progress_percent=1.0,
                            message="正在连接知识星球 MCP")
        async with Client(url, read_timeout_seconds=self.timeout) as client:
            self._emit_progress(progress_callback, phase="discovering_groups", progress_percent=2.0,
                                message="正在读取知识星球列表")
            groups = await self._configured_groups(client)
            if not groups:
                raise ZsxqMcpSyncError("没有可同步的知识星球，尚未完成范围覆盖")
            if history_cutoff is not None:
                # Record every group before fetching so restart also resumes groups
                # which had not yet begun when the process stopped.
                for group in groups:
                    with self._group_execution_lock(group["group_id"], "history") as acquired:
                        if acquired:
                            self._prepare_checkpoint(group, "history", history_cutoff, history_end)
            group_total = len(groups)
            coverages = [0.0 for _ in groups]
            completed_pages = 0
            completed_received = 0
            self._emit_progress(
                progress_callback,
                phase="fetching",
                progress_percent=3.0,
                groups_total=group_total,
                groups_completed=0,
                message=f"已找到 {group_total} 个知识星球，开始分页获取",
            )
            for group_index, group in enumerate(groups):
                def relay_group_progress(update: Dict[str, Any]) -> None:
                    local_progress = max(0.0, min(float(update.get("group_progress_percent", coverages[group_index])), 100.0))
                    coverages[group_index] = local_progress
                    overall = 3.0 + sum(coverages) / max(group_total, 1) * 0.94
                    self._emit_progress(
                        progress_callback,
                        **update,
                        progress_percent=round(min(overall, 97.0), 1),
                        groups_total=group_total,
                        groups_completed=sum(value == 100 for value in coverages),
                        pages_fetched=completed_pages + int(update.get("group_pages_fetched") or 0),
                        received=completed_received + int(update.get("group_received") or 0),
                    )

                result = await self._sync_group(
                    client,
                    group,
                    latest.get(group["group_id"]),
                    history_cutoff=history_cutoff,
                    history_end=history_end,
                    should_stop=should_stop,
                    enqueue_analysis=enqueue_analysis,
                    max_pages=max_pages,
                    progress_callback=relay_group_progress,
                )
                completed_pages += int(result.get("pages_fetched") or 0)
                completed_received += int(result.get("received") or 0)
                totals["groups"] += 1
                for key in ("received", "created", "updated", "unchanged", "media_downloaded"):
                    totals[key] += int(result.get(key) or 0)
                if result.get("status") == "failed":
                    totals["failed_groups"] += 1
                elif history_days and not result.get("history_complete"):
                    totals["incomplete_groups"] += 1
                coverages[group_index] = 100.0 if result.get("history_complete") else float(result.get("coverage_percent") or coverages[group_index])
                self._emit_progress(
                    progress_callback,
                    phase="fetching" if group_index + 1 < group_total else "finalizing",
                    progress_percent=round(min(3.0 + sum(coverages) / max(group_total, 1) * 0.94, 99.0), 1),
                    groups_total=group_total,
                    groups_completed=sum(value == 100 for value in coverages),
                    current_group_id=group["group_id"],
                    current_group_name=group["name"],
                    pages_fetched=completed_pages,
                    received=completed_received,
                    created=totals["created"],
                    updated=totals["updated"],
                    unchanged=totals["unchanged"],
                    message=f"{group['name']} 本批处理结束；未覆盖范围保留断点继续",
                )
        return {
            "mode": "history_backfill" if history_days else "incremental",
            "history_days": history_days,
            "analysis_enqueued": enqueue_analysis,
            "totals": totals,
            "states": self.repo.list_sync_states(),
        }

    async def _configured_groups(self, client: Any) -> List[Dict[str, str]]:
        configured = str(os.getenv("ZSXQ_MCP_GROUPS") or "").strip()
        if configured:
            groups = []
            for item in configured.split(","):
                group_id, _, name = item.strip().partition(":")
                if group_id:
                    groups.append({"group_id": group_id, "name": name or group_id})
            return groups
        existing = [row for row in self.repo.source_summary() if str(row["group_id"]).isdigit()]
        if existing:
            return [{"group_id": str(row["group_id"]), "name": str(row["group_name"])} for row in existing]
        profile = await self._call_json(client, "get_self_info", {})
        user_id = str((profile.get("user") or {}).get("user_id") or "")
        if not user_id:
            raise ZsxqMcpSyncError("知识星球 MCP 未返回当前用户 ID")
        payload = await self._call_json(client, "get_user_groups", {"user_id": user_id, "limit": 200, "scope": "normal"})
        return [
            {"group_id": str(row.get("group_id") or ""), "name": str(row.get("name") or row.get("group_id") or "")}
            for row in payload.get("groups") or []
            if row.get("group_id")
        ]

    async def _sync_group(
        self,
        client: Any,
        group: Dict[str, str],
        cursor: Optional[datetime],
        **options: Any,
    ) -> Dict[str, Any]:
        channel = "history" if options.get("history_cutoff") is not None else "incremental"
        # API and monitor-worker share this data volume, but not Python locks.
        # The kernel releases the lease on exit/crash; history and live use
        # different leases, so a slow archive request cannot pause live polling.
        with self._group_execution_lock(group["group_id"], channel) as acquired:
            if not acquired:
                return {"group_id": group["group_id"], "status": "already_running",
                        "received": 0, "created": 0, "updated": 0, "unchanged": 0,
                        "pages_fetched": 0, "media_downloaded": 0, "history_complete": False}
            return await self._sync_group_owned(client, group, cursor, **options)

    @contextmanager
    def _group_execution_lock(self, group_id: str, channel: str, *, blocking: bool = False):
        database_path = self.repo.db._engine.url.database
        if not database_path or database_path == ":memory:":
            raise ZsxqMcpSyncError("持久同步需要文件数据库")
        root = Path(database_path).resolve().parent / ".zsxq-sync-locks"
        root.mkdir(parents=True, exist_ok=True)
        # Include DB name for independent databases sharing one directory. Hash
        # group IDs so no untrusted provider identifier becomes a file path.
        identity = f"{Path(database_path).name}:{group_id}:{channel}"
        path = root / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
        with path.open("a+b") as handle:
            acquired = False
            try:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                        acquired = True
                    except BlockingIOError:
                        pass
                else:  # pragma: no cover - Windows same-volume locking
                    import msvcrt
                    if path.stat().st_size == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
                        acquired = True
                    except OSError:
                        pass
                yield acquired
            finally:
                if acquired:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    else:  # pragma: no cover
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    @contextmanager
    def _page_commit_lock(self, group_id: str):
        with self._page_write_lock, self._group_execution_lock(group_id, "page", blocking=True) as acquired:
            if not acquired:
                raise ZsxqMcpSyncError("另一进程正在提交本页，保留断点重试")
            yield

    async def _sync_group_owned(
        self,
        client: Any,
        group: Dict[str, str],
        cursor: Optional[datetime],
        *,
        history_cursor: Optional[datetime] = None,
        history_cutoff: Optional[datetime] = None,
        history_end: Optional[datetime] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        enqueue_analysis: bool = True,
        max_pages: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        # history_cursor is intentionally ignored: an old row proves nothing about
        # missing records BETWEEN it and today. Scan the fixed range, skip known IDs.
        group_id, group_name = group["group_id"], group["name"]
        channel = "history" if history_cutoff is not None else "incremental"
        aggregate = {"created": 0, "updated": 0, "unchanged": 0}
        received_count = pages_fetched = 0
        checkpoint: Dict[str, Any] = {}
        try:
            state = self._group_state(group_id)
            previous = state.get(channel + "_checkpoint") or {}
            pending = bool(previous and previous.get("status") != "completed")
            if channel == "history":
                end = history_end or self._date(previous.get("end_at")) or utc_naive_now()
                checkpoint = self._prepare_checkpoint(group, channel, history_cutoff, end)
            elif pending:
                checkpoint = dict(previous)
            else:
                # The committed watermark only advances after every page is covered.
                start = self._date(state.get("last_topic_at")) or cursor or utc_naive_now() - timedelta(days=1)
                checkpoint = self._prepare_checkpoint(group, channel, start, utc_naive_now())
            if checkpoint.get("status") == "completed":
                return {"group_id": group_id, "status": "success", "history_complete": True,
                        "received": 0, "pages_fetched": 0, "media_downloaded": 0, **aggregate}
            start, end = self._date(checkpoint["start_at"]), self._date(checkpoint["end_at"])
            self.repo.update_sync_state(group_id, group_name, last_attempt_at=utc_naive_now())
            if channel == "incremental" and pending:
                # Large outages can need many cycles. Keep new arrivals visible while
                # draining the old gap, without letting the head move its watermark.
                head = await self._fetch_page(client, group_id, None)
                with self._page_commit_lock(group_id):
                    saved = self._insert_missing(head[0], group, enqueue_analysis=enqueue_analysis)
                for key in aggregate:
                    aggregate[key] += saved[key]
                received_count += len(head[0])
            for _ in range(max_pages or self.max_pages):
                if should_stop and should_stop():
                    checkpoint = {**checkpoint, "status": "paused"}
                    self._save_checkpoint(group, channel, checkpoint)
                    break
                topics, oldest, next_cursor, exhausted = await self._fetch_page(
                    client, group_id, checkpoint["next_end_time"], progress_callback,
                )
                # Inclusive lower boundary: equal timestamps may occupy another page.
                complete = exhausted or (oldest is not None and oldest < start)
                eligible = [topic for topic in topics if start <= self._date(topic["create_time"]) < end]
                with self._page_commit_lock(group_id):
                    saved = self._insert_missing(eligible, group, enqueue_analysis=enqueue_analysis and channel != "history")
                    candidate = dict(checkpoint)
                    for key in aggregate:
                        candidate[key] = int(candidate.get(key) or 0) + saved[key]
                    candidate.update({
                        "pages_fetched": int(candidate.get("pages_fetched") or 0) + 1,
                        "received": int(candidate.get("received") or 0) + len(eligible),
                        "next_end_time": next_cursor or checkpoint["next_end_time"],
                        "status": "completed" if complete else "incomplete", "error": None,
                        "coverage_percent": 100.0 if complete else min(99.0, max(0.0,
                            (end - (oldest or end)).total_seconds() / max((end - start).total_seconds(), 1) * 100)),
                    })
                    newest = max(eligible, key=lambda item: self._date(item["create_time"]), default=None)
                    if newest and (not candidate.get("newest_at") or self._date(newest["create_time"]) > self._date(candidate["newest_at"])):
                        candidate.update(newest_at=self._stamp(self._date(newest["create_time"])), newest_topic_id=str(newest["topic_id"]))
                    # Publish the cursor ONLY after the page data has committed. A
                    # failed checkpoint causes a harmless replay, never a skipped page.
                    self._save_checkpoint(group, channel, candidate, saved_count=saved["created"], commit_watermark=complete)
                    checkpoint = candidate
                for key in aggregate:
                    aggregate[key] += saved[key]
                received_count += len(eligible)
                pages_fetched += 1
                coverage = min(99.0, max(0.0, (end - (oldest or end)).total_seconds() / max((end - start).total_seconds(), 1) * 100))
                progress = dict(
                    current_group_id=group_id, current_group_name=group_name,
                    group_pages_fetched=checkpoint["pages_fetched"], group_received=checkpoint["received"],
                    group_progress_percent=100.0 if complete else round(coverage, 1),
                    oldest_at=self._stamp(oldest) if oldest else None,
                    message=f"{group_name}：已扫描 {checkpoint['pages_fetched']} 页，确认 {checkpoint['received']} 条在库（含重复页与已存在）",
                )
                self._emit_progress(progress_callback, phase="fetching", **progress)
                if eligible:
                    self._emit_progress(progress_callback, phase="saving", group_saved=checkpoint["received"], **progress)
                if complete:
                    break
                if channel == "history":
                    await asyncio.sleep(self.history_request_interval)
            return {"group_id": group_id, "status": "success", "received": received_count,
                    "media_downloaded": 0, "media_storage": "remote_only",
                    "analysis_enqueued": enqueue_analysis and channel != "history", "pages_fetched": pages_fetched,
                    "coverage_percent": checkpoint.get("coverage_percent", 0),
                    "history_complete": checkpoint.get("status") == "completed", **aggregate}
        except Exception as exc:
            # Do not expose raw MCP response bodies or credential-bearing URLs.
            safe = f"知识星球同步失败（{type(exc).__name__}），保留原分页断点等待重试"
            if checkpoint:
                checkpoint = {**checkpoint, "status": "failed", "error": safe}
                try:
                    self._save_checkpoint(group, channel, checkpoint)
                except Exception:
                    logger.warning("[zsxq-mcp] group %s checkpoint write failed", group_id)
            logger.warning("[zsxq-mcp] group %s failed: %s", group_id, type(exc).__name__)
            return {"group_id": group_id, "status": "failed", "error": safe, "received": received_count,
                    "media_downloaded": 0, "pages_fetched": pages_fetched, "history_complete": False, **aggregate}

    @staticmethod
    def _date(value: Any) -> Optional[datetime]:
        return _parse_datetime(value, field_name="create_time")

    @staticmethod
    def _stamp(value: datetime) -> str:
        return value.isoformat(timespec="microseconds") + "Z"

    def _group_state(self, group_id: str) -> Dict[str, Any]:
        return next((row for row in self.repo.list_sync_states() if row["group_id"] == group_id), {})

    def _prepare_checkpoint(self, group: Dict[str, str], channel: str, start: datetime, end: datetime) -> Dict[str, Any]:
        if start is None or end is None or start >= end:
            raise ZsxqMcpSyncError("历史同步起止时间无效")
        with self._page_write_lock:
            old = self._group_state(group["group_id"]).get(channel + "_checkpoint") or {}
            if old.get("start_at") == self._stamp(start) and old.get("end_at") == self._stamp(end):
                return dict(old)
            checkpoint = {"version": 1, "status": "incomplete", "start_at": self._stamp(start),
                          "end_at": self._stamp(end), "next_end_time": self._stamp(end),
                          "pages_fetched": 0, "received": 0, "created": 0, "updated": 0, "unchanged": 0}
            self._save_checkpoint(group, channel, checkpoint)
            return checkpoint

    def _save_checkpoint(self, group: Dict[str, str], channel: str, checkpoint: Dict[str, Any],
                         *, saved_count: int = 0, commit_watermark: bool = False) -> None:
        checkpoint = {**checkpoint, "updated_at": self._stamp(utc_naive_now())}
        fields: Dict[str, Any] = {channel + "_checkpoint_json": json.dumps(checkpoint)}
        if saved_count:
            fields["total_saved"] = int(self._group_state(group["group_id"]).get("total_saved") or 0) + saved_count
        # History has its own status and must never regress live freshness/watermarks.
        if channel == "incremental":
            fields.update(last_status="failed" if checkpoint.get("status") == "failed" else "success",
                          last_error=checkpoint.get("error"), last_media_downloaded=0)
            if checkpoint.get("status") != "failed":
                fields.update(last_success_at=utc_naive_now(), last_received=checkpoint.get("received", 0), last_saved=saved_count)
            if commit_watermark and checkpoint.get("newest_at"):
                fields.update(last_topic_at=self._date(checkpoint["newest_at"]), last_topic_id=checkpoint["newest_topic_id"])
        self.repo.update_sync_state(group["group_id"], group["name"], **fields)

    def _insert_missing(self, topics: List[Dict[str, Any]], group: Dict[str, str], *, enqueue_analysis: bool) -> Dict[str, int]:
        deduped = {str(topic["topic_id"]): topic for topic in topics}
        existing = self.repo.existing_topic_ids(deduped)
        result = {"created": 0, "updated": 0, "unchanged": len(existing)}
        recent_cutoff = utc_naive_now() - timedelta(days=1)
        for analyze in (False, True):
            batch = [topic for topic_id, topic in deduped.items() if topic_id not in existing
                     and bool(enqueue_analysis and self._date(topic["create_time"]) >= recent_cutoff
                              and self._has_analysis_text(topic)) == analyze]
            if batch:
                # Attachment-only posts remain searchable/downloadable, not queued.
                saved = self.notes.import_topics(batch, group_id=group["group_id"], group_name=group["name"], enqueue_analysis=analyze)
                for key in result:
                    result[key] += int(saved.get(key) or 0)
        return result

    @staticmethod
    def _has_analysis_text(topic: Dict[str, Any]) -> bool:
        assets = [asset for asset in (topic.get("files") or []) + (topic.get("images") or []) if isinstance(asset, dict)]
        if not assets:
            return True
        content = str(topic.get("content") or "").strip()
        for asset in assets:
            name = str(asset.get("name") or asset.get("filename") or "").strip()
            if name:
                content = content.replace(name, "").strip()
        return content not in {"", "「文件」", "[文件]", "【文件】", "文件", "「图片」", "[图片]", "【图片】", "图片"}

    async def _fetch_page(self, client: Any, group_id: str, end_time: Optional[str],
                          progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        args = {"group_id": group_id, "limit": 30, "scope": "all"}
        if end_time:
            args["end_time"] = self._mcp_page_cursor(end_time)
        page = await self._call_json_with_retry(client, "get_group_topics", args, progress_callback=progress_callback)
        topics = page.get("topics_brief")
        if page.get("success") is False or not isinstance(topics, list) or not isinstance(page.get("has_more"), bool):
            raise ZsxqMcpSyncError("知识星球分页响应无效")
        for topic in topics:
            if not isinstance(topic, dict) or not topic.get("topic_id") or self._date(topic.get("create_time")) is None:
                raise ZsxqMcpSyncError("知识星球主题缺少 ID 或日期")
        oldest = min((self._date(topic["create_time"]) for topic in topics), default=None)
        next_cursor = str(page.get("next_end_time") or "") or None
        if page["has_more"]:
            if not topics or not next_cursor or self._date(next_cursor) is None:
                raise ZsxqMcpSyncError("知识星球分页缺少下一页断点")
            if end_time and self._date(next_cursor) >= self._date(end_time):
                raise ZsxqMcpSyncError("知识星球分页断点未向前推进")
        return topics, oldest, next_cursor, not page["has_more"]

    @classmethod
    def _mcp_page_cursor(cls, value: str) -> str:
        """ZSXQ accepts Shanghai milliseconds +0800, not our UTC Z checkpoint.

        Round an exclusive upper bound UP to the provider's precision so its
        final fractional millisecond is still recalled. Eligibility is checked
        separately against the original UTC range before anything is inserted.
        Already-millisecond provider cursors keep exactly the same instant.
        """
        parsed = cls._date(value)
        if parsed is None:
            raise ZsxqMcpSyncError("知识星球分页时间无效")
        remainder = parsed.microsecond % 1000
        if remainder:
            parsed += timedelta(microseconds=1000 - remainder)
        local = parsed.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        return local.isoformat(timespec="milliseconds").replace("+08:00", "+0800")

    async def _call_json_with_retry(
        self,
        client: Any,
        name: str,
        arguments: Dict[str, Any],
        *,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        progress_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.history_retry_attempts + 1):
            try:
                return await self._call_json(client, name, arguments)
            except Exception as exc:
                last_error = exc
                if attempt >= self.history_retry_attempts:
                    raise
                delay = min(2 ** attempt, 30)
                self._emit_progress(
                    progress_callback,
                    **(progress_context or {}),
                    phase="retry_wait",
                    retry_attempt=attempt,
                    retry_in_seconds=delay,
                    message=f"知识星球触发限流或短暂异常，{delay} 秒后自动重试（{attempt}/{self.history_retry_attempts - 1}）",
                )
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _emit_progress(
        callback: Optional[Callable[[Dict[str, Any]], None]],
        **payload: Any,
    ) -> None:
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:
            logger.debug("[zsxq-mcp] progress callback failed", exc_info=True)

    async def resolve_media_url(self, topic_id: str, kind: str, asset_id: str) -> str:
        """Resolve a fresh ZSXQ URL on demand without downloading media locally."""
        if kind not in {"images", "files"}:
            raise ZsxqMcpSyncError("不支持的附件类型")
        note = self.notes.get_note(topic_id)
        assets = note[kind]
        asset = next((item for item in assets if str(item.get("image_id") or item.get("file_id")) == asset_id), None)
        if not asset:
            raise ZsxqMcpSyncError("知识星球附件不存在")
        url = self._mcp_url()
        if not url:
            raise ZsxqMcpSyncError("知识星球 MCP 不可用，无法获取附件链接")
        from mcp import Client
        async with Client(url, read_timeout_seconds=self.timeout) as client:
            return await self._resolve_media_with_client(client, topic_id, kind, asset_id)

    async def _resolve_media_with_client(self, client: Any, topic_id: str, kind: str, asset_id: str) -> str:
        if kind == "files":
            link = await self._call_json(client, "call_zsxq_api", {
                "method": "GET", "path": f"/v2/files/{asset_id}/download_url",
            })
            url = str((((link.get("body") or {}).get("resp_data") or {}).get("download_url")) or "")
        else:
            detail = await self._call_json(client, "get_topic_info", {"topic_id": topic_id})
            topic = detail.get("topic") or {}
            images = topic.get("images") if isinstance(topic.get("images"), list) else []
            image = next((item for item in images if isinstance(item, dict) and
                          str(item.get("image_id") or self._url_key(str(((item.get("original") or {}).get("url")) or ""))) == asset_id), None)
            source = (image or {}).get("original") or (image or {}).get("large") or (image or {}).get("thumbnail") or {}
            url = str(source.get("url") or "")
        self._validate_remote_media_url(url, kind)
        return url

    def resolve_media_url_sync(self, topic_id: str, kind: str, asset_id: str) -> str:
        try:
            return asyncio.run(self.resolve_media_url(topic_id, kind, asset_id))
        except ZsxqMcpSyncError:
            raise
        except Exception as exc:
            raise ZsxqMcpSyncError(f"知识星球附件链接获取失败：{type(exc).__name__}") from exc

    @staticmethod
    async def _call_json(client: Any, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = await client.call_tool(name, arguments)
        if result.is_error:
            raise ZsxqMcpSyncError(f"MCP tool failed: {name}")
        if result.structured_content is not None:
            return dict(result.structured_content)
        text = "".join(str(item.text) for item in result.content if getattr(item, "type", None) == "text")
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ZsxqMcpSyncError(f"MCP tool returned invalid JSON: {name}") from exc
        if not isinstance(payload, dict):
            raise ZsxqMcpSyncError(f"MCP tool returned non-object: {name}")
        return payload

    @staticmethod
    def _validate_remote_media_url(url: str, kind: str) -> None:
        parsed = urlparse(url)
        expected = "images.zsxq.com" if kind == "images" else "files.zsxq.com"
        if parsed.scheme != "https" or parsed.hostname != expected:
            raise ZsxqMcpSyncError("附件地址不属于允许的知识星球 HTTPS 域名")

    def _mcp_url(self) -> Optional[str]:
        configured = str(os.getenv("ZSXQ_MCP_URL") or "").strip()
        if configured:
            return configured
        config_path = Path(os.getenv("CODEX_CONFIG_PATH", str(Path.home() / ".codex" / "config.toml"))).expanduser()
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            return str((((data.get("mcp_servers") or {}).get("zsxq") or {}).get("url")) or "").strip() or None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _url_key(url: str) -> str:
        return Path(urlparse(url).path).name or "image"


class ZsxqMcpSyncWorker:
    _instance: Optional["ZsxqMcpSyncWorker"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.interval = max(10.0, min(float(os.getenv("ZSXQ_MCP_POLL_SEC", "10")), 3600.0))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._last_sync_at: Optional[float] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._history_thread: Optional[threading.Thread] = None
        self._history_retry_after = 0.0
        self._history_state: Dict[str, Any] = {
            "running": False,
            "lookback_days": None,
            "analysis_enqueued": False,
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
            "phase": "idle",
            "progress_percent": 0.0,
            "groups_total": 0,
            "groups_completed": 0,
            "pages_fetched": 0,
            "received": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "message": None,
            "last_progress_at": None,
        }

    @classmethod
    def get_instance(cls) -> "ZsxqMcpSyncWorker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._state_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._wake.clear()
                self._thread = threading.Thread(target=self._run, name="zsxq-mcp-sync-worker", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        return self.status()

    def trigger(self) -> Dict[str, Any]:
        """Wake incremental MCP synchronization without starting a parallel sync."""
        self.start()
        self._wake.set()
        return {**self.status(), "refresh_requested": True}

    def sync_now(self) -> Dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            return {"status": "already_running", "totals": self._last_result or {}}
        try:
            result = ZsxqMcpSyncService().sync_once()
            totals = result.get("totals") or {}
            if int(totals.get("created") or 0) + int(totals.get("updated") or 0) > 0:
                from src.services.essay_quant_worker import EssayQuantWorker

                EssayQuantWorker.get_instance().request_refresh("zsxq_incremental")
            with self._state_lock:
                self._last_sync_at = time.time(); self._last_result = result.get("totals"); self._last_error = None
            return result
        finally:
            self._sync_lock.release()

    def start_history_backfill(self, *, days: int, start_at: Optional[datetime] = None,
                               end_at: Optional[datetime] = None) -> Dict[str, Any]:
        safe_days = 365 if int(days) <= 365 else 730
        if (start_at is None) != (end_at is None):
            raise ZsxqMcpSyncError("历史同步需要同时指定起止时间")
        if start_at is None:
            pending = self._pending_history()
            if pending and abs((ZsxqMcpSyncService._date(pending["end_at"]) - ZsxqMcpSyncService._date(pending["start_at"])).days - safe_days) <= 2:
                start_at, end_at = ZsxqMcpSyncService._date(pending["start_at"]), ZsxqMcpSyncService._date(pending["end_at"])
            else:
                end_at = utc_naive_now()
                start_at = end_at - timedelta(days=safe_days)
        if not start_at < end_at or end_at - start_at > timedelta(days=732):
            raise ZsxqMcpSyncError("历史同步起止时间无效，最长支持两年")
        with self._state_lock:
            if self._history_thread is not None and self._history_thread.is_alive():
                return {"status": "already_running", "history_backfill": dict(self._history_state)}
            self._history_state = {
                "running": True,
                "lookback_days": safe_days,
                "start_at": ZsxqMcpSyncService._stamp(start_at),
                "end_at": ZsxqMcpSyncService._stamp(end_at),
                "analysis_enqueued": False,
                "started_at": self._iso(time.time()),
                "finished_at": None,
                "result": None,
                "error": None,
                "phase": "connecting",
                "progress_percent": 0.0,
                "groups_total": 0,
                "groups_completed": 0,
                "pages_fetched": 0,
                "received": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "message": "从持久分页断点补齐固定日期范围，实时同步继续运行",
                "last_progress_at": self._iso(time.time()),
            }
            self._history_thread = threading.Thread(
                target=self._run_history_backfill,
                args=(safe_days, start_at, end_at),
                name="zsxq-history-backfill-worker",
                daemon=True,
            )
            self._history_thread.start()
            return {"status": "started", "history_backfill": dict(self._history_state)}

    def status(self) -> Dict[str, Any]:
        service_status = ZsxqMcpSyncService().status()
        with self._state_lock:
            history = dict(self._history_state)
            checkpoints = [row.get("history_checkpoint") or {} for row in service_status["groups"]]
            latest = max((item for item in checkpoints if item.get("start_at") and item.get("end_at")),
                         key=lambda item: item.get("updated_at") or "", default={})
            if latest and history.get("phase") == "idle":
                same_range = [item for item in checkpoints if item.get("start_at") == latest["start_at"] and item.get("end_at") == latest["end_at"]]
                completed = sum(item.get("status") == "completed" for item in same_range)
                history.update(start_at=latest["start_at"], end_at=latest["end_at"],
                               phase="completed" if completed == len(same_range) else "incomplete",
                               groups_total=len(same_range), groups_completed=completed,
                               progress_percent=round(sum(float(item.get("coverage_percent") or 0) for item in same_range) / len(same_range), 1),
                               message="已恢复磁盘保存的同步进度", last_progress_at=latest.get("updated_at"))
                for key in ("pages_fetched", "received", "created", "updated", "unchanged"):
                    history[key] = sum(int(item.get(key) or 0) for item in same_range)
            return {"running": bool(self._thread and self._thread.is_alive()), "poll_seconds": self.interval,
                    "syncing": self._sync_lock.locked(),
                    "last_sync_at": self._iso(self._last_sync_at), "last_result": self._last_result,
                    "last_sync_age_seconds": max(0, int(time.time() - self._last_sync_at)) if self._last_sync_at else None,
                    "last_error": self._last_error,
                    "history_backfill": history,
                    **service_status}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._resume_history_if_needed()
            except Exception as exc:
                # A paused archive must not prevent the live poll from running.
                logger.warning("[zsxq-mcp] history resume failed: %s", type(exc).__name__)
            try:
                self.sync_now()
            except Exception as exc:
                safe = f"{type(exc).__name__}: {str(exc)[:400]}"
                logger.warning("[zsxq-mcp] sync cycle failed: %s", safe)
                with self._state_lock:
                    self._last_error = safe
            self._wake.wait(self.interval)
            self._wake.clear()

    def _pending_history(self) -> Dict[str, Any]:
        checkpoints = [row.get("history_checkpoint") or {} for row in ZsxqMcpSyncService().repo.list_sync_states()]
        return max((item for item in checkpoints if item.get("status") != "completed" and item.get("start_at") and item.get("end_at")),
                   key=lambda item: item.get("updated_at") or "", default={})

    def _resume_history_if_needed(self) -> None:
        if self._stop.is_set() or time.monotonic() < self._history_retry_after:
            return
        if self._history_thread is not None and self._history_thread.is_alive():
            return
        pending = self._pending_history()
        if pending:
            start, end = ZsxqMcpSyncService._date(pending["start_at"]), ZsxqMcpSyncService._date(pending["end_at"])
            self.start_history_backfill(days=(end - start).days, start_at=start, end_at=end)

    def _run_history_backfill(self, days: int, start_at: Optional[datetime] = None,
                              end_at: Optional[datetime] = None) -> None:
        # No realtime sync lock: pagination, rate-limit waits and network retries
        # may take hours. Only each committed page's brief write is serialized.
        try:
            self._update_history_progress({
                "phase": "connecting",
                "progress_percent": 1.0,
                "message": "开始连接知识星球 MCP",
            })
            result = ZsxqMcpSyncService().sync_history(
                days=days,
                start_at=start_at,
                end_at=end_at,
                should_stop=self._stop.is_set,
                progress_callback=self._update_history_progress,
            )
            totals = result.get("totals") or {}
            if int(totals.get("created") or 0) + int(totals.get("updated") or 0) > 0 and not self._stop.is_set():
                from src.services.essay_quant_worker import EssayQuantWorker

                EssayQuantWorker.get_instance().request_refresh("zsxq_history_backfill", force=True)
            with self._state_lock:
                if int(totals.get("failed_groups") or 0) > 0:
                    self._history_state.update({
                        "running": False,
                        "finished_at": self._iso(time.time()),
                        "result": totals,
                        "error": f"{int(totals['failed_groups'])} 个知识星球同步失败；已入库的数据已保留，可重新执行继续补齐",
                        "phase": "failed",
                        "message": "历史纪要同步失败",
                        "last_progress_at": self._iso(time.time()),
                    })
                elif int(totals.get("incomplete_groups") or 0) > 0:
                    self._history_state.update({
                        "running": False,
                        "finished_at": self._iso(time.time()),
                        "result": totals,
                        "error": None,
                        "phase": "incomplete",
                        "progress_percent": min(float(self._history_state.get("progress_percent") or 0), 99.0),
                        "message": "断点已保存，后台将继续补齐未覆盖范围",
                        "last_progress_at": self._iso(time.time()),
                    })
                else:
                    self._history_state.update({
                        "running": False,
                        "finished_at": self._iso(time.time()),
                        "result": totals,
                        "error": None,
                        "phase": "completed",
                        "progress_percent": 100.0,
                        "message": "历史纪要同步完成",
                        "last_progress_at": self._iso(time.time()),
                    })
        except Exception as exc:
            safe = f"{type(exc).__name__}: {str(exc)[:400]}"
            logger.warning("[zsxq-mcp] history backfill failed: %s", safe)
            with self._state_lock:
                self._history_state.update({
                    "running": False,
                    "finished_at": self._iso(time.time()),
                    "error": safe,
                    "phase": "failed",
                    "message": "历史纪要同步失败",
                    "last_progress_at": self._iso(time.time()),
                })
        finally:
            self._history_retry_after = time.monotonic() + 60.0

    def _update_history_progress(self, update: Dict[str, Any]) -> None:
        allowed = {
            "phase", "progress_percent", "groups_total", "groups_completed",
            "current_group_id", "current_group_name", "pages_fetched", "received",
            "created", "updated", "unchanged", "message", "oldest_at", "group_saved",
            "retry_attempt", "retry_in_seconds",
        }
        with self._state_lock:
            for key, value in update.items():
                if key in allowed:
                    self._history_state[key] = value
            self._history_state["last_progress_at"] = self._iso(time.time())

    @staticmethod
    def _iso(value: Optional[float]) -> Optional[str]:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value)) if value else None
