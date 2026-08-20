# -*- coding: utf-8 -*-
"""Safe iCloud Drive knowledge snapshot tests."""

from __future__ import annotations

import json
import sqlite3

from src.services.icloud_knowledge_service import ICloudKnowledgeService


def _source_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE research_notes (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO research_notes(title) VALUES ('调研纪要')")
        connection.execute("CREATE TABLE monitoring_events (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO monitoring_events(title) VALUES ('行情事件')")
        connection.execute("CREATE TABLE conversation_messages (id INTEGER PRIMARY KEY, content TEXT)")
        connection.execute("INSERT INTO conversation_messages(content) VALUES ('private')")
        connection.commit()


def test_snapshot_is_verified_and_excludes_private_tables(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    cloud = tmp_path / "icloud"
    _source_database(source)
    monkeypatch.setenv("ICLOUD_KNOWLEDGE_DIR", str(cloud))
    service = ICloudKnowledgeService(database_path=str(source), cloud_dir=str(cloud))

    snapshot = service.create_snapshot()
    verification = service.verify(snapshot["filename"])

    assert verification["valid"] is True
    assert snapshot["tables"] == {"research_notes": 1, "monitoring_events": 1}
    with sqlite3.connect(snapshot["path"]) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "research_notes" in tables
    assert "conversation_messages" not in tables


def test_retention_removes_old_database_and_manifest(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    cloud = tmp_path / "icloud"
    _source_database(source)
    monkeypatch.setenv("ICLOUD_KNOWLEDGE_DIR", str(cloud))
    monkeypatch.setenv("ICLOUD_KNOWLEDGE_RETENTION", "1")
    service = ICloudKnowledgeService(database_path=str(source), cloud_dir=str(cloud))
    first = service.create_snapshot()
    first_path = cloud / first["filename"]
    # Make the second immutable version distinct even within the same second.
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO research_notes(title) VALUES ('新纪要')")
        connection.commit()
    second = service.create_snapshot()

    assert second["filename"] != first["filename"]
    assert not first_path.exists()
    assert len(service.list_snapshots()) == 1
    latest = json.loads((cloud / "latest.json").read_text(encoding="utf-8"))
    assert latest["filename"] == second["filename"]


def test_snapshot_listing_does_not_open_manifests(tmp_path, monkeypatch):
    """Listing must stay responsive when iCloud manifests are placeholders."""
    source = tmp_path / "source.db"
    cloud = tmp_path / "icloud"
    _source_database(source)
    monkeypatch.setenv("ICLOUD_KNOWLEDGE_DIR", str(cloud))
    service = ICloudKnowledgeService(database_path=str(source), cloud_dir=str(cloud))
    snapshot = service.create_snapshot()
    manifest = cloud / f'{snapshot["filename"]}.json'
    manifest.chmod(0)

    try:
        items = service.list_snapshots()
    finally:
        manifest.chmod(0o644)

    assert len(items) == 1
    assert items[0]["filename"] == snapshot["filename"]
    assert items[0]["created_at"].startswith(snapshot["created_at"][:19])
    assert items[0]["present"] is True
