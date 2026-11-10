"""Offline side-effect safety for actual command tool execution branches.

Scripted model output selects a tool branch; it does not establish live model
intent understanding. All storage, audio and packaging services are mocked.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from api.v1.endpoints import command
from src import config
from src.request_identity import reset_current_user_id, set_current_user_id


@pytest.fixture(autouse=True)
def offline_owned_request(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No model, upstream, real transcription or real export may execute")

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(database_path=str(tmp_path / "db.sqlite")))
    monkeypatch.setattr(command, "_wait_if_paused", lambda *args: None)
    for name in ("_llm_turn", "_complete_stream", "_internal_call", "_deepseek_search",
                 "_meeting_search", "_meeting_transcript", "_export_meeting_text", "_start_transcription"):
        monkeypatch.setattr(command, name, forbidden)
    identity = set_current_user_id(74)
    try:
        yield
    finally:
        reset_current_user_id(identity)


def tool(name, args, ident):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False),
    }}


def run(monkeypatch, calls, message, *, history=None, resume_state=None):
    turns = []

    def model(conversation, tools, *args, **kwargs):
        turns.append(deepcopy(conversation))
        return ("", deepcopy(calls)) if len(turns) == 1 else ("contract completed", [])

    monkeypatch.setattr(command, "_llm_turn", model)
    result = command._run_agent(
        "general", history or [{"role": "user", "content": message}], "owned-cookie", [],
        workspace="task-safety", resume_state=resume_state,
    )
    outputs = [json.loads(item["content"]) for item in turns[-1] if item.get("role") == "tool"]
    return result, outputs


def audio_fixture(monkeypatch, *, transcript="", present=True):
    audio = {"topic_id": "topic-a", "file_id": "file-a", "name": "华懋科技交流.mp3",
             "note_title": "华懋科技业绩交流", "download_url": "/private/audio-a", "size": 100,
             "duration_seconds": 1800, "transcribed": bool(transcript)}
    memo = {"title": "华懋科技AI纪要", "content": "NOT-TRANSCRIPT-仅AI概括，不能当逐字稿。" * 20}
    lookups, transcript_reads, exported = [], [], []

    def search(title, cookie, limit):
        lookups.append((title, cookie, limit))
        return ([deepcopy(audio)] if present else []), [deepcopy(memo)]

    def existing(item, cookie):
        transcript_reads.append((deepcopy(item), cookie))
        return transcript

    def export(note, text, workspace=None):
        exported.append((deepcopy(note), text, workspace))
        return {"filename": "existing-transcript.md", "size": len(text),
                "url": "/private/existing-transcript.md", "title": "华懋科技逐字稿"}

    monkeypatch.setattr(command, "_meeting_search", search)
    monkeypatch.setattr(command, "_meeting_transcript", existing)
    monkeypatch.setattr(command, "_export_meeting_text", export)
    return audio, lookups, transcript_reads, exported


@pytest.mark.parametrize("args", [{"title": "华懋科技"}, {"title": "华懋科技", "with_text": False}])
def test_audio_download_defaults_to_audio_only_without_read_or_new_transcription(args, monkeypatch):
    _, lookups, reads, exported = audio_fixture(monkeypatch)
    result, outputs = run(monkeypatch, [tool("meeting_download", args, "audio")], "下载华懋科技录音")

    assert len(lookups) == 1 and not reads and not exported
    assert result["tasks"] == []
    assert outputs[0]["ok"] and outputs[0]["data"]["transcription"] is None
    assert [item["title"] for item in outputs[0]["data"]["files"]] == ["会议录音"]
    assert [item["url"] for item in result["widgets"]] == ["/private/audio-a"]


@pytest.mark.parametrize("transcript", ["", "TRANSCRIPT-原始说话逐字文本"])
def test_audio_with_text_exports_only_existing_transcript_never_starts_job(transcript, monkeypatch):
    _, _, reads, exported = audio_fixture(monkeypatch, transcript=transcript)
    result, outputs = run(monkeypatch, [tool("meeting_download", {
        "title": "华懋科技", "with_text": True,
    }, "audio-with-text")], "录音下载，也带上已有文字稿")

    assert len(reads) == 1 and len(exported) == bool(transcript)
    data = outputs[0]["data"]
    assert outputs[0]["ok"] and data["transcription"] is None and not result["tasks"]
    assert len(data["files"]) == (2 if transcript else 1)
    assert data["transcript_available"] is bool(transcript)
    if transcript:
        assert exported[0][1] == transcript and "NOT-TRANSCRIPT" not in exported[0][1]


def test_existing_memo_body_does_not_count_as_raw_transcript(monkeypatch):
    _, _, reads, exported = audio_fixture(monkeypatch, transcript="")
    result, outputs = run(monkeypatch, [tool("meeting_text", {"title": "华懋科技"}, "text")],
                          "只给我现成的录音逐字稿，不要纪要，不要新转写")

    assert len(reads) == 1 and not exported
    assert not outputs[0]["ok"] and outputs[0]["data"]["transcript_available"] is False
    assert outputs[0]["data"]["can_transcribe"] is True
    assert result["widgets"] == [] and result["tasks"] == []


@pytest.mark.parametrize("message", [
    "给我现成的文字稿", "不要转写，只要已有资料",
    "不启动新的转写任务", "我只要已经转写过的原文",
])
def test_model_flag_cannot_authorise_new_transcription_without_current_user_request(message, monkeypatch):
    _, _, _, exported = audio_fixture(monkeypatch)
    history = [{"role": "user", "content": "把华懋科技录音转成文字"},
               {"role": "assistant", "content": "请选择"}, {"role": "user", "content": message}]
    result, outputs = run(monkeypatch, [tool("meeting_text", {
        "title": "华懋科技", "transcribe": True, "generate_memo": True,
    }, "unapproved")], message, history=history)

    assert not exported and not outputs[0]["ok"]
    assert result["tasks"] == [] and result["widgets"] == []


@pytest.mark.parametrize("message", [
    "把华懋科技录音转成文字", "可以，开始转写", "转吧，只要原文字稿不要AI纪要",
])
def test_explicit_transcription_requires_flag_and_never_generates_memo(message, monkeypatch):
    audio, _, _, exported = audio_fixture(monkeypatch)
    starts = []

    def start(item, cookie, generate_memo=False):
        starts.append((deepcopy(item), cookie, generate_memo))
        return {"task_id": "transcribe-a", "poll_path": "/api/v1/audio/tasks/transcribe-a", "status": "queued"}

    monkeypatch.setattr(command, "_start_transcription", start)
    result, outputs = run(monkeypatch, [tool("meeting_text", {
        "title": "华懋科技", "transcribe": True, "generate_memo": True,
    }, "approved")], message)

    assert starts == [(audio, "owned-cookie", False)]
    assert len(result["tasks"]) == 1 and result["tasks"][0]["task_id"] == "transcribe-a"
    assert outputs[0]["ok"] and not exported and not result["widgets"]


def test_transcription_not_started_without_tool_flag_even_when_requested(monkeypatch):
    audio_fixture(monkeypatch)
    result, outputs = run(monkeypatch, [tool("meeting_text", {"title": "华懋科技"}, "no-flag")],
                          "请把华懋科技录音转写成文字")
    assert not outputs[0]["ok"] and not result["tasks"]


def test_existing_transcript_reused_even_if_model_requests_transcription(monkeypatch):
    _, _, reads, exported = audio_fixture(monkeypatch, transcript="RAW-TRANSCRIPT-逐字稿原文")
    result, outputs = run(monkeypatch, [tool("meeting_text", {
        "title": "华懋科技", "transcribe": True,
    }, "already-exists")], "帮我把华懋科技录音转写成文字")

    assert len(reads) == 1 and exported[0][1] == "RAW-TRANSCRIPT-逐字稿原文"
    assert outputs[0]["ok"] and not result["tasks"]
    assert [item["url"] for item in result["widgets"]] == ["/private/existing-transcript.md"]


def test_missing_audio_does_not_attempt_transcription_of_memo(monkeypatch):
    audio_fixture(monkeypatch, present=False)
    result, outputs = run(monkeypatch, [tool("meeting_text", {
        "title": "华懋科技", "transcribe": True,
    }, "missing-audio")], "转写华懋科技录音")
    assert not outputs[0]["ok"] and not outputs[0]["data"]["can_transcribe"]
    assert not result["tasks"] and not result["widgets"]


def test_transcription_configuration_failure_is_not_success(monkeypatch):
    audio_fixture(monkeypatch)
    monkeypatch.setattr(command, "_start_transcription", lambda *a, **k: {
        "configured": False, "error": "语音转写未配置",
    })
    result, outputs = run(monkeypatch, [tool("meeting_text", {
        "title": "华懋科技", "transcribe": True,
    }, "unconfigured")], "开始转写华懋科技录音")
    assert not outputs[0]["ok"]
    assert not result["tasks"] and not result["widgets"]
    assert "未配置" in json.dumps(outputs[0], ensure_ascii=False)


@pytest.mark.parametrize("kwargs,expected", [({}, False), ({"generate_memo": False}, False)])
def test_transcription_submission_payload_defaults_to_transcript_only(kwargs, expected, monkeypatch):
    calls = []

    def internal(method, path, cookie, params=None, **other):
        calls.append((method, path, cookie, deepcopy(params)))
        if method == "GET":
            return {"ok": True, "data": {"transcription_configured": True}}
        return {"ok": True, "data": {"task_id": "transcribe-body"}}

    monkeypatch.setattr(command, "_internal_call", internal)
    # Exercise the real submission helper, restoring only this patched leaf.
    result = _REAL_START_TRANSCRIPTION({"topic_id": "topic-a", "file_id": "file-a"}, "owned-cookie", **kwargs)
    assert len(calls) == 2
    assert calls[1][0] == "POST" and calls[1][3]["body"]["generate_memo"] is expected
    assert calls[1][3]["body"]["items"] == [{"topic_id": "topic-a", "file_id": "file-a"}]
    assert result["task_id"] == "transcribe-body"


_REAL_START_TRANSCRIPTION = command._start_transcription


def install_pack_service(monkeypatch, *, results=None):
    starts = []
    def submit(method, path, cookie, params=None, **kwargs):
        assert method == "POST" and path == "/api/v1/data-acquisition/run-async"
        assert cookie == "owned-cookie"
        starts.append(deepcopy(params["body"]))
        if results:
            return deepcopy(results[len(starts) - 1])
        return {"ok": True, "status": 202, "task": {
            "task_id": "acq-one", "poll_path": "/api/v1/data-acquisition/tasks/acq-one"}}
    monkeypatch.setattr(command, "_internal_call", submit)
    return starts


def test_report_pack_submits_once_and_returns_queued_task_without_polling(monkeypatch):
    starts = install_pack_service(monkeypatch)
    result, outputs = run(monkeypatch, [tool("package_reports", {
        "symbol": "300750", "keyword": "宁德时代", "start_date": "2025-10-21", "end_date": "2026-10-21",
    }, "report")], "宁德时代2025-10-21至2026-10-21研报打包下载")

    assert len(starts) == 1 and "owner_id" not in starts[0]["plan"]
    params = starts[0]["plan"]["tasks"][0]["params"]
    assert params["start_date"] == "2025-10-21" and params["end_date"] == "2026-10-21"
    assert outputs[0]["status"] == "planned" and outputs[0]["data"]["download_ready"] is False
    assert result["tasks"][0]["task_id"] == "acq-one" and result["tasks"][0]["document_export"] is True
    assert not result["widgets"]  # A queued task is not an already downloadable file.


def test_same_report_args_in_one_batch_reuse_task_not_duplicate_submission(monkeypatch):
    starts = install_pack_service(monkeypatch)
    args = {"symbol": "300750", "keyword": "宁德时代", "days": 365}
    result, outputs = run(monkeypatch, [tool("package_reports", args, "first"),
                                      tool("package_reports", args, "duplicate")], "下载宁德时代近一年研报")
    assert len(starts) == 1 and len(result["tasks"]) == 1
    assert len(starts[0]["plan"]["tasks"]) == 1
    assert all(output["status"] == "planned" for output in outputs)


def test_different_report_parameters_do_not_reuse_other_company_task(monkeypatch):
    starts = install_pack_service(monkeypatch)
    result, outputs = run(monkeypatch, [
        tool("package_reports", {"symbol": "300750", "keyword": "宁德时代"}, "catl"),
        tool("package_reports", {"symbol": "603306", "keyword": "华懋科技"}, "huamao"),
    ], "宁德时代和华懋科技近一年研报分别下载")
    assert len(starts) == 1 and len(result["tasks"]) == 1
    assert [item["params"]["ts_codes"] for item in starts[0]["plan"]["tasks"]] == [["300750"], ["603306"]]
    assert all(not item.get("reused_existing_result") for item in outputs)


def test_failed_submission_can_retry_same_arguments_instead_of_caching_failure(monkeypatch):
    starts = install_pack_service(monkeypatch, results=[{"ok": False, "error": "temporary failure"},
        {"ok": True, "task": {"task_id": "retry-ok", "poll_path": "/api/v1/data-acquisition/tasks/retry-ok"}}])
    args = {"symbol": "300750", "keyword": "宁德时代"}
    failed, _ = run(monkeypatch, [tool("package_reports", args, "failed")], "宁德时代近一年研报打包")
    assert not failed["tasks"] and "未提交" in failed["reply"]
    result, outputs = run(monkeypatch, [tool("package_reports", args, "retry")], "宁德时代近一年研报打包")
    assert len(starts) == 2
    assert outputs[0]["status"] == "planned"
    assert result["tasks"][0]["task_id"] == "retry-ok"
    assert not outputs[0].get("reused_existing_result")


def test_resume_with_existing_report_task_does_not_resubmit(monkeypatch):
    starts = install_pack_service(monkeypatch)
    task = {"task_id": "existing-report", "poll_path": "/api/v1/reports/tasks/existing-report"}
    args = {"symbol": "300750"}
    state = {"steps": [{"name": "package_reports", "args": deepcopy(args), "ok": True,
                        "summary": "已提交", "_raw": {"ok": True, "task": deepcopy(task)}}], "tasks": [task]}
    result, outputs = run(monkeypatch, [tool("package_reports", args, "resume")], "继续原任务", resume_state=state)
    assert starts == [] and result["tasks"] == [task]
    assert outputs[0]["reused_existing_result"] is True


def test_existing_text_export_reused_for_identical_tools_in_same_batch(monkeypatch):
    _, lookups, reads, exported = audio_fixture(monkeypatch, transcript="TRANSCRIPT-CONTENT")
    args = {"title": "华懋科技"}
    result, outputs = run(monkeypatch, [tool("meeting_text", args, "first"), tool("meeting_text", args, "second")],
                          "下载华懋科技已有逐字稿")
    assert len(lookups) == len(reads) == len(exported) == 1
    assert outputs[1]["reused_existing_result"] is True
    assert all(item["url"] == "/private/existing-transcript.md" for item in result["widgets"])
