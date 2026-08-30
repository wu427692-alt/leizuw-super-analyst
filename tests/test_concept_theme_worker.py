from datetime import date

from src.services.concept_theme_worker import ConceptThemeWorker
from src.storage import (
    ConceptMembershipRecord,
    ConceptMembershipSyncState,
    ConceptThemeRecord,
    DatabaseManager,
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
