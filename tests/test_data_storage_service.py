# -*- coding: utf-8 -*-
"""Unified SQLite maintenance and logical-domain status tests."""

from __future__ import annotations

import os
import sqlite3

import pytest

from src.config import Config
from src.services.data_storage_service import DataStorageService
from src.storage import DatabaseManager, SQLITE_RUNTIME_INDEX_MIGRATION


@pytest.fixture()
def storage(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    database_path = tmp_path / "storage.db"
    os.environ["DATABASE_PATH"] = str(database_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    manager = DatabaseManager.get_instance()
    try:
        yield manager, database_path
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def test_storage_status_groups_logical_domains_and_reports_pragmas(storage) -> None:
    manager, database_path = storage
    result = DataStorageService(manager).status(include_integrity=True)

    assert result["storage"] == "sqlite"
    assert result["database"] == str(database_path)
    assert result["integrity"] == "ok"
    assert result["pragmas"]["journal_mode"] == "wal"
    assert result["pragmas"]["foreign_keys"] is True
    assert {item["domain"] for item in result["domains"]} >= {"market", "knowledge", "intelligence"}
    assert {item["table"] for item in result["tables"]} >= {"stock_ticks", "research_notes", "monitoring_events"}


def test_runtime_index_migration_is_recorded_and_redundant_indexes_are_absent(storage) -> None:
    _, database_path = storage
    with sqlite3.connect(database_path) as connection:
        migration = connection.execute(
            "SELECT description FROM schema_migrations WHERE version = ?", (SQLITE_RUNTIME_INDEX_MIGRATION,)
        ).fetchone()
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        tick_columns = {row[1] for row in connection.execute("PRAGMA table_info(stock_ticks)").fetchall()}
        index_columns = {row[1] for row in connection.execute("PRAGMA table_info(market_index_bars)").fetchall()}

    assert migration is not None
    assert "ix_tick_code_time" not in indexes
    assert "ix_monitoring_events_source_key" not in indexes
    assert "ix_monitoring_event_source_time" in indexes
    assert {"volume_delta", "amount_delta"} <= tick_columns
    assert {"volume_delta", "amount_delta"} <= index_columns


def test_storage_optimize_uses_passive_checkpoint(storage) -> None:
    manager, _ = storage
    result = DataStorageService(manager).optimize()

    assert result["optimized"] is True
    assert result["checkpoint"]["busy"] is False
    assert result["checkpoint"]["wal_frames"] >= 0
