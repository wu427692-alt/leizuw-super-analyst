# -*- coding: utf-8 -*-
"""Versioned, read-only knowledge database snapshots for iCloud Drive."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from src.config import get_config

logger = logging.getLogger(__name__)

KNOWLEDGE_TABLES = (
    "stock_daily", "stock_intraday", "stock_ticks", "market_index_bars", "news_intel", "intelligence_sources", "intelligence_items",
    "research_notes", "zsxq_sync_states", "essay_analysis_records", "essay_daily_reports", "monitoring_sources", "monitoring_events",
    "fundamental_snapshot", "analysis_history", "decision_signals",
    "decision_signal_outcomes", "decision_signal_feedback",
)


class ICloudKnowledgeError(RuntimeError):
    """Safe iCloud availability, configuration, or snapshot error."""


class ICloudKnowledgeService:
    """Export consistent knowledge-only SQLite versions to iCloud Drive."""

    def __init__(self, *, database_path: Optional[str] = None, cloud_dir: Optional[str] = None):
        configured_db = database_path or getattr(get_config(), "database_path", "./data/stock_analysis.db")
        self.database_path = Path(configured_db).expanduser().resolve()
        configured_cloud = cloud_dir or os.getenv("ICLOUD_KNOWLEDGE_DIR", "").strip()
        self.icloud_drive_root = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
        self.cloud_dir = (
            Path(configured_cloud).expanduser().resolve()
            if configured_cloud else self.icloud_drive_root / "Daily Stock Analysis/Knowledge"
        )
        self.catalog_path = self.database_path.parent / "icloud_snapshot_catalog.json"
        self.retention = max(1, min(int(os.getenv("ICLOUD_KNOWLEDGE_RETENTION", "12")), 365))

    def status(self, snapshots: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        snapshots = self.list_snapshots() if snapshots is None else snapshots
        return {
            "available": self.icloud_drive_root.is_dir() or bool(os.getenv("ICLOUD_KNOWLEDGE_DIR", "").strip()),
            "enabled": os.getenv("ICLOUD_KNOWLEDGE_AUTO_START", "").strip().lower() in {"1", "true", "yes", "on"},
            "database_path": str(self.database_path),
            "cloud_dir": str(self.cloud_dir),
            "retention": self.retention,
            "snapshot_count": len(snapshots),
            "latest": snapshots[0] if snapshots else None,
            "mode": "versioned_snapshot",
            "multi_device_writes": False,
        }

    def create_snapshot(self) -> Dict[str, Any]:
        if not self.database_path.is_file():
            raise ICloudKnowledgeError(f"local database not found: {self.database_path}")
        if not self.icloud_drive_root.is_dir() and not os.getenv("ICLOUD_KNOWLEDGE_DIR", "").strip():
            raise ICloudKnowledgeError("iCloud Drive is unavailable; sign in to iCloud and enable iCloud Drive")
        self.cloud_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        device = self._safe_device_name()
        with tempfile.TemporaryDirectory(prefix="dsa-icloud-") as temp_dir:
            raw_snapshot = Path(temp_dir) / "source.db"
            knowledge_db = Path(temp_dir) / "knowledge.db"
            self._sqlite_backup(self.database_path, raw_snapshot)
            counts = self._build_knowledge_database(raw_snapshot, knowledge_db, timestamp, device)
            digest = self._sha256(knowledge_db)
            filename = f"knowledge-{timestamp}-{device}-{digest[:12]}.db"
            target = self.cloud_dir / filename
            partial = self.cloud_dir / f".{filename}.partial"
            shutil.copy2(knowledge_db, partial)
            os.replace(partial, target)
        manifest = {
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": digest,
            "size_bytes": target.stat().st_size,
            "device": device,
            "tables": counts,
            "format": "sqlite3-readonly-knowledge-v1",
        }
        self._atomic_json(self.cloud_dir / f"{filename}.json", manifest)
        self._atomic_json(self.cloud_dir / "latest.json", manifest)
        self._remember_snapshot(manifest)
        removed = self._prune()
        return {**manifest, "path": str(target), "removed": removed}

    def list_snapshots(self) -> List[Dict[str, Any]]:
        catalog = self._read_catalog()
        if catalog is not None:
            return catalog
        if not self.cloud_dir.is_dir():
            return []
        items: List[Dict[str, Any]] = []
        # Do not read every manifest here.  iCloud FileProvider may expose old
        # manifests as evicted placeholders; opening each one can synchronously
        # hydrate it and make a harmless status request block for minutes.  The
        # immutable database filename already contains the information needed by
        # the list/status/prune paths.  Full metadata is read only by ``verify``.
        # FileProvider can wedge ``opendir`` inside a long-lived Python process
        # immediately after a large snapshot write.  Isolate enumeration in a
        # short-lived process so an iCloud kernel wait can never consume an API
        # worker indefinitely.
        try:
            completed = subprocess.run(
                ["/bin/ls", "-1t", "--", str(self.cloud_dir)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        for filename in completed.stdout.splitlines():
            if not filename.startswith("knowledge-") or not filename.endswith(".db"):
                continue
            created_at = self._created_at_from_filename(filename)
            parts = filename[:-3].split("-")
            items.append({
                "filename": filename,
                "created_at": created_at,
                "sha256": parts[-1] if len(parts) >= 4 else None,
                "size_bytes": None,
                "device": "-".join(parts[2:-1]) if len(parts) >= 4 else None,
                "format": "sqlite3-readonly-knowledge-v1",
                "present": True,
                "path": str(self.cloud_dir / filename),
            })
        self._write_catalog(items)
        return items

    def _read_catalog(self) -> Optional[List[Dict[str, Any]]]:
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        items = payload.get("snapshots") if isinstance(payload, dict) else None
        return items if isinstance(items, list) else None

    def _write_catalog(self, items: List[Dict[str, Any]]) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self.catalog_path, {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "snapshots": items,
        })

    def _remember_snapshot(self, manifest: Dict[str, Any]) -> None:
        item = {**manifest, "present": True, "path": str(self.cloud_dir / str(manifest["filename"]))}
        existing = self._read_catalog() or []
        items = [item, *(value for value in existing if value.get("filename") != item["filename"])]
        self._write_catalog(items)

    @staticmethod
    def _created_at_from_filename(filename: str) -> Optional[str]:
        match = re.match(r"^knowledge-(\d{8}T\d{6}Z)-", filename)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    def verify(self, filename: str) -> Dict[str, Any]:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith("knowledge-") or not safe_name.endswith(".db"):
            raise ICloudKnowledgeError("invalid snapshot filename")
        path = self.cloud_dir / safe_name
        manifest_path = self.cloud_dir / f"{safe_name}.json"
        if not path.is_file() or not manifest_path.is_file():
            raise ICloudKnowledgeError("snapshot or manifest not found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = self._sha256(path)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        return {
            "filename": safe_name,
            "valid": actual == manifest.get("sha256") and integrity == "ok",
            "expected_sha256": manifest.get("sha256"),
            "actual_sha256": actual,
            "integrity_check": integrity,
        }

    @staticmethod
    def _sqlite_backup(source: Path, target: Path) -> None:
        with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)

    @staticmethod
    def _build_knowledge_database(source: Path, target: Path, timestamp: str, device: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        with sqlite3.connect(target) as output:
            output.execute("ATTACH DATABASE ? AS source", (str(source),))
            existing = {row[0] for row in output.execute("SELECT name FROM source.sqlite_master WHERE type='table'")}
            for table in KNOWLEDGE_TABLES:
                if table not in existing:
                    continue
                output.execute(f'CREATE TABLE "{table}" AS SELECT * FROM source."{table}"')
                counts[table] = int(output.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            output.execute("CREATE TABLE cloud_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            output.executemany("INSERT INTO cloud_metadata(key, value) VALUES (?, ?)", (
                ("format", "dsa-knowledge-v1"), ("created_at_utc", timestamp), ("device", device),
            ))
            output.commit()
            output.execute("DETACH DATABASE source")
            output.execute("VACUUM")
        return counts

    def _prune(self) -> List[str]:
        removed: List[str] = []
        snapshots = self.list_snapshots()
        for item in snapshots[self.retention:]:
            filename = str(item.get("filename") or "")
            if not filename:
                continue
            for path in (self.cloud_dir / filename, self.cloud_dir / f"{filename}.json"):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not prune iCloud knowledge snapshot %s: %s", path, exc)
            removed.append(filename)
        if removed:
            self._write_catalog(snapshots[:self.retention])
        return removed

    @staticmethod
    def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
        partial = path.with_name(f".{path.name}.partial")
        partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(partial, path)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_device_name() -> str:
        raw = platform.node().strip().lower() or "mac"
        return "".join(char if char.isalnum() else "-" for char in raw).strip("-")[:40] or "mac"


class ICloudKnowledgeWorker:
    """Periodic snapshot worker; each version is immutable and conflict-safe."""

    _instance: Optional["ICloudKnowledgeWorker"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        minutes = max(5, min(int(os.getenv("ICLOUD_KNOWLEDGE_SYNC_INTERVAL_MINUTES", "15")), 1440))
        self.interval_seconds = minutes * 60
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "ICloudKnowledgeWorker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run, name="icloud-knowledge-worker", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=10)
        return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "last_snapshot": self._last_snapshot,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                snapshot = ICloudKnowledgeService().create_snapshot()
                with self._lock:
                    self._last_snapshot = snapshot
                    self._last_error = None
            except Exception as exc:  # noqa: BLE001 - cloud availability is transient.
                safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                logger.warning("[icloud-knowledge] snapshot failed: %s", safe_error)
                with self._lock:
                    self._last_error = safe_error
            self._stop_event.wait(self.interval_seconds)
