"""Bad tool parameters remain recoverable and never broaden a real query."""
import json
from types import SimpleNamespace

import pytest

from api.v1.endpoints import command
from src import config
from src.request_identity import reset_current_user_id, set_current_user_id


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db.sqlite")))
    monkeypatch.setattr(command, "_internal_call", lambda *a, **k: pytest.fail("must not query with bad parameters"))
    monkeypatch.setattr(command, "_deepseek_search", lambda *a, **k: pytest.fail("cannot substitute a website for originals"))
    token = set_current_user_id(31)
    try:
        yield
    finally:
        reset_current_user_id(token)


def call(name, arguments, ident):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.mark.parametrize("arguments", ["{", "[]", '"not an object"'])
def test_invalid_json_does_not_run_default_report_pack_and_can_ask_user(arguments, monkeypatch):
    rounds = []
    def model(conversation, *a, **k):
        rounds.append(conversation)
        if len(rounds) == 1:
            return "", [call("package_reports", arguments, "bad")]
        assert json.loads(conversation[-1]["content"])["needs_argument_correction"]
        return "", [call("ask_user", json.dumps({"question": "需要哪家公司的研报？", "options": [
            {"id": "catl", "label": "宁德时代"}, {"id": "byd", "label": "比亚迪"}]}), "ask")]
    monkeypatch.setattr(command, "_llm_turn", model)
    result = command._run_agent("general", [{"role": "user", "content": "帮我下载研报"}], "", [], workspace="recovery")
    assert result["status"] == "waiting_input"
    assert len(rounds) == 2 and result["steps"][0]["ok"] is False


@pytest.mark.parametrize("name,dates", [
    ("essay_radar", {"start_date": "2026-02-30", "end_date": "2026-03-01"}),
    ("package_essays", {"start_date": "2026-10-15", "end_date": "2026-10-01"}),
])
def test_invalid_dates_return_to_agent_instead_of_http_500_or_unfiltered_search(name, dates, monkeypatch):
    count = 0
    def model(conversation, *a, **k):
        nonlocal count
        count += 1
        if count == 1:
            return "", [call(name, json.dumps({"query": "宁德时代", "query_scope": "full", **dates}), "dates")]
        assert json.loads(conversation[-1]["content"])["needs_argument_correction"]
        return "请确认日期范围，我会保留公司与全文条件后继续。", []
    monkeypatch.setattr(command, "_llm_turn", model)
    result = command._run_agent("general", [{"role": "user", "content": "宁德时代全文提及的资料"}], "", [])
    assert count == 2 and result["steps"][0]["ok"] is False
    assert "确认日期" in result["reply"]


def test_essay_recovery_keeps_dates_and_does_not_relabel_public_news(monkeypatch):
    observed = []
    def query(args, cookie):
        observed.append(args)
        return {"ok": True, "data": {"items": [], "total": 0, "filters": args}}
    monkeypatch.setattr(command, "_query_essay_radar", query)
    result = command._resolve_data("essay", "宁德时代", "", 365, 20, "", essay_filters={
        "start_date": "2026-10-01", "end_date": "2026-10-15", "query_scope": "title"})
    assert result["source_kind"] == "institutional_originals"
    assert result["data"]["total"] == 0
    assert observed[0]["start_date"] == "2026-10-01" and observed[0]["end_date"] == "2026-10-15"
    assert observed[0]["query_scope"] == "title"


def test_older_confirmed_dates_cannot_override_new_relative_window():
    selection = {"query": "宁德时代", "start_date": "2026-10-01", "end_date": "2026-10-15", "query_scope": "title"}
    assert command._essay_selection_for_target({"query": "宁德时代", "days": 2}, {"essay_selection": selection}) is None
    assert command._essay_selection_for_target({"query": "宁德时代", "days": 0}, {"essay_selection": selection}) is None
    assert command._essay_selection_for_target({**selection, "days": 25}, {"essay_selection": selection}) == selection


def test_relative_confirmation_cannot_override_new_fixed_range():
    selection = {"query": "宁德时代", "days": 2, "query_scope": "full"}
    assert command._essay_selection_for_target({"query": "宁德时代", "start_date": "2026-10-01", "end_date": "2026-10-15"}, {"essay_selection": selection}) is None
