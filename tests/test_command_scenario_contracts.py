"""Executable orchestration contracts, not a live-model understanding benchmark.

Model turns below are deliberately scripted only to enter real guard, resume,
query, pagination and export branches. Assertions concern those real branches,
not whether a model understood the Chinese prompt or wrote a good answer.
No production service, paid model, source attachment or existing test is used.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from api.v1.endpoints import command
from src import config
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.command_question_service import CommandQuestionService


@pytest.fixture(autouse=True)
def offline_owned_scope(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Scenario contracts must not call a real model or upstream")

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db.sqlite")))
    monkeypatch.setattr(command, "_wait_if_paused", lambda *args: None)
    for name in ("_llm_turn", "_complete_stream", "_internal_call", "_deepseek_search"):
        monkeypatch.setattr(command, name, unexpected)
    identity = set_current_user_id(73)
    try:
        yield
    finally:
        reset_current_user_id(identity)


def tool_call(name, args, ident="contract-tool"):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False),
    }}


def script_tools(monkeypatch, calls):
    """Select branches only; this is not an assertion of model understanding."""
    turns = []

    def scripted(conversation, tools, *args, **kwargs):
        turns.append(deepcopy(conversation))
        assert tools is not None
        return ("", deepcopy(calls)) if len(turns) == 1 else ("contract completed", [])

    monkeypatch.setattr(command, "_llm_turn", scripted)
    return turns


def run_tools(monkeypatch, message, calls, *, resume_state=None, messages=None):
    turns = script_tools(monkeypatch, calls)
    result = command._run_agent(
        "general", messages or [{"role": "user", "content": message}], "owned-cookie", [],
        workspace="scenario-contract", resume_state=resume_state,
    )
    return result, turns


def note(number, company="宁德时代"):
    return {"topic_id": str(number), "note": {
        "title": f"{company} 原文 {number}", "content": f"{company} 未分析原文 {number}",
        "created_at": "2026-10-21T01:00:00Z", "group_name": "合成来源",
    }}


def install_feed(monkeypatch, total, *, scope_totals=None):
    queries = []

    def internal(method, path, cookie, params=None, **kwargs):
        assert method == "GET" and path == "/api/v1/essay-radar/feed"
        assert cookie == "owned-cookie"
        query = deepcopy(params["query"])
        queries.append(query)
        current_total = scope_totals.get(query["query_scope"], total) if scope_totals else total
        offset = (query["page"] - 1) * query["page_size"]
        items = [note(i, query["query"]) for i in range(offset, min(offset + query["page_size"], current_total))]
        return {"ok": True, "data": {"items": items, "total": current_total}}

    monkeypatch.setattr(command, "_internal_call", internal)
    return queries


@pytest.mark.parametrize("message", [
    "先看看宁德时代最近两天有哪些小作文，不要下载",
    "只统计宁德时代原文命中数，先别打包",
    "宁德时代相关的小作文先给我一个预览",
])
def test_read_only_recall_does_not_force_export_scope_question(message, monkeypatch):
    queries = install_feed(monkeypatch, 237)
    monkeypatch.setattr(
        command, "_package_essay_feed", lambda *a, **k: pytest.fail("Read-only intent must not export"),
    )
    result, turns = run_tools(monkeypatch, message, [tool_call("essay_radar", {
        "query": "宁德时代", "days": 2, "query_scope": "full", "page_size": 20,
    })])

    assert result.get("status") != "waiting_input"
    assert len(turns) == 2 and len(queries) == 1
    assert queries[0]["query"] == "宁德时代" and queries[0]["days"] == 2
    returned = json.loads(turns[1][-1]["content"])["data"]
    assert returned["total"] == 237 and returned["has_more"]
    assert returned["preview_only"] and returned["page_size"] == 20
    assert returned["model_sample_count"] == 12
    assert result["tasks"] == []


@pytest.mark.parametrize("message,expected", [
    ("下载最近两天标题含宁德时代的小作文，只有标题匹配的才要", "title"),
    ("全文提及宁德时代的小作文最近两天都下载", "full"),
    ("不只是标题，正文提及宁德时代都要，最近两天全部下载", "full"),
    ("不要只看标题，正文提及宁德时代都要，最近两天全部下载", "full"),
    ("不要只看标题，正文提及都要：宁德时代，最近两天全部下载", "full"),
])
def test_explicit_scope_in_real_export_control_flow_is_not_reasked(message, expected, monkeypatch):
    exported = []

    def package(args, cookie, workspace, emit):
        exported.append(deepcopy(args))
        return {"ok": True, "data": {"count": 1, "complete": True}}

    monkeypatch.setattr(command, "_package_essay_feed", package)
    # The explicit quotation is evidence a guard can verify, not proof a model
    # would have extracted it correctly in a real conversation.
    result, turns = run_tools(monkeypatch, message, [tool_call("package_essays", {
        "query": "宁德时代", "days": 2, "query_scope": expected, "scope_evidence": message,
    })])

    assert result.get("status") != "waiting_input"
    assert len(turns) == 2 and len(exported) == 1
    assert exported[0]["query_scope"] == expected
    assert exported[0]["query"] == "宁德时代" and exported[0]["days"] == 2


@pytest.mark.parametrize("message", [
    "给我看看标题", "先展示标题列表，不下载", "标题和来源先列出来",
])
def test_title_projection_wording_is_not_a_scope_filter(message):
    assert command._essay_explicit_scope(message) is None


def test_show_titles_preserves_previously_confirmed_full_scope(monkeypatch):
    queries = install_feed(monkeypatch, 237)
    state = {"essay_selections": [{"query": "宁德时代", "days": 7, "query_scope": "full"}]}
    result, _ = run_tools(monkeypatch, "先给我看看标题，不下载", [tool_call("essay_radar", {
        "query": "宁德时代", "days": 7, "query_scope": "title",
    })], resume_state=state)

    assert result.get("status") != "waiting_input"
    assert queries[0]["query_scope"] == "full" and queries[0]["days"] == 7
    assert not result["tasks"]


@pytest.mark.parametrize("known_first", [True, False])
def test_batch_keeps_confirmed_company_and_asks_only_unconfirmed_target(known_first, monkeypatch):
    queried = []

    def query(args, cookie):
        queried.append(deepcopy(args))
        return {"ok": True, "data": {"total": 9, "items": []}}

    monkeypatch.setattr(command, "_query_essay_radar", query)
    monkeypatch.setattr(
        command, "_package_essay_feed", lambda *a, **k: pytest.fail("Dependent batch must wait"),
    )
    catl = {"query": "宁德时代", "days": 2, "query_scope": "full", "analysis_status": "essay"}
    state = {"essay_selections": [catl]}
    calls = [tool_call("package_essays", {"query": "宁德时代", "days": 2}, "catl"),
             tool_call("package_essays", {"query": "华懋科技", "days": 7}, "huamao")]
    if not known_first:
        calls.reverse()
    result, _ = run_tools(monkeypatch, "两家公司都下载，分别沿用各自条件", calls, resume_state=state)

    assert result["status"] == "waiting_input"
    assert "华懋科技" in result["clarification"]["question"]
    assert len(queried) == 2 and {q["query_scope"] for q in queried} == {"title", "full"}
    assert all(q["query"] == "华懋科技" and q["days"] == 7 for q in queried)
    saved = CommandQuestionService().claim(result["clarification"]["id"], "title", None, "scenario-contract")
    assert saved["continuation"]["prior_state"]["essay_selections"] == [catl]
    assert saved["continuation"]["filters"]["query"] == "华懋科技"
    assert saved["continuation"]["prior_state"]["multiple_tools"]


def test_two_confirmed_companies_export_with_independent_scope_and_dates(monkeypatch):
    calls = []

    def package(args, *a):
        calls.append(deepcopy(args))
        return {"ok": True, "data": {"count": 1, "complete": True}}

    monkeypatch.setattr(command, "_package_essay_feed", package)
    state = {"essay_selections": [
        {"query": "宁德时代", "days": 2, "query_scope": "full"},
        {"query": "华懋科技", "days": 7, "query_scope": "title"},
    ]}
    result, _ = run_tools(monkeypatch, "两家各按已确认条件都下载", [
        tool_call("package_essays", {"query": "宁德时代", "days": 2}, "catl"),
        tool_call("package_essays", {"query": "华懋科技", "days": 7}, "huamao"),
    ], resume_state=state)

    assert result.get("status") != "waiting_input"
    assert [(a["query"], a["days"], a["query_scope"]) for a in calls] == [
        ("宁德时代", 2, "full"), ("华懋科技", 7, "title"),
    ]


@pytest.mark.parametrize("custom_text", [
    "顺带提及也算，但只要最近7天", "改成华懋科技，全文提及都要，最近7天",
])
def test_custom_answer_keeps_prior_work_and_original_download_goal(custom_text, monkeypatch):
    original = "下载宁德时代最近两天的小作文，同时下载年报，不要重复已有任务"
    steps = [{"name": "annual_report", "ok": True, "summary": "年报已提交"}]
    tasks = [{"task_id": "existing-annual", "poll_path": "/api/v1/data-acquisition/tasks/existing-annual"}]
    artifacts = [{"name": "existing-result", "data": {"title": "年报"}}]
    conversation = [{"role": "system", "content": "contract"}, {"role": "user", "content": original}]
    prior = {"steps": deepcopy(steps), "tasks": deepcopy(tasks), "artifacts": deepcopy(artifacts),
             "conversation": deepcopy(conversation), "multiple_tools": True}
    question = CommandQuestionService().create({"question": "范围？", "options": [
        {"id": "title", "label": "标题匹配"}, {"id": "full", "label": "全文提及"},
    ], "allow_custom": True}, {
        "kind": "essay", "agent": "general", "messages": [{"role": "user", "content": original}],
        "filters": {"query": "宁德时代", "days": 2}, "download": True, "prior_state": prior,
    }, "scenario-contract")
    observed = []

    def resumed(agent, messages, cookie, catalog, emit, session_id, **kwargs):
        observed.append((deepcopy(messages), deepcopy(kwargs["resume_state"])))
        return {"reply": "contract resumed", "steps": steps, "tasks": tasks, "artifacts": artifacts}

    monkeypatch.setattr(command, "_run_agent", resumed)
    body = command.CommandChatRequest(
        messages=[{"role": "user", "content": custom_text}], workspace_id="scenario-contract",
        clarification_answer={"question_id": question["id"], "text": custom_text},
    )
    result = command._resume_question(body, "owned-cookie", [], "scenario-contract", None)

    assert len(observed) == 1
    messages, state = observed[0]
    assert state["steps"] == steps and state["tasks"] == tasks and state["artifacts"] == artifacts
    assert state["original_goal"] == original and "下载" in state["original_goal"]
    assert messages[0]["content"] == original and custom_text in messages[-1]["content"]
    assert state["conversation"][:2] == conversation
    assert custom_text in state["conversation"][-1]["content"]
    assert result["tasks"] == tasks and result["artifacts"] == artifacts
    assert command._resume_question(body, "owned-cookie", [], "scenario-contract", None) == result
    assert len(observed) == 1  # Duplicate answer must not repeat existing work.


@pytest.mark.parametrize("scope", ["title", "full"])
def test_422_in_real_recall_branch_never_falls_back_unfiltered(scope, monkeypatch):
    queries = []

    def internal(method, path, cookie, params=None, **kwargs):
        queries.append(deepcopy(params["query"]))
        return {"ok": False, "status": 422, "error": "字段格式不兼容"}

    monkeypatch.setattr(command, "_internal_call", internal)
    wording = "标题匹配" if scope == "title" else "全文提及"
    result, turns = run_tools(
        monkeypatch, f"查宁德时代最近两天{wording}的小作文", [tool_call("essay_radar", {
            "query": "宁德时代", "days": 2, "query_scope": scope, "page_size": 200,
        })],
    )

    assert len(queries) == 1
    assert queries[0]["query"] == "宁德时代" and queries[0]["days"] == 2
    assert queries[0]["query_scope"] == scope and queries[0]["page_size"] == 100
    failure = json.loads(turns[1][-1]["content"])
    assert not failure["ok"] and failure["status"] == 422
    assert failure["filters"]["query"] == "宁德时代"
    assert result["steps"][0]["ok"] is False


@pytest.mark.parametrize("total,expected_pages", [(0, 1), (20, 1), (100, 1), (200, 2), (201, 3)])
@pytest.mark.parametrize("scope", ["title", "full"])
def test_collector_boundaries_preserve_scope_and_actual_total(total, expected_pages, scope, monkeypatch):
    queries = install_feed(monkeypatch, total)
    result = command._collect_essay_feed({"query": "宁德时代", "days": 2, "query_scope": scope,
                                         "known_total": 9999, "page": 8, "page_size": 20}, "owned-cookie")

    assert result["ok"] and result["data"]["complete"]
    assert result["data"]["count"] == result["data"]["expected_total"] == total
    assert result["data"]["pages"] == expected_pages
    assert [q["page"] for q in queries] == list(range(1, expected_pages + 1))
    assert all(q["query_scope"] == scope and q["query"] == "宁德时代" and q["days"] == 2 for q in queries)
    assert all(q["page_size"] == 100 and "known_total" not in q for q in queries)
    assert len({item["topic_id"] for item in result["items"]}) == total


@pytest.mark.parametrize("message,expected_count,expected_limit", [
    ("宁德时代最近两天所有提及的小作文全部下载", 237, None),
    ("宁德时代最近两天所有提及的小作文全部下载，不止20篇", 237, None),
    ("宁德时代最近两天所有提及的小作文全部下载，不是只要20篇", 237, None),
    ("宁德时代最近两天所有提及的小作文全部下载，别只给20篇", 237, None),
    ("宁德时代最近两天所有提及的小作文全部下载，20篇只是预览", 237, None),
    ("宁德时代最近两天所有提及的小作文只下载前20篇", 20, 20),
    ("宁德时代最近两天所有提及的小作文下载不超过20篇", 20, 20),
    ("宁德时代最近两天所有提及的小作文只下载前150篇", 150, 150),
])
def test_real_export_guard_distinguishes_model_preview_limit_and_human_limit(
    message, expected_count, expected_limit, monkeypatch,
):
    queries = install_feed(monkeypatch, 237)
    actual_args, collected = [], []

    def package(args, cookie, workspace, emit):
        actual_args.append(deepcopy(args))
        payload = command._collect_essay_feed(args, cookie, emit)
        collected.append(payload)
        return payload

    monkeypatch.setattr(command, "_package_essay_feed", package)
    result, _ = run_tools(monkeypatch, message, [tool_call("package_essays", {
        "query": "宁德时代", "days": 2, "query_scope": "full", "limit": 20,
    })])

    assert result.get("status") != "waiting_input"
    assert len(collected) == 1 and actual_args[0].get("limit") == expected_limit
    assert collected[0]["data"]["count"] == expected_count
    assert collected[0]["data"]["expected_total"] == 237
    assert collected[0]["data"]["complete"]
    assert collected[0]["data"]["library_complete"] is (expected_count == 237)
    assert len({item["topic_id"] for item in collected[0]["items"]}) == expected_count
    assert all(q["query"] == "宁德时代" and q["days"] == 2 and q["query_scope"] == "full" for q in queries)


def test_zero_hits_do_not_enter_archive_creation_or_publish_download(monkeypatch):
    from src.services.report_pack_service import ReportPackService

    install_feed(monkeypatch, 0)
    monkeypatch.setattr(
        ReportPackService, "package_essays", lambda *a, **k: pytest.fail("Zero hits must not create ZIP"),
    )
    result = command._collect_essay_feed(
        {"query": "宁德时代", "days": 2, "query_scope": "full"}, "owned-cookie", "scenario-contract",
    )

    assert result["ok"] and result["data"]["complete"] and result["data"]["count"] == 0
    assert result["data"]["expected_total"] == 0
    assert not result["data"].get("download_url")


def test_page_with_missing_identity_is_not_complete_even_if_raw_row_count_matches(monkeypatch):
    def internal(method, path, cookie, params=None, **kwargs):
        query = params["query"]
        items = [note(i) for i in range(100)] if query["page"] == 1 else []
        if items:
            items[-1].pop("topic_id")
        return {"ok": True, "data": {"items": items, "total": 100}}

    monkeypatch.setattr(command, "_internal_call", internal)
    result = command._collect_essay_feed({"query": "宁德时代", "days": 2, "query_scope": "full"}, "owned-cookie")

    assert not result["ok"] and not result["data"]["complete"]
    assert result["data"]["count"] == 99 and result["data"]["expected_total"] == 100
    assert any("topic_id" in error for error in result["data"]["errors"])
