from datetime import date
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import concept_themes as concept_api
from src.services.concept_theme_service import ConceptThemeService
from src.services.concept_theme_worker import ConceptThemeWorker
from src.storage import (
    ConceptSyncRunRecord,
    ConceptThemeRecord,
    DatabaseManager,
    StockDaily,
    utc_naive_now,
)


class _NoProviderIO:
    available = True

    def __init__(self) -> None:
        self.calls = []

    def query(self, api_name, *, params):
        self.calls.append((api_name, params))
        raise AssertionError("concept GET must not perform provider I/O")


def _build_client(tmp_path, monkeypatch):
    DatabaseManager.reset_instance()
    ConceptThemeService._market_date_value = None
    ConceptThemeService._market_date_checked_at = None
    ConceptThemeService._market_date_source = None
    ConceptThemeService._taxonomy_cache = {}
    ConceptThemeService._overview_cache = {}
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'concept-api.db'}")
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

    gateway = _NoProviderIO()
    service = ConceptThemeService(db=db, gateway=gateway)
    worker = ConceptThemeWorker()
    monkeypatch.setenv("CONCEPT_THEME_RUN_MODE", "external")
    monkeypatch.setattr(concept_api, "ConceptThemeService", lambda: service)
    monkeypatch.setattr(
        concept_api.ConceptThemeWorker, "get_instance", classmethod(lambda cls: worker),
    )
    app = FastAPI()
    app.include_router(concept_api.router, prefix="/concept-themes")
    return TestClient(app), db, gateway, theme_id


def _request_rows(db: DatabaseManager):
    with db.session_scope() as session:
        return [
            (str(row.run_type), str(row.status), str(row.source_stats_json))
            for row in session.query(ConceptSyncRunRecord).filter(
                ConceptSyncRunRecord.run_type.in_(("theme_refresh_request", "stock_refresh_request")),
            ).order_by(ConceptSyncRunRecord.id).all()
        ]


def test_get_endpoints_return_partial_data_and_persist_deduplicated_background_requests(
    tmp_path, monkeypatch,
) -> None:
    client, db, gateway, theme_id = _build_client(tmp_path, monkeypatch)

    no_refresh = client.get(
        f"/concept-themes/themes/{theme_id}", params={"refresh_if_empty": "false", "horizon_days": 60},
    )
    assert no_refresh.status_code == 200
    assert no_refresh.json()["refresh_needed"] is True
    assert no_refresh.json()["refresh_queued"] is False
    assert _request_rows(db) == []

    first = client.get(f"/concept-themes/themes/{theme_id}", params={"horizon_days": 60})
    repeated = client.get(f"/concept-themes/themes/{theme_id}", params={"horizon_days": 60})
    assert first.status_code == repeated.status_code == 200
    assert first.json()["stocks"] == []
    assert first.json()["refresh_needed"] is True
    assert first.json()["refresh_queued"] is True
    assert first.json()["refresh_request_status"] == "queued"
    assert repeated.json()["refresh_queued"] is True
    theme_rows = _request_rows(db)
    assert len(theme_rows) == 1
    assert theme_rows[0][:2] == ("theme_refresh_request", "queued")

    stock = client.get("/concept-themes/stocks/300308.SZ", params={"horizon_days": 120})
    assert stock.status_code == 200
    assert stock.json()["themes"] == []
    assert stock.json()["refresh_needed"] is True
    assert stock.json()["refresh_queued"] is True
    rows = _request_rows(db)
    assert len(rows) == 2
    stock_payload = json.loads(next(payload for run_type, _status, payload in rows
                                    if run_type == "stock_refresh_request"))
    assert stock_payload == {"horizon_days": 120, "ts_code": "300308.SZ"}
    assert gateway.calls == []

    DatabaseManager.reset_instance()


def test_queue_failure_keeps_partial_get_response_available(tmp_path, monkeypatch) -> None:
    client, db, gateway, theme_id = _build_client(tmp_path, monkeypatch)

    class _UnavailableQueue:
        def request_theme_refresh(self, theme_id, *, horizon_days):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(
        concept_api.ConceptThemeWorker, "get_instance",
        classmethod(lambda cls: _UnavailableQueue()),
    )
    response = client.get(f"/concept-themes/themes/{theme_id}", params={"horizon_days": 60})

    assert response.status_code == 200
    assert response.json()["stocks"] == []
    assert response.json()["refresh_needed"] is True
    assert response.json()["refresh_queued"] is False
    assert response.json()["refresh_request_status"] == "unavailable"
    assert _request_rows(db) == []
    assert gateway.calls == []

    DatabaseManager.reset_instance()
    ConceptThemeService._market_date_value = None
    ConceptThemeService._market_date_checked_at = None
    ConceptThemeService._market_date_source = None
