"""Capacity tuning must preserve bounded memory, WAL visibility and test DBs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import SingletonThreadPool

from src.config import Config
import src.storage as storage


@pytest.fixture
def database_factory(monkeypatch, tmp_path):
    storage.DatabaseManager.reset_instance()
    config = Config(sqlite_wal_enabled=True, sqlite_busy_timeout_ms=1000)
    monkeypatch.setattr(storage, "get_config", lambda: config)

    def create(memory=False):
        url = "sqlite:///:memory:" if memory else f"sqlite:///{tmp_path / 'reader-pool.db'}"
        return storage.DatabaseManager(db_url=url)

    yield create
    storage.DatabaseManager.reset_instance()


def test_reader_pool_reuses_connections_and_bounds_burst(database_factory, monkeypatch):
    monkeypatch.setenv("SQLITE_POOL_SIZE", "2")
    monkeypatch.setenv("SQLITE_MAX_OVERFLOW", "1")
    db = database_factory()
    db._engine.pool._timeout = 0.02
    connections = [db._engine.connect() for _ in range(3)]
    try:
        assert db._engine.pool.checkedout() == 3
        with pytest.raises(PoolTimeout):
            db._engine.connect()
        ids = {id(conn.connection.driver_connection) for conn in connections}
    finally:
        for conn in connections:
            conn.close()
    assert db._engine.pool.checkedout() == 0
    assert db._engine.pool.checkedin() == 2
    with db._engine.connect() as conn:
        assert id(conn.connection.driver_connection) in ids
        assert conn.exec_driver_sql("SELECT 1").scalar() == 1


@pytest.mark.parametrize("size,overflow,expected", [
    ("0", "-1", (2, 0)), ("99999", "99999", (16, 16)),
    ("bad", "bad", (5, 10)),
])
def test_invalid_capacity_cannot_enable_unlimited_pool(
    database_factory, monkeypatch, size, overflow, expected,
):
    monkeypatch.setenv("SQLITE_POOL_SIZE", size)
    monkeypatch.setenv("SQLITE_MAX_OVERFLOW", overflow)
    db = database_factory()
    assert (db._engine.pool.size(), db._engine.pool._max_overflow) == expected


def test_memory_database_keeps_single_connection_semantics(database_factory, monkeypatch):
    monkeypatch.setenv("SQLITE_POOL_SIZE", "8")
    monkeypatch.setenv("SQLITE_MAX_OVERFLOW", "4")
    db = database_factory(memory=True)
    assert isinstance(db._engine.pool, SingletonThreadPool)
    with db._engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE pool_probe(value INTEGER)")
        conn.exec_driver_sql("INSERT INTO pool_probe VALUES (1)")
    with db._engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM pool_probe").scalar() == 1


def test_cached_readers_observe_new_commits_without_blocking_writer(database_factory, monkeypatch):
    monkeypatch.setenv("SQLITE_POOL_SIZE", "2")
    monkeypatch.setenv("SQLITE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("SQLITE_CACHE_SIZE_KIB", "32768")
    monkeypatch.setenv("SQLITE_MMAP_SIZE_MB", "512")
    db = database_factory()
    with db._engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE pool_probe(value INTEGER)")
        conn.exec_driver_sql("INSERT INTO pool_probe VALUES (1)")
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA cache_size").scalar() == -32768
        assert conn.exec_driver_sql("PRAGMA mmap_size").scalar() == 512 * 1024**2
    pending, commit = Event(), Event()

    def write():
        with db._engine.begin() as conn:
            conn.exec_driver_sql("UPDATE pool_probe SET value=2")
            pending.set()
            assert commit.wait(5), "Reader blocked behind an uncommitted writer"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(write)
        try:
            assert pending.wait(5)
            with db._engine.connect() as conn:
                assert conn.exec_driver_sql("SELECT value FROM pool_probe").scalar() == 1
        finally:
            commit.set()
        future.result(timeout=5)
    with db._engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM pool_probe").scalar() == 2
