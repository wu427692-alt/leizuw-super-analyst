"""Offline, temporary-SQLite coverage of recovery and live ingestion boundaries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import re
import subprocess
import sys
import threading

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from api.v1.schemas.financial_data import ZsxqHistoryBackfillRequest
from src.config import Config
from src.repositories.research_note_repo import ResearchNoteRepository
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncService, ZsxqMcpSyncWorker
from src.storage import DatabaseManager, EssayAnalysisRecord, ResearchNote


GROUP = {"group_id": "1234", "name": "offline fixture"}
START = datetime(2024, 9, 15)
END = datetime(2026, 9, 15)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "checkpoint.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    result = ZsxqMcpSyncService(ResearchNoteRepository(DatabaseManager.get_instance()))
    result.history_request_interval = 0
    result.history_retry_attempts = 1
    # Tests never instantiate an MCP session or run any AI worker.
    yield result
    DatabaseManager.reset_instance()
    Config.reset_instance()


def topic(identifier, at, **extra):
    return {"topic_id": str(identifier), "title": "original fixture", "content": "公司调研正文",
            "create_time": at.isoformat() + "Z", "type": "talk", "files": [], "images": [], **extra}


class Pages:
    def __init__(self, topics):
        self.topics = topics
        self.calls = []

    async def fetch(self, client, name, arguments):
        assert name == "get_group_topics", "Existing records must not trigger detail requests"
        self.calls.append(dict(arguments))
        end = ZsxqMcpSyncService._date(arguments.get("end_time"))
        rows = sorted((row for row in self.topics if end is None or ZsxqMcpSyncService._date(row["create_time"]) < end),
                      key=lambda row: row["create_time"], reverse=True)
        current = rows[:30]
        return {"success": True, "topics_brief": current, "has_more": len(rows) > 30,
                "next_end_time": current[-1]["create_time"] if len(rows) > 30 else None}


def run_history(service, pages, limit=20):
    service._call_json = pages.fetch
    return asyncio.run(service._sync_group(object(), GROUP, None, history_cutoff=START,
                                          history_end=END, max_pages=limit, enqueue_analysis=False))


def count(service, model=ResearchNote):
    with service.repo.db.get_session() as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("value,expected", [
    ("2026-09-15T16:00:00.000000Z", "2026-09-16T00:00:00.000+0800"),
    ("2026-09-15T03:12:54.090001Z", "2026-09-15T11:12:54.091+0800"),
    ("2026-09-15T11:12:54.090+0800", "2026-09-15T11:12:54.090+0800"),
    ("2026-09-15T23:59:59.999999+0800", "2026-09-16T00:00:00.000+0800"),
])
def test_mcp_cursor_uses_accepted_format_without_truncating_upper_boundary(value, expected):
    assert ZsxqMcpSyncService._mcp_page_cursor(value) == expected
    difference = ZsxqMcpSyncService._date(expected) - ZsxqMcpSyncService._date(value)
    assert timedelta(0) <= difference < timedelta(milliseconds=1)


@pytest.mark.parametrize("channel", ["history", "incremental"])
def test_failed_utc_checkpoint_resumes_with_provider_cursor_and_no_reset(service, channel):
    # The real provider rejects the previous UTC microsecond Z wire format with
    # error 1001. Keep failed DB checkpoints unchanged until a valid page commits.
    end = END + timedelta(microseconds=1)
    checkpoint = service._prepare_checkpoint(GROUP, channel, START, end)
    checkpoint.update(status="failed", error="prior invalid end_time")
    service._save_checkpoint(GROUP, channel, checkpoint)
    rows = [topic("within-final-millisecond", END), topic("outside-frozen-end", END + timedelta(microseconds=500))]
    pages = Pages(rows)
    async def provider(client, name, args):
        if args.get("end_time"):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+0800", args["end_time"])
            return await pages.fetch(client, name, args)
        # Pending realtime has a separate latest-head refresh, not part of this
        # fixed range. No new head arrivals in this fixture.
        return {"success": True, "topics_brief": [], "has_more": False}
    service._call_json = provider
    options = {"history_cutoff": START, "history_end": end} if channel == "history" else {}
    result = asyncio.run(service._sync_group(object(), GROUP, None, enqueue_analysis=False, **options))
    assert result["status"] == "success" and result["pages_fetched"] == 1
    assert pages.calls[0]["end_time"] == "2026-09-15T08:00:00.001+0800"
    persisted = service.repo.list_sync_states()[0][channel + "_checkpoint"]
    assert persisted["start_at"] == checkpoint["start_at"] and persisted["end_at"] == checkpoint["end_at"]
    assert persisted["status"] == "completed" and persisted["error"] is None
    assert service.repo.existing_topic_ids(["within-final-millisecond", "outside-frozen-end"]) == {"within-final-millisecond"}
    assert count(service, EssayAnalysisRecord) == 0


def test_fixed_history_fills_middle_gap_and_skips_existing_without_changes(service):
    rows = [topic(i, START + timedelta(days=i + 1)) for i in range(70)]
    service.notes.import_topics([rows[0], rows[-1]], group_id=GROUP["group_id"], group_name=GROUP["name"], enqueue_analysis=False)
    rows[-1] = {**rows[-1], "title": "changed upstream title must not overwrite", "counts": {"likes_count": 99999}}
    calls = []
    original = service.notes.import_topics

    def capture(items, **kwargs):
        calls.extend(str(item["topic_id"]) for item in items)
        assert kwargs["enqueue_analysis"] is False
        return original(items, **kwargs)

    service.notes.import_topics = capture
    result = run_history(service, Pages(rows))
    assert result["history_complete"]
    assert result["created"] == 68 and result["updated"] == 0 and result["unchanged"] == 2
    assert count(service) == 70
    assert "0" not in calls and "69" not in calls
    assert service.notes.get_note("69")["title"] == "original fixture"
    assert count(service, EssayAnalysisRecord) == 0


def test_restart_resumes_committed_page_not_oldest_row(service):
    rows = [topic(i, START + timedelta(days=i + 1)) for i in range(95)]
    first = Pages(rows)
    result = run_history(service, first, limit=1)
    assert not result["history_complete"] and count(service) == 30
    checkpoint = service.repo.list_sync_states()[0]["history_checkpoint"]
    restarted = ZsxqMcpSyncService(service.repo)
    restarted.history_request_interval = 0
    second = Pages(rows)
    result = run_history(restarted, second)
    assert service._date(second.calls[0]["end_time"]) == service._date(checkpoint["next_end_time"])
    assert result["history_complete"] and count(service) == 95
    again = Pages(rows)
    assert run_history(restarted, again)["history_complete"] and not again.calls


def test_incremental_more_than_300_keeps_gap_and_does_not_queue_old_ai(service):
    base = datetime(2026, 7, 1)
    rows = [topic(i, base + timedelta(minutes=i + 1)) for i in range(650)]
    pages = Pages(rows)
    service._call_json = pages.fetch
    service.repo.update_sync_state(GROUP["group_id"], GROUP["name"], last_topic_at=base)
    for iteration, expected in enumerate((300, 600, 650)):
        # Simulate process restart and a DB latest cursor far ahead of the missing gap.
        current = ZsxqMcpSyncService(service.repo)
        current._call_json = pages.fetch
        result = asyncio.run(current._sync_group(object(), GROUP, base + timedelta(minutes=650)))
        assert result["status"] == "success"
        assert count(service) == expected
        state = service.repo.list_sync_states()[0]
        if iteration < 2:
            assert state["last_topic_at"] == base.isoformat() + "Z"
            assert state["incremental_checkpoint"]["status"] == "incomplete"
        else:
            assert state["last_topic_at"] == (base + timedelta(minutes=650)).isoformat() + "Z"
            assert state["incremental_checkpoint"]["status"] == "completed"
    assert len([call for call in pages.calls if "end_time" not in call]) == 2
    assert count(service, EssayAnalysisRecord) == 0


def test_failure_after_insert_replays_original_cursor_without_missing_data(service, monkeypatch):
    rows = [topic(i, START + timedelta(days=i + 1)) for i in range(45)]
    original = service.repo.update_sync_state
    fail_once = True

    def fail_checkpoint(group_id, group_name, **fields):
        nonlocal fail_once
        checkpoint = json.loads(fields.get("history_checkpoint_json", "{}"))
        if fail_once and checkpoint.get("pages_fetched") == 1:
            fail_once = False
            raise OSError("simulated durable state write failure")
        return original(group_id, group_name, **fields)

    monkeypatch.setattr(service.repo, "update_sync_state", fail_checkpoint)
    first = Pages(rows)
    assert run_history(service, first)["status"] == "failed"
    state = service.repo.list_sync_states()[0]["history_checkpoint"]
    assert state["pages_fetched"] == 0 and state["next_end_time"] == service._stamp(END)
    assert count(service) == 30
    next_run = Pages(rows)
    assert run_history(service, next_run)["history_complete"]
    assert next_run.calls[0]["end_time"] == first.calls[0]["end_time"]
    assert count(service) == 45


@pytest.mark.parametrize("payload", [
    {"success": False, "error": "secret URL"},
    {"success": True, "has_more": False},
    {"success": True, "topics_brief": [], "has_more": True, "next_end_time": None},
    {"success": True, "topics_brief": [topic("a", START)], "has_more": True, "next_end_time": END.isoformat() + "Z"},
])
def test_invalid_or_nonprogressing_pages_never_complete(service, payload):
    async def invalid(*args):
        return payload
    service._call_json = invalid
    result = asyncio.run(service._sync_group(object(), GROUP, None, history_cutoff=START, history_end=END))
    assert result["status"] == "failed" and not result["history_complete"]
    checkpoint = service.repo.list_sync_states()[0]["history_checkpoint"]
    assert checkpoint["pages_fetched"] == 0 and checkpoint["next_end_time"] == service._stamp(END)
    assert "secret" not in str(result)


def test_date_bounds_and_duplicates_at_inclusive_lower_boundary(service):
    # Equal start timestamps require the next page before we may declare coverage.
    responses = [
        {"topics_brief": [topic("upper", END), topic("within", START + timedelta(days=1)), topic("start1", START)],
         "has_more": True, "next_end_time": (START + timedelta(microseconds=1)).isoformat() + "Z"},
        {"topics_brief": [topic("start1", START), topic("start2", START), topic("before", START - timedelta(days=1))],
         "has_more": True, "next_end_time": (START - timedelta(days=1)).isoformat() + "Z"},
    ]
    async def fetch(*args):
        return {"success": True, **responses.pop(0)}
    service._call_json = fetch
    result = asyncio.run(service._sync_group(object(), GROUP, None, history_cutoff=START, history_end=END))
    assert result["history_complete"] and not responses
    assert count(service) == 3
    assert service.repo.existing_topic_ids(["upper", "before"]) == set()
    assert result["created"] == 3 and result["unchanged"] == 1


def test_history_does_not_regress_realtime_state(service):
    service.repo.update_sync_state(GROUP["group_id"], GROUP["name"], last_topic_at=END, last_status="success")
    assert run_history(service, Pages([topic("old", START)]))["history_complete"]
    state = service.repo.list_sync_states()[0]
    assert state["last_topic_at"] == END.isoformat() + "Z" and state["last_status"] == "success"


def test_live_head_refreshes_during_backlog_but_never_advances_its_watermark(service, monkeypatch):
    now = datetime(2026, 9, 15, 8)
    monkeypatch.setattr("src.services.zsxq_mcp_sync_service.utc_naive_now", lambda: now)
    base = datetime(2026, 7, 1)
    pages = Pages([topic(i, base + timedelta(minutes=i + 1)) for i in range(400)])
    service._call_json = pages.fetch
    service.repo.update_sync_state(GROUP["group_id"], GROUP["name"], last_topic_at=base)
    asyncio.run(service._sync_group(object(), GROUP, base, max_pages=1))
    now += timedelta(minutes=1)
    pages.topics.append(topic("arrived-during-backfill", now - timedelta(seconds=1)))
    enqueued = []
    real_import = service.notes.import_topics
    def capture(items, **kwargs):
        if kwargs["enqueue_analysis"]:
            enqueued.extend(item["topic_id"] for item in items)
        # Exercise actual persistence without loading optional model setup.
        kwargs["enqueue_analysis"] = False
        return real_import(items, **kwargs)
    monkeypatch.setattr(service.notes, "import_topics", capture)
    assert asyncio.run(service._sync_group(object(), GROUP, base, max_pages=1))["status"] == "success"
    assert service.repo.existing_topic_ids(["arrived-during-backfill"]) == {"arrived-during-backfill"}
    assert enqueued == ["arrived-during-backfill"]
    state = service.repo.list_sync_states()[0]
    assert state["last_topic_at"] == base.isoformat() + "Z"
    assert state["incremental_checkpoint"]["end_at"] == service._stamp(now - timedelta(minutes=1))


def test_attachment_only_media_never_auto_queue_even_if_new(service, monkeypatch):
    now = datetime(2026, 9, 15, 8)
    monkeypatch.setattr("src.services.zsxq_mcp_sync_service.utc_naive_now", lambda: now)
    rows = [topic("mp3", now - timedelta(seconds=5), title="交流.mp3", content="「文件」", files=[{"file_id": "a", "name": "交流.mp3"}]),
            topic("pdf", now - timedelta(seconds=4), title="研报.pdf", content="研报.pdf", files=[{"file_id": "b", "name": "研报.pdf"}]),
            topic("image", now - timedelta(seconds=3), content="", images=[{"image_id": "c"}])]
    original = service.notes.import_topics
    def capture(items, **kwargs):
        assert kwargs["enqueue_analysis"] is False
        return original(items, **kwargs)
    monkeypatch.setattr(service.notes, "import_topics", capture)
    pages = Pages(rows)
    service._call_json = pages.fetch
    result = asyncio.run(service._sync_group(object(), GROUP, now - timedelta(hours=1)))
    assert result["created"] == 3 and count(service, EssayAnalysisRecord) == 0


def test_existing_analysis_not_reset_by_repeated_history_page(service):
    row = topic("already-analyzed", START + timedelta(days=1))
    service.notes.import_topics([row], group_id=GROUP["group_id"], group_name=GROUP["name"], enqueue_analysis=False)
    with service.repo.db.get_session() as session:
        session.add(EssayAnalysisRecord(topic_id="already-analyzed", status="completed", model="fixture", prompt_version="fixture",
                                        input_hash="original", summary="preserved analysis"))
        session.commit()
    assert run_history(service, Pages([{**row, "content": "Changed upstream content"}]))["unchanged"] == 1
    with service.repo.db.get_session() as session:
        analysis = session.scalar(select(EssayAnalysisRecord))
        assert analysis.status == "completed" and analysis.summary == "preserved analysis" and analysis.input_hash == "original"


def test_api_inclusive_shanghai_dates_become_fixed_utc_range(monkeypatch):
    from api.v1.endpoints.financial_data import zsxq_history_backfill
    worker = ZsxqMcpSyncWorker()
    monkeypatch.setattr(ZsxqMcpSyncWorker, "get_instance", lambda: worker)
    monkeypatch.setattr(worker, "start_history_backfill", lambda **kwargs: kwargs)
    result = zsxq_history_backfill(ZsxqHistoryBackfillRequest(years=2, start_date="2024-09-15", end_date="2026-09-15"))
    assert result == {"days": 730, "start_at": datetime(2024, 9, 14, 16), "end_at": datetime(2026, 9, 15, 16)}


def test_realtime_not_blocked_by_slow_history_network(monkeypatch):
    entered, release, live_done = threading.Event(), threading.Event(), threading.Event()
    worker = ZsxqMcpSyncWorker()
    def delayed(self, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"totals": {}}
    monkeypatch.setattr(ZsxqMcpSyncService, "__init__", lambda self: None)
    monkeypatch.setattr(ZsxqMcpSyncService, "sync_history", delayed)
    monkeypatch.setattr(ZsxqMcpSyncService, "sync_once", lambda self: {"totals": {}, "status": "success"})
    history = threading.Thread(target=worker._run_history_backfill, args=(730, START, END))
    history.start()
    try:
        assert entered.wait(2)
        assert not worker._sync_lock.locked()
        live = threading.Thread(target=lambda: (worker.sync_now(), live_done.set()))
        live.start()
        assert live_done.wait(1), "History's network wait blocked realtime"
        live.join(1)
    finally:
        release.set()
        history.join(2)


def test_restart_exposes_persisted_history_and_resumes_same_range(service, monkeypatch):
    run_history(service, Pages([topic(i, START + timedelta(days=i + 1)) for i in range(40)]), limit=1)
    worker = ZsxqMcpSyncWorker()
    status = worker.status()["history_backfill"]
    assert status["pages_fetched"] == 1 and status["received"] == 30
    assert status["start_at"] == service._stamp(START) and status["phase"] == "incomplete"
    calls = []
    monkeypatch.setattr(worker, "start_history_backfill", lambda **kwargs: calls.append(kwargs))
    worker._resume_history_if_needed()
    assert calls == [{"days": 730, "start_at": START, "end_at": END}]


def test_stop_persists_cursor_without_fetch(service):
    called = []
    async def unexpected(*args):
        called.append(True)
        raise AssertionError("stopped")
    service._call_json = unexpected
    result = asyncio.run(service._sync_group(object(), GROUP, None, history_cutoff=START, history_end=END, should_stop=lambda: True))
    assert not result["history_complete"] and not called
    assert service.repo.list_sync_states()[0]["history_checkpoint"]["status"] == "paused"


def test_fixed_range_schema_preserves_legacy_and_rejects_partial_or_reversed():
    assert ZsxqHistoryBackfillRequest(years=2).start_date is None
    assert ZsxqHistoryBackfillRequest(start_date="2024-09-15", end_date="2026-09-15").start_date.isoformat() == "2024-09-15"
    for data in ({"start_date": "2024-09-15"}, {"start_date": "2026-09-15", "end_date": "2024-09-15"},
                 {"start_date": "2020-01-01", "end_date": "2026-09-15"}):
        with pytest.raises(ValueError):
            ZsxqHistoryBackfillRequest(**data)


def test_cross_process_history_lease_blocks_duplicate_runner_not_realtime(service):
    fcntl = pytest.importorskip("fcntl")
    import hashlib
    from pathlib import Path
    database_path = Path(service.repo.db._engine.url.database)
    with service._group_execution_lock(GROUP["group_id"], "history") as acquired:
        assert acquired
        path = database_path.parent / ".zsxq-sync-locks" / (hashlib.sha256(f"{database_path.name}:{GROUP['group_id']}:history".encode()).hexdigest() + ".lock")
        script = "import fcntl,sys\nf=open(sys.argv[1],'a+b')\ntry:\n fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:\n sys.exit(9)\n"
        process = subprocess.run([sys.executable, "-c", script, str(path)], timeout=5, capture_output=True)
        assert process.returncode == 9
        blocked = asyncio.run(service._sync_group(object(), GROUP, None, history_cutoff=START, history_end=END))
        assert blocked["status"] == "already_running" and not blocked["history_complete"]
        with service._group_execution_lock(GROUP["group_id"], "incremental") as live_acquired:
            assert live_acquired
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)


def test_history_write_requires_admin_even_for_approved_user(service, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.middlewares.auth import AuthMiddleware
    from api.v1.endpoints.financial_data import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/financial-data")
    @app.get("/api/v1/approved-user-probe")
    def approved_probe(request: Request):
        return {"user_id": request.state.user_id}
    app.add_middleware(AuthMiddleware)
    monkeypatch.setattr("api.middlewares.auth.is_auth_enabled", lambda: True)
    monkeypatch.setattr("api.middlewares.auth.user_access_enabled", lambda: True)
    monkeypatch.setattr("api.middlewares.auth.verify_session", lambda value: value == "valid-admin")
    approved = SimpleNamespace(id=7, display_name="test-approved")
    seen_sessions = []
    def account_for_session(cookie):
        seen_sessions.append(cookie)
        return approved if cookie == "valid-user" else None
    fake_accounts = SimpleNamespace(account_for_session=account_for_session)
    monkeypatch.setattr("api.middlewares.auth.UserAccountService", lambda: fake_accounts)
    calls = []
    worker = ZsxqMcpSyncWorker()
    monkeypatch.setattr(worker, "start_history_backfill", lambda **kwargs: (calls.append(kwargs) or {"status": "started"}))
    monkeypatch.setattr(ZsxqMcpSyncWorker, "get_instance", lambda: worker)

    with TestClient(app) as client:
        user_headers = {"Cookie": "dsa_user_session=valid-user"}
        assert client.get("/api/v1/approved-user-probe", headers=user_headers).json() == {"user_id": 7}, seen_sessions
        for path in ("/api/v1/financial-data/zsxq/history/backfill", "/api/v1/financial-data/zsxq/history/backfill/"):
            response = client.post(path, json={"years": 2}, headers=user_headers)
            assert response.status_code == 401
            assert response.json()["message"] == "Administrator login required"
        assert not calls
        assert client.post("/api/v1/financial-data/zsxq/history/backfill", json={"years": 2}).status_code == 401
        assert not calls
        assert client.post("/api/v1/financial-data/zsxq/history/backfill", json={"years": 2}, headers={"Cookie": "dsa_session=valid-admin"}).status_code == 200
        assert calls == [{"days": 730}]
