"""Offline preflight coverage: no paid AI, datasets, or attachment downloads."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService
from src.services.data_acquisition_task_service import DataAcquisitionTaskService
from src.services.cninfo_announcement_service import CninfoAnnouncementService


class OfficialIssuers:
    def __init__(self):
        self.queries = []
        self.fetches = []

    def search_securities(self, query, **kwargs):
        self.queries.append(query)
        if query in {"国仪", "国仪公司", "688828", "688828.SH"}:
            return [{"name": "国仪公司", "symbol": "688828.SH", "org_id": "official-test"}]
        return []

    def fetch(self, **kwargs):
        self.fetches.append(kwargs)
        return []


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.data_acquisition_service.get_config",
                        lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")))
    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: pytest.fail("unexpected network"))
    return DataAcquisitionService(
        planner=SimpleNamespace(model="offline", plan=lambda _: pytest.fail("unexpected AI")),
        financial=SimpleNamespace(query=lambda **_: {"rows": []}), monitor=object(),
        cninfo=OfficialIssuers(), output_root=tmp_path / "packages",
    )


def plan_for(name="国仪", **scope):
    return {"scope": {"company_names": [name], **scope}, "tasks": [
        {"id": "anns", "label": "公司公告", "source": "cninfo", "resource": "announcements", "params": {}},
        {"id": "reports", "label": "公司研报", "source": "tushare", "resource": "research_report", "params": {}},
    ]}


def test_missing_new_listing_resolves_during_plan_and_survives_revalidation(service):
    raw = plan_for()
    service.planner.plan = lambda _: deepcopy(raw)
    normalized = service.plan("国仪公司所有公告和研究报告")
    assert normalized["preflight"]["can_execute"] is True
    assert normalized["scope"]["symbols"] == ["688828.SH"]
    assert normalized["tasks"][0]["params"]["symbols"] == ["688828.SH"]
    assert normalized["tasks"][1]["params"]["ts_codes"] == ["688828.SH"]
    assert service.cninfo.fetches == []
    again = service.validate_plan("国仪公司所有公告和研究报告", normalized)
    assert again["preflight"]["can_execute"] is True
    assert again["scope"]["symbols"] == ["688828.SH"]


def test_unknown_company_and_forged_client_ready_cannot_execute_or_queue(service, tmp_path):
    raw = plan_for("未知未上市企业")
    raw["preflight"] = {"can_execute": True}
    assert not service.validate_plan("未知未上市企业公告", raw)["preflight"]["can_execute"]
    with pytest.raises(DataAcquisitionError, match="待确认"):
        service.run("未知未上市企业公告", raw)
    assert service.cninfo.fetches == []
    assert list(service.output_root.iterdir()) == []
    manager = DataAcquisitionTaskService(service_factory=lambda: service, task_root=tmp_path / "tasks")
    try:
        with pytest.raises(DataAcquisitionError, match="待确认"):
            manager.submit("未知未上市企业公告", raw)
        assert list(manager.task_root.iterdir()) == []
    finally:
        manager._executor.shutdown(wait=True)


def test_company_code_conflict_not_corrected_or_broadcast(service):
    normalized = service.validate_plan("华懋科技公告", plan_for("华懋科技", symbols=["600519.SH"]))
    assert not normalized["preflight"]["can_execute"]
    assert any(row["status"] == "conflict" for row in normalized["preflight"]["entities"])


def test_explicit_user_code_cannot_be_omitted_by_model_to_hide_conflict(service):
    normalized = service.validate_plan("华懋科技 600519.SH 公告", plan_for("华懋科技"))
    assert not normalized["preflight"]["can_execute"]


@pytest.mark.parametrize("request_text,scope,params", [
    ("华懋科技 600519.SH 公告", {"company_names": ["华懋科技"]}, {"company_name": "华懋科技"}),
    ("获取603306.SH公告", {"symbols": ["603306.SH"]}, {"ts_code": "600519.SH"}),
    ("获取603306.SH公告", {"symbols": ["600519.SH"]}, {"ts_code": "600519.SH"}),
])
def test_task_local_selector_cannot_hide_original_identity_constraint(service, request_text, scope, params):
    raw = {"scope": scope, "tasks": [{"source": "cninfo", "resource": "announcements", "params": params}]}
    blocked = service.validate_plan(request_text, raw)
    assert not blocked["preflight"]["can_execute"]
    assert not service.validate_plan(request_text, blocked)["preflight"]["can_execute"]


def test_revalidating_unknown_global_code_never_drops_blocker(service):
    raw = plan_for("华懋科技", symbols=["999999.SH"])
    blocked = service.validate_plan("华懋公告", raw)
    assert not blocked["preflight"]["can_execute"]
    for _ in range(2):
        blocked = service.validate_plan("华懋公告", blocked)
        assert not blocked["preflight"]["can_execute"]


def test_task_local_companies_do_not_broaden_each_other(service):
    raw = plan_for("华懋科技")
    raw["scope"]["company_names"] = ["华懋科技", "胜宏科技"]
    raw["tasks"][0]["params"] = {"company_name": "华懋科技"}
    raw["tasks"][1]["params"] = {"company_name": "胜宏科技"}
    normalized = service.validate_plan("华懋公告和胜宏研报", raw)
    assert normalized["preflight"]["can_execute"]
    assert normalized["tasks"][0]["params"]["symbols"] == ["603306.SH"]
    assert normalized["tasks"][1]["params"]["ts_codes"] == ["300476.SZ"]
    again = service.validate_plan("华懋公告和胜宏研报", normalized)
    assert again["tasks"][0]["params"]["symbols"] == ["603306.SH"]
    assert again["tasks"][1]["params"]["ts_codes"] == ["300476.SZ"]


def test_topic_reports_and_unlisted_corporate_targets_need_no_equity_code(service):
    report = {"scope": {"keywords": ["低空经济", "无人机"]}, "tasks": [
        {"source": "tushare", "resource": "research_report", "params": {}},
    ]}
    ready = service.validate_plan("下载两年低空经济或者无人机深度研报", report)
    assert ready["preflight"]["can_execute"]
    assert ready["scope"]["symbols"] == []
    corp = {"scope": {"company_names": ["尚未上市企业有限公司"]}, "tasks": [
        {"source": "tianyancha", "resource": "company_full", "params": {}},
    ]}
    assert service.validate_plan("企业全景", corp)["preflight"]["can_execute"]
    assert service.cninfo.queries == []


def test_index_code_and_global_resources_do_not_become_equities(service):
    raw = {"scope": {"company_names": ["华懋科技"]}, "tasks": [
        {"source": "tushare", "resource": "index_daily", "params": {"ts_code": "000001.SH"}},
        {"source": "tushare", "resource": "trade_cal", "params": {}},
        {"source": "tushare", "resource": "news", "params": {}},
    ]}
    normalized = service.validate_plan("指数和新闻", raw)
    assert normalized["preflight"]["can_execute"]
    assert normalized["tasks"][0]["params"]["ts_code"] == "000001.SH"
    for task in normalized["tasks"][1:]:
        assert task["resolved_scope"]["symbols"] == []
        assert "ts_code" not in task["params"]
    assert service.cninfo.queries == []


def test_index_basic_directory_can_query_market_without_single_code(service):
    raw = {"scope": {"market_wide": True}, "tasks": [
        {"source": "tushare", "resource": "index_basic", "params": {"market": "SSE"}},
    ]}
    assert service.validate_plan("获取上交所全部指数基础信息", raw)["preflight"]["can_execute"]


def test_listed_announcements_and_unlisted_corporate_tasks_keep_independent_scope(service):
    raw = {"scope": {"company_names": ["华懋科技", "尚未上市企业有限公司"]}, "tasks": [
        {"source": "cninfo", "resource": "announcements", "params": {"company_name": "华懋科技"}},
        {"source": "tianyancha", "resource": "company_full", "params": {"company_name": "尚未上市企业有限公司"}},
    ]}
    assert service.validate_plan("公司与企业资料", raw)["preflight"]["can_execute"]


def test_official_issuer_search_keeps_candidates_and_uses_response_market(monkeypatch):
    client = CninfoAnnouncementService()
    observed = []
    def response(url, payload, **kwargs):
        observed.append(payload)
        return [
            {"code": "688828", "zwjc": "国仪公司", "category": "A股", "type": "shj", "orgId": "1"},
            {"code": "920999", "zwjc": "北交样本", "category": "A股", "type": "bj", "orgId": "2"},
            {"code": "111111", "zwjc": "债券", "category": "债券", "type": "shj", "orgId": "3"},
        ]
    monkeypatch.setattr(client, "_post_json", response)
    assert [row["symbol"] for row in client.search_securities("国仪")] == ["688828.SH", "920999.BJ"]
    assert observed[0]["keyWord"] == "国仪"
    assert client.resolve_security("688828.SH")["name"] == "国仪公司"


def test_validate_plan_endpoint_does_not_call_ai_and_returns_blocked_plan(service, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.v1.endpoints import data_acquisition
    monkeypatch.setattr(data_acquisition, "DataAcquisitionService", lambda: service)
    app = FastAPI()
    app.include_router(data_acquisition.router, prefix="/acquisition")
    response = TestClient(app).post("/acquisition/validate-plan", json={
        "request": "未知企业公告", "plan": plan_for("未知企业"),
    })
    assert response.status_code == 200
    assert response.json()["preflight"]["can_execute"] is False
