"""Package reads must not construct unrelated AI, API, or SQLite services."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import time
from unittest.mock import Mock

import pytest

from api.v1.endpoints import data_acquisition as endpoint
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services import data_acquisition_service as module
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService


DEPENDENCIES = {
    "planner": "DataAcquisitionPlanner", "financial": "FinancialDataService",
    "monitor": "InvestmentMonitorService", "cninfo": "CninfoAnnouncementService",
}


@pytest.fixture
def lazy_service(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")))
    root = tmp_path / "packages"
    monkeypatch.setenv("DATA_ACQUISITION_OUTPUT_DIR", str(root))
    factories = {}
    for attribute, name in DEPENDENCIES.items():
        factory = Mock(return_value=SimpleNamespace(kind=attribute))
        monkeypatch.setattr(module, name, factory)
        factories[attribute] = factory
    return DataAcquisitionService(), factories


def test_manifest_list_and_authenticated_download_path_need_no_source_or_model_clients(lazy_service, monkeypatch):
    service, factories = lazy_service
    job_id = "20261018T000000Z-a1b2c3d4e5"
    directory = service.output_root / job_id
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({
        "job_id": job_id, "owner_id": "user:11", "status": "success",
    }))
    path = directory / f"{job_id}.zip"
    path.write_bytes(b"PK-test")

    token = set_current_user_id(11)
    try:
        assert service.get_job(job_id) == {"job_id": job_id, "status": "success"}
        assert service.package_path(job_id) == path
        assert service.list_jobs()["total"] == 1
        # Use the actual endpoint factory: each byte-range request may create
        # another service, but must still avoid source registration or clients.
        response = endpoint.download(job_id)
        assert response.path == path
        assert response.headers["content-encoding"] == "identity"
    finally:
        reset_current_user_id(token)

    token = set_current_user_id(22)
    try:
        with pytest.raises(DataAcquisitionError, match="无权访问"):
            service.package_path(job_id)
        assert service.list_jobs()["total"] == 0
    finally:
        reset_current_user_id(token)

    for factory in factories.values():
        factory.assert_not_called()


@pytest.mark.parametrize("attribute", list(DEPENDENCIES))
def test_dependency_is_lazy_cached_and_still_supports_assignment_and_reset(lazy_service, attribute):
    service, factories = lazy_service
    for factory in factories.values():
        factory.assert_not_called()

    initial = getattr(service, attribute)
    assert initial is factories[attribute].return_value
    assert getattr(service, attribute) is initial
    factories[attribute].assert_called_once_with()
    for other, factory in factories.items():
        if other != attribute:
            factory.assert_not_called()

    replacement = SimpleNamespace(kind="injected replacement")
    setattr(service, attribute, replacement)
    assert getattr(service, attribute) is replacement
    factories[attribute].assert_called_once_with()
    setattr(service, attribute, None)
    assert getattr(service, attribute) is initial
    assert factories[attribute].call_count == 2


def test_explicitly_injected_dependencies_are_preserved_even_when_falsey(lazy_service):
    service, factories = lazy_service

    class FalseyDependency:
        def __bool__(self):
            return False

    values = {attribute: FalseyDependency() for attribute in DEPENDENCIES}
    injected = DataAcquisitionService(**values, output_root=service.output_root)

    assert all(getattr(injected, attribute) is value for attribute, value in values.items())
    for factory in factories.values():
        factory.assert_not_called()


def test_tushare_execution_initializes_only_financial_dependency(lazy_service):
    service, factories = lazy_service
    query = Mock(return_value={"rows": [{"ts_code": "603306.SH", "trade_date": "20260824", "close": 75}]})
    factories["financial"].return_value = SimpleNamespace(query=query)
    task = {"source": "tushare", "resource": "daily", "params": {"ts_code": "603306.SH"}, "fields": []}
    scope = {"symbols": ["603306.SH"], "company_names": [], "keywords": [], "market_wide": False}

    rows = service._execute_tushare(task, scope)

    assert rows[0]["close"] == 75
    query.assert_called_once()
    factories["financial"].assert_called_once_with()
    assert all(not factory.called for key, factory in factories.items() if key != "financial")


def test_monitor_execution_initializes_only_monitor_dependency(lazy_service):
    service, factories = lazy_service
    events = Mock(return_value={"items": [{"id": 1, "symbol": "603306.SH", "title": "华懋科技新闻"}]})
    factories["monitor"].return_value = SimpleNamespace(list_events=events)
    task = {"source": "monitor", "resource": "events", "params": {"symbol": "603306.SH"}}
    scope = {"symbols": ["603306.SH"], "company_names": [], "keywords": [], "market_wide": False}

    rows = service._execute_task(task, scope)

    assert rows[0]["id"] == 1
    events.assert_called_once()
    factories["monitor"].assert_called_once_with()
    assert all(not factory.called for key, factory in factories.items() if key != "monitor")


def test_cninfo_execution_initializes_only_cninfo_dependency(lazy_service):
    service, factories = lazy_service
    fetch = Mock(return_value=[{"announcement_id": "one", "title": "华懋科技公告", "code": "603306"}])
    factories["cninfo"].return_value = SimpleNamespace(fetch=fetch)
    task = {"source": "cninfo", "resource": "announcements", "params": {
        "symbols": ["603306.SH"], "start_date": "20260801", "end_date": "20260824",
    }}
    scope = {"symbols": ["603306.SH"], "company_names": [], "keywords": [], "market_wide": False}

    rows = service._execute_task(task, scope)

    assert rows[0]["announcement_id"] == "one"
    fetch.assert_called_once()
    factories["cninfo"].assert_called_once_with()
    assert all(not factory.called for key, factory in factories.items() if key != "cninfo")


def test_planning_initializes_planner_when_used_not_on_construction(lazy_service, monkeypatch):
    service, factories = lazy_service
    plan = Mock(return_value={"title": "offline plan"})
    factories["planner"].return_value = SimpleNamespace(plan=plan)
    monkeypatch.setattr(service, "_validate_plan", lambda raw, request: {**raw, "request": request})

    assert service.plan("获取华懋科技行情") == {"title": "offline plan", "request": "获取华懋科技行情"}
    plan.assert_called_once_with("获取华懋科技行情")
    factories["planner"].assert_called_once_with()
    assert all(not factory.called for key, factory in factories.items() if key != "planner")


def test_concurrent_first_access_constructs_a_dependency_only_once(lazy_service):
    service, factories = lazy_service
    value = SimpleNamespace(kind="shared")

    def create():
        time.sleep(.01)
        return value

    factories["financial"].side_effect = create
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: service.financial, range(8)))

    assert all(result is value for result in results)
    factories["financial"].assert_called_once_with()


def test_failed_initialization_is_not_cached_as_a_broken_dependency(lazy_service):
    service, factories = lazy_service
    value = SimpleNamespace(kind="recovered client")
    factories["financial"].side_effect = [RuntimeError("client unavailable"), value]

    with pytest.raises(RuntimeError, match="client unavailable"):
        _ = service.financial
    assert service.financial is value
    assert factories["financial"].call_count == 2
