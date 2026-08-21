# -*- coding: utf-8 -*-
"""Direct ZSXQ MCP cursor, SQLite ingestion, and remote attachment-link tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from types import SimpleNamespace

from sqlalchemy import select

from src.config import Config
from src.repositories.research_note_repo import ResearchNoteRepository
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncService
from src.storage import DatabaseManager, EssayAnalysisRecord


class FakeMcpClient:
    async def call_tool(self, name, arguments):
        assert name == "get_group_topics"
        payload = {
            "success": True, "has_more": False, "next_end_time": None,
            "topics_brief": [{
                "topic_id": "topic-1", "title": "图片与文件纪要", "content": "600519 公司调研",
                "create_time": "2026-08-19T16:53:01.277+0800", "modify_time": "",
                "group": {"group_id": arguments["group_id"], "name": "调研纪要"},
                "owner": {"user_id": "u1", "name": "作者"}, "type": "talk", "digested": False,
                "sticky": False, "counts": {}, "files": [],
                "images": [{"image_id": "image-1", "type": "png",
                            "original": {"url": "https://images.zsxq.com/image-key?token=secret"}}],
            }],
        }
        return SimpleNamespace(is_error=False, structured_content=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def test_direct_mcp_page_is_ingested_without_local_media_download(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "zsxq.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        repo = ResearchNoteRepository(DatabaseManager.get_instance())
        service = ZsxqMcpSyncService(repo)
        result = asyncio.run(service._sync_group(
            FakeMcpClient(), {"group_id": "group-1", "name": "调研纪要"}, datetime(2026, 8, 19, 0, 0),
        ))
        assert result["created"] == 1
        assert result["media_downloaded"] == 0
        assert result["media_storage"] == "remote_only"
        note = service.notes.get_note("topic-1")
        assert note["images"][0]["view_url"].endswith("/media/images/image-1")
        assert note["images"][0]["download_status"] == "remote_on_demand"
        assert "local_path" not in note["images"][0]
        assert "token=" not in json.dumps(note["images"], ensure_ascii=False)
        state = repo.list_sync_states()[0]
        assert state["last_status"] == "success"
        assert state["last_media_downloaded"] == 0
        assert state["last_topic_at"].startswith("2026-08-19T08:53:01")
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_history_page_is_ingested_without_creating_ai_tasks(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "zsxq-history.db")
    Config.reset_instance(); DatabaseManager.reset_instance()
    try:
        db = DatabaseManager.get_instance()
        repo = ResearchNoteRepository(db)
        service = ZsxqMcpSyncService(repo)
        progress_updates = []
        result = asyncio.run(service._sync_group(
            FakeMcpClient(),
            {"group_id": "group-1", "name": "调研纪要"},
            None,
            history_cutoff=datetime(2026, 1, 1),
            enqueue_analysis=False,
            max_pages=20,
            progress_callback=progress_updates.append,
        ))
        assert result["created"] == 1
        assert result["analysis_enqueued"] is False
        assert [update["phase"] for update in progress_updates] == ["fetching", "saving"]
        assert progress_updates[0]["group_pages_fetched"] == 1
        assert progress_updates[0]["group_received"] == 1
        assert progress_updates[-1]["group_progress_percent"] > 0
        assert progress_updates[-1]["group_saved"] == 1
        with db.get_session() as session:
            assert session.execute(select(EssayAnalysisRecord)).scalars().all() == []
    finally:
        DatabaseManager.reset_instance(); Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


class FakeMediaClient:
    async def call_tool(self, name, arguments):
        if name == "get_topic_info":
            payload = {"topic": {"images": [{
                "image_id": "image-1",
                "original": {"url": "https://images.zsxq.com/fresh-image?token=fresh"},
            }]}}
        else:
            assert name == "call_zsxq_api"
            assert arguments["path"] == "/v2/files/file-1/download_url"
            payload = {"body": {"resp_data": {
                "download_url": "https://files.zsxq.com/fresh-file?token=fresh",
            }}}
        return SimpleNamespace(is_error=False, structured_content=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def test_media_links_are_resolved_remotely_on_demand():
    service = ZsxqMcpSyncService.__new__(ZsxqMcpSyncService)
    client = FakeMediaClient()
    image_url = asyncio.run(service._resolve_media_with_client(client, "topic-1", "images", "image-1"))
    file_url = asyncio.run(service._resolve_media_with_client(client, "topic-1", "files", "file-1"))
    assert image_url == "https://images.zsxq.com/fresh-image?token=fresh"
    assert file_url == "https://files.zsxq.com/fresh-file?token=fresh"


def test_history_mcp_call_retries_transient_rate_limit(monkeypatch):
    service = ZsxqMcpSyncService.__new__(ZsxqMcpSyncService)
    service.history_retry_attempts = 3
    attempts = []
    waits = []
    progress_updates = []

    async def fake_call_json(client, name, arguments):
        attempts.append((name, arguments))
        if len(attempts) == 1:
            raise RuntimeError("429 Too Many Requests")
        return {"success": True}

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(service, "_call_json", fake_call_json)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    result = asyncio.run(service._call_json_with_retry(
        object(),
        "get_group_topics",
        {"group_id": "group-1"},
        progress_callback=progress_updates.append,
        progress_context={"group_progress_percent": 12.5},
    ))

    assert result == {"success": True}
    assert len(attempts) == 2
    assert waits == [2]
    assert progress_updates[0]["phase"] == "retry_wait"
    assert progress_updates[0]["group_progress_percent"] == 12.5
