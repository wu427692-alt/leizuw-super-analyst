# -*- coding: utf-8 -*-
"""Tests for the unified Tushare and ZSXQ research-note gateway."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import financial_data
from src.config import Config
from src.services.financial_data_service import (
    FinancialDataService,
    FinancialDataUpstreamError,
    FinancialDataValidationError,
    ResearchNoteService,
    TushareGatewayService,
)
from src.storage import DatabaseManager, ResearchNote


@pytest.fixture()
def research_service(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "financial-data.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    service = ResearchNoteService()
    try:
        yield service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _topic(**overrides):
    payload = {
        "topic_id": "14422821525422552",
        "title": "野村——宇树科技（688836.SS）首次覆盖",
        "content": "目标价 370 元，关注人形机器人规模化盈利。",
        "create_time": "2026-08-19T14:26:11.062+0800",
        "modify_time": "",
        "digested": True,
        "sticky": False,
        "type": "talk",
        "text_type": "plain",
        "group": {"group_id": "28855458518111", "name": "调研纪要"},
        "owner": {
            "user_id": "owner-1",
            "name": "研究员",
            "avatar_url": "https://images.zsxq.com/avatar?token=temporary-signature",
        },
        "files": [
            {"file_id": "file-1", "name": "unitree.pdf", "size": 1000},
        ],
        "images": [
            {
                "image_id": "image-1",
                "original": {
                    "url": "https://images.zsxq.com/image?e=1&token=temporary-signature",
                    "width": 800,
                    "height": 1200,
                },
            }
        ],
        "counts": {"comments": 2, "likes": 3, "readers": 545},
    }
    payload.update(overrides)
    return payload


def test_import_is_idempotent_searchable_and_strips_signed_urls(research_service) -> None:
    first = research_service.import_topics([_topic()])
    second = research_service.import_topics([_topic()])

    assert first == {
        "received": 1,
        "saved": 1,
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "analysis_queue": {"created": 1, "reset": 0, "unchanged": 0},
    }
    assert second == {
        "received": 1,
        "saved": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 1,
        "analysis_queue": {"created": 0, "reset": 0, "unchanged": 0},
    }

    result = research_service.list_notes(query="规模化盈利", symbol="688836")
    assert result["total"] == 1
    assert result["items"][0]["symbols"] == ["688836.SH"]

    detail = research_service.get_note("14422821525422552")
    raw_text = str(detail["raw_payload"])
    assert "temporary-signature" not in raw_text
    assert "counts" not in detail["raw_payload"]
    assert detail["counts"] == {}
    assert detail["images"][0]["original"]["url"] == "https://images.zsxq.com/image"


def test_changed_topic_updates_existing_row(research_service) -> None:
    research_service.import_topics([_topic()])
    result = research_service.import_topics([_topic(content="更新后的调研正文")])

    assert result["updated"] == 1
    assert research_service.get_note("14422821525422552")["content"] == "更新后的调研正文"


def test_interaction_count_changes_do_not_write_or_enqueue(research_service) -> None:
    research_service.import_topics([_topic()])
    before = research_service.get_note("14422821525422552")

    result = research_service.import_topics([
        _topic(counts={"comments": 999, "likes": 10000, "readers": 500000})
    ])
    after = research_service.get_note("14422821525422552")

    assert result["updated"] == 0
    assert result["unchanged"] == 1
    assert result["analysis_queue"] == {"created": 0, "reset": 0, "unchanged": 0}
    assert after["synced_at"] == before["synced_at"]
    assert after["counts"] == {}


def test_legacy_hash_does_not_mass_requeue_unchanged_notes(research_service) -> None:
    research_service.import_topics([_topic()])
    with research_service.repo.db.get_session() as session:
        row = session.query(ResearchNote).filter_by(topic_id="14422821525422552").one()
        row.content_hash = "0" * 64
        session.commit()

    result = research_service.import_topics([_topic(digested=False)])

    assert result["updated"] == 0
    assert result["unchanged"] == 1
    assert result["analysis_queue"] == {"created": 0, "reset": 0, "unchanged": 0}


def test_attachment_changes_are_substantive_and_requeued(research_service) -> None:
    research_service.import_topics([_topic()])
    result = research_service.import_topics([
        _topic(files=[{"file_id": "file-2", "name": "new-report.pdf", "size": 2048}])
    ])

    assert result["updated"] == 1
    assert result["analysis_queue"]["reset"] == 1
    assert research_service.get_note("14422821525422552")["files"][0]["file_id"] == "file-2"


def test_import_mcp_page_rejects_unsuccessful_source(research_service) -> None:
    with pytest.raises(FinancialDataUpstreamError, match="Skill 权限"):
        research_service.import_mcp_page({"success": False, "error": "该星球暂未开通 Skill 权限"})


def test_tushare_gateway_accepts_any_valid_api_name_and_serializes_values(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_client.query.return_value = pd.DataFrame(
        [{"ts_code": "600519.SH", "close": 1688.0, "nullable": float("nan")}]
    )
    monkeypatch.setenv("TUSHARE_API_RATE_LIMIT_PER_MINUTE", "500")
    TushareGatewayService._request_times.clear()
    service = TushareGatewayService(client=fake_client)

    result = service.query(
        "daily",
        params={"ts_code": "600519.SH"},
        fields=["ts_code", "close", "nullable"],
    )

    fake_client.query.assert_called_once_with(
        "daily",
        fields="ts_code,close,nullable",
        ts_code="600519.SH",
    )
    assert result["rows"] == [{"ts_code": "600519.SH", "close": 1688.0, "nullable": None}]


def test_tushare_gateway_rejects_invalid_api_name() -> None:
    service = TushareGatewayService(client=MagicMock())
    with pytest.raises(FinancialDataValidationError, match="api_name"):
        service.query("__dict__")


def test_unified_query_routes_to_research_notes(research_service) -> None:
    research_service.import_topics([_topic()])
    fake_tushare = MagicMock()
    service = FinancialDataService(tushare=fake_tushare, research_notes=research_service)

    result = service.query(
        source="zsxq",
        resource="research_notes",
        params={"query": "宇树科技", "page": 1, "page_size": 10},
    )

    assert result["source"] == "zsxq"
    assert result["total"] == 1
    fake_tushare.query.assert_not_called()


def test_source_catalog_exposes_monitoring_freshness(research_service) -> None:
    fake_monitor = MagicMock()
    fake_monitor.list_sources.return_value = {
        "items": [
            {
                "source_key": "cninfo.announcements", "enabled": True,
                "last_status": "success", "last_success_at": "2026-08-19T12:00:00Z",
                "poll_interval_seconds": 900,
            },
            {
                "source_key": "tianyancha.enterprise", "enabled": True,
                "last_status": "success", "last_success_at": None,
                "poll_interval_seconds": 43200,
            },
        ],
        "total": 2, "healthy": 2, "enabled": 2,
    }
    service = FinancialDataService(
        tushare=MagicMock(available=True),
        research_notes=research_service,
        monitor=fake_monitor,
    )

    result = service.list_sources()

    sources = {item["source"]: item for item in result["sources"]}
    assert {"tushare", "zsxq", "monitor", "cninfo", "tianyancha"} <= set(sources)
    assert sources["monitor"]["summary"]["freshness"]["never"] == 1
    assert sources["cninfo"]["resource"] == "announcements"


def test_unified_query_routes_cninfo_and_tianyancha(research_service) -> None:
    fake_monitor = MagicMock()
    fake_monitor.list_announcements.return_value = {
        "items": [{"id": 1, "title": "公告"}], "total": 1, "page": 1, "page_size": 20,
    }
    fake_monitor.list_events.return_value = {
        "items": [{"id": 2, "title": "工商风险"}], "total": 1, "page": 1, "page_size": 20,
    }
    service = FinancialDataService(
        tushare=MagicMock(), research_notes=research_service, monitor=fake_monitor,
    )

    announcements = service.query(
        source="cninfo", resource="announcements", params={"symbol": "603306.SH", "page_size": 20},
    )
    enterprise = service.query(
        source="tianyancha", resource="enterprise_events", params={"days": 30, "page_size": 20},
    )

    assert announcements["rows"][0]["title"] == "公告"
    assert enterprise["rows"][0]["title"] == "工商风险"
    fake_monitor.list_events.assert_called_once_with(days=30, page_size=20, source_key="tianyancha.enterprise")


def test_financial_data_api_import_and_search(research_service) -> None:
    app = FastAPI()
    app.include_router(financial_data.router, prefix="/api/v1/financial-data")
    client = TestClient(app)

    import_response = client.post(
        "/api/v1/financial-data/zsxq/import",
        json={"topics": [_topic()]},
    )
    assert import_response.status_code == 200, import_response.text
    assert import_response.json()["created"] == 1

    search_response = client.get(
        "/api/v1/financial-data/research-notes",
        params={"query": "宇树科技", "symbol": "688836.SH"},
    )
    assert search_response.status_code == 200, search_response.text
    assert search_response.json()["total"] == 1
