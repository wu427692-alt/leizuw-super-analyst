from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.research_note_audio_analysis_service import (
    AliyunDashScopeAudioTranscriber,
    DeepSeekAudioMemoAnalyzer,
    ResearchNoteAudioAnalysisError,
    ResearchNoteAudioAnalysisTaskService,
)


class _FakeResearchNoteService:
    def get_note(self, topic_id: str):
        return {
            "topic_id": topic_id,
            "title": "低空经济公司交流",
            "created_at": "2026-08-24T09:00:00+08:00",
            "files": [{"file_id": "audio-1", "name": "公司交流.mp3", "size": 5, "asset_kind": "audio"}],
        }


class _FakeResponse:
    headers = {"Content-Length": "5"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield b"audio"

    def close(self):
        return None


def _fake_analyzer(transcripts, title, focus):
    assert transcripts[0]["transcript"] == "公司预计明年交付一百架，毛利率仍需观察。"
    assert focus == "业绩与交付"
    return {
        "title": title,
        "executive_summary": "公司给出下一年度交付目标，但盈利能力仍需后续验证。",
        "key_conclusions": ["明年交付目标为一百架"],
        "industry_chain": ["上游核心部件仍是约束"],
        "company_mentions": [{"name": "示例公司", "view": "交付提速", "evidence": "原录音口径"}],
        "financial_forecasts": [{"subject": "示例公司", "period": "明年", "metric": "交付", "value": "100架", "evidence": "管理层交流"}],
        "risks": ["毛利率不确定"],
        "follow_ups": ["核验订单公告"],
        "transcript_quality": "专有名词需回听",
        "model": "fake-model",
    }


def test_selected_audio_generates_owner_scoped_memo_and_downloads(tmp_path: Path) -> None:
    token = set_current_user_id(101)
    try:
        service = ResearchNoteAudioAnalysisTaskService(
            service_factory=_FakeResearchNoteService,
            resolver=lambda _topic, _file: "https://example.test/audio",
            http_get=lambda *_args, **_kwargs: _FakeResponse(),
            transcriber=lambda _path, _name: "公司预计明年交付一百架，毛利率仍需观察。",
            analyzer=_fake_analyzer,
            task_root=tmp_path,
            workers=1,
        )
        submitted = service.submit([("topic-1", "audio-1")], title="低空经济录音纪要", focus="业绩与交付")
        assert submitted["status"] == "queued"
        assert "items" not in submitted
        service._executor.shutdown(wait=True)

        completed = service.get(submitted["task_id"])
        assert completed["status"] == "completed"
        assert completed["progress"] == 100
        assert completed["result"]["executive_summary"].startswith("公司给出")
        assert completed["download_urls"]["docx"].endswith("format=docx")
        assert completed["transcript_artifacts"][0]["file_id"] == "audio-1"
        transcript = service.transcript(submitted["task_id"], "audio-1")
        assert transcript["lines"][0]["text"].startswith("公司预计")

        markdown_path, markdown_name, media_type = service.download(submitted["task_id"], "md")
        assert markdown_name == "低空经济录音纪要.md"
        assert media_type.startswith("text/markdown")
        assert "明年交付目标为一百架" in markdown_path.read_text(encoding="utf-8")

        bundle_path, _, _ = service.download(submitted["task_id"], "zip")
        with ZipFile(bundle_path) as archive:
            assert "report.md" in archive.namelist()
            assert "report.docx" in archive.namelist()
            assert "result.json" in archive.namelist()
            transcript_name = next(name for name in archive.namelist() if name.startswith("转写文本/"))
            assert "交付一百架" in archive.read(transcript_name).decode("utf-8")
            assert json.loads(archive.read("result.json"))["source_files"][0]["file_id"] == "audio-1"

        other = set_current_user_id(202)
        try:
            with pytest.raises(ResearchNoteAudioAnalysisError, match="无权访问"):
                service.get(submitted["task_id"])
        finally:
            reset_current_user_id(other)
    finally:
        reset_current_user_id(token)


def test_audio_analysis_rejects_unconfigured_transcriber(tmp_path: Path) -> None:
    class _MissingTranscriber:
        configured = False

    service = ResearchNoteAudioAnalysisTaskService(
        service_factory=_FakeResearchNoteService,
        transcriber=_MissingTranscriber(),
        analyzer=_fake_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    assert service.capability()["configured"] is False
    with pytest.raises(ResearchNoteAudioAnalysisError, match="语音转写"):
        service.submit([("topic-1", "audio-1")])
    service._executor.shutdown(wait=True)


def test_failed_audio_analysis_can_retry_with_persisted_selection(tmp_path: Path) -> None:
    class _FlakyTranscriber:
        configured = True

        def __init__(self):
            self.calls = 0

        def __call__(self, _path, _name):
            self.calls += 1
            if self.calls == 1:
                raise ResearchNoteAudioAnalysisError("临时识别故障")
            return "[00:00:01] 说话人1：第二次识别成功。"

    token = set_current_user_id(303)
    try:
        transcriber = _FlakyTranscriber()
        service = ResearchNoteAudioAnalysisTaskService(
            service_factory=_FakeResearchNoteService,
            resolver=lambda _topic, _file: "https://example.test/audio",
            http_get=lambda *_args, **_kwargs: _FakeResponse(),
            transcriber=transcriber,
            analyzer=lambda _transcripts, title, _focus: {"title": title, "executive_summary": "重试成功"},
            task_root=tmp_path,
            workers=1,
        )
        submitted = service.submit([("topic-1", "audio-1")])
        service._executor.shutdown(wait=True)
        assert service.get(submitted["task_id"])["status"] == "failed"

        service._executor = ThreadPoolExecutor(max_workers=1)
        retried = service.retry(submitted["task_id"])
        assert retried["retry_count"] == 1
        service._executor.shutdown(wait=True)
        assert service.get(submitted["task_id"])["status"] == "completed"
    finally:
        reset_current_user_id(token)


def test_retry_reuses_completed_transcript_after_analyzer_failure(tmp_path: Path) -> None:
    class _CountingTranscriber:
        configured = True

        def __init__(self):
            self.calls = 0

        def __call__(self, _path, _name):
            self.calls += 1
            return "[00:00:01] 说话人1：已经完成转写。"

    class _FlakyAnalyzer:
        configured = True

        def __init__(self):
            self.calls = 0

        def __call__(self, _transcripts, title, _focus):
            self.calls += 1
            if self.calls == 1:
                raise ResearchNoteAudioAnalysisError("DeepSeek 暂未返回完整纪要")
            return {"title": title, "executive_summary": "复用逐字稿后生成成功"}

    token = set_current_user_id(404)
    try:
        transcriber = _CountingTranscriber()
        analyzer = _FlakyAnalyzer()
        service = ResearchNoteAudioAnalysisTaskService(
            service_factory=_FakeResearchNoteService,
            resolver=lambda _topic, _file: "https://example.test/audio",
            http_get=lambda *_args, **_kwargs: _FakeResponse(),
            transcriber=transcriber,
            analyzer=analyzer,
            task_root=tmp_path,
            workers=1,
        )
        submitted = service.submit([("topic-1", "audio-1")])
        service._executor.shutdown(wait=True)
        failed = service.get(submitted["task_id"])
        assert failed["status"] == "failed"
        assert failed["transcript_artifacts"][0]["file_id"] == "audio-1"

        service._executor = ThreadPoolExecutor(max_workers=1)
        service.retry(submitted["task_id"])
        service._executor.shutdown(wait=True)
        completed = service.get(submitted["task_id"])

        assert completed["status"] == "completed"
        assert completed["result"]["executive_summary"] == "复用逐字稿后生成成功"
        assert transcriber.calls == 1
        assert analyzer.calls == 2
    finally:
        reset_current_user_id(token)


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DeepSeekSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def post(self, _url, **kwargs):
        self.calls.append(kwargs)
        return _JsonResponse(self.payloads.pop(0))


def test_deepseek_audio_memo_disables_thinking_and_recovers_empty_content(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("AUDIO_MEMO_ANALYSIS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("AUDIO_MEMO_ANALYSIS_MAX_RETRIES", "2")
    session = _DeepSeekSession([
        {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "推理内容占满输出"},
            }]
        },
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"summary":"重试成功","facts":[]}'},
            }]
        },
    ])
    analyzer = DeepSeekAudioMemoAnalyzer(session=session)

    parsed = analyzer._call_json("只返回 JSON", {"transcript": "测试"}, max_tokens=3200)

    assert parsed["summary"] == "重试成功"
    assert len(session.calls) == 2
    assert session.calls[0]["json"]["thinking"] == {"type": "disabled"}


def test_deepseek_audio_memo_never_surfaces_raw_provider_response(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("AUDIO_MEMO_ANALYSIS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("AUDIO_MEMO_ANALYSIS_MAX_RETRIES", "1")
    session = _DeepSeekSession([
        {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "内部推理详情"}}]},
    ])
    analyzer = DeepSeekAudioMemoAnalyzer(session=session)

    with pytest.raises(ResearchNoteAudioAnalysisError) as captured:
        analyzer._call_json("只返回 JSON", {"transcript": "测试"}, max_tokens=3200)

    assert "内部推理详情" not in str(captured.value)
    assert "自动重试" in str(captured.value)


class _FakeDashScopeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            return _JsonResponse({"output": {"task_id": "aliyun-task-1", "task_status": "PENDING"}})
        if "/api/v1/tasks/" in url:
            return _JsonResponse(
                {
                    "output": {
                        "task_id": "aliyun-task-1",
                        "task_status": "SUCCEEDED",
                        "results": [
                            {
                                "subtask_status": "SUCCEEDED",
                                "transcription_url": "https://result.test/transcript.json",
                            }
                        ],
                    }
                }
            )
        return _JsonResponse(
            {
                "transcripts": [
                    {
                        "sentences": [
                            {"begin_time": 1200, "speaker_id": 0, "text": "公司预计明年交付一百架。"},
                            {"begin_time": 4200, "speaker_id": 1, "text": "毛利率仍需观察。"},
                        ]
                    }
                ]
            }
        )


def test_aliyun_dashscope_transcriber_submits_polls_and_keeps_speakers(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-test-key")
    monkeypatch.setenv("DASHSCOPE_ASR_BASE_URL", "https://dashscope.test")
    session = _FakeDashScopeSession()
    transcriber = AliyunDashScopeAudioTranscriber(session=session)
    stages = []

    transcript = transcriber.transcribe_url(
        "https://source.test/company.mp3",
        "公司交流.mp3",
        progress=lambda status, message: stages.append((status, message)),
        hotwords=["CPO", "中际旭创"],
        speaker_count=3,
    )

    assert "[00:00:01] 说话人1：公司预计明年交付一百架。" in transcript
    assert "[00:00:04] 说话人2：毛利率仍需观察。" in transcript
    post = session.calls[0]
    assert post[0] == "POST"
    assert post[2]["headers"]["X-DashScope-Async"] == "enable"
    assert post[2]["json"]["model"] == "qwen-audio-3.0-asr-flash-filetrans"
    assert post[2]["json"]["parameters"]["diarization_enabled"] is True
    assert post[2]["json"]["parameters"]["speaker_count"] == 3
    assert post[2]["json"]["parameters"]["vocabulary"]["中际旭创"] == 5
    assert [item[0] for item in stages] == ["submitting", "pending", "fetching"]
