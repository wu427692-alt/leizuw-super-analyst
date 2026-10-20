"""Atomic owner-private snapshots for explicitly started bounded paper sessions."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID


class LiveTStore:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("JEV_LIVE_T_DIR") or
                         (Path(os.getenv("DATABASE_PATH", "./data/stock_analysis.db")).parent / "jev-live-t"))

    def save(self, owner: str, run: dict):
        run_id = str(UUID(run["run_id"]))
        if owner != "admin" and not (owner.startswith("user:") and owner[5:].isdigit()):
            raise ValueError("an authenticated owner namespace is required")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = json.dumps({"owner": owner, "run": run}, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("paper run snapshot exceeds limit")
        fd, name = tempfile.mkstemp(prefix=".live-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.root / f"{run_id}.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def get(self, owner: str, run_id: str):
        try:
            name = str(UUID(run_id))
            fd = os.open(self.root / f"{name}.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
                    return None
                item = json.load(stream)
            if (not isinstance(item, dict) or not isinstance(item.get("run"), dict)
                    or item.get("owner") != owner or item["run"].get("run_id") != name):
                return None
            return item["run"]
        except (ValueError, OSError, TypeError, KeyError):
            return None

    def list(self, owner: str, limit=20):
        # Server-owned UUID files only. Never read a path selected by a client.
        paths = sorted(self.root.glob("*.json"), key=lambda p: p.name)
        runs = [run for path in paths if (run := self.get(owner, path.stem)) is not None]
        return sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)[:limit]
