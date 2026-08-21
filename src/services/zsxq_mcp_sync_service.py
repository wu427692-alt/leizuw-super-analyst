# -*- coding: utf-8 -*-
"""Direct, cursor-based ZSXQ MCP synchronization with remote-only media links."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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

logger = logging.getLogger(__name__)
# The configured MCP URL may carry a query credential. Transport-level INFO logs
# include the full URL, so keep them above INFO and expose sanitized worker state instead.
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)


class ZsxqMcpSyncError(RuntimeError):
    """Safe MCP synchronization failure without credential disclosure."""


class ZsxqMcpSyncService:
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
        enqueue_analysis: bool = True,
        max_pages: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        from mcp import Client

        latest = self.repo.latest_created_by_group()
        oldest = self.repo.oldest_created_by_group() if history_days else {}
        totals = {"groups": 0, "received": 0, "created": 0, "updated": 0, "unchanged": 0,
                  "media_downloaded": 0, "failed_groups": 0, "incomplete_groups": 0}
        history_cutoff = utc_naive_now() - timedelta(days=history_days) if history_days else None
        self._emit_progress(progress_callback, phase="connecting", progress_percent=1.0,
                            message="正在连接知识星球 MCP")
        async with Client(url, read_timeout_seconds=self.timeout) as client:
            self._emit_progress(progress_callback, phase="discovering_groups", progress_percent=2.0,
                                message="正在读取知识星球列表")
            groups = await self._configured_groups(client)
            group_total = len(groups)
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
                    local_progress = max(0.0, min(float(update.get("group_progress_percent") or 0), 100.0))
                    overall = 3.0 + ((group_index + local_progress / 100.0) / max(group_total, 1)) * 94.0
                    self._emit_progress(
                        progress_callback,
                        **update,
                        progress_percent=round(min(overall, 97.0), 1),
                        groups_total=group_total,
                        groups_completed=group_index,
                        pages_fetched=completed_pages + int(update.get("group_pages_fetched") or 0),
                        received=completed_received + int(update.get("group_received") or 0),
                    )

                result = await self._sync_group(
                    client,
                    group,
                    latest.get(group["group_id"]),
                    history_cursor=oldest.get(group["group_id"]),
                    history_cutoff=history_cutoff,
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
                self._emit_progress(
                    progress_callback,
                    phase="fetching" if group_index + 1 < group_total else "finalizing",
                    progress_percent=round(min(3.0 + ((group_index + 1) / max(group_total, 1)) * 94.0, 99.0), 1),
                    groups_total=group_total,
                    groups_completed=group_index + 1,
                    current_group_id=group["group_id"],
                    current_group_name=group["name"],
                    pages_fetched=completed_pages,
                    received=completed_received,
                    created=totals["created"],
                    updated=totals["updated"],
                    unchanged=totals["unchanged"],
                    message=f"{group['name']} 已完成，正在整理同步结果",
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
        existing = self.repo.source_summary()
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
        *,
        history_cursor: Optional[datetime] = None,
        history_cutoff: Optional[datetime] = None,
        enqueue_analysis: bool = True,
        max_pages: Optional[int] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        group_id, group_name = group["group_id"], group["name"]
        self.repo.update_sync_state(group_id, group_name, last_attempt_at=utc_naive_now(), last_status="running", last_error=None)
        received: List[Dict[str, Any]] = []
        seen_topic_ids: set[str] = set()
        aggregate = {"created": 0, "updated": 0, "unchanged": 0}
        received_count = 0
        newest: Optional[Dict[str, Any]] = None
        end_time: Optional[str] = (
            history_cursor.isoformat(timespec="milliseconds") + "Z"
            if history_cutoff is not None and history_cursor is not None and history_cursor > history_cutoff
            else None
        )
        pages_fetched = 0
        last_group_progress = 0.0
        history_complete = history_cutoff is None
        try:
            page_limit = max_pages or self.max_pages
            for _ in range(page_limit):
                pages_fetched += 1
                args: Dict[str, Any] = {"group_id": group_id, "limit": 30, "scope": "all"}
                if end_time:
                    args["end_time"] = end_time
                page = await self._call_json_with_retry(
                    client,
                    "get_group_topics",
                    args,
                    progress_callback=progress_callback if history_cutoff is not None else None,
                    progress_context={
                        "current_group_id": group_id,
                        "current_group_name": group_name,
                        "group_pages_fetched": pages_fetched - 1,
                        "group_received": received_count,
                        "group_progress_percent": last_group_progress,
                    },
                )
                if page.get("success") is False:
                    raise ZsxqMcpSyncError(str(page.get("error") or "知识星球返回失败")[:300])
                topics = page.get("topics_brief") or []
                if not isinstance(topics, list):
                    raise ZsxqMcpSyncError("知识星球主题页缺少 topics_brief")
                valid_topics = [topic for topic in topics if isinstance(topic, dict)]
                if history_cutoff is not None:
                    valid_topics = [
                        topic for topic in valid_topics
                        if _parse_datetime(topic.get("create_time"), field_name="create_time") >= history_cutoff
                    ]
                    page_topics: List[Dict[str, Any]] = []
                    for topic in valid_topics:
                        topic_id = str(topic.get("topic_id") or "")
                        if topic_id and topic_id not in seen_topic_ids:
                            seen_topic_ids.add(topic_id)
                            page_topics.append(topic)
                    if page_topics:
                        saved = self.notes.import_topics(
                            page_topics,
                            group_id=group_id,
                            group_name=group_name,
                            enqueue_analysis=enqueue_analysis,
                        )
                        for key in aggregate:
                            aggregate[key] += int(saved.get(key) or 0)
                        received_count += len(page_topics)
                        page_newest = max(page_topics, key=lambda topic: str(topic.get("create_time") or ""))
                        if newest is None or str(page_newest.get("create_time") or "") > str(newest.get("create_time") or ""):
                            newest = page_newest
                else:
                    received.extend(valid_topics)
                oldest = min((_parse_datetime(topic.get("create_time"), field_name="create_time") for topic in topics), default=None)
                coverage = 0.0
                if history_cutoff is not None and oldest is not None:
                    total_seconds = max((utc_naive_now() - history_cutoff).total_seconds(), 1.0)
                    coverage = min(max((utc_naive_now() - oldest).total_seconds() / total_seconds * 100.0, 0.0), 100.0)
                    last_group_progress = round(min(coverage * 0.82, 82.0), 1)
                self._emit_progress(
                    progress_callback,
                    phase="fetching",
                    group_progress_percent=last_group_progress if history_cutoff else 40.0,
                    current_group_id=group_id,
                    current_group_name=group_name,
                    group_pages_fetched=pages_fetched,
                    group_received=received_count if history_cutoff is not None else len(received),
                    oldest_at=oldest.isoformat() + "Z" if oldest else None,
                    message=f"{group_name}：已获取 {pages_fetched} 页 / {received_count if history_cutoff is not None else len(received)} 条",
                )
                if history_cutoff is not None and page_topics:
                    self._emit_progress(
                        progress_callback,
                        phase="saving",
                        group_progress_percent=round(min(coverage * 0.82 + 0.5, 82.0), 1),
                        current_group_id=group_id,
                        current_group_name=group_name,
                        group_pages_fetched=pages_fetched,
                        group_received=received_count,
                        group_saved=received_count,
                        oldest_at=oldest.isoformat() + "Z" if oldest else None,
                        created=aggregate["created"],
                        updated=aggregate["updated"],
                        unchanged=aggregate["unchanged"],
                        message=f"{group_name}：{received_count} 条已写入本地库",
                    )
                if not topics or not page.get("has_more"):
                    history_complete = True
                    break
                if history_cutoff is not None and oldest is not None and oldest <= history_cutoff:
                    history_complete = True
                    break
                if history_cutoff is None and cursor is not None and oldest is not None and oldest <= cursor:
                    break
                end_time = str(page.get("next_end_time") or "") or None
                if not end_time:
                    history_complete = True
                    break
                if history_cutoff is not None:
                    await asyncio.sleep(self.history_request_interval)
            if history_cutoff is None:
                deduped = {str(topic.get("topic_id")): topic for topic in received if topic.get("topic_id")}
                topics = list(deduped.values())
                received_count = len(topics)
                for offset in range(0, len(topics), 200):
                    saved = self.notes.import_topics(
                        topics[offset:offset + 200],
                        group_id=group_id,
                        group_name=group_name,
                        enqueue_analysis=enqueue_analysis,
                    )
                    for key in aggregate:
                        aggregate[key] += int(saved.get(key) or 0)
                newest = max(topics, key=lambda topic: str(topic.get("create_time") or ""), default=None)
            newest_at = _parse_datetime(newest.get("create_time"), field_name="create_time") if newest else cursor
            saved_count = aggregate["created"] + aggregate["updated"]
            self.repo.update_sync_state(
                group_id, group_name, last_status="success", last_success_at=utc_naive_now(),
                last_topic_id=str(newest.get("topic_id")) if newest else None,
                last_topic_at=newest_at,
                last_received=received_count, last_saved=saved_count, last_media_downloaded=0,
                total_saved=(next((row["total_saved"] for row in self.repo.list_sync_states()
                                   if row["group_id"] == group_id), 0) + saved_count),
            )
            return {"group_id": group_id, "status": "success", "received": received_count,
                    "media_downloaded": 0, "media_storage": "remote_only",
                    "analysis_enqueued": enqueue_analysis, "pages_fetched": pages_fetched,
                    "history_complete": history_complete, **aggregate}
        except Exception as exc:
            safe = f"{type(exc).__name__}: {str(exc)[:400]}"
            self.repo.update_sync_state(group_id, group_name, last_status="failed", last_error=safe)
            logger.warning("[zsxq-mcp] group %s failed: %s", group_id, safe)
            return {
                "group_id": group_id,
                "status": "failed",
                "error": safe,
                "received": received_count,
                "created": aggregate["created"],
                "updated": aggregate["updated"],
                "unchanged": aggregate["unchanged"],
                "media_downloaded": 0,
                "pages_fetched": pages_fetched,
                "history_complete": False,
            }

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
        self.interval = max(10.0, min(float(os.getenv("ZSXQ_MCP_POLL_SEC", "30")), 3600.0))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._last_sync_at: Optional[float] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._history_thread: Optional[threading.Thread] = None
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

    def start_history_backfill(self, *, days: int) -> Dict[str, Any]:
        safe_days = 365 if int(days) <= 365 else 730
        with self._state_lock:
            if self._history_thread is not None and self._history_thread.is_alive():
                return {"status": "already_running", "history_backfill": dict(self._history_state)}
            self._history_state = {
                "running": True,
                "lookback_days": safe_days,
                "analysis_enqueued": False,
                "started_at": self._iso(time.time()),
                "finished_at": None,
                "result": None,
                "error": None,
                "phase": "waiting_for_incremental_sync",
                "progress_percent": 0.0,
                "groups_total": 0,
                "groups_completed": 0,
                "pages_fetched": 0,
                "received": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "message": "正在等待当前增量同步结束",
                "last_progress_at": self._iso(time.time()),
            }
            self._history_thread = threading.Thread(
                target=self._run_history_backfill,
                args=(safe_days,),
                name="zsxq-history-backfill-worker",
                daemon=True,
            )
            self._history_thread.start()
            return {"status": "started", "history_backfill": dict(self._history_state)}

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {"running": bool(self._thread and self._thread.is_alive()), "poll_seconds": self.interval,
                    "syncing": self._sync_lock.locked(),
                    "last_sync_at": self._iso(self._last_sync_at), "last_result": self._last_result,
                    "last_sync_age_seconds": max(0, int(time.time() - self._last_sync_at)) if self._last_sync_at else None,
                    "last_error": self._last_error,
                    "history_backfill": dict(self._history_state),
                    **ZsxqMcpSyncService().status()}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_now()
            except Exception as exc:
                safe = f"{type(exc).__name__}: {str(exc)[:400]}"
                logger.warning("[zsxq-mcp] sync cycle failed: %s", safe)
                with self._state_lock:
                    self._last_error = safe
            self._wake.wait(self.interval)
            self._wake.clear()

    def _run_history_backfill(self, days: int) -> None:
        self._sync_lock.acquire()
        try:
            self._update_history_progress({
                "phase": "connecting",
                "progress_percent": 1.0,
                "message": "开始连接知识星球 MCP",
            })
            result = ZsxqMcpSyncService().sync_history(
                days=days,
                progress_callback=self._update_history_progress,
            )
            totals = result.get("totals") or {}
            if int(totals.get("created") or 0) + int(totals.get("updated") or 0) > 0:
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
                        "message": "已达到分页安全上限，当前范围尚未完整覆盖",
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
            self._sync_lock.release()

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
