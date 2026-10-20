#!/usr/bin/env python3
"""Plan, export and verify a stopped-writer migration snapshot using stdlib only.

This does not stop services, parse environment files, restore data, extract
archives, or certify deployment readiness. Run ``plan`` first on a disk with
enough free space. A final export requires all writers to stay stopped until
the command finishes. The resulting directory contains private business data
even when --include-secrets is omitted; transfer it over an encrypted channel.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import sys
import time


SCHEMA_VERSION = 1
MANIFEST_PATH = "metadata/manifest.json"
PUBLIC_TREES = {"data": "runtime/data", "reports": "runtime/reports",
                "strategies": "runtime/strategies"}
SECRET_TREES = {"cloud-config": "runtime/config", ".env": "runtime/.env",
                "tyc-config": "runtime/tyc", "longbridge_tokens": "runtime/longbridge_tokens"}
AUTH_SECRET_NAMES = {".admin_password_hash", ".session_secret", ".user_auth_secret",
                     ".llm_usage_hmac_secret"}
REQUIRED_DIRS = {"metadata", "runtime", "runtime/data", "runtime/logs",
                 "runtime/reports", "runtime/strategies", "runtime/config",
                 "runtime/tyc", "runtime/longbridge_tokens"}
EXCLUDED_NAMES = {"logs", "backups", "node_modules", ".git", "__pycache__"}
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
SQLITE_HEADER = b"SQLite format 3\x00"
AUDIT_WARNING = (
    "External storage coverage is unverified: environment files are never parsed; "
    "custom DATABASE_PATH, attachment/transcript/task paths, container-only volumes, "
    "and storage outside the selected trees require a separate mount/path audit."
)


class BundleError(RuntimeError):
    """A safety gate or bundle integrity check failed."""


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BundleError("Invalid relative manifest path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..", "") for part in value.split("/")):
        raise BundleError(f"Unsafe relative path: {value!r}")
    if ":" in path.parts[0]:
        raise BundleError(f"Unsafe relative path: {value!r}")
    return value


def _checked_path(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise BundleError(f"Symlink path is not allowed: {component}")
    return path.resolve()


def _source_and_destination(source: str | Path, destination: str | Path | None):
    source = _checked_path(source)
    if not source.is_dir():
        raise BundleError("Source must be an existing project directory")
    if destination is None:
        return source, None
    destination = _checked_path(destination)
    if source == destination or source in destination.parents or destination in source.parents:
        raise BundleError("Source and destination paths must not overlap")
    if destination.exists():
        raise BundleError("Destination must be entirely new; existing directories are never reused")
    if not destination.parent.is_dir():
        raise BundleError("Destination parent must already exist on the intended disk")
    return source, destination


def _signature(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    if not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode)):
        raise BundleError(f"Symlinks and special files are not allowed: {path}")
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _scan(source: Path, include_secrets: bool) -> dict:
    """Enumerate and stat only; notably, never read an .env during planning."""
    files, directories, signatures, omissions, missing, mounts = {}, set(REQUIRED_DIRS), {}, [], [], []
    sidecar_bytes = 0
    source_device = source.stat().st_dev

    def visit(path: Path, target: str, secret_tree: bool = False):
        nonlocal sidecar_bytes
        relative = path.relative_to(source).as_posix()
        _safe_relative(target)
        signature = _signature(path)
        signatures[relative] = signature
        if signature[0] != source_device and path.parent.stat().st_dev != signature[0]:
            mounts.append(relative)
        if path.is_dir():
            directories.add(target)
            for child in sorted(path.iterdir()):
                child_relative = child.relative_to(source).as_posix()
                if child.name in EXCLUDED_NAMES:
                    omissions.append({"source": child_relative, "reason": "excluded directory name"})
                    continue
                if not include_secrets and _secret_name(child.name):
                    omissions.append({"source": child_relative, "reason": "secrets not requested"})
                    continue
                visit(child, f"{target}/{child.name}", secret_tree)
            return
        if path.name.endswith(SIDECAR_SUFFIXES):
            sidecar_bytes += signature[3]
            omissions.append({"source": relative, "reason": "SQLite sidecar; never copied"})
            return
        files[target] = {"source": relative, "size": signature[3],
                         "sensitive": secret_tree or _secret_name(path.name)}

    for relative, target in PUBLIC_TREES.items():
        path = source / relative
        if path.exists() or path.is_symlink():
            visit(path, target)
        else:
            missing.append(relative)
    secrets = []
    for relative, target in SECRET_TREES.items():
        path = source / relative
        present = path.exists() or path.is_symlink()
        status = "included" if present and include_secrets else "excluded" if present else "missing"
        secrets.append({"source": relative, "target": target, "status": status})
        if present and include_secrets:
            visit(path, target, True)
    # cloud-config may exist without its actual persistent runtime configuration.
    persistent_env = source / "cloud-config/.env"
    secrets.append({"source": "cloud-config/.env", "target": "runtime/config/.env",
                    "status": "included" if include_secrets and persistent_env.is_file()
                    else "excluded" if persistent_env.exists() else "missing"})
    for name in sorted(AUTH_SECRET_NAMES):
        present = (source / "data" / name).exists()
        secrets.append({"source": f"data/{name}", "target": f"runtime/data/{name}",
                        "status": "included" if include_secrets and present
                        else "excluded" if present else "missing"})
    warnings = [AUDIT_WARNING, "No deployment readiness is implied; secrets, paths, ownership, "
                "application versions and a restored smoke test must be reviewed separately."]
    if missing:
        warnings.append("Missing standard trees: " + ", ".join(missing))
    if any(item["status"] != "included" for item in secrets):
        warnings.append("Sensitive configuration is excluded or missing; this bundle is not sufficient to go live.")
    if omissions:
        warnings.append("Excluded paths are recorded; review them for needed historical or custom data.")
    if mounts:
        warnings.append("Included mount boundaries require manual completeness review: " + ", ".join(mounts))
    return {"files": files, "directories": sorted(directories), "signatures": signatures,
            "omissions": omissions, "missing_trees": missing, "secrets": secrets,
            "warnings": warnings, "sidecar_bytes": sidecar_bytes}


def _secret_name(name: str) -> bool:
    return name == ".env" or name.startswith(".env.") or name in AUTH_SECRET_NAMES


def plan(source: str | Path, destination: str | Path | None = None,
         include_secrets: bool = False) -> dict:
    source, destination = _source_and_destination(source, destination)
    inventory = _scan(source, include_secrets)
    selected_bytes = sum(item["size"] for item in inventory["files"].values())
    # WAL pages may enlarge a database backup. Leave extra filesystem/headroom space.
    upper_estimate = selected_bytes + inventory["sidecar_bytes"]
    required = upper_estimate + max(64 * 1024 * 1024, upper_estimate // 5)
    free = shutil.disk_usage(destination.parent).free if destination else None
    return {"operation": "plan", "writes_performed": False, "file_count": len(inventory["files"]),
            "selected_bytes": selected_bytes, "sqlite_sidecar_bytes": inventory["sidecar_bytes"],
            "estimated_required_bytes": required, "destination_free_bytes": free,
            "estimate_scope": "Additional bundle space only; retain and separately budget the raw source "
            "staging snapshot. Offline verification is immutable and creates no WAL or full-size copy.",
            "space_sufficient_estimate": free >= required if free is not None else None,
            "include_secrets": include_secrets, "production_ready": False,
            **{key: inventory[key] for key in ("missing_trees", "secrets", "omissions", "warnings")}}


def _open_regular(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise BundleError(f"Not a regular file: {path}")
    return os.fdopen(descriptor, "rb")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_regular(path) as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sqlite(path: Path) -> bool:
    with _open_regular(path) as handle:
        header = handle.read(16)
    return header == SQLITE_HEADER or path.suffix.lower() in SQLITE_SUFFIXES


def _quick_check(path: Path):
    # Offline output only: immutable avoids creating WAL/SHM during verification.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("PRAGMA quick_check").fetchall()
        if rows != [("ok",)]:
            raise BundleError(f"SQLite quick_check failed: {path.name}")


def _backup_sqlite(source: Path, destination: Path):
    wal = Path(str(source) + "-wal")
    journal = Path(str(source) + "-journal")
    if journal.exists() and journal.stat().st_size:
        raise BundleError(f"Nonempty rollback journal requires separate recovery: {source.name}")
    has_wal = wal.exists() and wal.stat().st_size > 0
    if has_wal:
        # mode=ro alone can write/create shared memory. A genuinely read-only
        # filesystem is required when committed WAL frames still need reading.
        readonly_flag = getattr(os, "ST_RDONLY", 1)
        shm = Path(str(source) + "-shm")
        protected = all(path.exists() and not os.access(path, os.W_OK)
                        for path in (source, wal, shm, source.parent))
        if not os.statvfs(source).f_flag & readonly_flag and not protected:
            raise BundleError("Nonempty SQLite WAL requires a read-only source snapshot (including SHM); "
                              "otherwise cleanly close all database connections before export")
        uri = source.as_uri() + "?mode=ro"
    else:
        # Safe only for the explicitly stopped-writer, sidecar-free source.
        uri = source.as_uri() + "?mode=ro&immutable=1"
    deadline = time.monotonic() + 300

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise BundleError("SQLite backup timed out; verify all writers are stopped")

    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as reader:
        reader.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(destination)) as writer:
            source_version = reader.execute("PRAGMA data_version").fetchone()
            reader.backup(writer, pages=1024, progress=progress, sleep=0.05)
            # A standalone DELETE-mode database must not depend on output sidecars.
            writer.execute("PRAGMA journal_mode=DELETE")
            if reader.execute("PRAGMA data_version").fetchone() != source_version:
                raise BundleError("SQLite changed while exporting; final snapshot rejected")
    _quick_check(destination)


def export(source: str | Path, destination: str | Path, *, writers_stopped: bool = False,
           include_secrets: bool = False) -> dict:
    if not writers_stopped:
        raise BundleError("Final snapshot export requires explicit --writers-stopped")
    source, destination = _source_and_destination(source, destination)
    inventory = _scan(source, include_secrets)
    if "runtime/data/stock_analysis.db" not in inventory["files"]:
        raise BundleError("Required data/stock_analysis.db is missing")
    estimate = plan(source, destination, include_secrets)
    if not estimate["space_sufficient_estimate"]:
        raise BundleError("Insufficient destination space for the conservative export estimate")
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    for relative in inventory["directories"]:
        (destination / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
        (destination / relative).chmod(0o700)
    records = []
    for relative, item in sorted(inventory["files"].items()):
        original, target = source / item["source"], destination / relative
        if _signature(original) != inventory["signatures"][item["source"]]:
            raise BundleError("Source changed after planning; partial destination must not be used")
        # Reserve each file exclusively and privately before SQLite opens it.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        sqlite = _is_sqlite(original)
        if sqlite:
            _backup_sqlite(original, target)
        else:
            with _open_regular(original) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
        target.chmod(0o600)
        records.append({"path": relative, "source": item["source"], "size": target.stat().st_size,
                        "sha256": _hash(target), "sqlite": sqlite, "sensitive": item["sensitive"]})
    if _scan(source, include_secrets)["signatures"] != inventory["signatures"]:
        raise BundleError("Source changed during export; partial destination must not be used")
    manifest = {"schema_version": SCHEMA_VERSION, "kind": "final-snapshot",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "writers_stopped_acknowledged": True, "include_secrets": include_secrets,
                "production_ready": False, "external_storage_audit": "required",
                "directories": inventory["directories"], "files": records,
                **{key: inventory[key] for key in ("missing_trees", "secrets", "omissions", "warnings")}}
    with (destination / MANIFEST_PATH).open("x", encoding="utf-8") as handle:
        (destination / MANIFEST_PATH).chmod(0o600)
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return verify(destination)


def verify(bundle: str | Path) -> dict:
    bundle = _checked_path(bundle)
    if not bundle.is_dir():
        raise BundleError("Bundle must be an existing directory")
    actual_files, actual_dirs = set(), set()

    def walk(directory: Path):
        if stat.S_IMODE(directory.lstat().st_mode) != 0o700:
            raise BundleError(f"Directory permissions must be 0700: {directory.name}")
        for path in directory.iterdir():
            mode = path.lstat().st_mode
            relative = path.relative_to(bundle).as_posix()
            _safe_relative(relative)
            if stat.S_ISDIR(mode):
                actual_dirs.add(relative)
                walk(path)
            elif stat.S_ISREG(mode) and path.stat().st_nlink == 1:
                if stat.S_IMODE(mode) != 0o600:
                    raise BundleError(f"File permissions must be 0600: {relative}")
                actual_files.add(relative)
            else:
                raise BundleError(f"Symlinks, hardlinks and special files are forbidden: {relative}")

    walk(bundle)
    if MANIFEST_PATH not in actual_files:
        raise BundleError("Completed metadata/manifest.json is missing; incomplete bundle")
    try:
        with _open_regular(bundle / MANIFEST_PATH) as handle:
            manifest = json.load(handle)
    except (ValueError, UnicodeError) as error:
        raise BundleError("Invalid manifest JSON") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("Unsupported manifest schema")
    if (manifest.get("kind") != "final-snapshot"
            or manifest.get("writers_stopped_acknowledged") is not True
            or manifest.get("production_ready") is not False
            or manifest.get("external_storage_audit") != "required"):
        raise BundleError("Manifest is not a gated final snapshot")
    if not isinstance(manifest.get("files"), list) or not isinstance(manifest.get("directories"), list):
        raise BundleError("Invalid manifest inventory")
    declared_dirs = [_safe_relative(value) for value in manifest["directories"]]
    if len(set(declared_dirs)) != len(declared_dirs) or set(declared_dirs) != actual_dirs:
        raise BundleError("Directory inventory mismatch")
    declared_files, sqlite_count = set(), 0
    for record in manifest["files"]:
        if not isinstance(record, dict):
            raise BundleError("Invalid file record")
        relative = _safe_relative(record.get("path"))
        _safe_relative(record.get("source"))
        if relative == MANIFEST_PATH or relative in declared_files:
            raise BundleError("Duplicate or reserved manifest file path")
        declared_files.add(relative)
        if relative not in actual_files:
            raise BundleError(f"Declared file missing: {relative}")
        path = bundle / relative
        if type(record.get("size")) is not int or record["size"] < 0:
            raise BundleError("Invalid declared file size")
        digest = record.get("sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            raise BundleError("Invalid declared SHA256")
        if path.stat().st_size != record["size"] or _hash(path) != digest:
            raise BundleError(f"File size or SHA256 mismatch: {relative}")
        if type(record.get("sqlite")) is not bool or record["sqlite"] != _is_sqlite(path):
            raise BundleError(f"SQLite type declaration mismatch: {relative}")
        if type(record.get("sensitive")) is not bool:
            raise BundleError("Invalid sensitive file declaration")
        expected_sensitive = _secret_name(path.name) or any(
            relative == target or relative.startswith(target + "/")
            for target in SECRET_TREES.values())
        if record["sensitive"] != expected_sensitive:
            raise BundleError("Sensitive file declaration mismatch")
        if record["sensitive"] and manifest.get("include_secrets") is not True:
            raise BundleError("Sensitive files present without explicit inclusion declaration")
        if path.name.endswith(SIDECAR_SUFFIXES):
            raise BundleError("Standalone bundles may not contain SQLite sidecars")
        if record["sqlite"]:
            _quick_check(path)
            sqlite_count += 1
    if declared_files | {MANIFEST_PATH} != actual_files:
        raise BundleError("Undeclared files found in bundle")
    if "runtime/data/stock_analysis.db" not in declared_files:
        raise BundleError("Required stock_analysis.db missing from bundle")
    return {"operation": "verify", "integrity_ok": True, "production_ready": False,
            "file_count": len(declared_files), "sqlite_count": sqlite_count,
            "include_secrets": manifest.get("include_secrets"),
            "secrets": manifest.get("secrets"), "warnings": manifest.get("warnings")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "export"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True, type=Path, help="Project/source snapshot root")
        command.add_argument("--destination", required=name == "export", type=Path,
                             help="New bundle path with an existing parent on a spacious disk")
        command.add_argument("--include-secrets", action="store_true",
                             help="Include cloud-config, root .env, tyc-config, longbridge_tokens "
                             "and data authentication secrets")
        if name == "export":
            command.add_argument("--writers-stopped", action="store_true",
                                 help="Acknowledge every writer is stopped for the entire export")
    commands.add_parser("verify").add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.source, args.destination, args.include_secrets)
        elif args.command == "export":
            result = export(args.source, args.destination, writers_stopped=args.writers_stopped,
                            include_secrets=args.include_secrets)
        else:
            result = verify(args.bundle)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BundleError, OSError, sqlite3.Error) as error:
        print(f"Migration bundle refused: {error}. Any partial output must not be used.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
