#!/usr/bin/env python3
"""Exercise current GET routes through ASGI against an explicit read-only SQLite DB.

Run in a disposable process: this installs irreversible Python audit guards.
On macOS it first re-executes under the built-in sandbox-exec with all native
file writes and network operations denied; other platforms fail closed.
No socket listener, app lifespan, production authentication, schema initialization,
upstream request, model call or task recovery is permitted. Only summary metadata
is printed; response originals and task contents are never emitted.

Example (from the project root):
  .venv/bin/python -B scripts/readonly_http_acceptance.py \
      --database data/stock_analysis.db --query 中际旭创 --symbol 300308.SZ

The database may have a live WAL: reads include SQLite's committed WAL state, not
an immutable point-in-time backup. External writers are not stopped by this tool.
This is route/schema/service acceptance, NOT deployed HTTP or login acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import _thread
import ctypes
import errno
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date
import importlib
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
AUDIT_USER_ID = 2_147_483_647
NATIVE_PROFILE = "(version 1)(allow default)(deny file-write*)(deny network*)"
RESEARCH_EVENT_TYPES = {"research_report_pdf", "institution_forecast", "institution_survey", "broker_recommendation"}
RELATION_KINDS = {"company_direct", "industry_related", "other_company_mention", "unknown"}
RELATION_FIELDS = {"kind", "target_symbol", "subject_symbol", "subject_name", "match_field", "match_text", "has_subject_conflict"}
CAPITAL_DATE_FIELDS = ("chip_trade_date", "moneyflow_trade_date", "margin_trade_date", "northbound_trade_date")


class ReadOnlyViolation(BaseException):
    """Fail closed, even when application code catches ordinary Exceptions."""


class ReadOnlyGuard:
    def __init__(self, database: Path):
        self.database = database.resolve(strict=True)
        self.uri = self.database.as_uri() + "?mode=ro"
        self.denied: Counter[str] = Counter()
        self.sql_reads = 0
        self.connections = 0
        self.stage = "guard"
        self.outcomes = []
        self.http_checks = {}
        self._connect = sqlite3.connect
        self.library_roots = tuple({Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()})
        self.stock_indexes = {(ROOT / relative).resolve() for relative in (
            "data/cache/stocks.index.json", "apps/dsa-web/public/stocks.index.json", "static/stocks.index.json",
        )}

    def refuse(self, operation: str):
        self.denied[operation] += 1
        raise ReadOnlyViolation(operation)

    def verify_native_write_denied(self):
        # A read-only SQL connection can otherwise update WAL shared memory in
        # SQLite's native VFS, outside Python's file audit. Probe write-open
        # capability without O_CREAT/O_TRUNC and never write any bytes.
        if sys.platform != "darwin":
            self.refuse("native_sandbox_unavailable")
        native = ctypes.CDLL(None, use_errno=True)
        # File permissions alone are not proof of a native sandbox: a caller
        # could pass the internal child flag with an already read-only DB.
        # Check the current process policy without making a network request.
        native.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        native.sandbox_check.restype = ctypes.c_int
        if any(native.sandbox_check(os.getpid(), operation, 0) != 1
               for operation in (b"file-write-data", b"network-outbound")):
            self.refuse("native_sandbox_policy_missing")
        native.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
        native.open.restype = ctypes.c_int
        for path in (self.database, Path(str(self.database) + "-wal"), Path(str(self.database) + "-shm")):
            if not path.is_file():
                continue
            descriptor = native.open(os.fsencode(path), os.O_WRONLY)
            if descriptor >= 0:
                native.close(descriptor)
                self.refuse("native_write_guard_missing")
            if ctypes.get_errno() not in {errno.EPERM, errno.EACCES, errno.EROFS}:
                self.refuse("native_write_guard_unverified")

    def audit(self, event: str, args: tuple):
        if event == "open":
            name, mode, flags = args
            if (isinstance(mode, str) and any(char in mode for char in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            ):
                self.refuse("file_write")
            if isinstance(name, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(name)).resolve()
                if path.name.startswith(".env") or any(part in {
                    ".admin_password_hash", ".session_secret", ".user_auth_secret", ".tyc", ".longbridge",
                } for part in path.parts):
                    self.refuse("private_file_read")
                code = path.is_relative_to(ROOT) and path.suffix in {".py", ".pyc", ".so"}
                library = any(path.is_relative_to(base) for base in self.library_roots)
                stock_index = path in self.stock_indexes
                system = path in {Path("/dev/null"), Path("/System/Library/CoreServices/SystemVersion.plist")} or str(path).startswith((
                    "/usr/share/zoneinfo/", "/var/db/timezone/", "/private/var/db/timezone/",
                )) or path == Path("/etc/localtime").resolve()
                if not (code or library or stock_index or system or path == self.database):
                    self.refuse("file_read_outside_allowlist")
        elif event in {
            "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
            "os.link", "os.symlink", "os.truncate", "os.utime", "os.system",
            "os.fork", "os.forkpty", "os.posix_spawn", "os.exec", "subprocess.Popen",
            "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto",
        }:
            self.refuse(event)
        elif event == "sqlite3.connect" and str(args[0]) != self.uri:
            self.refuse("non_readonly_database")

    def authorize(self, action, arg1, arg2, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            self.sql_reads += 1
            return sqlite3.SQLITE_OK
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() not in {"load_extension", "writefile", "readfile"}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in {
            "table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list",
            "schema_version", "data_version", "database_list", "read_uncommitted", "query_only",
        } and (arg2 is None or str(arg1).lower() in {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list"}):
            return sqlite3.SQLITE_OK
        self.denied["sql_write_or_unsafe_pragma"] += 1
        return sqlite3.SQLITE_DENY

    def connect(self, *_args, **_kwargs):
        connection = self._connect(self.uri, uri=True, check_same_thread=False, timeout=5)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.set_authorizer(self.authorize)
        deadline = time.monotonic() + 45
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        self.connections += 1
        return connection

    def install(self):
        sys.dont_write_bytecode = True
        sys.addaudithook(self.audit)
        threading.Thread.start = lambda *_args, **_kwargs: self.refuse("thread_start")
        # Some libraries use the lower-level API instead of Thread.start.
        for name in ("start_new_thread", "start_new", "start_joinable_thread"):
            if hasattr(_thread, name):
                setattr(_thread, name, lambda *_args, **_kwargs: self.refuse("thread_start"))
        # urllib3 otherwise binds a probe socket merely while importing. No
        # network family is usable in this harness, including that IPv6 probe.
        socket.has_ipv6 = False
        socket.socket.connect = lambda *_args, **_kwargs: self.refuse("network_connect")
        socket.socket.connect_ex = lambda *_args, **_kwargs: self.refuse("network_connect")
        sqlite3.connect = lambda *_args, **_kwargs: self.refuse("unmanaged_database_connection")
        sqlite3.dbapi2.connect = sqlite3.connect

    def self_test(self):
        checks = {}
        probes = {
            "file_write": lambda: (self.database.parent / "readonly-audit-must-not-exist").write_text("blocked"),
            "private_file_read": lambda: (ROOT / ".env").read_text(),
            "thread_start": lambda: threading.Thread(target=lambda: None).start(),
            "low_level_thread_start": lambda: _thread.start_new_thread(lambda: None, ()),
            "network_connect": lambda: socket.create_connection(("127.0.0.1", 9), timeout=0.1),
            "process_start": lambda: subprocess.run([sys.executable, "-c", "pass"], check=True),
            "unmanaged_database": lambda: sqlite3.connect(str(self.database)),
        }
        for name, probe in probes.items():
            try:
                probe()
            except ReadOnlyViolation:
                checks[name] = True
            else:
                checks[name] = False
        with self.connect() as connection:
            checks["query_only"] = connection.execute("PRAGMA query_only").fetchone()[0] == 1
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            for name, sql in {
                "sql_write": "CREATE TABLE readonly_audit_must_not_exist (id INTEGER)",
                "disable_query_only": "PRAGMA query_only=OFF",
                "attach_database": "ATTACH DATABASE ':memory:' AS forbidden",
            }.items():
                try:
                    connection.execute(sql)
                except sqlite3.DatabaseError:
                    checks[name] = True
                else:
                    checks[name] = False
        self.denied.clear()  # Only deliberate self-test attempts are cleared.
        if not all(checks.values()):
            self.refuse("guard_self_test_failed")
        return checks


def prepare_routes(guard: ReadOnlyGuard):
    # Export libraries imported by services only need built-in MIME mappings;
    # do not inspect host-level web-server configuration during this audit.
    import mimetypes
    mimetypes.knownfiles = []
    mimetypes.init(files=[])
    # Some optional parsers call gettempdir at import. Avoid its write probe;
    # actual temporary-file creation remains blocked by the audit hook.
    tempfile.tempdir = "/tmp"
    # Configuration is a plain dataclass with no account keys. Never call its
    # environment/file loader or the application's create_app/lifespan.
    from src.config import Config
    Config._instance = Config(stock_index_remote_update_enabled=False)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import NullPool
    from src.storage import DatabaseManager

    engine = create_engine("sqlite://", creator=guard.connect, poolclass=NullPool)
    database = object.__new__(DatabaseManager)
    database._initialized = True
    database._db_url = guard.uri
    database._engine = engine
    database._SessionLocal = sessionmaker(bind=engine, autoflush=False)
    DatabaseManager.__init__ = lambda *_args, **_kwargs: guard.refuse("database_initialization")
    DatabaseManager.get_instance = classmethod(lambda _cls: database)
    Session.commit = lambda *_args, **_kwargs: guard.refuse("session_commit")

    # Request dispatch normally uses AnyIO's worker threads for sync endpoints.
    # Execute those same callables inline only in this isolated ASGI process.
    import anyio.to_thread
    async def inline(function, *args, **_kwargs):
        return function(*args)
    anyio.to_thread.run_sync = inline
    from fastapi import APIRouter, FastAPI
    from fastapi.routing import APIRoute

    # api.v1.__init__ eagerly imports every unrelated application router. Load
    # only the real endpoint modules below, using their normal package names so
    # their schema imports still work, without running those aggregator files.
    for name, relative in (("api.v1", "api/v1"), ("api.v1.endpoints", "api/v1/endpoints")):
        package = ModuleType(name)
        package.__path__ = [str(ROOT / relative)]
        sys.modules[name] = package

    @asynccontextmanager
    async def forbidden_lifespan(_app):
        guard.refuse("app_lifespan")
        yield

    app = FastAPI(lifespan=forbidden_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    modules = {}
    for module_name, prefix, paths in (
        ("essay_radar", "/essay-radar", {"/feed"}),
        ("investment_monitor", "/investment-monitor", {"/stock-workspace/{symbol}"}),
    ):
        guard.stage = "import_router:" + module_name
        module = importlib.import_module(f"api.v1.endpoints.{module_name}")
        modules[module_name] = module
        selected = APIRouter()
        selected.routes.extend(route for route in module.router.routes
                               if isinstance(route, APIRoute) and route.path in paths and route.methods == {"GET"})
        if len(selected.routes) != len(paths):
            guard.refuse("route_allowlist_drift")
        app.include_router(selected, prefix="/api/v1" + prefix)

    # Constructors that eagerly create upstream clients are replaced only at
    # the boundary. The selected endpoint, service and repository reads remain
    # their actual source implementations, not canned responses.
    class NoUpstream:
        def __getattr__(self, _name):
            guard.refuse("upstream_adapter")
    from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
    from src.services.investment_monitor_service import InvestmentMonitorService
    monitor = InvestmentMonitorService(
        repository=InvestmentMonitorRepository(database), initialize_sources=False,
        tushare=NoUpstream(), stock_service=NoUpstream(), cninfo=NoUpstream(),
        exchanges=NoUpstream(), guba=NoUpstream(),
    )
    modules["investment_monitor"].InvestmentMonitorService = lambda **_kwargs: monitor
    return app, engine


def walk_objects(value):
    """Inspect JSON structure without printing or parsing embedded source text."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_objects(child)


def valid_stock_relation(relation, symbol: str) -> bool:
    if not isinstance(relation, dict) or not set(relation) <= RELATION_FIELDS:
        return False
    kind = relation.get("kind")
    if kind not in RELATION_KINDS or relation.get("target_symbol") != symbol:
        return False
    if kind == "company_direct" and relation.get("subject_symbol") != symbol:
        return False
    if kind == "other_company_mention" and (
        not isinstance(relation.get("subject_symbol"), str) or not relation["subject_symbol"]
        or relation["subject_symbol"] == symbol
    ):
        return False
    if "has_subject_conflict" in relation and not isinstance(relation["has_subject_conflict"], bool):
        return False
    return all(relation.get(key) is None or isinstance(relation[key], str)
               for key in ("subject_symbol", "subject_name", "match_field", "match_text"))


def research_contract(stock: dict) -> dict:
    """Validate every compact occurrence, but count unique research event IDs."""
    objects = list(walk_objects(stock))
    events = [row for row in objects if row.get("event_type") in RESEARCH_EVENT_TYPES]
    unique = {row.get("id"): row for row in events}
    schema_ok = all(type(row.get("id")) is int and row["id"] > 0
                    and valid_stock_relation(row.get("stock_relation"), stock.get("symbol")) for row in events)
    compact_only = all(isinstance(row.get("metrics"), dict) and set(row["metrics"]) <= {"_evidence"} for row in events)
    no_raw = all("raw_payload" not in row for row in objects)
    counts = Counter(row.get("stock_relation", {}).get("kind") for row in unique.values()
                     if isinstance(row.get("stock_relation"), dict))
    return {
        "event_occurrences": len(events), "unique_events": len(unique),
        "relation_counts": {kind: counts[kind] for kind in sorted(RELATION_KINDS)},
        "enum_target_schema_ok": schema_ok, "compact_metrics_only": compact_only,
        "no_raw_payload": no_raw, "passed": schema_ok and compact_only and no_raw,
    }


def broker_sample_contract(sample, symbol: str, view: str) -> dict:
    count_keys = ("loaded_record_count", "input_record_count", "relation_excluded_count", "report_count",
                  "duplicate_record_count", "valid_report_count", "unknown_relation_count", "missing_period_report_count")
    sample = sample if isinstance(sample, dict) else {}
    counts_ok = all(type(sample.get(key)) is int and sample[key] >= 0 for key in count_keys)
    notes = sample.get("source_notes")
    notes = notes if isinstance(notes, list) else None
    notes_schema_ok = notes is not None and all(
        isinstance(note, dict) and set(note) <= {"event_id", "title", "event_at", "org_name", "stock_relation"}
        and type(note.get("event_id")) is int and note["event_id"] > 0
        and valid_stock_relation(note.get("stock_relation"), symbol) for note in notes
    )
    no_other_or_conflict = notes_schema_ok and all(
        note["stock_relation"]["kind"] != "other_company_mention"
        and note["stock_relation"].get("has_subject_conflict") is not True for note in notes
    )
    unique_ids = notes_schema_ok and len({note["event_id"] for note in notes}) == len(notes)
    equations = counts_ok and (
        sample["loaded_record_count"] == sample["input_record_count"] + sample["relation_excluded_count"]
        and sample["input_record_count"] == sample["report_count"] + sample["duplicate_record_count"]
        and all(sample[key] <= sample["report_count"]
                for key in ("valid_report_count", "unknown_relation_count", "missing_period_report_count"))
        and type(sample.get("sample_limit")) is int and sample["sample_limit"] > 0
        and sample["loaded_record_count"] <= sample["sample_limit"]
    )
    note_counts = counts_ok and notes_schema_ok and (
        len(notes) == sample["report_count"]
        and sum(note["stock_relation"]["kind"] != "company_direct" for note in notes) == sample["unknown_relation_count"]
    )
    if view == "overview":
        coverage_ok = counts_ok and sample.get("loaded") is False and all(sample[key] == 0 for key in count_keys)
        coverage_ok = coverage_ok and sample.get("total_record_count") is None and sample.get("truncated") is None
    else:
        coverage_ok = counts_ok and view == "full" and sample.get("loaded") is True
        coverage_ok = coverage_ok and type(sample.get("total_record_count")) is int
        coverage_ok = coverage_ok and sample["total_record_count"] >= sample["loaded_record_count"]
        coverage_ok = coverage_ok and sample.get("truncated") is (sample["total_record_count"] > sample["loaded_record_count"])
    return {
        "count_equations_ok": bool(equations), "coverage_state_ok": bool(coverage_ok),
        "source_notes_schema_ok": bool(notes_schema_ok), "source_notes_count": len(notes) if notes is not None else None,
        "source_note_counts_ok": bool(note_counts), "source_event_ids_unique": bool(unique_ids),
        "no_other_company_or_conflicting_sources": bool(no_other_or_conflict),
        "passed": bool(equations and coverage_ok and notes_schema_ok and unique_ids and note_counts and no_other_or_conflict),
    }


def normalized_capital_source_date(value):
    """Independent calendar-date oracle; never copy source text into a report."""
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if len(raw) == 8 and raw.isascii() and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def capital_contract(stock: dict, view: str, agent_context=None) -> dict:
    """Check returned capital dates and bindings, not the unexposed raw chip row."""
    capital = stock.get("capital")
    capital = capital if isinstance(capital, dict) else {}
    fields_present = all(key in capital for key in CAPITAL_DATE_FIELDS)
    dates_schema_ok = fields_present and all(
        capital[key] is None or (isinstance(capital[key], str)
                                and normalized_capital_source_date(capital[key]) == capital[key])
        for key in CAPITAL_DATE_FIELDS
    )
    nested_matches = {
        name: isinstance(capital.get(name), dict)
        and f"{name}_trade_date" in capital
        and capital[f"{name}_trade_date"] == normalized_capital_source_date(capital[name].get("trade_date"))
        for name in ("moneyflow", "margin", "northbound")
    }
    overview = stock.get("overview")
    data_as_of = overview.get("data_as_of") if isinstance(overview, dict) else None
    overview_binding = (
        isinstance(data_as_of, dict) and "capital" in data_as_of
        and data_as_of["capital"] == capital.get("chip_trade_date")
    ) if view == "overview" else None
    context_capital = agent_context.get("chip_distribution") if isinstance(agent_context, dict) else None
    context_binding = (
        isinstance(context_capital, dict)
        and all(key in context_capital and context_capital[key] == capital.get(key) for key in CAPITAL_DATE_FIELDS)
    ) if view == "full" else None
    return {
        "date_fields_present": fields_present, "normalized_dates_schema_ok": dates_schema_ok,
        "nested_source_date_matches": nested_matches,
        "overview_chip_binding_ok": overview_binding, "agent_context_date_binding_ok": context_binding,
        "chip_source_check": "raw_chip_trade_date_not_exposed_by_workspace_api",
        "passed": bool(dates_schema_ok and all(nested_matches.values())
                       and (overview_binding if view == "overview" else context_binding if view == "full" else False)),
    }


def overview_direct_event_ids(stock: dict) -> set[int]:
    return {row["id"] for section in stock.get("overview", {}).get("sections", []) if section.get("key") == "research"
            for row in section.get("items", []) if (row.get("stock_relation") or {}).get("kind") == "company_direct"}


def direct_retention_contract(direct_ids: set[int], full_stock: dict) -> dict:
    full_ids = {row.get("id") for row in full_stock.get("institution", {}).get("latest", [])}
    return {"overview_direct_count": len(direct_ids), "retained_count": len(direct_ids & full_ids),
            "missing_count": len(direct_ids - full_ids), "passed": direct_ids <= full_ids}


def summarize_response(path: str, response, seconds: float, reads: int):
    summary = {"path": path, "status": response.status_code, "seconds": round(seconds, 4), "sql_reads": reads}
    if response.status_code != 200:
        summary["schema_ok"] = False
        return summary
    payload = response.json()
    summary["keys"] = sorted(payload) if isinstance(payload, dict) else []
    if "/stock-workspace/" in path:
        stock = payload.get("stock", {})
        evidence = stock.get("evidence", {})
        consensus = stock.get("consensus", {})
        broker_sample = consensus.get("broker_sample")
        forecasts = consensus.get("forecasts", [])
        counts_complete = evidence.get("counts_complete")
        evidence_schema_ok = (
            isinstance(counts_complete, bool)
            and counts_complete == (payload.get("view") == "full")
            and all(type(evidence.get(key)) is int and evidence[key] >= 0
                    for key in ("event_count", "raw_event_count", "source_count", "original_link_count"))
        )
        relations = research_contract(stock)
        broker_check = broker_sample_contract(broker_sample, stock.get("symbol"), payload.get("view"))
        capital_check = capital_contract(stock, payload.get("view"), payload.get("agent_context"))
        broker_schema_ok = broker_check["passed"]
        forecast_schema_ok = isinstance(forecasts, list) and all(
            isinstance(row, dict) and (row.get("np_median") is None or type(row["np_median"]) in (int, float))
            for row in forecasts
        )
        summary.update({
            "schema_ok": isinstance(stock, dict) and bool(stock.get("symbol")) and payload.get("view") in {"overview", "full"}
                         and evidence_schema_ok and broker_schema_ok and forecast_schema_ok
                         and relations["passed"] and capital_check["passed"],
            "view": payload.get("view"), "symbol": stock.get("symbol"),
            "market_updated_at": stock.get("market", {}).get("updated_at"),
            "event_count": stock.get("evidence", {}).get("event_count") if payload.get("view") == "full" else None,
            "evidence_counts_complete": counts_complete,
            "evidence_schema_ok": evidence_schema_ok,
            "preview_counts": {section.get("key"): len(section.get("items", [])) for section in stock.get("overview", {}).get("sections", [])},
            "cache": payload.get("cache"),
            "broker_sample_present": isinstance(broker_sample, dict),
            "broker_sample_schema_ok": broker_schema_ok,
            "research_contract": relations, "broker_contract": broker_check,
            "capital_contract": capital_check,
            "broker_sample": {key: broker_sample.get(key) for key in (
                "loaded", "total_record_count", "loaded_record_count", "input_record_count", "relation_excluded_count",
                "report_count", "duplicate_record_count", "valid_report_count", "unknown_relation_count",
                "missing_period_report_count", "sample_limit", "truncated", "aggregation",
            )} if isinstance(broker_sample, dict) else None,
            "forecast_rows": len(forecasts),
            "forecast_np_numeric_count": sum(isinstance(row.get("np_median"), (int, float)) for row in forecasts),
            # Report what the API actually carries; do not invent an API unit
            # field from frontend display labels or silently convert its value.
            "forecast_unit_field_keys": sorted({key for row in forecasts for key in row if "unit" in key}),
        })
    else:
        summary.update({
            "schema_ok": isinstance(payload.get("items"), list) and isinstance(payload.get("total"), int),
            "total": payload.get("total"), "items_count": len(payload.get("items", [])),
            **{key: payload[key] for key in ("page", "page_size", "offset", "limit", "counts") if key in payload},
        })
        if path.endswith("/essay-radar/feed"):
            items = payload.get("items", [])
            ids = [str(item.get("topic_id") or "") for item in items]
            summary.update({
                "nonempty_original_count": sum(bool(str(item.get("note", {}).get("content") or "").strip()) for item in items),
                "unique_topic_ids": len(set(ids)),
                "topic_ids_sha256": hashlib.sha256(json.dumps(ids, ensure_ascii=False).encode()).hexdigest(),
            })
            summary["schema_ok"] = summary["schema_ok"] and all(ids) and len(set(ids)) == len(ids) and summary["nonempty_original_count"] == len(items)
    return summary


async def inspect_routes(guard: ReadOnlyGuard, query: str, symbols: list[str]):
    app, engine = prepare_routes(guard)
    import httpx
    from src.request_identity import reset_current_user_id, set_current_user_id
    token = set_current_user_id(AUDIT_USER_ID)
    outcomes = guard.outcomes
    direct_ids_by_symbol = {}
    requests = [
        ("/api/v1/essay-radar/feed", {"days": 0, "page": 1, "query": query, "query_scope": scope, "analysis_status": "essay", "page_size": 20})
        for scope in ("full", "title", "full")
    ]
    requests += [(f"/api/v1/investment-monitor/stock-workspace/{symbol}", {"days": 183, "view": view})
                 for symbol in symbols for view in ("overview", "overview", "full")]
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://readonly-asgi.invalid") as client:
            for path, params in requests:
                guard.stage = "request:" + path
                started, reads = time.monotonic(), guard.sql_reads
                try:
                    response = await client.get(path, params=params)
                    item = summarize_response(path, response, time.monotonic() - started, guard.sql_reads - reads)
                    if response.status_code == 200 and "/stock-workspace/" in path:
                        stock = response.json()["stock"]
                        if params["view"] == "overview":
                            direct_ids_by_symbol[stock["symbol"]] = overview_direct_event_ids(stock)
                        elif stock["symbol"] in direct_ids_by_symbol:
                            retained = direct_retention_contract(direct_ids_by_symbol[stock["symbol"]], stock)
                            item["overview_direct_retention"] = retained
                            item["schema_ok"] = item["schema_ok"] and retained["passed"]
                    item["request_params"] = params
                    outcomes.append(item)
                except Exception as exc:
                    outcomes.append({"path": path, "status": None, "exception_type": type(exc).__name__, "schema_ok": False})
            denied = await client.post("/api/v1/essay-radar/backfill-count", json={"count": 1})
            invalid_scope = await client.get("/api/v1/essay-radar/feed", params={"query_scope": "unsupported"})
            invalid_view = await client.get("/api/v1/investment-monitor/stock-workspace/300308.SZ", params={"view": "unsupported"})
            guard.http_checks.update({"write_route_absent": denied.status_code in {404, 405},
                                      "query_scope_validation": invalid_scope.status_code == 422,
                                      "workspace_view_validation": invalid_view.status_code == 422})
            if not all(guard.http_checks.values()):
                guard.refuse("write_route_exposed")
    finally:
        reset_current_user_id(token)
        engine.dispose()
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--query", default="中际旭创")
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--self-test-only", action="store_true")
    parser.add_argument("--native-readonly-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.database.is_file() or len(args.query) > 200:
        parser.error("Use an existing SQLite database and a query of at most 200 characters")
    if any(not symbol or len(symbol) > 24 or not all(char.isalnum() or char in ".-_" for char in symbol) for symbol in args.symbol):
        parser.error("Symbols must be nonempty alphanumeric market codes")
    # Drop inherited account/API configuration before importing application code.
    os.environ.clear()
    os.environ.update({"ENV_FILE": "/dev/null", "LITELLM_LOCAL_MODEL_COST_MAP": "True", "PYTHONDONTWRITEBYTECODE": "1",
                       "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    if not args.native_readonly_child:
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            parser.error("Native read-only enforcement requires macOS sandbox-exec; no unsafe fallback is provided")
        forwarded = list(argv) if argv is not None else sys.argv[1:]
        return subprocess.run([
            "/usr/bin/sandbox-exec", "-p", NATIVE_PROFILE, sys.executable, "-B", str(Path(__file__).resolve()),
            *forwarded, "--native-readonly-child",
        ], check=False).returncode
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(ROOT))
    guard = ReadOnlyGuard(args.database)
    report = {"kind": "isolated_asgi_readonly", "production_auth_verified": False,
              "request_identity": "synthetic_owner_not_production_login", "network_listener": False,
              "native_write_guard_verified": False,
              "unverified_task_channels": ["industry-research", "essay-quant", "audio-analysis", "audio-package", "data-acquisition"]}
    try:
        guard.verify_native_write_denied()
        report["native_write_guard_verified"] = True
        guard.install()
        report["guard_self_tests"] = guard.self_test()
        if not args.self_test_only:
            report["requests"] = guard.outcomes
            report["requests"] = asyncio.run(inspect_routes(guard, args.query, args.symbol or ["300308.SZ"]))
        report["blocked_operations_during_acceptance"] = dict(guard.denied)
        report["http_boundary_checks"] = guard.http_checks
        repeated = [row for row in guard.outcomes if row.get("request_params", {}).get("query_scope") == "full"]
        if len(repeated) == 2 and all(row.get("schema_ok") for row in repeated):
            report["repeated_full_search"] = {
                "same_total": repeated[0]["total"] == repeated[1]["total"],
                "same_ordered_topic_ids": repeated[0]["topic_ids_sha256"] == repeated[1]["topic_ids_sha256"],
            }
        report["passed"] = not guard.denied and all(item["schema_ok"] for item in report.get("requests", []))
        if "repeated_full_search" in report:
            report["passed"] = report["passed"] and all(report["repeated_full_search"].values())
    except BaseException as exc:
        frames = []
        trace = exc.__traceback__
        while trace is not None:
            frames.append(f"{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}")
            trace = trace.tb_next
        report.update({"passed": False, "failure_type": type(exc).__name__, "stage": guard.stage, "code_frames": frames[-20:],
                       "blocked_operations_during_acceptance": dict(guard.denied)})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
