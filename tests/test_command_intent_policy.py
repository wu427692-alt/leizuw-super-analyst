"""The chat entry preserves intent and recovery, never keyword-executes work.

These deterministic tests exercise orchestration with scripted model turns.
They do not claim that a live model passed the same language-understanding cases.
"""
from types import SimpleNamespace
import json

import pytest

from api.v1.endpoints import command
from src import config
from src.request_identity import reset_current_user_id, set_current_user_id


@pytest.fixture(autouse=True)
def offline_chat(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("No live model, external API, or legacy shortcut may run")

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db.sqlite")))
    monkeypatch.setattr(command, "_build_catalog", lambda app: [])
    monkeypatch.setattr(command, "_wait_if_paused", lambda *args: None)
    for name in (
        "_llm_turn", "_complete_stream", "_internal_call", "_deepseek_search",
        "_is_dag_request", "_handle_screen_then_news", "_plan_parallel", "_route",
        "_stock_history_payload", "_meeting_download_payload", "_meeting_text_payload",
        "_start_transcription",
    ):
        monkeypatch.setattr(command, name, unexpected)
    identity = set_current_user_id(29)
    try:
        yield
    finally:
        reset_current_user_id(identity)


def chat(messages, **kwargs):
    request = SimpleNamespace(headers={"cookie": "owner-cookie"}, app=None)
    body = command.CommandChatRequest(messages=messages, workspace_id="intent", **kwargs)
    return command._handle_chat_in_workspace(body, request)


def tool(name, args, ident):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False),
    }}


@pytest.mark.parametrize("message", [
    "不要下载录音，只找宁德时代小作文",
    "不要小作文，只查公告",
    "把宁德时代小作文和研报都下载",
    "宁德时代小作文与走势有什么关系",
    "把宁德时代小作文中的业绩预期做一份报告",
    "宁德时代最近走势",
    "下载宁德时代这两天的小作文",
    "宁德时代所有提到的都要，不止20篇",
    "宁德时代和比亚迪分别查，同样的时间范围",
    "先选3只股票，再查各自新闻",
    "查宁德时代和比亚迪的新闻并比较，不下载",
    "只查标题包含宁德时代的，今天全部导出",
    "我不知道怎么选，你帮我把研究范围聚焦一下",
    "只统计宁德时代命中数，先不要下载",
    "没有数据就重试原条件，不要换成行业新闻",
    "下载华懋科技录音，不转写，不分析",
    "看看华懋科技半年报交流会有哪些录音",
    "只要现成的文字稿，不启动新的转写",
    "我只要录音转写，不要生成AI纪要",
    "两个范围都要，先给我看数量",
])
def test_every_request_reaches_context_capable_agent_without_hard_routing(message, monkeypatch):
    calls = []
    def run(agent, messages, cookie, catalog, emit, session_id, **kwargs):
        calls.append((agent, messages, kwargs))
        return {"reply": "已理解目标", "steps": [], "tasks": [], "widgets": []}
    monkeypatch.setattr(command, "_run_agent", run)

    result = chat([{"role": "user", "content": message}])

    assert result["reply"] == "已理解目标"
    assert len(calls) == 1
    agent, messages, kwargs = calls[0]
    assert agent == "general"
    assert messages == [{"role": "user", "content": message}]
    assert kwargs == {"workspace": "intent"}


@pytest.mark.parametrize("followup", [
    "同样条件再看比亚迪", "就标题吧", "继续，全部提及就行",
    "日期不变，改成只看未分析的", "也要研报，但不要重复已提交的任务",
])
def test_followup_retains_original_goal_and_prior_constraints(followup, monkeypatch):
    history = [
        {"role": "user", "content": "下载宁德时代10月1日至15日的小作文，不要录音"},
        {"role": "assistant", "content": "已召回，标题匹配41条、全文提及237条，等待范围确认"},
        {"role": "user", "content": followup},
    ]
    def run(agent, messages, *args, **kwargs):
        assert messages == history
        assert "forced_tool" not in kwargs and "max_tool_rounds" not in kwargs
        return {"reply": "继续原任务", "steps": [], "tasks": [], "widgets": []}
    monkeypatch.setattr(command, "_run_agent", run)
    assert chat(history)["reply"] == "继续原任务"


def test_first_tool_parameter_failure_can_be_corrected_before_final_answer(monkeypatch):
    model_turns, parameters = [], []
    def call(catalog, name, params, agent, cookie):
        parameters.append(params)
        if len(parameters) == 1:
            return {"ok": False, "status": 422, "error": "参数需放在query下"}
        return {"ok": True, "data": {"items": [{"title": "宁德时代半年报"}]}}
    def llm(conversation, tools, *args, **kwargs):
        model_turns.append(conversation)
        assert tools is not None
        if len(model_turns) == 1:
            return "", [tool("call_capability", {"name": "test_reports", "params": {"company": "宁德时代"}}, "bad")]
        if len(model_turns) == 2:
            previous = json.loads(conversation[-1]["content"])
            assert previous["status"] == 422
            return "", [tool("call_capability", {"name": "test_reports", "params": {"query": {"company": "宁德时代"}}}, "corrected")]
        assert len(model_turns) == 3
        assert json.loads(conversation[-1]["content"])["ok"] is True
        return "已修正参数并取得研报", []
    monkeypatch.setattr(command, "_call_capability", call)
    monkeypatch.setattr(command, "_llm_turn", llm)

    result = chat([{"role": "user", "content": "找宁德时代研报"}])

    assert result["reply"] == "已修正参数并取得研报"
    assert [s["ok"] for s in result["steps"]] == [False, True]
    assert parameters[1] == {"query": {"company": "宁德时代"}}
    assert len(model_turns) == 3


def test_first_tool_failure_can_lead_to_clickable_clarification(monkeypatch):
    turns = []
    def llm(conversation, tools, *args, **kwargs):
        turns.append(conversation)
        assert tools is not None
        if len(turns) == 1:
            return "", [tool("call_capability", {"name": "lookup_company"}, "lookup")]
        assert json.loads(conversation[-1]["content"])["ok"] is False
        return "", [tool("ask_user", {
            "question": "你指哪家国仪公司？",
            "context": "公司名称存在歧义，确认后继续原来的公告下载任务。",
            "options": [{"id": "quantum", "label": "国仪量子"}, {"id": "other", "label": "其他公司"}],
            "allow_custom": True,
        }, "clarify")]
    monkeypatch.setattr(command, "_llm_turn", llm)
    monkeypatch.setattr(command, "_call_capability", lambda *args: {"ok": False, "error": "公司名称不唯一"})

    result = chat([{"role": "user", "content": "下载国仪公司公告"}])

    assert result["status"] == "waiting_input"
    assert result["clarification"]["question"] == "你指哪家国仪公司？"
    assert len(turns) == 2
    assert result["steps"][0]["ok"] is False


def test_unified_entry_retains_artifacts_and_pending_question(monkeypatch):
    payload = {"reply": "请确认", "steps": [], "tasks": [{"task_id": "existing"}],
               "widgets": [], "artifacts": [{"name": "existing-result"}],
               "status": "waiting_input", "clarification": {"id": "pending"}}
    monkeypatch.setattr(command, "_run_agent", lambda *args, **kwargs: payload)
    result = chat([{"role": "user", "content": "继续"}])
    for key, value in payload.items():
        assert result[key] == value


def test_explicit_question_answer_uses_saved_continuation_not_new_planning(monkeypatch):
    monkeypatch.setattr(command, "_run_agent", lambda *args, **kwargs: pytest.fail("Answer must resume saved work"))
    monkeypatch.setattr(command, "_resume_question", lambda *args: {"reply": "已恢复原任务"})
    result = chat([{"role": "user", "content": "全文"}], clarification_answer={
        "question_id": "q-" + "a" * 32, "option_id": "full",
    })
    assert result == {"reply": "已恢复原任务"}
