from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import jev_backtest, jev_convertible_t as endpoint


def client_for(monkeypatch, authenticated=True):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/event")
    monkeypatch.setattr(jev_backtest, "_resolve_request_user", lambda _: (None, "admin" if authenticated else None))
    service = Mock()
    service.repo.list.return_value = []
    service.repo.get.return_value = None
    monkeypatch.setattr(endpoint.JevEventService, "instance", lambda: service)
    monkeypatch.setattr(endpoint, "event_capabilities", lambda: {"configured": False})
    monkeypatch.setattr(endpoint, "research_report", lambda: {"rounds": []})
    monkeypatch.setattr("src.services.jev_cb_data.availability", lambda _: {"status": "partial"})
    return TestClient(app), service


def test_all_event_reads_require_session(monkeypatch):
    client, service = client_for(monkeypatch, False)
    for route in ("capabilities", "availability", "research", "tasks", "tasks/id"):
        assert client.get("/event/" + route).status_code == 401
    assert client.post("/event/tasks/id/cancel").status_code == 401
    service.submit.assert_not_called()


def test_reads_no_charge_private_headers_and_owner_namespace(monkeypatch):
    client, service = client_for(monkeypatch)
    for route in ("capabilities", "availability", "research", "tasks"):
        response = client.get("/event/" + route)
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
    assert client.get("/event/tasks/id").status_code == 404
    service.repo.get.assert_called_with("event:admin", "id")
    service.submit.assert_not_called()


def test_invalid_sources_amounts_and_identity_cannot_reach_paid_worker(monkeypatch):
    client, service = client_for(monkeypatch)
    payload = {"request_id": str(uuid4()), "symbol": "NVDA", "start_date": "2025-09-16", "end_date": "2025-09-16"}
    for patch in ({"owner": "victim"}, {"dataset_id": "../../private"}, {"participation_rate": 2},
                  {"max_cost_usd": 1}, {"account_mode": "levered"}, {"factors": ["news"]}):
        assert client.post("/event/tasks", json={**payload, **patch}).status_code == 422
    service.submit.assert_not_called()
