"""Count constraints at the real Agent tool boundary, without model/network/DB."""
import json
from types import SimpleNamespace

import pytest

from api.v1.endpoints import command
from src.services import essay_export_service


@pytest.fixture()
def agent_boundary(monkeypatch):
    calls = []

    class Service:
        def create(self, filters, workspace=None, requested_limit=None):
            calls.append({"filters": filters, "requested_limit": requested_limit, "workspace": workspace})
            return {"task_id": "essay-export-" + f"{len(calls):032x}", "status": "queued",
                    "requested_limit": requested_limit, "progress": None}

        def start_worker(self):
            pass

    monkeypatch.setattr(essay_export_service, "get_essay_export_service", lambda: Service())
    monkeypatch.setattr(command, "_internal_call", lambda *args, **kwargs: pytest.fail("No HTTP call allowed"))

    def execute(texts, tool_args, *, state=None):
        call_list = tool_args if isinstance(tool_args, list) else [tool_args]
        tools = [{"id": f"call-{index}", "type": "function", "function": {
            "name": "package_essays", "arguments": json.dumps(args, ensure_ascii=False),
        }} for index, args in enumerate(call_list)]
        turns = iter([("", tools), ("已提交后台任务。", [])])
        monkeypatch.setattr(command, "_llm_turn", lambda *args, **kwargs: next(turns))
        result = command._run_agent("research", [{"role": "user", "content": text} for text in texts],
                                    "", [], stream_tokens=False, workspace="count-test", resume_state=state)
        return SimpleNamespace(result=result, calls=calls)

    return execute


def test_custom_original_only_answer_keeps_previous_explicit_count(agent_boundary):
    result = agent_boundary([
        "只下载宁德时代最近20篇标题匹配的小作文",
        "关于原任务的补充要求：标题匹配；只要原文，不做AI分析",
    ], {"query": "宁德时代", "query_scope": "title", "limit": 20},
        state={"original_goal": "只下载宁德时代最近20篇标题匹配的小作文"})
    assert len(result.calls) == 1
    assert result.calls[0]["requested_limit"] == 20
    assert result.result["status"] == "submitted"


@pytest.mark.parametrize("words,count", [("二十", 20), ("一百", 100)])
def test_explicit_chinese_count_is_not_discarded_as_a_model_default(agent_boundary, words, count):
    result = agent_boundary([f"只下载宁德时代{words}篇标题匹配的小作文"],
                            {"query": "宁德时代", "query_scope": "title", "limit": count})
    assert len(result.calls) == 1
    assert result.calls[0]["requested_limit"] == count


@pytest.mark.parametrize("latest", [
    "那就全部下载，其他条件不变",
    "宁德时代相关的都要，不用限制篇数",
    "下载宁德时代所有匹配原文",
])
def test_latest_all_download_cancels_previous_twenty_and_saved_limit(agent_boundary, latest):
    result = agent_boundary([
        "只下载宁德时代最近20篇标题匹配的小作文",
        latest,
    ], {"query": "宁德时代", "query_scope": "title", "limit": 20},
        state={"essay_selection": {"query": "宁德时代", "query_scope": "title", "limit": 20}})
    assert len(result.calls) == 1
    assert result.calls[0]["requested_limit"] is None
    assert result.calls[0]["filters"]["query_scope"] == "title"


@pytest.mark.parametrize("text", [
    "按标题匹配下载宁德时代小作文，页面显示20条",
    "我已看20篇；按标题匹配下载宁德时代小作文",
    "按标题匹配下载宁德时代小作文，至少20篇",
    "按标题匹配下载宁德时代小作文，不止20篇",
    "按标题匹配下载宁德时代小作文，不要只下载20篇",
    "按标题匹配下载宁德时代小作文",
    "库里总共237篇小作文，下载标题匹配的宁德时代原文",
    "这20篇看起来无关，重新按标题匹配下载宁德时代的小作文",
    "不应该只有20篇，按标题匹配下载宁德时代小作文",
    "我已经看了20篇；按标题匹配下载宁德时代小作文",
])
def test_factual_preview_or_minimum_counts_and_model_defaults_do_not_limit(text, agent_boundary):
    result = agent_boundary([text], {"query": "宁德时代", "query_scope": "title", "limit": 20})
    assert len(result.calls) == 1
    assert result.calls[0]["requested_limit"] is None


def test_saved_title_scope_does_not_overwrite_new_explicit_count(agent_boundary):
    result = agent_boundary([
        "按标题匹配下载宁德时代20篇小作文",
        "改为宁德时代50篇，其他条件不变",
    ], {"query": "宁德时代", "query_scope": "title", "limit": 20},
        state={"essay_selection": {"query": "宁德时代", "query_scope": "title", "limit": 20}})
    assert len(result.calls) == 1
    assert result.calls[0]["requested_limit"] == 50
    assert result.calls[0]["filters"]["query_scope"] == "title"


def test_multiple_companies_counts_need_target_specific_exact_evidence(agent_boundary):
    text = "宁德时代只要20篇，华懋科技只要30篇；均按标题匹配下载小作文"
    result = agent_boundary([text], [
        {"query": "宁德时代", "query_scope": "title", "limit": 999, "limit_evidence": "宁德时代只要20篇"},
        {"query": "华懋科技", "query_scope": "title", "limit": 999, "limit_evidence": "华懋科技只要30篇"},
    ])
    assert [(call["filters"]["query"], call["requested_limit"]) for call in result.calls] == [
        ("宁德时代", 20), ("华懋科技", 30),
    ]


def test_one_company_count_never_caps_another_company_explicit_all_request(agent_boundary):
    result = agent_boundary(["宁德时代只要20篇，华懋科技全部下载；都按标题匹配"], [
        {"query": "宁德时代", "query_scope": "title", "limit": 20, "limit_evidence": "宁德时代只要20篇"},
        {"query": "华懋科技", "query_scope": "title"},
    ])
    assert [(call["filters"]["query"], call["requested_limit"]) for call in result.calls] == [
        ("宁德时代", 20), ("华懋科技", None),
    ]


@pytest.mark.parametrize("evidence", [None, "华懋科技只要30篇", "宁德时代只要200篇"])
def test_ambiguous_wrong_company_or_forged_evidence_never_submits(agent_boundary, evidence):
    args = {"query": "宁德时代", "query_scope": "title", "limit": 20}
    if evidence is not None:
        args["limit_evidence"] = evidence
    result = agent_boundary(["宁德时代只要20篇，华懋科技只要30篇；均按标题匹配下载小作文"], args)
    assert not result.calls
    assert result.result["steps"] and not result.result["steps"][0]["ok"]


@pytest.mark.parametrize("args", [
    {"stock": "300750.SZ", "query_scope": "full"},
    {"keyword": "宁德时代", "query_scope": "full"},
])
def test_missing_query_or_misspelled_target_never_silently_exports_the_whole_library(agent_boundary, args):
    result = agent_boundary(["下载所有提及宁德时代的小作文"], args)
    assert not result.calls or all(call["filters"].get("query") == "宁德时代" for call in result.calls)


def test_explicit_whole_library_request_may_intentionally_have_no_keyword(agent_boundary):
    result = agent_boundary(["下载整个小作文库，原文全部，日期不限"], {"query_scope": "full"})
    assert len(result.calls) == 1
    assert result.calls[0]["requested_limit"] is None
    assert not result.calls[0]["filters"].get("query")
