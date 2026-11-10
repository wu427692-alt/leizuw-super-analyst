"""Choices pause without running dependent tools and resume within one owner."""
from types import SimpleNamespace
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.endpoints import command
from src import config
from src.request_identity import set_current_user_id, reset_current_user_id
from src.services.command_question_service import CommandQuestionService, QuestionStateError


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db.sqlite")))
    token = set_current_user_id(17)
    try:
        yield tmp_path
    finally:
        reset_current_user_id(token)


def question():
    return {"question": "选择检索范围", "options": [
        {"id": "title", "label": "标题匹配", "count": 41},
        {"id": "full", "label": "全文提及", "count": 237},
    ]}


def tool(name, args, ident="call-choice"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_choice_persists_privately_and_rejects_other_owner_and_workspace(owner):
    service = CommandQuestionService()
    item = service.create(question(), {"kind": "agent"}, "test")
    assert CommandQuestionService().get(item["id"], "test")["status"] == "pending"
    path = next((owner / "command_questions").rglob(item["id"] + ".json"))
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(QuestionStateError):
        service.get(item["id"], "different")
    token = set_current_user_id(18)
    try:
        with pytest.raises(QuestionStateError):
            service.get(item["id"], "test")
    finally:
        reset_current_user_id(token)


@pytest.mark.parametrize("option,text", [("forged", None), (None, ""), (None, "x" * 3001)])
def test_invalid_answers_never_claim_task(owner, option, text):
    service = CommandQuestionService()
    item = service.create(question(), {}, "test")
    with pytest.raises(QuestionStateError):
        service.claim(item["id"], option, text, "test")
    assert service.get(item["id"], "test")["status"] == "pending"


def test_duplicate_answer_has_cached_result_not_reexecution(owner):
    service = CommandQuestionService()
    item = service.create(question(), {}, "test")
    service.claim(item["id"], "full", None, "test")
    with pytest.raises(QuestionStateError) as error:
        service.claim(item["id"], "full", None, "test")
    assert error.value.status_code == 409
    service.finish(item["id"], result={"reply": "done"}, workspace="test")
    assert service.claim(item["id"], "full", None, "test") == {"cached_result": {"reply": "done"}}
    with pytest.raises(QuestionStateError):
        service.claim(item["id"], "title", None, "test")


def test_generic_ask_user_returns_immediately_and_resumes_saved_tool_turn(owner, monkeypatch):
    calls = []
    def llm(conversation, *args, **kwargs):
        calls.append(conversation)
        if len(calls) == 1:
            return "", [tool("ask_user", question()), tool("package_essays", {"query": "不应执行"}, "dependent")]
        assert conversation[-1]["role"] == "tool"
        assert json.loads(conversation[-1]["content"])["user_answer"]["option_id"] == "full"
        assert conversation[-2]["tool_calls"][0]["function"]["name"] == "ask_user"
        assert len(conversation[-2]["tool_calls"]) == 1
        return "已根据用户选择继续", []
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_internal_call", lambda *a, **k: pytest.fail("dependent call must wait"))
    events = []
    result = command._run_agent("research", [{"role": "user", "content": "查公司资料"}], "", [], events.append, workspace="test")
    assert result["status"] == "waiting_input"
    assert [e for e in events if e["type"] == "clarification"]
    assert len(calls) == 1
    body = command.CommandChatRequest(messages=[{"role": "user", "content": "用户可见历史"}], workspace_id="test",
        clarification_answer={"question_id": result["clarification"]["id"], "option_id": "full"})
    resumed = command._resume_question(body, "", [], "test", None)
    assert resumed["reply"] == "已根据用户选择继续"
    assert command._resume_question(body, "", [], "test", None) == resumed
    assert len(calls) == 2


def test_catl_scope_recall_counts_are_not_page_size_and_dates_are_preserved(owner, monkeypatch):
    queries = []
    def query(args, cookie):
        queries.append(args)
        return {"ok": True, "data": {"total": 41 if args["query_scope"] == "title" else 237, "items": []}}
    monkeypatch.setattr(command, "_query_essay_radar", query)
    monkeypatch.setattr(command, "_llm_turn", lambda *a, **k: ("", [tool("package_essays", {"query": "宁德时代", "days": 30, "limit": 20})]))
    messages = [{"role": "user", "content": "给我下载宁德时代这两天的小作文"}]
    result = command._run_agent("research", messages, "cookie", [], workspace="test")
    assert result["status"] == "waiting_input"
    assert all(q["query"] == "宁德时代" and q["days"] == 2 and q["page_size"] == 1 for q in queries)
    options = {o["id"]: o for o in result["clarification"]["options"]}
    assert list(options) == ["focused", "full", "title"]
    assert options["title"]["count"] == 41 and options["full"]["count"] == 237
    assert "count" not in options["focused"] and "候选 237 篇" in options["focused"]["description"]
    assert "不是聚焦篇数" in options["focused"]["description"] and "AI" in options["focused"]["description"]
    observed = []
    def package(args, cookie, workspace, emit):
        observed.append(args)
        return {"ok": True, "task": {"task_id": "essay-export-" + "a" * 32, "status": "queued", "progress": None},
                "data": {"status": "queued", "download_ready": False}}
    monkeypatch.setattr(command, "_package_essay_feed", package)
    body = command.CommandChatRequest(messages=messages, workspace_id="test", clarification_answer={
        "question_id": result["clarification"]["id"], "option_id": "full"})
    resumed = command._resume_question(body, "cookie", [], "test", None)
    assert observed == [{"query": "宁德时代", "days": 2, "analysis_status": "essay", "query_scope": "full"}]
    assert resumed["status"] == "submitted" and "后台导出" in resumed["reply"]
    assert resumed["tasks"][0]["status"] == "queued" and not resumed["widgets"]


@pytest.mark.parametrize("phrase", [
    "标题包含宁德时代", "标题含有宁德时代", "标题中含宁德时代", "标题里包含宁德时代",
    "标题中提及宁德时代", "标题里出现宁德时代", "标题 含 宁德时代",
])
def test_explicit_title_predicate_is_a_scope_not_an_extra_question(phrase):
    text = f"请下载{phrase}的小作文"
    assert command._essay_explicit_scope(text) == "title"
    assert command._essay_scope_for_call({"query": "宁德时代", "query_scope": "title"},
        [{"role": "user", "content": text}], None) == "title"


def test_live_request_title_contains_exports_three_with_exact_dates_without_reasking(owner, monkeypatch):
    message = "请下载标题包含宁德时代的最新3篇小作文，日期2026-10-21至2026-10-22，只要原文和已有标签，不做新的AI分析。"
    captured, turns = [], []
    def llm(*args, **kwargs):
        turns.append(1)
        if len(turns) == 1:
            return "", [tool("package_essays", {"query": "宁德时代", "query_scope": "title", "limit": 3,
                "start_date": "2026-10-21", "end_date": "2026-10-22"})]
        return "已提交标题匹配的最新3篇原文导出，不进行新AI分析。", []
    def package(filters, cookie, workspace, emit):
        captured.append(filters)
        return {"ok": True, "task": {"task_id": "essay-export-" + "a" * 32,
            "status": "queued", "progress": None}, "data": {"status": "queued"}}
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_package_essay_feed", package)
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: pytest.fail("An explicit title scope must not re-ask"))
    result = command._run_agent("general", [{"role": "user", "content": message}], "", [], workspace="test")
    assert result["status"] == "submitted" and not result.get("clarification")
    assert len(turns) == 2 and len(captured) == 1
    assert captured[0]["query"] == "宁德时代" and captured[0]["query_scope"] == "title"
    assert captured[0]["limit"] == 3 and captured[0]["analysis_status"] == "essay"
    assert captured[0]["start_date"] == "2026-10-21" and captured[0]["end_date"] == "2026-10-22"
    assert "days" not in captured[0]
    assert [step["name"] for step in result["steps"]] == ["package_essays"]


@pytest.mark.parametrize("query,scope,evidence,expected", [
    ("宁德时代", "title", "标题包含宁德时代的取3篇", "title"),
    ("华懋科技", "full", "全文提及华懋科技的全部下载", "full"),
    ("宁德时代", "title", "全文提及华懋科技的全部下载", None),
    ("华懋科技", "title", "标题包含宁德时代的取3篇", None),
    ("宁德时代", "title", "标题包含宁德时代的取3篇（只要已分析的）", None),
    ("宁德时代", "title", "标题包含宁德时代的取3篇；全文提及华懋科技的全部下载", None),
    ("华懋科技", "full", "标题包含宁德时代的取3篇；全文提及华懋科技的全部下载", None),
    ("宁德时代", "title", "", None),
    ("华懋科技", "title", "", None),
])
def test_title_contains_keeps_multi_company_scope_evidence_target_bound(query, scope, evidence, expected):
    text = "标题包含宁德时代的取3篇；全文提及华懋科技的全部下载"
    args = {"query": query, "query_scope": scope, "scope_evidence": evidence}
    assert command._essay_scope_for_call(args, [{"role": "user", "content": text}], None) == expected


@pytest.mark.parametrize("text,expected", [
    ("请给下载包标题改为宁德时代", None),
    ("标题中不含宁德时代的小作文", None),
    ("全文提及宁德时代，标题可以没有公司名称", "full"),
    ("不要只看标题，正文提及宁德时代的都要", "full"),
    ("标题包含宁德时代的和全文提及华懋科技的分别下载", None),
])
def test_title_predicate_does_not_turn_renaming_negation_or_mixed_scopes_into_title(text, expected):
    assert command._essay_explicit_scope(text) == expected


@pytest.mark.parametrize("text", [
    "只要专门讲宁德时代的小作文", "我要整篇围绕宁德时代的，不是仅标题匹配", 
    "不是只看标题，我要整篇围绕宁德时代", "宁德时代主旨聚焦的小作文",
    "全文围绕宁德时代的材料", "以宁德时代为主体的小作文", "主要讨论宁德时代的小作文",
    "我要聚焦宁德时代的小作文", "专门关于宁德时代的材料", "只要主讲宁德时代的纪要",
    "聚焦不等于标题匹配，而是整个小作文围绕宁德时代分析的这种",
    "聚焦指全文主旨，不是标题匹配", "聚焦是整篇的主题，并非标题匹配",
    "聚焦不是标题匹配", "聚焦不代表标题命中",
])
def test_focused_means_the_article_subject_not_title_keywords(text):
    # The explicit rejection of title matching is not another positive scope.
    assert command._essay_explicit_scope(text) == "focused"


@pytest.mark.parametrize("text", ["全文", "全文检索宁德时代", "全文匹配宁德时代", "全文提及宁德时代的都要"])
def test_full_text_recall_still_means_all_mentions(text):
    assert command._essay_explicit_scope(text) == "full"


@pytest.mark.parametrize("message,expected", [
    ("宁德时代不要标题匹配，要主旨聚焦", "focused"),
    ("宁德时代不要主旨聚焦，全部提及都下载", "full"),
])
def test_negating_one_scope_executes_the_positive_choice_without_reasking(owner, monkeypatch, message, expected):
    observed, turns = [], []
    def llm(*args, **kwargs):
        turns.append(1)
        return ("", [tool("package_essays", {"query": "宁德时代", "query_scope": expected})]) if len(turns) == 1 else ("已提交", [])
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: pytest.fail("A rejected scope is not a competing choice"))
    monkeypatch.setattr(command, "_package_essay_feed", lambda filters, *a: observed.append(filters) or {
        "ok": True, "task": {"task_id": "essay-export-" + "a" * 32, "status": "queued"}})
    assert command._essay_explicit_scope(message) == expected
    result = command._run_agent("general", [{"role": "user", "content": message}], "", [], workspace="test")
    assert result["status"] == "submitted" and len(observed) == 1 and len(turns) == 2
    assert observed[0]["query_scope"] == expected


def test_local_scope_negation_does_not_remove_another_company_positive_scope():
    text = "宁德时代不要标题匹配，要主旨聚焦；华懋科技只要标题匹配"
    assert command._essay_explicit_scope(text) is None
    for query, scope, evidence in [
        ("宁德时代", "focused", "宁德时代不要标题匹配，要主旨聚焦"),
        ("华懋科技", "title", "华懋科技只要标题匹配"),
    ]:
        assert command._essay_scope_for_call({"query": query, "query_scope": scope, "scope_evidence": evidence},
            [{"role": "user", "content": text}], None) == scope


def test_live_full_article_subject_request_submits_without_reasking(owner, monkeypatch):
    message = ("下载2026-10-21至2026-10-22整篇围绕宁德时代开展分析的所有文字小作文。"
               "聚焦指全文主旨，不是标题匹配；不设篇数上限，只导出原文与已有标签，不生成新的研究报告。")
    observed, turns = [], []
    def llm(*args, **kwargs):
        turns.append(1)
        if len(turns) == 1:
            return "", [tool("package_essays", {"query": "宁德时代", "query_scope": "focused",
                "start_date": "2026-10-21", "end_date": "2026-10-22"})]
        return "已提交逐篇主旨筛选任务", []
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: pytest.fail("Explicit full-article subject must not re-ask"))
    monkeypatch.setattr(command, "_package_essay_feed", lambda filters, *a: observed.append(filters) or {
        "ok": True, "task": {"task_id": "essay-export-" + "a" * 32, "status": "queued"}})
    result = command._run_agent("general", [{"role": "user", "content": message}], "", [], workspace="test")
    assert result["status"] == "submitted" and len(turns) == 2 and len(observed) == 1
    assert observed[0]["query_scope"] == "focused" and "limit" not in observed[0]
    assert observed[0]["start_date"] == "2026-10-21" and observed[0]["end_date"] == "2026-10-22"


def test_canonical_focus_choice_resumes_real_agent_with_prior_steps_without_loop(owner, monkeypatch):
    # The old message is deliberately ambiguous. The newly clicked choice,
    # not generated option descriptions or the old conflict, is the decision.
    original = "宁德时代要整篇围绕公司的小作文还是全文提及的？请让我选，日期2026-10-21至2026-10-22"
    messages = [{"role": "user", "content": original}]
    task_id = "essay-export-" + "a" * 32
    existing = {"task_id": "existing-report", "poll_path": "/api/v1/data-acquisition/tasks/existing-report"}
    prior = {"conversation": [{"role": "system", "content": "offline contract"}] + messages,
             "steps": [{"name": "get_task_status", "ok": True, "summary": "已有研报任务"}],
             "tasks": [existing], "multiple_tools": True}
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: {"ok": True, "data": {"total": 25, "items": []}})
    question_result = command._essay_scope_question({"query": "宁德时代", "start_date": "2026-10-21", "end_date": "2026-10-22"},
        original, "research", messages, "", "test", None, prior)
    observed, turns = [], []
    def llm(conversation, *args, **kwargs):
        turns.append(1)
        if len(turns) == 1:
            assert "范围已确认：主旨聚焦 宁德时代" in conversation[-1]["content"]
            return "", [tool("package_essays", {"query": "宁德时代", "query_scope": "focused",
                "start_date": "2026-10-21", "end_date": "2026-10-22"})]
        return "已提交聚焦任务，保留已有研报任务", []
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_essay_scope_question", lambda *a, **k: pytest.fail("Clicked scope must not ask again"))
    monkeypatch.setattr(command, "_package_essay_feed", lambda filters, *a: observed.append(filters) or {
        "ok": True, "task": {"task_id": task_id, "status": "queued"}})
    body = command.CommandChatRequest(messages=messages, workspace_id="test", clarification_answer={
        "question_id": question_result["clarification"]["id"], "option_id": "focused"})
    result = command._resume_question(body, "", [], "test", None)
    assert result["status"] == "submitted" and len(observed) == 1 and len(turns) == 2
    assert observed[0]["query_scope"] == "focused" and "limit" not in observed[0]
    assert observed[0]["start_date"] == "2026-10-21" and observed[0]["end_date"] == "2026-10-22"
    assert {t["task_id"] for t in result["tasks"]} == {existing["task_id"], task_id}
    assert command._resume_question(body, "", [], "test", None) == result
    assert len(observed) == 1 and len(turns) == 2


@pytest.mark.parametrize("query,start,end,evidence,expected", [
    ("宁德时代", "2026-10-21", "2026-10-22", "", "focused"),
    ("华懋科技", "2026-10-21", "2026-10-22", "华懋科技要全文提及的小作文", "full"),
    ("华懋科技", "2026-10-21", "2026-10-22", "", None),
    ("宁德时代", "2026-10-19", "2026-10-20", "", None),
])
def test_canonical_scope_confirmation_stays_bound_to_company_and_dates(query, start, end, evidence, expected):
    messages = [{"role": "user", "content": "宁德时代要整篇围绕公司的小作文；华懋科技要全文提及的小作文"},
        {"role": "user", "content": "范围已确认：主旨聚焦 宁德时代（整篇围绕公司）。继续原任务，不要重新提交已有任务编号对应的工作。"}]
    state = {"essay_selections": [{"query": "宁德时代", "query_scope": "focused",
        "start_date": "2026-10-21", "end_date": "2026-10-22"}]}
    state["essay_scope_confirmation"] = {"content": messages[-1]["content"], "selection": state["essay_selections"][-1]}
    args = {"query": query, "start_date": start, "end_date": end,
        "query_scope": expected or "focused", "scope_evidence": evidence}
    assert command._essay_scope_for_call(args, messages, state) == expected


def test_canonical_confirmation_does_not_copy_original_single_company_scope_to_another():
    original = "下载宁德时代主旨聚焦的小作文"
    confirmation = "范围已确认：主旨聚焦 宁德时代（整篇围绕公司）。继续原任务，不要重新提交已有任务编号对应的工作。"
    state = {"essay_selections": [{"query": "宁德时代", "query_scope": "focused", "days": 2}]}
    state["essay_scope_confirmation"] = {"content": confirmation, "selection": state["essay_selections"][-1]}
    messages = [{"role": "user", "content": original}, {"role": "user", "content": confirmation}]
    assert command._essay_scope_for_call({"query": "华懋科技", "days": 2, "query_scope": "focused"}, messages, state) is None
    state["essay_selections"].insert(0, {"query": "华懋科技", "query_scope": "full", "days": 2})
    assert command._essay_scope_for_call({"query": "华懋科技", "days": 2, "query_scope": "focused"}, messages, state) == "full"


def test_legacy_focused_title_card_reissues_one_current_question_without_export(owner, monkeypatch):
    original = "下载宁德时代最近两天的小作文"
    existing = {"task_id": "existing-report", "poll_path": "/api/v1/data-acquisition/tasks/existing-report"}
    service = CommandQuestionService()
    legacy = service.create({"question": "范围？", "options": [
        {"id": "title", "label": "聚焦 宁德时代（标题匹配）"},
        {"id": "full", "label": "全部提及 宁德时代"}]}, {
        "kind": "essay", "agent": "research", "messages": [{"role": "user", "content": original}],
        "filters": {"query": "宁德时代", "days": 2}, "download": True,
        "steps": [{"name": "essay_radar", "ok": True, "summary": "旧只读召回"}],
        "prior_state": {"tasks": [existing], "artifacts": [{"name": "existing-artifact"}]},
    }, "test")
    queries = []
    monkeypatch.setattr(command, "_query_essay_radar", lambda args, *a: queries.append(args) or {
        "ok": True, "data": {"total": 25, "items": []}})
    monkeypatch.setattr(command, "_llm_turn", lambda *a, **k: pytest.fail("Legacy choice repair must not use a model"))
    monkeypatch.setattr(command, "_package_essay_feed", lambda *a: pytest.fail("Legacy ambiguous focus must not export"))
    body = command.CommandChatRequest(messages=[{"role": "user", "content": original}], workspace_id="test",
        clarification_answer={"question_id": legacy["id"], "option_id": "title"})
    result = command._resume_question(body, "", [], "test", None)
    assert result["status"] == "waiting_input" and result["clarification"]["id"] != legacy["id"]
    assert [option["id"] for option in result["clarification"]["options"]] == ["focused", "full", "title"]
    assert result["tasks"] == [existing] and result["artifacts"] == [{"name": "existing-artifact"}]
    assert result["steps"][0]["summary"] == "旧只读召回"
    fresh = service.claim(result["clarification"]["id"], "focused", None, "test")["continuation"]
    assert fresh["filters"]["query"] == "宁德时代" and fresh["filters"]["days"] == 2 and fresh["download"]
    assert command._resume_question(body, "", [], "test", None) == result and len(queries) == 2
    assert service.snapshot(legacy["id"], "test")["result"] == result


def test_new_literal_title_card_is_not_blocked_as_legacy_focus(owner, monkeypatch):
    item = CommandQuestionService().create({"question": "范围？", "options": [
        {"id": "title", "label": "仅标题包含 宁德时代"},
        {"id": "full", "label": "所有提及 宁德时代"}]}, {
        "kind": "essay", "agent": "research", "messages": [{"role": "user", "content": "下载宁德时代最近两天小作文"}],
        "filters": {"query": "宁德时代", "days": 2}, "download": True,
    }, "test")
    observed = []
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: pytest.fail("Literal title confirmation must not re-ask"))
    monkeypatch.setattr(command, "_package_essay_feed", lambda filters, *a: observed.append(filters) or {
        "ok": True, "task": {"task_id": "essay-export-" + "a" * 32, "status": "queued"}})
    body = command.CommandChatRequest(messages=[{"role": "user", "content": "下载宁德时代最近两天小作文"}], workspace_id="test",
        clarification_answer={"question_id": item["id"], "option_id": "title"})
    result = command._resume_question(body, "", [], "test", None)
    assert result["status"] == "submitted" and len(observed) == 1 and observed[0]["query_scope"] == "title"


def test_latest_focused_correction_overrides_saved_title_without_reasking(owner, monkeypatch):
    observed, turns = [], []
    messages = [{"role": "user", "content": "按标题匹配下载宁德时代最近两天小作文"},
        {"role": "user", "content": "不要只看标题，我要整篇围绕宁德时代的，最新3篇，日期2026-10-21至2026-10-22"}]
    def llm(*args, **kwargs):
        turns.append(1)
        return ("", [tool("package_essays", {"query": "宁德时代", "query_scope": "title", "limit": 3,
            "limit_evidence": "我要整篇围绕宁德时代的，最新3篇",
            "start_date": "2026-10-21", "end_date": "2026-10-22"})]) if len(turns) == 1 else ("已提交主旨筛选后台任务", [])
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: pytest.fail("A clear focused correction must not re-ask"))
    monkeypatch.setattr(command, "_package_essay_feed", lambda filters, *a: observed.append(filters) or {
        "ok": True, "task": {"task_id": "essay-export-" + "a" * 32, "status": "queued"}})
    state = {"essay_selection": {"query": "宁德时代", "query_scope": "title",
        "start_date": "2026-10-21", "end_date": "2026-10-22"}}
    result = command._run_agent("general", messages, "", [], workspace="test", resume_state=state)
    assert result["status"] == "submitted" and len(observed) == 1
    assert observed[0]["query_scope"] == "focused" and observed[0]["limit"] == 3


def test_title_projection_does_not_override_confirmed_focused_subject_scope():
    args = {"query": "宁德时代", "query_scope": "title", "days": 2}
    state = {"essay_selection": {"query": "宁德时代", "query_scope": "focused", "days": 2}}
    assert command._essay_scope_for_call(args, [{"role": "user", "content": "先给我看标题列表"}], state) == "focused"
    assert command._essay_scope_for_call(args, [{"role": "user", "content": "不要只看标题，改为整篇围绕这家公司"}],
        {"essay_selection": {"query": "宁德时代", "query_scope": "title", "days": 2}}) == "focused"


@pytest.mark.parametrize("query,scope,evidence,expected", [
    ("宁德时代", "focused", "宁德时代取整篇围绕公司的小作文", "focused"),
    ("华懋科技", "full", "华懋科技取全文提及的全部小作文", "full"),
    ("比亚迪", "title", "比亚迪取标题包含公司名的", "title"),
    ("宁德时代", "title", "比亚迪取标题包含公司名的", None),
    ("宁德时代", "focused", "宁德时代取整篇围绕公司的小作文；华懋科技取全文提及的全部小作文；比亚迪取标题包含公司名的", None),
    ("宁德时代", "focused", "", None),
    ("华懋科技", "focused", "", None),
])
def test_three_company_scopes_need_their_own_exact_evidence(query, scope, evidence, expected):
    message = "宁德时代取整篇围绕公司的小作文；华懋科技取全文提及的全部小作文；比亚迪取标题包含公司名的"
    assert command._essay_scope_for_call({"query": query, "query_scope": scope, "scope_evidence": evidence},
        [{"role": "user", "content": message}], None) == expected


def test_clicking_focused_submits_that_scope_and_does_not_invent_a_count(owner, monkeypatch):
    monkeypatch.setattr(command, "_query_essay_radar", lambda args, cookie: {
        "ok": True, "data": {"total": 17 if args["query_scope"] == "title" else 37, "items": []}})
    question_result = command._essay_scope_question({"query": "宁德时代", "days": 2, "limit": 3},
        "下载宁德时代最近两天最新3篇小作文", "research", [], "", "test", None)
    focused = next(o for o in question_result["clarification"]["options"] if o["id"] == "focused")
    assert "count" not in focused and "候选 37 篇" in focused["description"]
    observed = []
    monkeypatch.setattr(command, "_package_essay_feed", lambda filters, *a: observed.append(filters) or {
        "ok": True, "task": {"task_id": "essay-export-" + "a" * 32, "status": "queued"}})
    body = command.CommandChatRequest(messages=[{"role": "user", "content": "确认主旨聚焦"}], workspace_id="test", clarification_answer={
        "question_id": question_result["clarification"]["id"], "option_id": "focused"})
    result = command._resume_question(body, "", [], "test", None)
    assert result["status"] == "submitted" and len(observed) == 1
    assert observed[0]["query_scope"] == "focused" and observed[0]["limit"] == 3
    assert "聚焦总数待筛选完成" in result["reply"] and "不生成新纪要或转写" in result["reply"]


@pytest.mark.parametrize("ban", ["完全不调用AI", "禁止使用AI", "不使用大模型", "完全禁止AI"])
def test_explicit_ai_ban_is_clarified_not_silently_used_for_subject_screening(owner, monkeypatch, ban):
    turns = []
    def llm(conversation, *args, **kwargs):
        turns.append(1)
        if len(turns) == 1:
            return "", [tool("package_essays", {"query": "宁德时代", "query_scope": "focused"})]
        error = json.loads(conversation[-1]["content"])
        assert error["needs_argument_correction"] and "用户明确禁止调用AI" in error["error"]
        return "", [tool("ask_user", {"question": "主旨聚焦需要AI阅读全文，是否允许仅做分类？",
            "options": [{"id": "allow_subject_only", "label": "允许AI仅判定主旨"},
                        {"id": "full_without_ai", "label": "改为全部提及，不调用AI"}]})]
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_package_essay_feed", lambda *a: pytest.fail("AI ban must not submit a semantic job"))
    result = command._run_agent("general", [{"role": "user", "content": f"只要专门讲宁德时代的小作文，{ban}"}], "", [], workspace="test")
    assert result["status"] == "waiting_input" and result["clarification"]
    assert not result["tasks"] and len(turns) == 2


def test_fast_path_keeps_question_in_done_payload(owner, monkeypatch):
    monkeypatch.setattr(command, "_build_catalog", lambda *a: [])
    monkeypatch.setattr(command, "_run_agent", lambda *a, **k: {
        "reply": "请选择", "steps": [], "tasks": [], "widgets": [],
        "status": "waiting_input", "clarification": {"id": "q-test"}})
    request = SimpleNamespace(headers={}, app=None)
    body = command.CommandChatRequest(messages=[{"role": "user", "content": "下载宁德时代两天的小作文"}])
    result = command._handle_chat_in_workspace(body, request)
    assert result["status"] == "waiting_input"
    assert result["clarification"]["id"] == "q-test"


def test_question_tool_is_available_to_all_specialists():
    for tools in command.AGENT_TOOLS.values():
        assert "ask_user" in {t["function"]["name"] for t in tools}


def test_model_cannot_silently_add_stock_tag_or_analysis_filters():
    result = command._essay_filters_for_user({"query": "宁德时代", "days": 30, "stock": "300750.SZ",
        "tag": "动力电池", "analysis_status": "completed", "sentiment": "bullish"}, "下载宁德时代这两天的小作文")
    assert result == {"query": "宁德时代", "days": 2, "analysis_status": "essay"}


def test_model_preview_keeps_valid_json_and_total_for_long_originals():
    result = {"ok": True, "data": {"items": [{"note": {"title": "宁德时代", "content": "原文" * 20000}}] * 100,
        "total": 237, "page": 1, "page_size": 100, "has_more": True, "filters": {"query": "宁德时代"}}}
    encoded = json.dumps(command._tool_result_for_model("essay_radar", result), ensure_ascii=False)
    decoded = json.loads(encoded)
    assert decoded["data"]["total"] == 237 and decoded["data"]["has_more"]
    assert decoded["data"]["model_sample_count"] == 12
    assert len(encoded) < 14000
    assert len(result["data"]["items"][0]["note"]["content"]) == 40000


def test_failed_submission_does_not_claim_download_completed(owner, monkeypatch):
    service = CommandQuestionService()
    item = service.create(question(), {"kind": "essay", "agent": "research", "messages": [],
        "filters": {"query": "宁德时代", "days": 2}, "download": True}, "test")
    monkeypatch.setattr(command, "_package_essay_feed", lambda *a: {
        "ok": False, "error": "命中0条，没有生成空ZIP", "data": {"count": 0, "expected_total": 0, "complete": True}})
    body = command.CommandChatRequest(messages=[{"role": "user", "content": "下载"}], clarification_answer={
        "question_id": item["id"], "option_id": "full"})
    result = command._resume_question(body, "", [], "test", None)
    assert result["status"] == "failed" and not result["widgets"] and not result["tasks"]
    snapshot = service.snapshot(item["id"], "test")
    assert snapshot["result"] == result


def test_scope_choice_precedes_other_tools_and_keeps_previous_state(owner, monkeypatch):
    before = {"name": "get_task_status", "ok": True, "summary": "已有任务", "_raw": {"task": {"task_id": "existing"}}}
    task = {"task_id": "existing", "poll_path": "/api/v1/data-acquisition/tasks/existing"}
    monkeypatch.setattr(command, "_query_essay_radar", lambda *a: {"ok": True, "data": {"items": [], "total": 25}})
    monkeypatch.setattr(command, "_llm_turn", lambda *a, **k: ("", [
        tool("package_reports", {"keyword": "宁德时代"}, "reports"),
        tool("package_essays", {"query": "宁德时代", "days": 2}, "essays")]))
    result = command._run_agent("research", [{"role": "user", "content": "下载宁德时代小作文和研报"}], "", [], workspace="test",
        resume_state={"steps": [before], "tasks": [task]})
    assert result["status"] == "waiting_input"
    assert result["tasks"] == [task]
    assert result["steps"][0]["summary"] == "已有任务"
    # No report packaging request was executed before the user's selection.
    record = CommandQuestionService().claim(result["clarification"]["id"], "full", None, "test")
    assert record["continuation"]["prior_state"]["multiple_tools"] is True
    assert record["continuation"]["prior_state"]["tasks"] == [task]


def test_question_status_endpoint_is_owner_bound_and_not_publicly_cacheable(owner):
    item = CommandQuestionService().create(question(), {}, "test")
    app = FastAPI()
    app.include_router(command.router)
    @app.middleware("http")
    async def identity(request, next_call):
        request.state.user_id = int(request.headers.get("x-test-owner", "17"))
        return await next_call(request)
    client = TestClient(app)
    url = f"/clarifications/{item['id']}?workspace_id=test"
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["status"] == "waiting_input"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["cdn-cache-control"] == "no-store"
    assert client.get(url, headers={"x-test-owner": "18"}).status_code == 404
    assert client.get(url.replace("test", "different")).status_code == 404
