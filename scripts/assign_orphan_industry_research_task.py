#!/usr/bin/env python3
"""Safely assign one completed ownerless research task to an existing user."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id")
    parser.add_argument("owner_id")
    parser.add_argument("--expected-source-hash", required=True)
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "/app/data/stock_analysis.db"))
    parser.add_argument("--backup-dir", default="/app/reports/repair-backups")
    parser.add_argument("--commit", action="store_true")
    return parser


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _read_row(connection: sqlite3.Connection, project_id: str) -> dict[str, object]:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM industry_research_projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("课题不存在")
    return dict(row)


def _validate(row: dict[str, object], owner_id: str, expected_source_hash: str) -> None:
    if not re.fullmatch(r"user:[1-9][0-9]*", owner_id):
        raise RuntimeError("owner_id 必须是明确的 user:<数字>")
    if row.get("owner_id") not in (None, ""):
        raise RuntimeError(f"课题已有 owner_id={row.get('owner_id')!r}，拒绝改写")
    if row.get("status") != "completed":
        raise RuntimeError(f"课题状态为 {row.get('status')!r}，仅允许归属已完整完成任务")
    if row.get("source_hash") != expected_source_hash:
        raise RuntimeError("source_hash 与预期不一致，拒绝归属")
    report = json.loads(str(row.get("report_json") or "{}"))
    quality = report.get("quality_assurance") if isinstance(report, dict) else None
    if not isinstance(quality, dict) or quality.get("status") != "ready":
        raise RuntimeError("报告没有通过 ready 质量门，拒绝归属")
    if quality.get("critical_failures"):
        raise RuntimeError("报告仍有 critical_failures，拒绝归属")


def _backup(row: dict[str, object], backup_dir: Path) -> tuple[Path, str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(row)
    digest = hashlib.sha256(payload).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"{row['project_id']}-{stamp}-pre-owner-{digest[:12]}.json.gz"
    handle = tempfile.NamedTemporaryFile(prefix=".owner-backup-", suffix=".tmp", dir=backup_dir, delete=False)
    temporary = Path(handle.name)
    try:
        with handle, gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as archive:
            archive.write(payload)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination, digest


def main() -> int:
    args = _parser().parse_args()
    database = Path(args.database).resolve()
    if not database.is_file():
        raise RuntimeError(f"数据库不存在：{database}")

    connection = sqlite3.connect(str(database), timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        before = _read_row(connection, args.project_id)
        _validate(before, args.owner_id, args.expected_source_hash)
        report_hash_before = hashlib.sha256(str(before.get("report_json") or "").encode("utf-8")).hexdigest()
        if not args.commit:
            print(json.dumps({
                "ok": True,
                "mode": "dry-run",
                "project_id": args.project_id,
                "from_owner": before.get("owner_id"),
                "to_owner": args.owner_id,
                "status": before.get("status"),
                "source_hash": before.get("source_hash"),
                "report_sha256": report_hash_before,
            }, ensure_ascii=False))
            return 0

        backup_path, backup_sha256 = _backup(before, Path(args.backup_dir).resolve())
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE industry_research_projects
               SET owner_id = ?, updated_at = CURRENT_TIMESTAMP
             WHERE project_id = ?
               AND owner_id IS NULL
               AND status = 'completed'
               AND source_hash = ?
               AND report_json = ?
            """,
            (args.owner_id, args.project_id, args.expected_source_hash, before.get("report_json")),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError("CAS 归属失败：课题已变化，未写入")
        connection.commit()
        after = _read_row(connection, args.project_id)
        report_hash_after = hashlib.sha256(str(after.get("report_json") or "").encode("utf-8")).hexdigest()
        if after.get("owner_id") != args.owner_id or report_hash_after != report_hash_before:
            raise RuntimeError("归属后校验失败")
        print(json.dumps({
            "ok": True,
            "mode": "commit",
            "project_id": args.project_id,
            "owner_id": after.get("owner_id"),
            "status": after.get("status"),
            "source_hash": after.get("source_hash"),
            "report_sha256": report_hash_after,
            "report_unchanged": report_hash_after == report_hash_before,
            "backup_path": str(backup_path),
            "backup_sha256": backup_sha256,
        }, ensure_ascii=False))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
