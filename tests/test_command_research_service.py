"""Offline pure contracts: exact originals, scoped recalls and partial sources.

Fake sources verify the service contract, not real model tool selection.
"""
import hashlib

import pytest

from src.services.command_research_service import (
    build_research_context, format_essay_read, prepare_research_targets,
)


INDEX = [
    ["300001.SZ", "300001", "甲方科技", "jiafang", "jf", ["JFA", "甲方"], "CN", "stock", True],
    ["600002.SH", "600002", "乙方股份", "yifang", "yf", ["乙方"], "CN", "stock", True],
    ["600003.SH", "600003", "其他公司", "qita", "qt", ["旧任务公司"], "CN", "stock", True],
]


def note(identifier="n1", text="甲方科技利润率承压。", **kwargs):
    return {"topic_id": identifier, "title": "实际机构原文", "content": text,
            "group_name": "研究组", "author_name": "研究员", "created_at": "2026-10-22T01:00:00Z", **kwargs}


def empty_forum(_):
    return {"ok": True, "data": {"status": "empty", "items": [], "total": 0}}


@pytest.mark.parametrize("query", ["甲方科技今天为什么大跌", "查甲方的需求", "jfa利润怎么样", "300001今天走势", "300001.SZ需求"])
def test_current_name_code_alias_resolve_from_verified_catalog_without_guessed_ticker(query):
    result = prepare_research_targets(query, records=INDEX)
    assert result["symbols"] == ["300001.SZ"]
    assert "甲方科技" in result["local_queries"]
    assert not result["need_refinement"]


def test_current_multi_company_entities_survive_and_prior_task_terms_do_not():
    result = prepare_research_targets("对比甲方科技和乙方股份的光模块需求", ["光模块", "旧任务公司"], records=INDEX)
    assert set(result["symbols"]) == {"300001.SZ", "600002.SH"}
    assert result["local_queries"][0] == "光模块"
    assert "旧任务公司" not in result["local_queries"]
    assert result["rejected_local_queries"] == ["旧任务公司"]


def test_unknown_question_is_preserved_exactly_not_empty_or_all_library():
    query = "尚未登记的产品为什么缺货？"
    result = prepare_research_targets(query, ["旧任务公司"], symbol="600003.SH", records=INDEX)
    assert result["local_queries"] == [query] and result["targets"] == []
    assert result["need_refinement"]


def test_known_company_with_conflicting_explicit_symbol_does_not_choose_one():
    result = prepare_research_targets("甲方科技今天下跌", symbol="600002.SH", records=INDEX)
    assert result["targets"] == [] and result["need_refinement"]


def test_ascii_alias_is_not_matched_inside_another_word():
    result = prepare_research_targets("NOTJFA股票是什么意思", records=INDEX)
    assert result["targets"] == []


def test_ambiguous_alias_does_not_rank_and_guess_a_symbol():
    rows = [*INDEX, ["600004.SH", "600004", "甲方材料", "", "", ["甲方"], "CN", "stock", True]]
    result = prepare_research_targets("甲方今天怎么样", records=rows)
    assert not result["targets"] and result["need_refinement"]
    assert result["entities"][0]["status"] == "ambiguous"


def test_four_query_budget_is_explicit_not_an_export_limit():
    result = prepare_research_targets("甲方科技乙方股份，光模块、硅光、订单、毛利", ["光模块", "硅光", "订单", "毛利"], records=INDEX)
    assert len(result["local_queries"]) == 4
    assert result["overflow"]["queries_omitted"] >= 2 and result["overflow"]["not_export_limit"]


def test_complete_read_preserves_unicode_newlines_and_full_body_hash():
    original = "公司\u00a0A\n盈利\n\n😃下一段。"
    result = format_essay_read(note(text=original), "n1")
    assert result["text"] == original and result["read_complete_current_response"]
    assert result["span"] == {"char_start": 0, "char_end": len(original)}
    assert result["content_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert result["content_kind"] == "full_text" and result["attachments_read"] is False


def test_long_original_pages_reconstruct_exact_body_and_tail_is_not_claimed_as_full_read():
    original = "前文" * 4000 + "真正解释毛利变化的末段。"
    parts, offset = [], 0
    while True:
        result = format_essay_read(note(text=original), "n1", offset=offset, max_chars=6000)
        parts.append(result["text"])
        assert original[result["span"]["char_start"]:result["span"]["char_end"]] == result["text"]
        assert not result["read_complete_current_response"]
        if result["next_offset"] is None:
            assert result["reached_end"]
            break
        offset = result["next_offset"]
    assert "".join(parts) == original


@pytest.mark.parametrize("offset,max_chars", [(True, 10), (-1, 10), (1.0, 10), (0, False), (0, 0), (0, 8001), (99, 1)])
def test_invalid_offsets_budgets_fail_safely(offset, max_chars):
    result = format_essay_read(note(text="原文"), "n1", offset, max_chars)
    assert not result["ok"] and result["status"] == "invalid_span"


def test_summary_or_prior_analysis_never_substitutes_for_missing_original():
    result = format_essay_read({"topic_id": "n1", "summary": "公司利润增长"}, "n1")
    assert result["status"] == "missing_original" and not result["read_complete_current_response"]


def test_topic_mismatch_prevents_reading_wrong_document():
    assert format_essay_read(note(), "n2")["status"] == "topic_mismatch"


@pytest.mark.parametrize("body", ["", "「文件」", "「录音」", "公司会议.pdf"])
def test_only_proven_attachment_placeholder_is_source_index_not_analyzed_text(body):
    result = format_essay_read(note(text=body, files=[{"name": "公司会议.pdf"}]), "n1")
    assert result["content_kind"] == "file_index" and result["status"] == "source_only"
    assert not result["read_complete_current_response"] and not result["attachments_read"]


def test_real_short_body_with_files_remains_readable_original():
    result = format_essay_read(note(text="公司订单增长，毛利承压。", files=[{"name": "原稿.pdf"}]), "n1")
    assert result["content_kind"] == "full_text" and result["read_complete_current_response"]


def test_recall_is_full_bounded_preview_with_true_total_and_alias_union_deduplication():
    calls = []
    def search(filters):
        calls.append(filters)
        return {"ok": True, "data": {"items": [{"note": note()}], "total": 51, "has_more": True, "filters": filters}}
    result = build_research_context({"query": "JFA今天下跌原因"}, essay_search=search, forum_search=empty_forum, records=INDEX)
    assert {call["query"] for call in calls} == {"甲方科技", "JFA"}
    assert all(call["query_scope"] == "full" and call["page"] == 1 and call["page_size"] == 6 for call in calls)
    assert result["institutional"]["retrieved"] == 1
    assert all(item["total"] == 51 and item["has_more"] for item in result["institutional"]["total_by_query"])
    assert result["preview_only"] and result["overflow"]["not_export_limit"]


def test_focus_terms_locate_exact_late_body_evidence_without_changing_recall_queries():
    original = "甲方科技" + "无关前文。" * 800 + "毛利率下降导致利润承压，这才是关键原因。"
    calls = []
    def search(filters):
        calls.append(filters)
        return {"items": [note(text=original)], "total": 1}
    result = build_research_context({"query": "甲方科技为什么大跌", "focus_terms": ["毛利率", "利润承压"]},
                                    essay_search=search, forum_search=empty_forum, records=INDEX)
    item = result["institutional"]["items"][0]
    assert "毛利率下降" in item["excerpt"] and item["excerpt_start"] > 3000
    assert item["excerpt"] == original[item["excerpt_start"]:item["excerpt_end"]]
    assert len(item["excerpt"]) <= 900 and not item["read_complete_current_response"]
    assert all(call["query"] == "甲方科技" for call in calls)


def test_literal_question_anchor_locates_late_evidence_without_explicit_focus_terms():
    original = "甲方科技介绍。" + "其他不相关段落。" * 500 + "毛利率下降的原因在这一段。"
    seen = []
    def search(filters):
        seen.append(filters["query"])
        return {"items": [note(text=original)], "total": 1}
    result = build_research_context({"query": "甲方科技毛利下降为什么"}, essay_search=search,
                                    forum_search=empty_forum, records=INDEX)
    item = result["institutional"]["items"][0]
    assert "毛利率下降" in item["excerpt"] and item["excerpt_start"] > 3000
    assert seen == ["甲方科技"]


def test_failed_source_does_not_erase_other_source_or_turn_error_into_zero():
    def search(filters):
        if filters["query"] == "甲方科技":
            raise TimeoutError("secret-key-do-not-output")
        return {"items": [note()], "total": 9}
    result = build_research_context({"query": "甲方科技JFA新闻"}, essay_search=search, forum_search=empty_forum, records=INDEX)
    by_query = {item["query"]: item for item in result["institutional"]["total_by_query"]}
    assert by_query["甲方科技"]["total"] is None and by_query["甲方科技"]["status"] == "error"
    assert by_query["JFA"]["total"] == 9 and result["institutional"]["status"] == "partial"
    assert result["institutional"]["items"] and "secret-key" not in str(result)


@pytest.mark.parametrize("response,status", [
    ({"ok": False, "status": 403, "error": "private upstream body"}, "403"),
    ({"ok": True, "data": {"items": "broken", "total": 0}}, "invalid_response"),
    ({"ok": True, "data": {"items": ["not-a-note"], "total": 1}}, "invalid_response"),
    ({"ok": True, "data": {"items": [{"title": "缺标识资料"}], "total": 1}}, "invalid_response"),
    ({"items": [], "total": 0, "filters": {"query": ""}}, "filter_mismatch"),
])
def test_source_errors_bad_schema_and_dropped_filters_are_not_empty_success(response, status):
    result = build_research_context({"query": "甲方科技新闻"}, essay_search=lambda _: response, forum_search=empty_forum, records=INDEX)
    receipt = result["institutional"]["total_by_query"][0]
    assert receipt["status"] == status and receipt["total"] is None
    assert result["institutional"]["status"] == "error"


def test_file_indexes_are_separate_and_real_text_with_file_not_excluded():
    rows = [note("index", "「文件」", files=[{"name": "会议.pdf"}]),
            note("text", "真实经营文字。", files=[{"name": "资料.pdf"}])]
    result = build_research_context({"query": "甲方科技资料"}, essay_search=lambda _: {"items": rows, "total": 2},
                                    forum_search=empty_forum, records=INDEX)
    assert [item["topic_id"] for item in result["institutional"]["source_indexes"]] == ["index"]
    assert [item["topic_id"] for item in result["institutional"]["items"]] == ["text"]
    assert not result["institutional"]["source_indexes"][0]["read_complete_current_response"]


def test_context_budget_not_match_limit_and_each_unique_company_forum_only_once():
    forum_calls = []
    def search(filters):
        return {"items": [note(filters["query"] + str(index)) for index in range(6)], "total": 123}
    def forum(filters):
        forum_calls.append(filters)
        return {"ok": True, "data": {"status": "ok", "items": [{"content": "公开帖摘录" * 500, "author": "作者", "raw": "private"}] * 9, "total": 200}}
    result = build_research_context({"query": "甲方科技和乙方股份JFA今天如何"}, essay_search=search, forum_search=forum, records=INDEX)
    assert len(result["institutional"]["items"]) == 8 and result["overflow"]["institutional_items_omitted"] > 0
    assert {call["symbol"] for call in forum_calls} == {"300001.SZ", "600002.SH"} and len(forum_calls) == 2
    for source in result["forums"]:
        assert source["data"]["total"] == 200 and len(source["data"]["items"]) == 4
        assert all(len(item["excerpt"]) <= 600 and "raw" not in item for item in source["data"]["items"])


def test_no_empty_query_or_history_fallback_ever_reaches_sources():
    result = build_research_context({"query": "", "history": ["旧任务公司"]},
                                    essay_search=lambda _: pytest.fail("unfiltered essay request"),
                                    forum_search=lambda _: pytest.fail("unfiltered forum request"), records=INDEX)
    assert result["local_queries"] == [] and result["institutional"]["status"] == "needs_refinement"


def test_forum_receives_exact_dates_symbol_without_redundant_company_keyword_and_keeps_excerpt():
    calls = []
    def forum(filters):
        calls.append(filters)
        return {"ok": True, "data": {"items": [{"excerpt": "真实公开帖已有节选", "author": "作者"}], "total": 1}}
    result = build_research_context({"query": "甲方科技历史消息", "start_date": "2026-10-01", "end_date": "2026-10-15"},
                                    essay_search=lambda _: {"items": [], "total": 0}, forum_search=forum, records=INDEX)
    assert calls[0]["symbol"] == "300001.SZ" and calls[0]["query"] == ""
    assert (calls[0]["start_date"], calls[0]["end_date"]) == ("2026-10-01", "2026-10-15")
    assert result["forums"][0]["data"]["items"][0]["excerpt"] == "真实公开帖已有节选"


def test_signed_attachment_urls_and_raw_payload_are_not_forwarded_in_read_metadata():
    row = note(files=[{"name": "原文件.pdf", "url": "https://files.invalid/?signed=secret"}], raw_payload={"credential": "secret"})
    result = format_essay_read(row, "n1")
    assert result["attachment_count"] == 1 and "signed=secret" not in str(result)
    assert "credential" not in str(result)


def test_conflicting_code_keeps_grounded_company_keyword_and_exposes_repair_context():
    essay_calls, forum_calls = [], []
    def essay(filters):
        essay_calls.append(filters)
        return {"items": [note()], "total": 1}
    def forum(filters):
        forum_calls.append(filters)
        return {"ok": True, "data": {"items": [], "total": 0}}
    result = build_research_context({"query": "甲方科技今天下跌", "symbol": "600002.SH", "local_queries": ["旧任务公司"]},
                                    essay_search=essay, forum_search=forum, records=INDEX)
    assert [item["query"] for item in essay_calls] == ["甲方科技"]
    assert all(not item.get("symbol") for item in forum_calls)
    assert result["entities"][0]["status"] == "conflict" and result["need_refinement"]
    assert result["rejected_local_queries"] == ["旧任务公司"]
    assert result["resolution_status"] == "keyword_only"
    assert result["institutional"]["items"][0]["detail_url"].endswith("/n1")
