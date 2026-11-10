"""Durable export contract against private temporary files and real SQLite.

No application database, external model, provider or source attachment is used.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import io
import csv
import fcntl
import json
import os
import sqlite3
import zipfile

import pytest
from sqlalchemy import create_engine, insert, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from src.request_identity import reset_current_user_id, set_current_user_id
from src.repositories.essay_analysis_repo import EssayAnalysisRepository
from src.services.essay_export_service import EssayExportService
from src.storage import EssayAnalysisRecord, ResearchNote


@pytest.fixture(autouse=True)
def owner():
    token = set_current_user_id(17)
    try:
        yield
    finally:
        reset_current_user_id(token)


def item(number, *, text=None):
    return {"topic_id": str(number), "status": "completed", "summary": "分析摘要", "tags": ["电池", "储能"],
            "sentiment": "bullish", "note": {"title": f"宁德时代 {number}",
                "content": text or f"宁德时代原始全文 {number}", "group_name": "机构甲", "author_name": "研究员",
                "created_at": "2026-10-20T08:00:00Z"}}


class FixtureRepository:
    def __init__(self, total):
        self.total = total
        self.reads = []
        self.snapshot_calls = []

    def iter_export_snapshot(self, *, requested_limit=None, batch_size=250, **filters):
        self.snapshot_calls.append(filters)
        target = min(self.total, requested_limit) if requested_limit else self.total
        yield {"total": self.total, "target_total": target, "items": [], "done": False}
        for offset in range(0, target, batch_size):
            yield {"items": [(number, str(number)) for number in range(offset, min(target, offset + batch_size))],
                   "done": False}
        yield {"items": [], "done": True}

    def export_payloads(self, topic_ids):
        self.reads.append(list(topic_ids))
        for topic_id in reversed(topic_ids):
            yield item(int(topic_id))


def finish(service, task_id, maximum_steps=1000):
    for _ in range(maximum_steps):
        state = service.get(task_id)
        if state["status"] in {"completed", "no_results", "failed"}:
            return state
        assert service.run_once(), state
    pytest.fail("Temporary test job did not finish")


def read_package(service, task_id):
    with zipfile.ZipFile(service.download_file(task_id)) as archive:
        assert archive.testzip() is None
        return json.loads(archive.read("小作文.json")), json.loads(archive.read("检索与完整性.json"))


def test_unlimited_export_really_exceeds_old_ten_thousand_cap(tmp_path):
    repository = FixtureRepository(10003)
    service = EssayExportService(root=tmp_path / "jobs", repo=repository)
    task = service.create({"query": "宁德时代", "days": 2}, workspace="meeting")
    assert task["requested_limit"] is None and task["progress"] is None
    result = finish(service, task["task_id"])
    assert result["status"] == "completed", result
    assert result["target_total"] == result["matched_total"] == result["retrieved_count"] == 10003
    assert result["packaged_count"] == 10003 and result["progress"] == 100
    assert result["bytes_written"] == result["bytes_total"] > 0
    rows, receipt = read_package(service, task["task_id"])
    assert len(rows) == len({row["topic_id"] for row in rows}) == 10003
    assert rows[-1]["topic_id"] == "10002" and rows[-1]["content"].endswith("10002")
    assert rows[0]["tags"] == ["电池", "储能"]
    assert receipt["complete"] and receipt["library_complete"] and not receipt["user_limited"]
    assert all(len(batch) <= 250 for batch in repository.reads)
    assert [value for batch in repository.reads for value in batch] == [str(number) for number in range(10003)]


def test_explicit_limit_means_complete_request_not_incomplete_failure(tmp_path):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(237))
    task = service.create({"query": "宁德时代"}, requested_limit=20)
    result = finish(service, task["task_id"])
    rows, receipt = read_package(service, task["task_id"])
    assert result["status"] == "completed" and result["complete"] and result["request_complete"]
    assert result["matched_total"] == 237 and result["target_total"] == len(rows) == 20
    assert not result["library_complete"]
    assert receipt["user_limited"] and receipt["requested_limit"] == 20 and not receipt["library_complete"]


@pytest.mark.parametrize("limit", [0, -1, True, "20", 1.5])
def test_only_explicit_positive_integer_can_limit(tmp_path, limit):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(1))
    with pytest.raises(ValueError):
        service.create({"query": "宁德时代"}, requested_limit=limit)
    assert not list(tmp_path.rglob("task.sqlite3"))


def test_no_results_is_not_an_empty_success_zip(tmp_path):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(0))
    task = service.create({"query": "不存在"})
    state = finish(service, task["task_id"])
    assert state["status"] == "no_results" and state["matched_total"] == 0 and state["result"] is None
    assert state["progress"] == 100 and not list(tmp_path.rglob("*.zip"))
    with pytest.raises(FileNotFoundError):
        service.download_file(task["task_id"])


def test_progress_is_factual_and_unfinished_job_has_no_download(tmp_path):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(501))
    task = service.create({"query": "宁德时代"})
    assert service.run_once()  # Index snapshot only.
    state = service.get(task["task_id"])
    assert state["matched_total"] == 501 and state["indexed_count"] == 501
    assert state["retrieved_count"] == 0 and state["progress"] == 0 and state["result"] is None
    service.run_once()
    state = service.get(task["task_id"])
    assert state["retrieved_count"] == 250 and 24 < state["progress"] < 25
    assert state["checkpoint"]["collected_cursor"] == 250


def test_restart_resumes_collection_without_rereading_committed_members(tmp_path):
    repository = FixtureRepository(600)
    first = EssayExportService(root=tmp_path, repo=repository)
    task = first.create({"query": "宁德时代"})
    first.run_once()
    first.run_once()
    assert first.get(task["task_id"])["retrieved_count"] == 250
    second = EssayExportService(root=tmp_path, repo=repository)
    state = finish(second, task["task_id"])
    assert state["status"] == "completed" and len(repository.snapshot_calls) == 1
    assert [value for batch in repository.reads for value in batch] == [str(number) for number in range(600)]


def test_interrupted_source_snapshot_is_rebuilt_and_never_claims_old_membership(tmp_path):
    class InterruptedRepository(FixtureRepository):
        interrupt = True
        def iter_export_snapshot(self, **kwargs):
            for batch in super().iter_export_snapshot(**kwargs):
                yield batch
                if self.interrupt and batch["items"]:
                    self.interrupt = False
                    raise sqlite3.OperationalError("database is locked")

    now = [1800000000.0]
    repository = InterruptedRepository(501)
    service = EssayExportService(root=tmp_path, repo=repository, clock=lambda: now[0])
    task = service.create({"query": "宁德时代"})
    service.run_once()
    state = service.get(task["task_id"])
    assert state["status"] == "retrying" and state["indexed_count"] == 250
    assert not state["checkpoint"]["snapshot_complete"] and not repository.reads
    now[0] += 61
    state = finish(service, task["task_id"])
    assert state["checkpoint"]["generation"] == 2 and state["retrieved_count"] == 501
    assert len(read_package(service, task["task_id"])[0]) == 501


def test_restart_truncates_only_uncommitted_private_tails_and_keeps_source_checkpoint(tmp_path):
    repository = FixtureRepository(501)
    first = EssayExportService(root=tmp_path, repo=repository)
    task = first.create({"query": "宁德时代"})
    for _ in range(5):  # index, 3 collection batches, first preparation batch
        first.run_once()
    state = first.get(task["task_id"])
    assert state["packaged_count"] == 250
    for path in tmp_path.rglob("*.partial"):
        with path.open("ab") as handle:
            handle.write(b"uncommitted garbage simulating an interrupted write")
    second = EssayExportService(root=tmp_path, repo=repository)
    state = finish(second, task["task_id"])
    rows, _ = read_package(second, task["task_id"])
    assert state["status"] == "completed" and len(rows) == 501
    assert len(repository.reads) == 3 and rows[250]["topic_id"] == "250"


def test_package_retry_reuses_prepared_files_without_recollection(tmp_path, monkeypatch):
    repository = FixtureRepository(30)
    service = EssayExportService(root=tmp_path, repo=repository)
    task = service.create({"query": "宁德时代"})
    for _ in range(3):
        service.run_once()
    assert service.get(task["task_id"])["checkpoint"]["prepared_complete"]
    original = service._package
    monkeypatch.setattr(service, "_package", lambda *args: (_ for _ in ()).throw(ValueError("simulated ZIP failure")))
    service.run_once()
    assert service.get(task["task_id"])["status"] == "failed"
    same = service.resume(task["task_id"])
    assert same["task_id"] == task["task_id"]
    monkeypatch.setattr(service, "_package", original)
    assert finish(service, task["task_id"])["status"] == "completed"
    assert len(repository.reads) == len(repository.snapshot_calls) == 1


def test_missing_snapshot_item_is_failure_not_smaller_complete_zip(tmp_path):
    class MissingRepository(FixtureRepository):
        def export_payloads(self, topic_ids):
            return iter([item(0)])

    service = EssayExportService(root=tmp_path, repo=MissingRepository(2))
    task = service.create({"query": "宁德时代"})
    state = finish(service, task["task_id"])
    assert state["status"] == "failed" and state["resumable"] and state["retrieved_count"] == 0
    assert state["result"] is None and "1 篇原文" in state["error"]


def test_missing_cached_payload_keeps_resumable_failure_without_publishing_short_archive(tmp_path):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(3))
    task = service.create({"query": "宁德时代"})
    service.run_once()
    service.run_once()
    database = next(tmp_path.rglob("task.sqlite3"))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE members SET payload=NULL WHERE seq=2")
    service.run_once()
    state = service.get(task["task_id"])
    assert state["status"] == "failed" and state["resumable"]
    assert "缺少完整原文载荷" in state["error"] and state["packaged_count"] == 0
    assert not state["complete"] and state["result"] is None
    assert not list(tmp_path.rglob("*.zip"))


def test_relative_days_freeze_at_creation_and_explicit_dates_replace_cutoff(tmp_path):
    now = [1800000000.0]
    repository = FixtureRepository(1)
    service = EssayExportService(root=tmp_path, repo=repository, clock=lambda: now[0])
    task = service.create({"query": "宁德时代", "days": 2})
    cutoff = task["filters"]["cutoff"]
    now[0] += 86400 * 5
    service.run_once()
    assert repository.snapshot_calls[0]["cutoff"].isoformat() == cutoff
    explicit = service.create({"query": "宁德时代", "days": 1,
                               "start_date": "2026-10-01", "end_date": "2026-10-07"})
    assert "cutoff" not in explicit["filters"]


def test_owner_isolation_and_cross_workspace_listing(tmp_path):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(1))
    first = service.create({"query": "宁德时代"}, workspace="first")
    second = service.create({"query": "宁德时代"}, workspace="second")
    assert len(service.list_tasks()) == 2 and len(service.list_tasks("first")) == 1
    assert service.get(first["task_id"], workspace="second") is None
    assert service.resume(first["task_id"], workspace="second") is None
    finish(service, first["task_id"])
    token = set_current_user_id(18)
    try:
        assert service.list_tasks() == [] and service.get(first["task_id"]) is None
        assert service.resume(second["task_id"]) is None
        with pytest.raises(FileNotFoundError):
            service.download_file(first["task_id"])
    finally:
        reset_current_user_id(token)


def test_two_workers_cannot_claim_same_private_job(tmp_path):
    repository = FixtureRepository(1)
    first = EssayExportService(root=tmp_path, repo=repository)
    task = first.create({"query": "宁德时代"})
    job = next(tmp_path.rglob("task.sqlite3")).parent
    lock = os.open(job / "runner.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not EssayExportService(root=tmp_path, repo=repository).run_once()
        assert not repository.snapshot_calls
    finally:
        os.close(lock)
    assert first.run_once()
    assert first.get(task["task_id"])["checkpoint"]["snapshot_complete"]


def test_large_original_and_tags_survive_all_formats_and_files_are_private(tmp_path):
    original = "=完整原文，不能截断。\n" * 5000 + "原文最后一行"
    class LongRepository(FixtureRepository):
        def export_payloads(self, topic_ids):
            yield item(0, text=original)

    service = EssayExportService(root=tmp_path, repo=LongRepository(1))
    task = service.create({"query": "宁德时代"})
    finish(service, task["task_id"])
    with zipfile.ZipFile(service.download_file(task["task_id"])) as archive:
        assert all(member.compress_type == zipfile.ZIP_DEFLATED for member in archive.infolist())
        assert json.loads(archive.read("小作文.json"))[0]["content"] == original
        rows = list(csv.DictReader(io.StringIO(archive.read("小作文.csv").decode("utf-8-sig"))))
        assert rows[0]["content"] == "'" + original
        assert json.loads(rows[0]["tags"]) == ["电池", "储能"]
        assert original in archive.read("清单.md").decode("utf-8")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("filters", [{"cookie": "never persist"}, {"query_scope": "topic"}, {"days": True},
                                     {"subject": "宁德时代"},
                                     {"analysis_status": "unknown-would-have-matched-all"},
                                     {"start_date": "2026-10-20", "end_date": "2026-10-19"}])
def test_bad_or_secret_filters_never_enter_a_job(tmp_path, filters):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(1))
    with pytest.raises(ValueError):
        service.create(filters)
    assert not list(tmp_path.rglob("task.sqlite3"))


def test_real_repository_snapshot_is_readonly_and_membership_survives_source_update(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    ResearchNote.__table__.create(engine)
    EssayAnalysisRecord.__table__.create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.execute(insert(ResearchNote), [
            {"topic_id": str(number), "group_id": "fixture", "group_name": "机构甲", "title": "宁德时代",
             "content": f"原文{number}", "topic_type": "talk", "content_hash": str(number),
             "created_at": datetime(2026, 10, 20) + timedelta(seconds=number)} for number in range(3)])

    class Database:
        @contextmanager
        def get_session(self):
            with Session(engine) as session:
                yield session

    repository = EssayAnalysisRepository(db_manager=Database())
    snapshot = repository.iter_export_snapshot(query="宁德时代", analysis_status="essay", batch_size=1)
    assert next(snapshot)["total"] == 3
    with engine.begin() as connection:
        connection.execute(insert(ResearchNote), {"topic_id": "new", "group_id": "fixture", "group_name": "机构甲", "title": "宁德时代",
            "topic_type": "talk", "content_hash": "new", "created_at": datetime(2026, 10, 21)})
    membership = [value for batch in snapshot for value in batch["items"]]
    assert [topic for _, topic in membership] == ["2", "1", "0"]
    with engine.begin() as connection:
        connection.execute(update(ResearchNote).where(ResearchNote.topic_id == "2").values(title="标题已更名"))
    fetched = list(repository.export_payloads(["2", "1", "0"]))
    assert len(fetched) == 3 and any(row["note"]["title"] == "标题已更名" for row in fetched)
    with repository._export_read_session() as session:
        with pytest.raises(Exception, match="readonly|read-only"):
            session.connection().exec_driver_sql("DELETE FROM research_notes")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM research_notes").scalar() == 4
    engine.dispose()


def test_real_sqlite_ten_thousand_and_three_rows_export_without_truncation(tmp_path):
    """Exercise the actual ORM count, frozen IDs, raw-body reads and ZIP path."""
    engine = create_engine(f"sqlite:///{tmp_path / 'large-source.db'}")
    ResearchNote.__table__.create(engine)
    EssayAnalysisRecord.__table__.create(engine)
    with engine.begin() as connection:
        for offset in range(0, 10003, 250):
            connection.execute(insert(ResearchNote), [
                {"topic_id": str(number), "group_id": "fixture", "group_name": "机构甲", "title": "宁德时代",
                 "content": f"未分析的原始全文 {number}", "topic_type": "talk", "content_hash": str(number),
                 "created_at": datetime(2026, 10, 20) + timedelta(seconds=number)}
                for number in range(offset, min(10003, offset + 250))])

    class Database:
        @contextmanager
        def get_session(self):
            with Session(engine) as session:
                yield session

    service = EssayExportService(root=tmp_path / "jobs", repo=EssayAnalysisRepository(db_manager=Database()))
    task = service.create({"query": "宁德时代", "start_date": "2026-10-19", "end_date": "2026-10-21"})
    state = finish(service, task["task_id"])
    assert state["status"] == "completed" and state["matched_total"] == state["packaged_count"] == 10003
    rows, receipt = read_package(service, task["task_id"])
    assert len(rows) == len({row["topic_id"] for row in rows}) == 10003
    assert rows[0]["topic_id"] == "10002" and rows[-1]["topic_id"] == "0"
    assert rows[-1]["status"] == "not_queued" and rows[-1]["content"] == "未分析的原始全文 0"
    assert receipt["request_complete"] and receipt["library_complete"]
    engine.dispose()


def test_sqlalchemy_locked_source_automatically_retries_same_task(tmp_path):
    class LockedRepository(FixtureRepository):
        locked = True
        def export_payloads(self, topic_ids):
            if self.locked:
                self.locked = False
                raise SQLAlchemyOperationalError("SELECT", {}, sqlite3.OperationalError("database is locked"))
            yield from super().export_payloads(topic_ids)

    now = [1800000000.0]
    service = EssayExportService(root=tmp_path, repo=LockedRepository(3), clock=lambda: now[0])
    task = service.create({"query": "宁德时代"})
    service.run_once()
    service.run_once()
    state = service.get(task["task_id"])
    assert state["status"] == "retrying" and state["retrieved_count"] == 0
    assert not service.run_once()  # Backoff, not a hot retry loop.
    now[0] += 61
    state = finish(service, task["task_id"])
    assert state["status"] == "completed" and state["attempts"] == 1


def test_stale_lease_is_recovered_but_live_lease_is_not_stolen(tmp_path):
    now = [1800000000.0]
    repository = FixtureRepository(3)
    first = EssayExportService(root=tmp_path, repo=repository, clock=lambda: now[0])
    task = first.create({"query": "宁德时代"})
    job = next(tmp_path.rglob("task.sqlite3")).parent
    assert first._claim(job) is not None
    second = EssayExportService(root=tmp_path, repo=repository, clock=lambda: now[0])
    assert not second.run_once()
    assert second.resume(task["task_id"])["status"] == "queued"
    assert not second.run_once()
    now[0] += 181
    assert finish(second, task["task_id"])["status"] == "completed"


@pytest.mark.parametrize("message", ["no such table: research_notes", "no such column: raw_body",
                                      "malformed database schema - invalid expression"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_permanent_sql_errors_pause_resumably_without_fake_completion(tmp_path, message, wrapped):
    class BrokenRepository(FixtureRepository):
        def export_payloads(self, topic_ids):
            source = sqlite3.OperationalError(message)
            raise SQLAlchemyOperationalError("SELECT", {}, source) if wrapped else source

    service = EssayExportService(root=tmp_path, repo=BrokenRepository(3))
    task = service.create({"query": "宁德时代"})
    service.run_once()
    service.run_once()
    state = service.get(task["task_id"])
    assert state["status"] == "failed" and state["resumable"] and message in state["error"]
    assert not state["complete"] and state["result"] is None and state["retrieved_count"] == 0
    assert not service.run_once() and not list(tmp_path.rglob("*.zip"))


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR,
                                   sqlite3.SQLITE_FULL, sqlite3.SQLITE_CANTOPEN,
                                   sqlite3.SQLITE_IOERR | (2 << 8)])
def test_sqlite_resource_result_codes_are_auto_retried(tmp_path, code):
    class ResourceRepository(FixtureRepository):
        def export_payloads(self, topic_ids):
            source = sqlite3.OperationalError("resource fault without a text heuristic")
            source.sqlite_errorcode = code
            raise SQLAlchemyOperationalError("SELECT", {}, source)

    service = EssayExportService(root=tmp_path, repo=ResourceRepository(1))
    task = service.create({"query": "宁德时代"})
    service.run_once()
    service.run_once()
    state = service.get(task["task_id"])
    assert state["status"] == "retrying" and state["resumable"] and not state["complete"]
    assert state["retrieved_count"] == 0 and state["result"] is None


def test_worker_resume_does_not_require_a_browser_or_identity_context(tmp_path):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(3))
    task = service.create({"query": "宁德时代"})
    token = set_current_user_id(None)
    try:
        service.start_worker()
        service.start_worker()  # Idempotent: no second worker thread.
        service._worker.join(timeout=0.05)
    finally:
        service.stop_worker()
        reset_current_user_id(token)
    # It may have stopped at a committed intermediate stage; finish with a new
    # service as a process restart would, without changing the task identity.
    replacement = EssayExportService(root=tmp_path, repo=FixtureRepository(3))
    assert finish(replacement, task["task_id"])["status"] == "completed"


@pytest.mark.parametrize("invalid", ["not json", "[]", '{}'])
def test_corrupt_job_is_preserved_without_starving_healthy_jobs(tmp_path, invalid):
    service = EssayExportService(root=tmp_path, repo=FixtureRepository(1))
    damaged = service.create({"query": "坏任务"})
    database = next(tmp_path.rglob("task.sqlite3"))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE metadata SET value=? WHERE id=1", (invalid,))
    healthy = service.create({"query": "宁德时代"})
    assert finish(service, healthy["task_id"])["status"] == "completed"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM metadata WHERE id=1").fetchone()[0] == invalid
    assert damaged["task_id"] != healthy["task_id"]
