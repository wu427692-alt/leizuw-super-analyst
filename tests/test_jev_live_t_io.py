"""Live adapter units, model privacy and owner boundary; no external calls."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_provider.jev_live_snapshot import parse_sina_snapshots, BoundedSinaLiveSnapshot
from src.services.jev_live_t_features import build_live_state
from src.services.jev_live_t_store import LiveTStore


def raw_sina(date="2026-09-21", status="00"):
    return ('var hq_str_sh603306="Synthetic,83.55,83.52,84.50,85.69,81.10,84.53,84.54,'
            '7307666,612364681,100,84.53,100,84.49,200,84.42,200,84.41,400,84.39,'
            f'100,84.54,300,84.55,300,84.56,700,84.60,100,84.63,{date},10:35:52,{status},";')


def test_sina_keeps_exact_shares_amount_book_and_actual_clock():
    q = parse_sina_snapshots(raw_sina(), "2026-09-21T02:35:56+00:00", ["sh603306"])["sh603306"]
    assert q["cumulative_volume"] == 7307666
    assert q["bid_volume"] == q["ask_volume"] == 100
    assert q["cumulative_amount"] == 612364681
    assert q["source_time"] == "2026-09-21T10:35:52+08:00"
    assert q["trading_status"] == "trading"


@pytest.mark.parametrize("text", [raw_sina(date="2026-09-18"), raw_sina(status="01")])
def test_old_session_or_unknown_provider_state_is_not_tradable(text):
    q = parse_sina_snapshots(text, "2026-09-21T02:35:56+00:00", ["sh603306"])["sh603306"]
    assert q["trading_status"] == "halted"


def test_wrong_volume_units_and_missing_symbol_fail_closed():
    with pytest.raises(ValueError, match="units"):
        parse_sina_snapshots(raw_sina().replace("7307666", "73076"), "2026-09-21T02:35:56+00:00", ["sh603306"])
    with pytest.raises(ValueError, match="missing"):
        parse_sina_snapshots(raw_sina(), "2026-09-21T02:35:56+00:00", ["sh603306", "sh000001"])


def test_network_wall_clock_timeout_is_bounded(monkeypatch):
    import subprocess
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 6
        raise subprocess.TimeoutExpired(args[0], 6)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="wall_clock"):
        BoundedSinaLiveSnapshot("sh603306").poll()


def test_live_features_share_event_normalization_without_account_identity():
    start = datetime.fromisoformat("2026-09-21T10:00:00+08:00")
    events = [{"time": (start + timedelta(seconds=i * 3)).isoformat(), "last_price": 84 + i*.01,
               "trade_volume": 1000, "bid_price": 83.99 + i*.01, "ask_price": 84.01 + i*.01,
               "bid_volume": 200, "ask_volume": 100, "volume_kind": "incremental", "quantity_unit": "shares",
               "event_type": "quote", "trading_status": "trading", "quote_age_ms": 3000} for i in range(20)]
    state = build_live_state(events, [])
    assert state["instrument_type"] == "a_share"
    assert state["normalized_events"][0]["price"] == 1
    assert state["window_seconds"] == 57
    assert state["factors"]["market"]["status"] == "missing"
    for private in ("cash", "603306", "8000000", "2026-09-21"):
        assert private not in str(state)


def record():
    return {"run_id": str(uuid4()), "status": "completed", "created_at": "2026-09-21T03:00:00Z",
            "updated_at": "2026-09-21T03:05:00Z", "config": {}, "account": {"cash": 4000000}}


def test_store_is_atomic_private_and_rejects_malformed_or_symlink(tmp_path):
    store = LiveTStore(tmp_path)
    run = record()
    store.save("admin", run)
    assert (tmp_path / (run["run_id"] + ".json")).stat().st_mode & 0o777 == 0o600
    assert store.get("admin", run["run_id"]) == run
    assert store.get("user:1", run["run_id"]) is None
    assert store.list("user:1") == []
    path = tmp_path / (str(uuid4()) + ".json")
    path.symlink_to(tmp_path / (run["run_id"] + ".json"))
    assert store.get("admin", path.stem) is None
    path.unlink(); path.write_text("[]")
    assert store.get("admin", path.stem) is None
    assert store.get("admin", "../../private") is None


def test_readonly_api_requires_owner_and_never_exposes_another_account(tmp_path, monkeypatch):
    from api.v1.endpoints import jev_backtest, jev_live_t
    monkeypatch.setenv("JEV_LIVE_T_DIR", str(tmp_path))
    run = record(); LiveTStore().save("admin", run)
    app = FastAPI(); app.include_router(jev_live_t.router, prefix="/api/v1/jev-live-t")
    client = TestClient(app)
    monkeypatch.setattr(jev_backtest, "_resolve_request_user", lambda request: (None, None))
    assert client.get("/api/v1/jev-live-t/status").status_code == 401
    monkeypatch.setattr(jev_backtest, "_resolve_request_user", lambda request: (SimpleNamespace(id=1,status="approved"), "session"))
    assert client.get("/api/v1/jev-live-t/status").json()["current_run"] is None
    assert client.get("/api/v1/jev-live-t/runs/" + run["run_id"]).status_code == 404
    monkeypatch.setattr(jev_backtest, "_resolve_request_user", lambda request: (None, "admin"))
    response = client.get("/api/v1/jev-live-t/status")
    assert response.json()["current_run"]["account"]["cash"] == 4000000
    assert "no-store" in response.headers["cache-control"]
    assert client.post("/api/v1/jev-live-t/runs").status_code == 405


def test_missing_heartbeat_is_projected_as_paused_without_mutating_saved_run(tmp_path):
    from api.v1.endpoints.jev_live_t import observed_run
    run = record()
    run.update(status="running", quote={"stale": False})
    store = LiveTStore(tmp_path)
    store.save("admin", run)
    now = datetime.fromisoformat("2026-09-21T03:05:16+00:00")
    view = observed_run(run, now)
    assert view["status"] == "paused" and view["quote"]["stale"] is True
    assert store.get("admin", run["run_id"])["status"] == run["status"] == "running"
    assert observed_run(run, now - timedelta(seconds=2))["status"] == "running"
    run["status"] = "completed"
    assert observed_run(run, now)["status"] == "completed"
