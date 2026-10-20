"""Interrupted report collection resumes an atomic, frozen-range page cursor."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.services.research_report_library_service import ResearchReportLibraryService, _ReportSyncLeaseLost, _SOURCE_KEY
from src.storage import ResearchReportRecord, ResearchReportSyncState, utc_naive_now


@pytest.fixture
def report_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resume.db'}")
    ResearchReportRecord.__table__.create(engine)
    ResearchReportSyncState.__table__.create(engine)
    db = SimpleNamespace(get_session=lambda: Session(engine, expire_on_commit=False))
    services = []

    def create(financial=None):
        service = ResearchReportLibraryService(db_manager=db, financial=financial or SimpleNamespace(query=Mock()))
        services.append(service)
        return service

    yield create
    for service in services:
        service._executor.shutdown(wait=True, cancel_futures=True)
    engine.dispose()


def row(number, day="20240915"):
    return {"title": f"Report {number}", "trade_date": day, "url": f"https://example.test/{number}.pdf"}


def run(service, start, end):
    service.start_sync(start_date=start, end_date=end, force=True)
    assert service._future is not None
    service._future.result(timeout=10)
    return service.status()


def test_restart_skips_completed_windows_and_resumes_exact_next_page(report_store):
    start, end = date(2024, 9, 15), date(2024, 10, 12)
    calls = []

    def interrupted(**kwargs):
        params = kwargs["params"]
        calls.append(dict(params))
        if params["start_date"] == "20240915":
            return {"rows": [row("first")]}
        if params["offset"] == 0:
            return {"rows": [row(number, "20240929") for number in range(1000)]}
        raise TimeoutError("provider interrupted")

    first = report_store(SimpleNamespace(query=interrupted))
    failed = run(first, start, end)
    assert failed["status"] == "failed"
    assert failed["completed_windows"] == 1
    assert failed["cursor_offset"] == 1000
    assert failed["scanned_rows"] == failed["saved_rows"] == failed["total"] == 1001
    assert failed["progress"] < 100

    resumed_calls = []

    def resumed(**kwargs):
        resumed_calls.append(dict(kwargs["params"]))
        return {"rows": [row("last", "20240930")]}

    restarted = report_store(SimpleNamespace(query=resumed))
    final = run(restarted, start, end)
    assert resumed_calls == [{"start_date": "20240929", "end_date": "20241012", "limit": 1000, "offset": 1000}]
    assert final["status"] == "completed"
    assert final["completed_windows"] == 2 and final["cursor_offset"] == 0
    assert final["scanned_rows"] == final["saved_rows"] == final["total"] == 1002


def test_same_page_and_repeated_records_do_not_count_as_new_data(report_store):
    service = report_store()
    assert service._upsert([row(1), row(1)]) == 1
    assert service._upsert([row(1)]) == 0
    assert service.status()["total"] == 1
    changed = {**row(1), "abstr": "New information"}
    assert service._upsert([changed]) == 1
    assert service._upsert([changed]) == 0


def test_new_range_does_not_inherit_old_completed_windows(report_store):
    service = report_store(SimpleNamespace(query=Mock(return_value={"rows": []})))
    service._write_state(status="failed", start_date=date(2024, 9, 15), end_date=date(2024, 10, 12),
                         total_windows=2, completed_windows=1, cursor_offset=1000,
                         scanned_rows=1001, saved_rows=1001)
    final = run(service, date(2024, 10, 13), date(2024, 11, 9))
    assert [call.kwargs["params"]["offset"] for call in service.financial.query.call_args_list] == [0, 0]
    assert final["scanned_rows"] == 0 and final["saved_rows"] == 0


def test_expired_lease_cannot_write_page_or_advance_successor_cursor(report_store):
    service = report_store()
    old = utc_naive_now() - timedelta(hours=1)
    service._write_state(status="running", started_at=old, cursor_offset=1000, completed_windows=1)
    current = utc_naive_now()
    service._write_state(status="running", started_at=current, cursor_offset=2000)
    with pytest.raises(_ReportSyncLeaseLost):
        service._upsert([row(1)], _claim_started_at=old,
                        _checkpoint={"cursor_offset": 0, "completed_windows": 2})
    state = service.status()
    assert state["cursor_offset"] == 2000 and state["completed_windows"] == 1
    assert state["total"] == 0


@pytest.mark.parametrize("payload", [{"error": "upstream unavailable"}, {}, {"rows": None}])
def test_invalid_upstream_payload_never_completes_window(report_store, payload):
    service = report_store(SimpleNamespace(query=Mock(return_value=payload)))
    status = run(service, date(2024, 9, 15), date(2024, 9, 28))
    assert status["status"] == "failed"
    assert status["completed_windows"] == status["cursor_offset"] == 0
    assert status["progress"] < 100


def test_backfill_progress_and_insert_rollback_together(report_store):
    service = report_store()
    service._write_state(status="running", cursor_offset=1000, completed_windows=1)
    # An invalid state update fails after the ORM row was staged for insertion.
    with pytest.raises(Exception):
        service._upsert([row(1)], _checkpoint={"cursor_offset": None, "completed_windows": 2})
    with service.db.get_session() as session:
        assert session.execute(select(ResearchReportRecord)).scalars().all() == []
        state = session.get(ResearchReportSyncState, _SOURCE_KEY)
        assert state.cursor_offset == 1000 and state.completed_windows == 1
