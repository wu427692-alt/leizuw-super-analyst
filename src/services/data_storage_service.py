"""Unified SQLite health, freshness and non-blocking planner maintenance."""

from __future__ import annotations

from datetime import datetime, time as clock_time, timezone
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.config import get_config
from src.storage import DatabaseManager


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TABLE_SPECS: Dict[str, Dict[str, Any]] = {
    "stock_ticks": {"domain": "market", "latest": "timestamp", "clock": "local", "mode": "realtime", "target": 5},
    "stock_intraday": {"domain": "market", "latest": "timestamp", "clock": "local", "mode": "near_realtime", "target": 120},
    "stock_daily": {"domain": "market", "latest": "date", "clock": "local", "mode": "daily", "target": 129600},
    "market_index_bars": {"domain": "market", "latest": "timestamp", "clock": "local", "mode": "realtime", "target": 5},
    "research_notes": {"domain": "knowledge", "latest": "synced_at", "clock": "utc", "mode": "near_realtime", "target": 90},
    "zsxq_sync_states": {"domain": "knowledge", "latest": "last_success_at", "clock": "utc", "mode": "near_realtime", "target": 90},
    "essay_analysis_records": {"domain": "knowledge", "latest": "updated_at", "clock": "utc", "mode": "queue", "target": 300},
    "essay_daily_reports": {"domain": "knowledge", "latest": "updated_at", "clock": "utc", "mode": "daily", "target": 129600},
    "monitoring_events": {"domain": "intelligence", "latest": "ingested_at", "clock": "utc", "mode": "near_realtime", "target": 120},
    "monitoring_sources": {"domain": "intelligence", "latest": "last_success_at", "clock": "utc", "mode": "scheduled", "target": 900},
    "analysis_history": {"domain": "analysis", "latest": "created_at", "clock": "local", "mode": "on_demand", "target": None},
    "fundamental_snapshot": {"domain": "analysis", "latest": "created_at", "clock": "local", "mode": "on_demand", "target": None},
    "essay_quant_runs": {"domain": "analysis", "latest": "created_at", "clock": "utc", "mode": "on_demand", "target": None},
    "portfolio_daily_snapshots": {"domain": "portfolio", "latest": "updated_at", "clock": "local", "mode": "daily", "target": 129600},
}


def _market_open(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now(_SHANGHAI)
    if current.weekday() >= 5:
        return False
    value = current.time().replace(tzinfo=None)
    return clock_time(9, 30) <= value <= clock_time(11, 30) or clock_time(13, 0) <= value <= clock_time(15, 0)


class DataStorageService:
    """Read a single source-of-truth SQLite as several logical data domains."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()
        configured = str(get_config().database_path or "./data/stock_analysis.db")
        engine_path = str(getattr(self.db._engine.url, "database", "") or configured)
        self.database_path = Path(engine_path).expanduser().resolve()

    def status(self, *, include_integrity: bool = False) -> Dict[str, Any]:
        if not getattr(self.db, "_is_sqlite_engine", False):
            return {"storage": "external", "database": str(self.db._engine.url), "domains": [], "tables": []}

        with self.db._engine.connect() as connection:
            table_names = {
                str(row[0]) for row in connection.execute(text(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )).all()
            }
            pragmas = {
                "journal_mode": connection.exec_driver_sql("PRAGMA journal_mode").scalar(),
                "synchronous": int(connection.exec_driver_sql("PRAGMA synchronous").scalar() or 0),
                "wal_autocheckpoint_pages": int(connection.exec_driver_sql("PRAGMA wal_autocheckpoint").scalar() or 0),
                "foreign_keys": bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar()),
                "page_size": int(connection.exec_driver_sql("PRAGMA page_size").scalar() or 0),
                "page_count": int(connection.exec_driver_sql("PRAGMA page_count").scalar() or 0),
                "freelist_count": int(connection.exec_driver_sql("PRAGMA freelist_count").scalar() or 0),
            }
            integrity = connection.exec_driver_sql("PRAGMA quick_check").scalar() if include_integrity else None
            table_rows = []
            for table_name, spec in _TABLE_SPECS.items():
                if table_name not in table_names:
                    continue
                latest_column = str(spec["latest"])
                row = connection.exec_driver_sql(
                    f'SELECT COUNT(*) AS row_count, MAX("{latest_column}") AS latest FROM "{table_name}"'
                ).one()
                latest = str(row[1]) if row[1] is not None else None
                age = self._age_seconds(latest, clock=str(spec["clock"]))
                target = spec.get("target")
                state = "empty" if not row[0] else "available"
                if target is not None and age is not None:
                    if spec["domain"] == "market" and spec["mode"] in {"realtime", "near_realtime"} and not _market_open():
                        state = "market_closed"
                    else:
                        state = "fresh" if age <= int(target) * 3 else "stale"
                table_rows.append({
                    "table": table_name,
                    "domain": spec["domain"],
                    "mode": spec["mode"],
                    "row_count": int(row[0] or 0),
                    "latest_at": latest,
                    "age_seconds": age,
                    "target_seconds": target,
                    "state": state,
                })

        domains: Dict[str, Dict[str, Any]] = {}
        for item in table_rows:
            domain = domains.setdefault(item["domain"], {
                "domain": item["domain"], "tables": 0, "rows": 0, "fresh": 0, "stale": 0, "empty": 0,
            })
            domain["tables"] += 1
            domain["rows"] += item["row_count"]
            if item["state"] == "stale":
                domain["stale"] += 1
            elif item["state"] == "empty":
                domain["empty"] += 1
            elif item["state"] in {"fresh", "market_closed", "available"}:
                domain["fresh"] += 1

        db_size = self._file_size(self.database_path)
        wal_size = self._file_size(Path(f"{self.database_path}-wal"))
        shm_size = self._file_size(Path(f"{self.database_path}-shm"))
        return {
            "storage": "sqlite",
            "database": str(self.database_path),
            "files": {"database_bytes": db_size, "wal_bytes": wal_size, "shm_bytes": shm_size},
            "pragmas": pragmas,
            "integrity": integrity,
            "market_open": _market_open(),
            "domains": sorted(domains.values(), key=lambda item: item["domain"]),
            "tables": table_rows,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def optimize(self) -> Dict[str, Any]:
        """Run SQLite-recommended bounded planner maintenance and a passive WAL checkpoint."""
        if not getattr(self.db, "_is_sqlite_engine", False):
            return {"storage": "external", "optimized": False, "reason": "not_sqlite"}
        with self.db._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            optimize_rows = [str(row[0]) for row in connection.exec_driver_sql("PRAGMA optimize").all()]
            checkpoint = connection.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)").one()
        return {
            "storage": "sqlite",
            "optimized": True,
            "planner_actions": optimize_rows,
            "checkpoint": {
                "busy": bool(checkpoint[0]),
                "wal_frames": int(checkpoint[1]),
                "checkpointed_frames": int(checkpoint[2]),
            },
            "files": {
                "database_bytes": self._file_size(self.database_path),
                "wal_bytes": self._file_size(Path(f"{self.database_path}-wal")),
            },
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _age_seconds(value: Optional[str], *, clock: str) -> Optional[int]:
        if not value:
            return None
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            now = datetime.now(timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        elif clock == "utc":
            now = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            now = datetime.now(_SHANGHAI).replace(tzinfo=None)
        return max(0, int((now - parsed).total_seconds()))

    @staticmethod
    def _file_size(path: Path) -> int:
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0


class DataStorageMaintenanceWorker:
    """Keep SQLite planner statistics and WAL checkpoints healthy in the background."""

    _instance: Optional["DataStorageMaintenanceWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        minutes = max(15, min(int(os.getenv("DATA_STORAGE_MAINTENANCE_MINUTES", "60")), 1440))
        self.interval_seconds = minutes * 60
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_run_at: Optional[str] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "DataStorageMaintenanceWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="data-storage-maintenance", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self, timeout: float = 10.0) -> Dict[str, Any]:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        return self.status()

    def run_now(self) -> Dict[str, Any]:
        try:
            result = DataStorageService().optimize()
            with self._lock:
                self._last_run_at = datetime.now(timezone.utc).isoformat()
                self._last_result = result
                self._last_error = None
            return result
        except Exception as exc:  # noqa: BLE001 - maintenance must never stop data ingestion.
            with self._lock:
                self._last_run_at = datetime.now(timezone.utc).isoformat()
                self._last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            return {"optimized": False, "error": self._last_error}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "last_run_at": self._last_run_at,
                "last_result": self._last_result,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        self.run_now()
        while not self._stop.wait(self.interval_seconds):
            self.run_now()
