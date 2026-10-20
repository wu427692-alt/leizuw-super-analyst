"""Collect approved users' public company facts without sharing list membership."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.watchlist_announcement_sync_worker import WatchlistAnnouncementSyncWorker
from src.storage import UserAccount, UserWatchlistItem, WatchlistAnnouncementSyncRecord


@pytest.fixture
def worker(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'announcement-users.db'}")
    for table in (UserAccount, UserWatchlistItem, WatchlistAnnouncementSyncRecord):
        table.__table__.create(engine)
    db = SimpleNamespace(get_session=lambda: Session(engine, expire_on_commit=False))
    with db.get_session() as session:
        for user_id, status in ((1, "approved"), (2, "approved"), (3, "pending"), (4, "disabled")):
            session.add(UserAccount(id=user_id, display_name=f"user-{user_id}", normalized_name=f"user-{user_id}",
                                    password_salt="fixture", password_hash="fixture", status=status))
        session.add_all([
            UserWatchlistItem(user_id=1, symbol="603306.SH"),
            UserWatchlistItem(user_id=1, symbol="300308.SZ"),
            UserWatchlistItem(user_id=2, symbol="300308.SZ"),
            UserWatchlistItem(user_id=2, symbol="300476.SZ"),
            UserWatchlistItem(user_id=2, symbol="113677.SH"),
            UserWatchlistItem(user_id=2, symbol="00700.HK"),
            UserWatchlistItem(user_id=3, symbol="600519.SH"),
            UserWatchlistItem(user_id=4, symbol="000001.SZ"),
        ])
        session.commit()
    monkeypatch.setattr("src.services.watchlist_announcement_sync_worker.get_config", lambda: SimpleNamespace(refresh_stock_list=lambda: None))
    service = SimpleNamespace(equity_watchlist=lambda: ["603306.SH", "603306.SH"],
                              _canonical_ts_code=lambda symbol: symbol,
                              _stock_name=lambda symbol: symbol)
    instance = WatchlistAnnouncementSyncWorker(repository=InvestmentMonitorRepository(db), service=service)
    yield instance
    engine.dispose()


def test_collects_approved_union_once_without_changing_memberships(worker):
    with worker.repository.db.get_session() as session:
        before = session.execute(select(UserWatchlistItem.user_id, UserWatchlistItem.symbol)).all()
    assert worker._watchlist_symbols() == ["603306.SH", "300308.SZ", "300476.SZ"]
    with worker.repository.db.get_session() as session:
        assert session.execute(select(UserWatchlistItem.user_id, UserWatchlistItem.symbol)).all() == before


def test_private_status_excludes_other_users_symbols_errors_and_counts(worker):
    for symbol in worker._watchlist_symbols():
        worker._ensure_state(symbol)
    worker.repository.update_announcement_sync_state("300476.SZ", status="retry", last_error="private-symbol-300476.SZ")
    worker._last_error = "private-symbol-300476.SZ"
    worker._last_result = {
        "status": "partial", "symbols": 3, "failed": 1, "succeeded": 1,
        "incremental": [{"symbol": "603306.SH", "status": "success"},
                        {"symbol": "300476.SZ", "status": "failed", "error": "private-symbol-300476.SZ"}],
        "backfill": [],
    }
    own = worker.status(symbols=["603306.SH"])
    assert own["last_error"] is None
    assert own["last_result"]["symbols"] == 1
    assert own["last_result"]["failed"] == 0
    assert own["status"] == "healthy"
    assert "300476" not in json.dumps(own)
    empty = worker.status(symbols=[])
    assert empty["symbols"] == [] and empty["last_result"]["symbols"] == 0


def test_two_year_target_is_durable_and_only_widens(worker):
    worker.history_days = 365
    first = worker._ensure_state("603306.SH")
    worker.history_days = 730
    second = worker._ensure_state("603306.SH")
    assert (date.fromisoformat(second["target_end"]) - date.fromisoformat(second["target_start"])).days == 730
    assert second["target_start"] < first["target_start"]
    assert second["status"] == "pending" and second["backfill_completed_at"] is None
    # A normal worker restart may have the old default; it must not shrink coverage.
    worker.history_days = 365
    assert worker._ensure_state("603306.SH")["target_start"] == second["target_start"]


def test_status_route_uses_logged_in_users_memberships_only(worker, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from api.v1.endpoints import investment_monitor
    monkeypatch.setattr(WatchlistAnnouncementSyncWorker, "get_instance", lambda: worker)
    worker.status = Mock(return_value={"status": "idle"})
    app = FastAPI()

    @app.middleware("http")
    async def fixture_user(request: Request, call_next):
        request.state.user_id = 1
        return await call_next(request)

    app.include_router(investment_monitor.router, prefix="/monitor")
    response = TestClient(app).get("/monitor/announcements/watchlist-sync/status")
    assert response.status_code == 200
    assert set(worker.status.call_args.kwargs["symbols"]) == {"603306.SH", "300308.SZ"}


@pytest.mark.parametrize("nested", [False, True])
def test_public_watchdog_health_does_not_leak_collection_union_or_modify_guard(nested):
    from api.v1.endpoints.investment_monitor import _public_watchdog_state
    internal = {"workers": {"cninfo_watchlist": {
        "status": "degraded", "running": True, "symbols": [{"symbol": "private-300476"}],
        "degraded_symbols": ["private-300476"], "last_error": "private-300476 failed",
        "last_result": {"backfill": [{"symbol": "private-300476"}]},
    }}}
    payload = {"last_result": internal} if nested else internal
    visible = _public_watchdog_state(payload)
    assert "private-300476" not in json.dumps(visible)
    assert "private-300476" in json.dumps(payload)
    state = (visible["last_result"] if nested else visible)["workers"]["cninfo_watchlist"]
    assert state["status"] == "degraded" and state["running"] is True
