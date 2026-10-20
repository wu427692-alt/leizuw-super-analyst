"""Report synchronization owns a shared SQLite lease, not a web-process PID."""

from concurrent.futures import Future
from datetime import date, timedelta
import multiprocessing
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.services.research_report_library_service as library_module
from src.services.research_report_library_service import ResearchReportLibraryService
from src.storage import ResearchReportRecord, ResearchReportSyncState, utc_naive_now


def _service(path, financial=None):
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 5})
    db = SimpleNamespace(get_session=lambda: Session(engine, expire_on_commit=False))
    service = ResearchReportLibraryService(db_manager=db, financial=financial or SimpleNamespace(query=Mock()))
    return service, engine


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "report-lease.db"
    service, engine = _service(path)
    ResearchReportRecord.__table__.create(engine)
    ResearchReportSyncState.__table__.create(engine)
    yield service, path
    service._executor.shutdown(wait=True, cancel_futures=True)
    engine.dispose()


def _state(service):
    with service.db.get_session() as session:
        row = session.get(ResearchReportSyncState, library_module._SOURCE_KEY)
        return {column.name: getattr(row, column.name) for column in ResearchReportSyncState.__table__.columns}


def _raw_report(title="fresh report"):
    return {"trade_date": date.today().strftime("%Y%m%d"), "title": title,
            "url": "https://example.com/report.pdf", "inst_csname": "test broker"}


@pytest.mark.parametrize("status", ["queued", "running"])
def test_constructing_and_reading_another_process_does_not_interrupt_sync(library, status):
    service, path = library
    token = service._claim_sync(only_if_due=False, status=status, progress=42)
    before = _state(service)
    reader, engine = _service(path)
    try:
        assert reader.status()["status"] == status
        assert reader.status()["progress"] == 42
        assert _state(reader) == before
        assert before["started_at"] == token
    finally:
        reader._executor.shutdown(wait=True)
        engine.dispose()


def test_status_http_endpoint_never_starts_sync(monkeypatch):
    from api.v1.endpoints.data_acquisition import research_report_status

    service = SimpleNamespace(status=Mock(return_value={"status": "running"}), ensure_background_sync=Mock())
    monkeypatch.setattr(ResearchReportLibraryService, "get_instance", Mock(return_value=service))
    assert research_report_status() == {"status": "running"}
    service.status.assert_called_once_with()
    service.ensure_background_sync.assert_not_called()


def _claim_in_process(path, barrier, results):
    service, engine = _service(path)
    try:
        barrier.wait(timeout=15)
        claim = service._claim_sync(only_if_due=False, status="queued")
        results.put({"claimed": claim is not None})
    except Exception as exc:
        results.put({"error": type(exc).__name__})
    finally:
        service._executor.shutdown(wait=True)
        engine.dispose()


@pytest.mark.parametrize("initial_row", [False, True])
def test_only_one_real_process_claims_the_shared_sqlite_row(library, initial_row):
    service, path = library
    if initial_row:
        service._write_state(status="completed", completed_at=utc_naive_now() - timedelta(hours=7))
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_claim_in_process, args=(str(path), barrier, results)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        responses = [results.get(timeout=30) for _ in processes]
        assert responses.count({"claimed": True}) == 1
        assert responses.count({"claimed": False}) == 1
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert service.status()["status"] == "queued"
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


def test_force_does_not_overlap_another_process_and_completion_cadence_is_rechecked(library):
    service, path = library
    service._claim_sync(only_if_due=False, status="running")
    contender, engine = _service(path)
    try:
        contender._executor.submit = Mock()
        assert contender.start_sync(force=True)["status"] == "running"
        contender._executor.submit.assert_not_called()
        # A caller may have observed an old status before another worker
        # completed; the claim itself rechecks the six-hour cadence.
        service._write_state(status="completed", completed_at=utc_naive_now())
        assert contender.start_sync(only_if_due=True)["status"] == "completed"
        contender._executor.submit.assert_not_called()
    finally:
        contender._executor.shutdown(wait=True)
        engine.dispose()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_background_reclaims_expired_task_with_its_original_scope(library, status):
    service, _ = library
    start, end = date.today() - timedelta(days=12), date.today() - timedelta(days=3)
    old_token = service._claim_sync(only_if_due=False, status=status, start_date=start, end_date=end)
    with service.db.get_session() as session:
        row = session.get(ResearchReportSyncState, library_module._SOURCE_KEY)
        row.updated_at = utc_naive_now() - timedelta(minutes=16)
        session.commit()
    service._executor.submit = Mock(return_value=Future())

    result = service.ensure_background_sync()

    assert result["status"] == "queued"
    assert (result["start_date"], result["end_date"]) == (start.isoformat(), end.isoformat())
    assert _state(service)["started_at"] != old_token
    service._executor.submit.assert_called_once()
    assert service._executor.submit.call_args.args[1:3] == (start, end)


def test_replaced_collector_cannot_write_progress_or_report_rows(library):
    service, _ = library
    old = service._claim_sync(only_if_due=False, status="running")
    with service.db.get_session() as session:
        row = session.get(ResearchReportSyncState, library_module._SOURCE_KEY)
        row.updated_at = utc_naive_now() - timedelta(minutes=16)
        session.commit()
    new = service._claim_sync(only_if_due=False, status="queued")
    assert new and new != old
    assert service._write_state(_claim_started_at=old, status="failed") is False
    with pytest.raises(library_module._ReportSyncLeaseLost):
        service._upsert([_raw_report("stale writer")], _claim_started_at=old)
    assert service.status()["total"] == 0
    assert service.status()["status"] == "queued"
    assert service._upsert([_raw_report("new writer")], _claim_started_at=new) == 1
    assert service.status()["total"] == 1


def test_slow_upstream_renews_lease_and_result_is_saved_once(library, monkeypatch):
    service, path = library
    entered, release = threading.Event(), threading.Event()

    def query(**_kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return {"rows": [_raw_report()]}

    service.financial = SimpleNamespace(query=Mock(side_effect=query))
    monkeypatch.setattr(library_module, "_SYNC_HEARTBEAT_SECONDS", 0.01)
    contender, engine = _service(path)
    try:
        service.start_sync(start_date=date.today(), end_date=date.today())
        assert entered.wait(timeout=5)
        before = _state(service)
        # Observe the real background heartbeat; no HTTP request renews it.
        deadline = time.monotonic() + 3
        while _state(service)["updated_at"] <= before["updated_at"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _state(service)["updated_at"] > before["updated_at"]
        contender._executor.submit = Mock()
        assert contender.ensure_background_sync()["status"] == "running"
        contender.start_sync(force=True)
        contender._executor.submit.assert_not_called()
        release.set()
        service._future.result(timeout=5)
        result = service.status()
        assert result["status"] == "completed" and result["total"] == 1
        assert result["saved_rows"] == 1
        assert service.financial.query.call_count == 1
    finally:
        release.set()
        contender._executor.shutdown(wait=True)
        engine.dispose()


def test_upstream_failure_finishes_owned_state_without_touching_saved_reports(library):
    service, _ = library
    service._upsert([_raw_report("existing")])
    service.financial = SimpleNamespace(query=Mock(side_effect=RuntimeError("offline provider unavailable")))
    service.start_sync(start_date=date.today(), end_date=date.today())
    service._future.result(timeout=5)
    result = service.status()
    assert result["status"] == "failed" and result["completed_at"]
    assert result["total"] == 1
    assert service._claim_sync(only_if_due=True, status="queued") is None
    with service.db.get_session() as session:
        assert session.execute(select(ResearchReportRecord.title)).scalar_one() == "existing"


def test_heartbeat_start_failure_is_durable_instead_of_stuck_queued(library, monkeypatch):
    service, _ = library
    token = service._claim_sync(only_if_due=False, status="queued")
    thread = SimpleNamespace(start=Mock(side_effect=RuntimeError("cannot start new thread")), ident=None, join=Mock())
    monkeypatch.setattr(library_module, "threading", SimpleNamespace(Event=threading.Event, Thread=lambda **_kwargs: thread))

    service._run_sync(date.today(), date.today(), [(date.today(), date.today())], claim_started_at=token)

    assert service.status()["status"] == "failed"
    assert service.status()["completed_at"]
    thread.join.assert_not_called()
    service.financial.query.assert_not_called()
