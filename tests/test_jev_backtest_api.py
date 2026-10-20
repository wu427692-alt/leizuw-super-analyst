"""Authentication, private response, and paid POST boundaries."""
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import jev_backtest as endpoint


def app_client(monkeypatch, *, authenticated=True):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1/jev-backtest")
    monkeypatch.setattr(endpoint, "_resolve_request_user", lambda request: (None, "admin" if authenticated else None))
    service = Mock()
    service.repo.list.return_value = []
    service.repo.get.return_value = None
    service.cancel.return_value = None
    monkeypatch.setattr(endpoint.JevBacktestService, "instance", lambda: service)
    monkeypatch.setattr(endpoint, "capabilities", lambda: {"configured": False})
    return TestClient(app), service


def test_all_routes_require_real_session_even_if_global_auth_is_disabled(monkeypatch):
    client, service = app_client(monkeypatch, authenticated=False)
    for path in ("capabilities", "tasks", "tasks/unknown"):
        assert client.get("/api/v1/jev-backtest/" + path).status_code == 401
    assert client.post("/api/v1/jev-backtest/tasks/unknown/cancel").status_code == 401
    service.submit.assert_not_called()


def test_reads_never_create_paid_tasks_and_private_data_is_not_cached(monkeypatch):
    client, service = app_client(monkeypatch)
    for path in ("capabilities", "tasks"):
        response = client.get("/api/v1/jev-backtest/" + path)
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "Cookie" in response.headers["vary"]
    assert client.get("/api/v1/jev-backtest/tasks/other-owner").status_code == 404
    service.repo.get.assert_called_with("admin", "other-owner")
    service.submit.assert_not_called()


def test_invalid_conditions_rejected_before_paid_submission(monkeypatch):
    client, service = app_client(monkeypatch)
    payload = {"request_id": str(uuid4()), "symbol": "600000", "start_date": "2026-09-01", "end_date": "2026-09-02"}
    for invalid in ({"timeframe": "1d"}, {"max_cost_usd": 1}, {"position_pct": 10},
                    {"confidence_threshold": 2}, {"unknown": "ignored?"}):
        assert client.post("/api/v1/jev-backtest/tasks", json={**payload, **invalid}).status_code == 422
    service.submit.assert_not_called()


def test_only_post_submits_and_owner_is_server_resolved(monkeypatch):
    client, service = app_client(monkeypatch)
    service.submit.return_value = {"task_id": "test", "status": "queued"}
    response = client.post("/api/v1/jev-backtest/tasks", json={
        "request_id": str(uuid4()), "symbol": "600000", "start_date": "2026-09-01", "end_date": "2026-09-02"})
    assert response.status_code == 202
    args = service.submit.call_args.args
    assert args[0] == "admin"
    assert args[1]["timeframe"] == "1min"
    assert args[1]["max_cost_usd"] == .2
