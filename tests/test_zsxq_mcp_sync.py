# -*- coding: utf-8 -*-
"""Direct ZSXQ MCP cursor, SQLite ingestion, and remote attachment-link tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from types import SimpleNamespace

from src.config import Config
from src.repositories.research_note_repo import ResearchNoteRepository
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncService
from src.storage import DatabaseManager


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
