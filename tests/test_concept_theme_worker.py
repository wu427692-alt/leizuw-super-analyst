from datetime import date
from types import SimpleNamespace

from src.services.concept_theme_service import ConceptThemeService
from src.services.concept_theme_worker import ConceptThemeWorker
from src.storage import (
    ConceptMembershipRecord,
    ConceptMembershipSyncState,
    ConceptSyncRunRecord,
    ConceptThemeRecord,
    DatabaseManager,
    StockDaily,
    utc_naive_now,
)


class _ThemeService:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def refresh_theme(self, theme_id: int, *, calculate: bool = False):
        self.calls.append(theme_id)
        return {"received": theme_id, "saved": theme_id, "exposures": 0}


def test_progressive_membership_scan_resumes_from_database_after_restart(tmp_path, monkeypatch) -> None:
    DatabaseManager.reset_instance()
    ConceptThemeService._market_date_value = None
    ConceptThemeService._market_date_checked_at = None
    ConceptThemeService._market_date_source = None
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'concept-worker.db'}")
    now = utc_naive_now()
    with db.session_scope() as session:
        for index in range(3):
            session.add(ConceptThemeRecord(
                source="ths", source_code=f"88500{index}.TI", name=f"主题{index}",
                canonical_name=f"主题{index}", theme_type="theme", level=3,
                market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
            ))
    monkeypatch.setattr("src.services.concept_theme_worker.time.sleep", lambda _: None)

    first_service = _ThemeService()
    first_worker = ConceptThemeWorker()
    first_worker.batch_size = 2
    first = first_worker._refresh_progressive_batch(first_service)
    assert first_service.calls == [1, 2]
    assert first["attempted_themes"] == 2

    restarted_service = _ThemeService()
    restarted_worker = ConceptThemeWorker()
    restarted_worker.batch_size = 1
    restarted_worker._refresh_progressive_batch(restarted_service)
    assert restarted_service.calls == [3]
    with db.session_scope() as session:
        states = session.query(ConceptMembershipSyncState).all()
        assert len(states) == 3
        assert all(item.status == "completed" for item in states)

    DatabaseManager.reset_instance()


def test_legacy_memberships_are_bootstrapped_into_durable_scan_ledger(tmp_path) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'concept-worker-bootstrap.db'}")
    now = utc_naive_now()
    with db.session_scope() as session:
        theme = ConceptThemeRecord(
            source="ths", source_code="885009.TI", name="光模块", canonical_name="光模块",
            theme_type="theme", level=3, market_date=date(2026, 8, 28),
            first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add(theme)
        session.flush()
        session.add(ConceptMembershipRecord(
            theme_id=theme.id, source="ths", ts_code="300308.SZ", stock_name="中际旭创",
            active=True, first_seen_at=now, last_seen_at=now, updated_at=now,
        ))

    assert ConceptThemeWorker._bootstrap_membership_state() == 1
    assert ConceptThemeWorker._bootstrap_membership_state() == 0
    with db.session_scope() as session:
        state = session.query(ConceptMembershipSyncState).one()
        assert state.status == "completed"
        assert state.last_success_at is not None

    DatabaseManager.reset_instance()


def test_progressive_batch_allocates_capacity_across_sources(tmp_path) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'concept-worker-fairness.db'}")
    now = utc_naive_now()
    with db.session_scope() as session:
        for source in ("ths", "dc_board", "dc_theme", "kpl", "tdx", "sw"):
            for index in range(3):
                session.add(ConceptThemeRecord(
                    source=source, source_code=f"{source}-{index}", name=f"{source}主题{index}",
                    canonical_name=f"{source}主题{index}", theme_type="theme", level=3,
                    market_date=date(2026, 8, 28), first_seen_at=now, last_seen_at=now, updated_at=now,
                ))

    rows = ConceptThemeWorker._next_progressive_theme_ids(db, 12)
    assert len(rows) == 12
    assert {source for _, source in rows} == {"ths", "dc_board", "dc_theme", "kpl", "tdx", "sw"}
    assert all(sum(1 for _, value in rows if value == source) == 2 for source in {value for _, value in rows})

    DatabaseManager.reset_instance()


def test_new_watchlist_trigger_bypasses_periodic_defer(monkeypatch) -> None:
    worker = ConceptThemeWorker()
    worker._last_watchlist_refresh_at = 123.0
    monkeypatch.setattr(worker, "trigger", lambda: {"running": True, "refresh_requested": True})

    result = worker.trigger_watchlist_refresh("300308.SZ")

    assert worker._last_watchlist_refresh_at is None
    assert worker._priority_watchlist_codes == {"300308.SZ"}
    assert result["watchlist_refresh_requested"] is True
    assert result["refresh_requested"] is True


def test_external_mode_never_starts_heavy_worker_in_web_process(monkeypatch) -> None:
    worker = ConceptThemeWorker()
    monkeypatch.setenv("CONCEPT_THEME_RUN_MODE", "external")

    start_result = worker.start()
    refresh_result = worker.trigger_watchlist_refresh("300308.SZ")

    assert start_result["external"] is True
    assert start_result["deferred"] is True
    assert worker._thread is None
    assert worker._priority_watchlist_codes == set()
    assert refresh_result["watchlist_refresh_requested"] is True
    assert refresh_result["symbol"] == "300308.SZ"


def test_request_only_wakeup_processes_queue_without_starting_full_maintenance(monkeypatch) -> None:
    worker = ConceptThemeWorker()
    process_calls = []

    def process_requests(service):
        process_calls.append(service)
        worker._stop_event.set()
        return {"attempted": 1, "completed": 1, "failed": 0}

    monkeypatch.setattr(worker, "_process_refresh_requests", process_requests)
    worker._wake_event.set()
    worker._wait_for_next_cycle(SimpleNamespace())

    assert len(process_calls) == 1
    assert worker._maintenance_requested is False


def test_explicit_trigger_marks_wakeup_for_full_maintenance(monkeypatch) -> None:
    worker = ConceptThemeWorker()
    monkeypatch.setattr(worker, "start", lambda: {"running": True})

    worker.trigger()

    assert worker._maintenance_requested is True
    assert worker._wake_event.is_set()


def test_empty_theme_read_is_deduplicated_then_filled_by_durable_worker_request(tmp_path, monkeypatch) -> None:
    DatabaseManager.reset_instance()
    ConceptThemeService._market_date_value = None
    ConceptThemeService._market_date_checked_at = None
    ConceptThemeService._market_date_source = None
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'concept-worker-requests.db'}")
    now = utc_naive_now()
    with db.session_scope() as session:
        theme = ConceptThemeRecord(
            source="ths", source_code="885001.TI", name="CPO概念",
            canonical_name="CPO/共封装光学", theme_type="theme", level=3,
            market_date=date(2026, 9, 4), first_seen_at=now, last_seen_at=now, updated_at=now,
        )
        session.add(theme)
        session.add(StockDaily(code="000001", date=date(2026, 9, 4), close=10.0, data_source="test"))
        session.flush()
        theme_id = int(theme.id)

    calls = []

    def query(api_name, *, params):
        calls.append(api_name)
        if api_name == "trade_cal":
            return {"rows": [{"cal_date": "20260904"}]}
        if api_name == "ths_member":
            return {"rows": [{"con_code": "300308.SZ", "con_name": "中际旭创"}]}
        raise AssertionError(f"unexpected provider call: {api_name}")

    service = ConceptThemeService(db=db, gateway=SimpleNamespace(available=True, query=query))
    read = service.theme_detail(theme_id, refresh_if_empty=True)
    assert read["stocks"] == []
    assert read["refresh_needed"] is True
    assert calls == []

    monkeypatch.setenv("CONCEPT_THEME_RUN_MODE", "external")
    worker = ConceptThemeWorker()
    first = worker.request_theme_refresh(theme_id, horizon_days=60)
    duplicate = worker.request_theme_refresh(theme_id, horizon_days=60)
    assert first["queued"] is True
    assert duplicate["deduplicated"] is True
    assert duplicate["request_id"] == first["request_id"]

    processed = worker._process_refresh_requests(service)
    assert processed == {"attempted": 1, "completed": 1, "failed": 0}
    assert calls == ["trade_cal", "ths_member"]
    with db.session_scope() as session:
        requests = session.query(ConceptSyncRunRecord).filter(
            ConceptSyncRunRecord.run_type == "theme_refresh_request",
        ).all()
        memberships = session.query(ConceptMembershipRecord).all()
        request_statuses = [row.status for row in requests]
        membership_values = [(row.ts_code, row.stock_name) for row in memberships]
    assert request_statuses == ["completed"]
    assert membership_values == [("300308.SZ", "中际旭创")]
    assert service.sync_status()["latest_run"] is None

    cooldown = worker.request_theme_refresh(theme_id, horizon_days=60)
    assert cooldown["status"] == "cooldown"
    with db.session_scope() as session:
        assert session.query(ConceptSyncRunRecord).filter(
            ConceptSyncRunRecord.run_type == "theme_refresh_request",
        ).count() == 1

    DatabaseManager.reset_instance()
    ConceptThemeService._market_date_value = None
    ConceptThemeService._market_date_checked_at = None
    ConceptThemeService._market_date_source = None
