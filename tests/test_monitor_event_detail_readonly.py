"""Saved event detail stays read-only and never loads an unused raw blob."""

from contextlib import contextmanager
from datetime import datetime
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import Session

from api.v1.endpoints import investment_monitor as endpoints
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.investment_monitor_service import InvestmentMonitorService
from src.storage import DatabaseManager, MonitoringEventRecord, MonitoringSourceRecord


@pytest.fixture()
def saved_event(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'event-detail.db'}")
    for model in (MonitoringSourceRecord, MonitoringEventRecord):
        model.__table__.create(engine)
    metrics = {
        "ts_code": "300308.SZ", "name": "中际旭创", "abstr": "数据源研究摘要" * 1000,
        "report_type": "个股研报", "inst_csname": "测试证券", "author": "研究员",
        "_evidence": {"evidence_level": "licensed", "origin_apis": ["research_report"]},
    }
    summary = "已保存摘录，不等于报告全文。" * 500
    with Session(engine) as session:
        session.add(MonitoringSourceRecord(
            source_key="tushare.research_pdf", name="用户保留的来源名称", adapter_type="api",
            provider="tushare", category="research", poll_interval_seconds=777,
            config_json='{"user_config":"preserve"}',
        ))
        row = MonitoringEventRecord(
            source_key="tushare.research_pdf", source_name="测试证券", source_type="api",
            external_id="saved-report", event_type="research_report_pdf", perspective="institution",
            title="已保存研究资料", summary=summary, symbol_codes="300308.SZ", url=None,
            tags_json='["研报"]', actors_json='["研究员"]',
            metrics_json=json.dumps(metrics, ensure_ascii=False),
            raw_payload=json.dumps({"unused_original_blob": "x" * 1024 * 1024}),
            event_at=datetime(2025, 9, 7, 16), ingested_at=datetime(2026, 9, 11, 8),
        )
        session.add(row)
        session.commit()
        identifier = row.id
        expected = InvestmentMonitorRepository._event_dict(row)

    class TemporaryDatabase:
        @contextmanager
        def get_session(self):
            with Session(engine, autoflush=False) as session:
                yield session

    monkeypatch.setattr(DatabaseManager, "get_instance", lambda: pytest.fail("Application DB must not initialize"))
    repo = InvestmentMonitorRepository(db_manager=TemporaryDatabase())
    observed = {"sql": [], "commits": 0, "ensure_calls": 0, "constructors": []}
    sqlalchemy_event.listen(engine, "before_cursor_execute",
                            lambda _c, _cu, sql, _p, _co, _e: observed["sql"].append(sql.lower()))

    def committed(_connection):
        observed["commits"] += 1

    sqlalchemy_event.listen(engine, "commit", committed)
    original_ensure = repo.ensure_sources

    def ensure_sources(definitions):
        observed["ensure_calls"] += 1
        return original_ensure(definitions)  # Exercise the real risk; do not suppress its SQL/commit.

    monkeypatch.setattr(repo, "ensure_sources", ensure_sources)

    def local_service(**kwargs):
        observed["constructors"].append(kwargs)
        # Only infrastructure is injected. The real service constructor, event
        # lookup, serializer, and endpoint error handling still run unchanged.
        unused = SimpleNamespace()
        return InvestmentMonitorService(
            repository=repo, tushare=unused, stock_service=unused, cninfo=unused,
            announcement_artifacts=unused, guba=unused, exchanges=unused, **kwargs,
        )

    monkeypatch.setattr(endpoints, "InvestmentMonitorService", local_service)
    app = FastAPI()
    app.include_router(endpoints.router, prefix="/api/v1/investment-monitor")
    try:
        with TestClient(app) as client:
            yield repo, client, identifier, expected, observed
    finally:
        engine.dispose()


def assert_read_only_projection(observed, *, selects=1):
    assert observed["ensure_calls"] == 0
    assert observed["commits"] == 0
    assert len(observed["sql"]) == selects
    assert all(sql.lstrip().startswith("select") for sql in observed["sql"])
    assert all("raw_payload" not in sql and "monitoring_sources" not in sql for sql in observed["sql"])


def test_repository_detail_keeps_the_existing_payload_without_fetching_raw(saved_event):
    repo, _client, identifier, expected, observed = saved_event
    result = repo.get_event(identifier)
    assert result == expected
    assert len(result["summary"]) > 600
    assert len(result["metrics"]["abstr"]) > 3000
    assert "raw_payload" not in result
    assert result["event_at"] == "2025-09-07T16:00:00Z"
    assert_read_only_projection(observed)


def test_http_event_detail_runs_real_constructor_without_source_initialization(saved_event):
    _repo, client, identifier, expected, observed = saved_event
    response = client.get(f"/api/v1/investment-monitor/events/{identifier}")
    assert response.status_code == 200
    assert response.json() == expected
    assert observed["constructors"] == [{"initialize_sources": False}]
    assert_read_only_projection(observed)


def test_http_missing_event_keeps_404_and_remains_read_only(saved_event):
    _repo, client, identifier, _expected, observed = saved_event
    response = client.get(f"/api/v1/investment-monitor/events/{identifier + 1}")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "investment_monitor_error"
    assert observed["constructors"] == [{"initialize_sources": False}]
    assert_read_only_projection(observed)


def test_http_invalid_event_id_never_constructs_a_service(saved_event):
    _repo, client, _identifier, _expected, observed = saved_event
    assert client.get("/api/v1/investment-monitor/events/not-an-id").status_code == 422
    assert observed["constructors"] == []
    assert_read_only_projection(observed, selects=0)


def test_control_real_source_initialization_is_not_stubbed_out(saved_event):
    _repo, _client, _identifier, _expected, observed = saved_event
    # This deliberately writes only the temporary database, proving the SQL
    # observation would catch the old GET behavior rather than mock it away.
    endpoints.InvestmentMonitorService(initialize_sources=True)
    assert observed["ensure_calls"] == 1
    assert observed["commits"] == 1
    assert any(sql.lstrip().startswith(("insert", "update")) for sql in observed["sql"])
    assert any("monitoring_sources" in sql for sql in observed["sql"])
