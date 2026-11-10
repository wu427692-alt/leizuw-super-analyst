"""Platform scenarios at the real Agent/catalog/HTTP-normalisation boundary.

Only model output and the HTTP transport are deterministic doubles. Actual
route OpenAPI, capability search/filtering, argument forwarding, response
normalisation and task/widget extraction remain under test. No endpoint
service, worker, real model, external provider, or paid task is executed.
"""
from copy import deepcopy
from datetime import date, timedelta
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest
import requests

from api.v1.endpoints import ai_news, auth, command, concept_themes, essay_quant, essay_radar, industry_research, investment_monitor, stocks, user_auth
from api.v1.schemas.essay_quant import EssayQuantRunRequest, EssayQuantTaskResponse
from api.v1.schemas.essay_radar import EssaySelectedExportRequest
from api.v1.schemas.industry_research import IndustryResearchProjectRequest
from src import config
from src.request_identity import reset_current_user_id, set_current_user_id


@pytest.fixture
def platform(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db"), webui_port=48000))
    monkeypatch.setattr(command, "_CATALOG", None)
    monkeypatch.setattr(command, "_wait_if_paused", lambda *a: None)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("Live HTTP is forbidden"))
    app = FastAPI()
    for prefix, module in (("ai-news", ai_news), ("concept-themes", concept_themes), ("essay-quant", essay_quant),
                           ("industry-research", industry_research), ("investment-monitor", investment_monitor),
                           ("user-auth", user_auth), ("auth", auth), ("stocks", stocks), ("essay-radar", essay_radar)):
        app.include_router(module.router, prefix="/api/v1/" + prefix)
    catalog = command._build_catalog(app)
    owner = set_current_user_id(74)
    try:
        yield SimpleNamespace(app=app, catalog=catalog, monkeypatch=monkeypatch)
    finally:
        reset_current_user_id(owner)


def invoke(platform, name, arguments, *, transport=None, message="按我的要求调用平台能力"):
    calls, turns = [], []
    def model(conversation, *args, **kwargs):
        turns.append(deepcopy(conversation))
        return ("", [{"id": "platform-call", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]) if len(turns) == 1 else ("已处理", [])
    def request(method, url, **kwargs):
        calls.append((method, url, deepcopy(kwargs)))
        assert kwargs["headers"] == {"Cookie": "owner-cookie"}
        if transport is None:
            pytest.fail("Read-only capability discovery must not invoke HTTP")
        return transport(method, url, kwargs)
    platform.monkeypatch.setattr(command, "_llm_turn", model)
    platform.monkeypatch.setattr(requests, "request", request)
    result = command._run_agent("general", [{"role": "user", "content": message}], "owner-cookie", platform.catalog,
                                workspace="platform-scenarios", stream_tokens=False)
    output = next((json.loads(row["content"]) for row in reversed(turns[-1]) if row.get("role") == "tool"), {})
    return result, output, calls


def json_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(payload, ensure_ascii=False).encode()
    return response


@pytest.mark.parametrize("query,path", [
    ("最近20篇小作文 一致预期", "/api/v1/investment-monitor/super-watchlist/{symbol}/essay-consensus"),
    ("共享个股事实 资料", "/api/v1/investment-monitor/stock-workspace/{symbol}"),
    ("龙虎榜单日全市场", "/api/v1/investment-monitor/dragon-tiger/daily"),
])
def test_visible_user_features_are_discoverable_without_exposing_admin(platform, query, path):
    _, output, calls = invoke(platform, "search_capabilities", {"query": query, "limit": 12})
    assert not calls
    assert path in {item["path"] for item in output["capabilities"]}
    assert not any("/admin" in item["path"] or "/worker/" in item["path"] for item in output["capabilities"])


def test_quant_task_keeps_exact_parameters_and_returns_a_trackable_task(platform):
    body = {"name": "看多机构事件研究", "source_query": "华懋科技", "holding_periods": [5, 20],
            "validation_method": "time_split", "refresh_prices": False, "max_symbols": 40}
    def transport(method, url, args):
        assert method == "POST" and url.endswith("/api/v1/essay-quant/tasks")
        parsed = EssayQuantRunRequest.model_validate(args["json"])
        assert parsed.refresh_prices is False and parsed.validation_method == "time_split"
        return json_response(EssayQuantTaskResponse(task_id="quant-owner-1", status="queued", progress=0,
            message="排队中", name=parsed.name, strategy_type=parsed.strategy_type).model_dump(), 202)
    result, _, calls = invoke(platform, "call_capability", {"name": "post_essay-quant_tasks", "params": {"body": body}},
                              transport=transport, message="以本地行情运行华懋科技看多事件研究，不刷新行情")
    assert len(calls) == 1
    assert result["status"] == "submitted"
    assert result["tasks"][0]["poll_path"] == "/api/v1/essay-quant/tasks/quant-owner-1"
    assert result["tasks"][0]["task_id"] == "quant-owner-1"


def test_company_research_project_remains_trackable_after_leaving_chat(platform):
    body = {"topic": "华懋科技", "research_type": "company", "lookback_days": 730,
            "objective": "分析年报、半年报、机构原文与现成录音文字稿", "query_terms": ["华懋", "光通信"]}
    def transport(method, url, args):
        assert method == "POST" and url.endswith("/api/v1/industry-research/projects")
        parsed = IndustryResearchProjectRequest.model_validate(args["json"])
        return json_response({"project_id": "project-owner-1", "topic": parsed.topic, "research_type": parsed.research_type,
                              "status": "queued", "progress": 0, "phase": "queued"}, 202)
    result, _, calls = invoke(platform, "query_capability", {"name": "post_industry-research_projects", "body": body},
                              transport=transport, message="新建华懋科技公司调研任务，可以后台执行")
    assert len(calls) == 1
    assert result["status"] == "submitted"
    assert result["tasks"][0]["poll_path"] == "/api/v1/industry-research/projects/project-owner-1"
    assert result["tasks"][0]["task_id"] == "project-owner-1"


def test_quant_capability_metadata_does_not_hide_risk_relevant_late_fields(platform):
    _, output, _ = invoke(platform, "search_capabilities", {"query": "提交本人量化后台任务", "limit": 12})
    capability = next(item for item in output["capabilities"] if item["path"] == "/api/v1/essay-quant/tasks" and item["method"] == "POST")
    body = capability["params"]["body"]
    assert {"validation_method", "refresh_prices", "max_symbols"} <= set(body)
    assert body["holding_periods"]["items"]["type"] == "integer"


def test_nested_quant_plan_schema_preserves_the_executable_rule_shape(platform):
    _, output, _ = invoke(platform, "search_capabilities", {"query": "确认执行自然语言研究方案", "limit": 12})
    capability = next(item for item in output["capabilities"] if item["path"] == "/api/v1/essay-quant/natural-language/execute")
    rule = capability["params"]["body"]["rule"]
    assert rule["type"] == "object"
    assert "holding_periods" in rule["properties"] and "source_query" in rule["properties"]


def test_no_match_does_not_present_arbitrary_endpoints_as_matching_capabilities(platform):
    _, output, calls = invoke(platform, "search_capabilities", {"query": "zzzz_unavailable_wombat_capability", "limit": 12})
    assert not calls
    assert output["capabilities"] == []


def test_pdf_delivery_is_an_authenticated_artifact_not_binary_model_text(platform):
    decoded = []
    class PdfResponse:
        status_code = 200
        headers = {"content-type": "application/pdf", "content-disposition": 'attachment; filename="report.pdf"',
                   "content-length": "30"}
        @property
        def text(self):
            decoded.append(True)
            return "%PDF-1.7 binary body must never become model context"
    result, _, calls = invoke(platform, "query_capability", {
        "name": "get_industry-research_projects_download", "path": {"project_id": "project-owner-1"}, "query": {"format": "pdf"}},
        transport=lambda *a: PdfResponse(), message="下载我已完成的公司调研PDF")
    assert len(calls) == 1
    assert decoded == []
    assert any(widget["type"] == "file" and widget["url"].startswith("/api/v1/industry-research/projects/project-owner-1/download")
               for widget in result["widgets"])


def test_safe_user_reads_still_forward_owner_cookie_path_and_filters(platform):
    def transport(method, url, args):
        assert method == "GET" and url.endswith("/api/v1/concept-themes/stocks/603306.SH")
        assert args["params"] == {"refresh_if_empty": False, "horizon_days": 60}
        return json_response({"symbol": "603306.SH", "themes": [], "refresh_queued": False})
    result, output, calls = invoke(platform, "query_capability", {"name": "get_concept-themes_stocks", "path": {"ts_code": "603306.SH"},
        "query": {"refresh_if_empty": False, "horizon_days": 60}}, transport=transport, message="只读取华懋科技60日题材画像，不刷新")
    assert len(calls) == 1 and output["ok"] is True
    assert result["tasks"] == [] and result["status"] == "completed"


def test_model_cannot_override_registered_read_method_with_delete(platform):
    def transport(method, url, args):
        assert method == "GET", "A catalogued read must never be converted into DELETE"
        return json_response({"items": []})
    _, output, calls = invoke(platform, "query_capability", {"name": "get_industry-research_projects", "method": "DELETE"},
                              transport=transport, message="只查看我的调研任务，不删除")
    assert not calls or all(method == "GET" for method, _, _ in calls)


def test_status_wait_zero_is_one_nonblocking_snapshot_not_a_sixty_second_wait(platform):
    platform.monkeypatch.setattr(command.time, "sleep", lambda *a: pytest.fail("wait_seconds=0 must not sleep"))
    result, output, calls = invoke(platform, "get_task_status", {
        "task_id": "quant-owner-1", "poll_path": "/api/v1/essay-quant/tasks/quant-owner-1", "wait_seconds": 0},
        transport=lambda *a: json_response({"task_id": "quant-owner-1", "status": "running", "progress": 50}),
        message="查一下量化进度，不用等")
    assert len(calls) == 1 and output["ok"] is True


@pytest.mark.parametrize("status", ["limited", "no_results", "paused"])
def test_non_running_status_returns_instead_of_polling_until_timeout(platform, status):
    platform.monkeypatch.setattr(command.time, "sleep", lambda *a: pytest.fail(f"{status} must not busy-wait"))
    _, output, calls = invoke(platform, "get_task_status", {
        "task_id": "project-owner-1", "poll_path": "/api/v1/industry-research/projects/project-owner-1", "wait_seconds": 10},
        transport=lambda *a: json_response({"project_id": "project-owner-1", "status": status, "progress": 100}),
        message="看看这个任务是不是结束或需要我处理")
    assert len(calls) == 1 and output["ok"] is True


def test_large_stock_history_keeps_real_chart_but_only_forty_model_rows(platform):
    start = date(2024, 1, 1)
    rows = [{"date": (start + timedelta(days=index)).isoformat(), "close": 100 + index / 10,
             "open": 99 + index / 10, "high": 101 + index / 10, "low": 98 + index / 10,
             "volume": 100000 + index, "amount": 10000000 + index * 100, "adjustment_note": "原始行情口径" * 20}
            for index in range(600)]
    payload = {"stock_code": "300750", "stock_name": "宁德时代", "data": rows, "source": "local_history"}
    assert 12000 < len(json.dumps(payload, ensure_ascii=False)) < 350000
    def transport(method, url, args):
        assert method == "GET" and url.endswith("/api/v1/stocks/300750/history")
        assert args["params"] == {"period": "daily", "days": 730, "refresh": False, "max_points": 1000}
        return json_response(payload)
    result, output, calls = invoke(platform, "stock_history", {
        "stock_code": "300750", "period": "day", "days": 730, "refresh": False, "max_points": 1000},
        transport=transport, message="看看宁德时代两年走势，只读本地行情")
    assert len(calls) == 1 and output["ok"] is True
    preview = output["data"]
    assert len(preview["rows"]) == preview["model_sample_count"] == 40
    assert preview["retrieved_count"] == 600 and preview["model_preview_only"] is True
    assert preview["rows"][0]["date"] == rows[-40]["date"]
    assert not any(key in preview for key in ("data", "items"))
    charts = [widget for widget in result["widgets"] if widget["type"] == "chart"]
    assert len(charts) == 1 and "宁德时代" in charts[0]["title"]
    assert len(charts[0]["series"]) == 180
    assert charts[0]["series"][0] == {"date": rows[-180]["date"], "close": rows[-180]["close"]}
    assert charts[0]["series"][-1]["close"] == rows[-1]["close"]


def test_platform_map_describes_real_registered_modules_not_upstream_health(platform):
    _, output, calls = invoke(platform, "platform_capabilities", {})
    assert not calls and output["ok"] is True
    modules = {item["id"]: item for item in output["modules"]}
    assert {"themes", "watchlist", "research", "quant", "ai_news"} <= set(modules)
    assert modules["research"]["page"] == "/industry-research"
    assert modules["watchlist"]["page"] == "/super-watchlist"
    assert all(item["registered_capabilities"] > 0 and item["search_hints"] for item in modules.values())
    assert output["registered_total"] == len(platform.catalog)
    assert "不代表" in output["meaning"]
    assert "/api/v1/industry-research/projects/{task_id}" in output["task_poll_templates"]


def test_search_pages_continue_without_duplicates_and_end_truthfully(platform):
    _, first, calls = invoke(platform, "search_capabilities", {"query": "", "limit": 3, "page": 1})
    _, second, more_calls = invoke(platform, "search_capabilities", {"query": "", "limit": 3, "page": 2})
    assert not calls and not more_calls
    assert first["page"] == 1 and first["page_size"] == 3 and first["next_page"] == 2 and first["has_more"] is True
    assert second["page"] == 2 and second["next_page"] == 3
    first_names = {item["name"] for item in first["capabilities"]}
    second_names = {item["name"] for item in second["capabilities"]}
    assert len(first_names) == len(second_names) == 3 and not first_names & second_names
    beyond_page = (len(platform.catalog) + 2) // 3 + 1
    _, final, _ = invoke(platform, "search_capabilities", {"query": "", "limit": 3, "page": beyond_page})
    assert final["capabilities"] == [] and final["has_more"] is False and final["next_page"] is None


def test_describe_capability_retains_default_enum_range_and_effect(platform):
    _, output, calls = invoke(platform, "describe_capability", {"name": "get_ai-news_feed"})
    assert not calls and output["ok"] is True
    capability = output["capability"]
    query = capability["params"]["query"]
    assert capability["method"] == "GET" and capability["effect"] == "read_may_refresh_cache"
    assert query["view"]["default"] == "hot" and query["view"]["enum"] == ["hot", "latest"]
    assert query["window"]["enum"] == ["24h", "7d", "30d"]
    assert query["page_size"]["minimum"] == 1 and query["page_size"]["maximum"] == 100
    assert query["q"]["maxLength"] == 200 and query["q"]["required"] is False


def test_describe_preserves_nested_rule_defaults_beyond_model_preview_budget(platform):
    _, output, _ = invoke(platform, "describe_capability", {"name": "post_essay-quant_natural-language_execute"})
    capability = output["capability"]
    body = capability["params"]["body"]
    rule = body["rule"]
    assert rule["required"] is True and rule["type"] == "object"
    assert rule["properties"]["holding_periods"]["items"]["type"] == "integer"
    assert rule["properties"]["holding_periods"]["minItems"] == 1
    assert rule["properties"]["validation_method"]["default"] == "walk_forward"
    assert body["refresh_prices"]["type"] == "boolean" and body["refresh_prices"]["default"] is True


@pytest.mark.parametrize("name", ["get_user-auth_status", "post_user-auth_login", "post_auth_login",
                                  "post_investment-monitor_worker_start", "post_investment-monitor_sync"])
def test_identity_and_monitor_write_interfaces_stay_hidden_and_cannot_be_called(platform, name):
    raw_paths = platform.app.openapi()["paths"]
    assert "/api/v1/user-auth/status" in raw_paths and "/api/v1/investment-monitor/worker/start" in raw_paths
    assert not any(item["path"].startswith(("/api/v1/user-auth", "/api/v1/auth")) for item in platform.catalog)
    assert not any(item["method"] != "GET" for item in platform.catalog if item["path"].startswith("/api/v1/investment-monitor"))
    result, output, calls = invoke(platform, "call_capability", {"name": name, "params": {}})
    assert not calls and output["ok"] is False and result["steps"][0]["ok"] is False


@pytest.mark.parametrize("status", [200, 202])
def test_http_success_with_invalid_json_is_not_successful_business_data(platform, status):
    def transport(*args):
        response = requests.Response()
        response.status_code = status
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"broken":'
        return response
    result, output, calls = invoke(platform, "query_capability", {"name": "get_industry-research_projects"}, transport=transport)
    assert len(calls) == 1
    assert output["ok"] is False and result["steps"][0]["ok"] is False
    assert output["data"]["error"] == "invalid_json_response"
    assert not result["tasks"] and not result["widgets"]


def test_invalid_tool_argument_json_never_runs_a_capability(platform):
    turns = []
    def model(conversation, *a, **k):
        turns.append(deepcopy(conversation))
        return ("", [{"id": "broken", "type": "function", "function": {
            "name": "query_capability", "arguments": '{"name":'}}]) if len(turns) == 1 else ("参数有误，没有执行", [])
    platform.monkeypatch.setattr(command, "_llm_turn", model)
    platform.monkeypatch.setattr(requests, "request", lambda *a, **k: pytest.fail("Invalid tool JSON must not invoke HTTP"))
    result = command._run_agent("general", [{"role": "user", "content": "只查看任务列表"}], "owner-cookie", platform.catalog,
                                workspace="platform-scenarios", stream_tokens=False)
    output = json.loads(turns[-1][-1]["content"])
    assert output["ok"] is False and output["needs_argument_correction"] is True
    assert result["steps"][0]["ok"] is False and not result["tasks"]


def test_post_binary_is_not_replayed_as_a_get_download_or_read_into_context(platform):
    class ImmediateExport:
        status_code = 200
        headers = {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   "content-disposition": 'attachment; filename="selected.xlsx"', "content-length": "100"}
        @property
        def text(self):
            pytest.fail("A POST export body must not be decoded into model context")
    def transport(method, url, args):
        assert method == "POST" and url.endswith("/api/v1/essay-radar/feed/export-selected")
        assert EssaySelectedExportRequest.model_validate(args["json"]).topic_ids == ["topic-owned-1"]
        return ImmediateExport()
    result, output, calls = invoke(platform, "query_capability", {
        "name": "post_essay-radar_feed_export-selected", "body": {"topic_ids": ["topic-owned-1"]}},
        transport=transport, message="导出我选中的这篇机构原文Excel")
    assert len(calls) == 1 and output["ok"] is False
    assert output["data"]["error"] == "non_replayable_binary"
    assert not result["tasks"] and not result["widgets"]


@pytest.mark.parametrize("tool_name", ["call_capability", "query_capability"])
def test_multipart_capability_is_described_truthfully_and_never_faked_with_json(platform, tool_name):
    _, described, _ = invoke(platform, "describe_capability", {"name": "post_stocks_extract-from-image"})
    assert described["capability"]["request_media_types"] == ["multipart/form-data"]
    assert described["capability"]["transport_supported"] is False
    args = {"name": "post_stocks_extract-from-image"}
    args["params" if tool_name == "call_capability" else "body"] = {}
    result, output, calls = invoke(platform, tool_name, args, message="帮我识别截图中的股票，但我还没附图片")
    assert not calls and output["ok"] is False and result["steps"][0]["ok"] is False
    assert "真实文件" in output["error"] or "真实文件上传" in output["error"]


def test_expired_task_cannot_make_the_conversation_look_successfully_completed(platform):
    result, output, calls = invoke(platform, "get_task_status", {
        "task_id": "quant-owner-1", "poll_path": "/api/v1/essay-quant/tasks/quant-owner-1", "wait_seconds": 0},
        transport=lambda *a: json_response({"task_id": "quant-owner-1", "status": "expired", "progress": 100}),
        message="看看刚才那个后台任务结果")
    assert len(calls) == 1 and output["ok"] is True
    assert result["status"] == "failed" and result["tasks"][0]["status"] == "expired"


def test_watchlist_quote_preview_exposes_total_and_never_claims_all_are_read(platform):
    codes = [f"{600000 + index:06d}" for index in range(13)]
    def transport(method, url, args):
        assert method == "GET"
        if url.endswith("/api/v1/stocks/watchlist"):
            return json_response({"stock_codes": codes})
        if url.endswith("/api/v1/portfolio/accounts"):
            return json_response({"accounts": []})
        if url.endswith("/quote"):
            code = url.split("/")[-2]
            assert code in codes[:10]
            return json_response({"code": code, "name": code, "current_price": 100, "change_percent": 1})
        pytest.fail("Unexpected extra quote/source request")
    result, output, calls = invoke(platform, "portfolio_overview", {}, transport=transport,
                                   message="只读看一下我的自选行情概览")
    assert len(calls) == 12 and len(output["quotes"]) == 10
    assert output["quote_coverage"]["retrieved"] == 10 and output["quote_coverage"]["total"] == 13
    assert output["quote_coverage"]["preview_only"] is True
    assert "不能称为全部" in output["quote_coverage"]["note"]
    assert len(next(widget for widget in result["widgets"] if widget["type"] == "table")["rows"]) == 10


def test_consensus_dedicated_flag_is_only_a_stock_tag_rule_not_full_text_focus():
    from src.services.essay_consensus_service import EssayConsensusService
    # A genuinely company-focused body can still carry a second stock tag.
    focused_body_with_two_tags = {"symbols": ["603306.SH", "300476.SZ"],
        "title": "华懋科技专项研究", "content": "本文主体完全围绕华懋科技的业务、盈利预测与估值。"}
    assert EssayConsensusService._is_dedicated(focused_body_with_two_tags, "603306.SH") is False
    # Conversely, this metadata-only classifier never reads title or body.
    incidental_body_with_one_tag = {"symbols": ["603306.SH"], "title": "全行业周报",
        "content": "整篇讨论其他公司，仅一处顺带提及华懋科技。"}
    assert EssayConsensusService._is_dedicated(incidental_body_with_one_tag, "603306.SH") is True


def test_consensus_stale_report_and_current_candidate_counts_are_distinct_without_database():
    from datetime import datetime
    from src.services.essay_consensus_service import EssayConsensusService
    service = object.__new__(EssayConsensusService)
    service.analyzer = SimpleNamespace(model="offline")
    previous_source = {"topic_id": "old-focused", "source_kind": "dedicated", "title": "旧样本",
                       "author_name": "同一记录员", "event_at": "2026-09-01T00:00:00Z"}
    row = SimpleNamespace(status="completed", source_hash="old-hash", source_count=1,
        model="offline", prompt_version="v3", result_json=json.dumps({"summary": "旧样本分析结果", "estimates": [],
            "_source_snapshot": {"version": 1, "source_hash": "old-hash", "source_count": 1,
                "source_notes": [previous_source], "sources_complete": True,
                "analysis_cutoff_at": "2026-09-01T00:00:00Z", "completed_at": "2026-09-02T00:00:00Z"}}),
        error_message=None, updated_at=datetime(2026, 9, 2), completed_at=datetime(2026, 9, 2))
    selected = [{"topic_id": "new-candidate", "source_kind": "related", "title": "当前候选",
                 "created_at": "2026-10-20T00:00:00Z", "author_name": "同一记录员"}]
    snapshot = service._snapshot_dict(row, selected=selected, source_hash="new-hash")
    assert snapshot["status"] == "stale" and snapshot["dedicated_source_count"] == 0
    assert snapshot["source_notes"][0]["source_kind"] == "dedicated"
    assert snapshot["candidate_source_notes"][0]["source_kind"] == "related"
    assert snapshot["analysis_cutoff_at"] == "2026-09-01T00:00:00Z"
    assert snapshot["candidate_cutoff_at"] == "2026-10-20T00:00:00Z"


def consensus_payload(*, large=False, empty=False):
    report_sources = [] if empty else [{"topic_id": f"report-{index}", "title": f"同一机构口径记录{index}",
        "event_at": "2026-09-01T00:00:00Z" if index == 0 else "2026-10-01T00:00:00Z",
        "author_name": "天风电子", "source_kind": "dedicated"} for index in range(2)]
    return {"symbol": "603306.SH", "name": "华懋科技", "consensus": {
        "status": "not_started" if empty else "stale", "has_report": not empty,
        "source_count": 0 if empty else 20, "related_source_count": 0 if empty else 20,
        "dedicated_source_count": 0, "candidate_cutoff_at": "2026-10-21T00:00:00Z",
        "report_source_count": None if empty else 2, "report_sources_complete": not empty,
        "analysis_cutoff_at": None if empty else "2026-10-01T00:00:00Z",
        "completed_at": None if empty else "2026-10-02T00:00:00Z", "source_notes": report_sources,
        "candidate_source_notes": [{"topic_id": f"candidate-{index}", "source_kind": "related",
            "title": "华懋科技专项研究但具有多股票标签", "author_name": "天风电子"} for index in range(0 if empty else 20)],
        "summary": "" if empty else ("尚未核验的旧报告摘要" * (1600 if large else 1)),
        "conflicts": [] if empty else ["同一机构在不同日期将2027年利润口径从20亿元修订到25亿元"],
        "time_observations": [] if empty else ["9月与10月口径变化，尚不能算独立机构之间的分歧"],
    }}


def test_consensus_model_evidence_distinguishes_tag_counts_freshness_and_institution_revisions(platform):
    payload = consensus_payload()
    result, output, calls = invoke(platform, "query_capability", {
        "name": "get_investment-monitor_super-watchlist_essay-consensus", "path": {"symbol": "603306.SH"}},
        transport=lambda *a: json_response(payload), message="读华懋科技一致预期，先不重跑AI；区分时间修订和机构分歧")
    assert len(calls) == 1 and not result["tasks"]
    scope = output["evidence_scope"]
    assert scope["kind"] == "institutional_expectations" and scope["dedicated_basis"] == "single_stock_tag_set"
    assert scope["semantic_focus_verified"] is False and scope["zero_count_proves_absence"] is False
    assert scope["per_source_char_limit"] == 2500
    data = output["data"]
    assert data["symbol"] == "603306.SH" and data["name"] == "华懋科技" and data["status"] == "stale"
    assert data["candidate"] == {"count": 20, "as_of": "2026-10-21T00:00:00Z", "single_stock_tag_count": 0,
                                  "multiple_or_other_stock_tag_count": 20, "semantic_focus_status": "not_assessed"}
    assert data["analyzed_report"]["status"] == "stale" and data["analyzed_report"]["report_source_count"] == 2
    assert data["analyzed_report"]["analysis_cutoff_at"] == "2026-10-01T00:00:00Z"
    notes = data["analyzed_report"]["source_notes"]
    assert {item["topic_id"] for item in notes} == {"report-0", "report-1"}
    assert all(item["stock_tag_group"] == "single_stock_tags" for item in notes)
    assert_no_legacy_consensus_labels(output)
    comparability = json.dumps(scope["comparability"], ensure_ascii=False)
    assert "机构" in comparability and "分歧" in comparability and ("修订" in comparability or "变化" in comparability)


def test_long_consensus_response_retains_model_evidence_scope_through_both_compaction_layers(platform):
    payload = consensus_payload(large=True)
    assert len(json.dumps(payload, ensure_ascii=False)) > 12000
    _, output, calls = invoke(platform, "call_capability", {
        "name": "get_investment-monitor_super-watchlist_essay-consensus", "params": {"path": {"symbol": "603306.SH"}}},
        transport=lambda *a: json_response(payload), message="阅读旧一致预期快照，说明样本限制")
    assert len(calls) == 1 and output["ok"] is True
    scope = output["evidence_scope"]
    assert scope["dedicated_basis"] == "single_stock_tag_set" and scope["zero_count_proves_absence"] is False
    data = output["data"]
    assert data["analyzed_report"]["status"] == "stale" and data["candidate"]["single_stock_tag_count"] == 0
    assert len(data["analyzed_report"]["source_notes"]) == 2
    assert data["analyzed_report"]["report_sources_complete"] is True
    assert scope["per_source_char_limit"] == 2500
    assert_no_legacy_consensus_labels(output)
    assert "尚未核验的旧报告摘要" not in json.dumps(output, ensure_ascii=False)


def test_unanalyzed_empty_candidate_counts_are_not_global_negative_evidence(platform):
    _, output, calls = invoke(platform, "query_capability", {
        "name": "get_investment-monitor_super-watchlist_essay-consensus", "path": {"symbol": "603306.SH"}},
        transport=lambda *a: json_response(consensus_payload(empty=True)), message="先看看有没有现成一致预期，不启动分析")
    assert len(calls) == 1
    scope = output["evidence_scope"]
    data = output["data"]
    assert data["candidate"]["count"] == 0 and scope["zero_count_proves_absence"] is False
    assert data["analyzed_report"]["status"] == "not_started"
    assert data["analyzed_report"]["report_source_count"] is None
    assert data["analyzed_report"]["report_sources_complete"] is False
    assert data["analyzed_report"]["source_notes"] == []
    assert_no_legacy_consensus_labels(output)


def assert_no_legacy_consensus_labels(value):
    if isinstance(value, dict):
        assert not ({"dedicated_source_count", "related_source_count", "source_kind", "consensus"} & value.keys())
        for item in value.values():
            assert_no_legacy_consensus_labels(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_legacy_consensus_labels(item)


def test_consensus_internal_result_keeps_source_contract_but_model_sees_only_safe_projection(platform):
    payload = consensus_payload()
    legacy_summary = "绝无公司专题原文，所有记录都只是相关提及，这是旧模型诱导结论标记"
    payload["consensus"]["summary"] = legacy_summary
    payload["consensus"]["estimates"] = [{"topic_id": "report-1", "subject": "华懋科技",
        "metric": "net_profit", "period": "2027年", "value_text": "25亿元", "unit": "亿元",
        "evidence": "2026年10月1日同一机构将2027年利润预期修订为25亿元"}]
    original = deepcopy(payload)
    calls = []
    def transport(method, url, **kwargs):
        assert method == "GET" and kwargs["headers"] == {"Cookie": "owner-cookie"}
        calls.append((method, url))
        return json_response(payload)
    platform.monkeypatch.setattr(requests, "request", transport)
    actual = command._internal_call("GET", "/api/v1/investment-monitor/super-watchlist/{symbol}/essay-consensus",
        "owner-cookie", {"path": {"symbol": "603306.SH"}})
    assert len(calls) == 1 and actual["ok"] is True and payload == original
    # The program/UI source response remains backwards compatible; only the
    # model boundary removes misleading legacy names and unverified summaries.
    assert actual["data"]["consensus"]["summary"] == legacy_summary
    assert actual["data"]["consensus"]["dedicated_source_count"] == 0
    assert actual["data"]["consensus"]["source_notes"][0]["source_kind"] == "dedicated"
    assert actual["evidence_scope"]["candidate_counts"]["dedicated_source_count"] == 0
    assert "model_evidence" in actual
    visible = command._tool_result_for_model("query_capability", actual)
    assert_no_legacy_consensus_labels(visible)
    assert legacy_summary not in json.dumps(visible, ensure_ascii=False)
    assert visible["data"]["recorded_expectations"] == payload["consensus"]["estimates"]
    assert visible["data"]["analyzed_report"]["source_notes"][1]["event_at"] == "2026-10-01T00:00:00Z"
    result, model_input, agent_calls = invoke(platform, "query_capability", {
        "name": "get_investment-monitor_super-watchlist_essay-consensus", "path": {"symbol": "603306.SH"}},
        transport=lambda *a: json_response(payload), message="读取已有的一致预期，不能沿用旧摘要的主旨结论")
    assert len(agent_calls) == 1 and not result["tasks"] and model_input == visible
    assert legacy_summary not in json.dumps(model_input, ensure_ascii=False)


def test_consensus_projection_preserves_expectation_periods_but_labels_old_ai_as_interpretation(platform):
    payload = consensus_payload()
    payload["consensus"].update({"summary": "禁止流入模型的旧自然语言摘要", "profit_outlook": "旧AI利润口径",
        "valuation_outlook": "旧AI估值口径", "estimates": [{"topic_id": "report-1", "metric": "net_profit",
            "period": f"{2027 + index}年", "value_text": f"{25 + index}亿元", "unit": "亿元"} for index in range(43)]})
    _, output, calls = invoke(platform, "query_capability", {
        "name": "get_investment-monitor_super-watchlist_essay-consensus", "path": {"symbol": "603306.SH"}},
        transport=lambda *a: json_response(payload), message="读取旧预测并保留每项预测期，不把旧AI观点当新事实")
    assert len(calls) == 1 and output["ok"] is True
    data = output["data"]
    assert len(data["recorded_expectations"]) == 40
    assert data["recorded_expectations"][0]["period"] == "2027年" and data["recorded_expectations"][-1]["period"] == "2066年"
    assert data["recorded_expectations_count"] == 43 and data["expectations_preview_only"] is True
    interpretation = data["historical_ai_interpretation"]
    assert interpretation["profit_outlook"] == "旧AI利润口径" and interpretation["valuation_outlook"] == "旧AI估值口径"
    assert "AI" in interpretation["label"] and "不是官方事实" in interpretation["label"]
    assert "禁止流入模型的旧自然语言摘要" not in json.dumps(output, ensure_ascii=False)
    assert_no_legacy_consensus_labels(output)


@pytest.mark.parametrize("file_name_field", ["name", "file_name"])
def test_meeting_search_matches_each_file_not_the_title_of_a_multi_file_post(platform, file_name_field):
    target = {"topic_id": "post-huaqin", "file_id": "audio-huamao", file_name_field: "华懋科技20261020.mp3",
              "note_title": "华勤技术20261021.mp3"}
    unrelated = {"topic_id": "post-huamao", "file_id": "audio-other", file_name_field: "中际旭创20261020.mp3",
                 "note_title": "华懋科技20261021.mp3"}
    legacy = {"topic_id": "legacy-post", "file_id": "audio-legacy", "note_title": "华懋科技20250101.mp3"}
    def transport(method, url, args):
        assert method == "GET" and args["params"]["query"] == "华懋"
        if url.endswith("/research-notes/audio-files"):
            assert args["params"]["page_size"] == 20
            return json_response({"total": 37, "items": [target, unrelated, legacy]})
        assert url.endswith("/research-notes")
        return json_response({"total": 0, "items": []})
    result, output, calls = invoke(platform, "meeting_search", {"title": "华懋", "limit": 20},
        transport=transport, message="只检索华懋公司的录音，先看清文件名，不启动下载或转写")
    assert len(calls) == 2 and output["ok"] is True and not result["tasks"]
    assert {item["file_id"] for item in output["data"]["audio"]} == {"audio-huamao", "audio-legacy"}
    scope = output["evidence_scope"]
    assert scope["kind"] == "audio_search_preview" and scope["preview_only"] is True
    assert scope["absence_claim_supported"] is False and scope["returned_audio_count"] == 2


@pytest.mark.parametrize("failure", ["forbidden", "task_preview_miss"])
def test_meeting_text_unavailable_is_specific_attempt_not_proof_no_transcript_exists(platform, failure):
    audio = {"topic_id": "post-huaqin", "file_id": "audio-huamao", "name": "华懋科技20261020.mp3",
             "note_title": "华勤技术20261021.mp3"}
    if failure == "forbidden":
        audio["transcript_task_id"] = "owner-audio-task"
    def transport(method, url, args):
        assert method == "GET", "An existing-transcript read must never submit a paid transcription task"
        if url.endswith("/research-notes/audio-files"):
            return json_response({"items": [audio], "total": 1})
        if url.endswith("/research-notes"):
            return json_response({"items": [], "total": 0})
        if failure == "forbidden":
            assert url.endswith("/audio-analysis/tasks/owner-audio-task/transcripts/audio-huamao")
            return json_response({"detail": "Current owner could not read this transcript"}, status=403)
        assert url.endswith("/audio-analysis/tasks") and args["params"] == {"limit": 20}
        return json_response({"items": [{"task_id": f"other-{index}", "items": [{"file_id": f"other-{index}"}]}
                                        for index in range(20)], "total": 120, "has_more": True})
    result, output, calls = invoke(platform, "meeting_text", {"title": "华懋", "transcribe": False},
        transport=transport, message="只看华懋已有的逐字稿，不启动转写或AI纪要")
    assert len(calls) == 3 and output["ok"] is False
    assert not result["tasks"] and not result["widgets"]
    assert output["data"]["audio"]["file_id"] == "audio-huamao"
    assert output["data"]["transcript_available"] is False and output["data"]["can_transcribe"] is True
    scope = output["evidence_scope"]
    assert scope["kind"] == "specific_audio" and scope["preview_only"] is True
    assert scope["absence_claim_supported"] is False
    assert "全库" in scope["note"] and ("未取到" in scope["note"] or "未取得" in scope["note"])
