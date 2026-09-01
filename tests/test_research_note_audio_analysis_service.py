from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import requests
from sqlalchemy import func, select

from src.config import Config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.repositories.research_note_repo import ResearchNoteRepository
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.research_note_audio_analysis_service import (
    AliyunDashScopeAudioTranscriber,
    DeepSeekAudioMemoAnalyzer,
    ResearchNoteAudioAnalysisError,
    ResearchNoteAudioAnalysisTaskService,
)
from src.storage import DatabaseManager, MonitoringEventRecord, ResearchNote


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


def test_audio_memo_is_unique_per_source_and_uses_source_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "audio-memo-index.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    try:
        service = object.__new__(ResearchNoteAudioAnalysisTaskService)
        service._should_index = True
        source_files = [{
            "filename": "华懋科技20260826.mp3",
            "topic_id": "source-topic-1",
            "file_id": "stable-audio-file-1",
            "created_at": "2026-08-26T09:30:00+08:00",
        }]
        first = {
            "title": "华懋科技 · 深度研究录音纪要",
            "executive_summary": "第一次生成",
            "source_files": source_files,
            "company_mentions": [{"name": "华懋科技", "ts_code": "603306.SH"}],
        }
        second = {
            **first,
            "executive_summary": "重试后的同源纪要",
            "source_files": [{**source_files[0], "topic_id": "audio-memo-legacy-parent"}],
        }

        first_topic = service._index_completed_memo("audio-analysis-first", first, "第一版正文")
        second_topic = service._index_completed_memo("audio-analysis-retry", second, "第二版正文")

        assert first_topic == second_topic
        note_repository = ResearchNoteRepository()
        notes, total = note_repository.list_notes(group_id="ai-audio-memo", page_size=100)
        assert total == 1
        assert notes[0].topic_id == first_topic
        assert notes[0].created_at.isoformat() == "2026-08-26T01:30:00"
        events, event_total = InvestmentMonitorRepository().list_events(
            days=3650,
            source_key="audio.memo.ai",
            page_size=100,
        )
        assert event_total == 1
        assert events[0]["external_id"] == first_topic
        assert events[0]["event_at"].startswith("2026-08-26T01:30:00")

        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            assert session.scalar(select(func.count(ResearchNote.id))) == 1
            assert session.scalar(select(func.count(MonitoringEventRecord.id))) == 1
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


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


def test_selected_audio_can_transcribe_without_calling_ai(tmp_path: Path) -> None:
    class _UnavailableAnalyzer:
        configured = False

        def __call__(self, *_args, **_kwargs):
            raise AssertionError("transcript-only mode must not call the analyzer")

    token = set_current_user_id(102)
    try:
        service = ResearchNoteAudioAnalysisTaskService(
            service_factory=_FakeResearchNoteService,
            resolver=lambda _topic, _file: "https://example.test/audio",
            http_get=lambda *_args, **_kwargs: _FakeResponse(),
            transcriber=lambda _path, _name: "[00:00:01] 说话人1：只转写，不生成纪要。",
            analyzer=_UnavailableAnalyzer(),
            task_root=tmp_path,
            workers=1,
        )
        assert service.capability()["transcription_configured"] is True
        assert service.capability()["configured"] is False

        submitted = service.submit(
            [("topic-1", "audio-1")],
            title="公司交流逐字稿",
            generate_memo=False,
        )
        service._executor.shutdown(wait=True)
        completed = service.get(submitted["task_id"])

        assert completed["status"] == "completed"
        assert completed["generate_memo"] is False
        assert completed["result"]["transcript_only"] is True
        assert completed["download_urls"].keys() == {"zip"}
        assert "只转写" in service.transcript(submitted["task_id"], "audio-1")["text"]
        bundle_path, _, _ = service.download(submitted["task_id"], "zip")
        with ZipFile(bundle_path) as archive:
            assert len(archive.namelist()) == 1
            assert archive.namelist()[0].startswith("转写文本/")

        availability = service.transcript_availability([("topic-1", "audio-1")])
        assert availability[("topic-1", "audio-1")]["transcribed"] is True
        assert availability[("topic-1", "audio-1")]["transcript_task_id"] == submitted["task_id"]
        reindexed = service.transcript_availability([("audio-memo-synthetic", "audio-1")])
        assert reindexed[("audio-memo-synthetic", "audio-1")]["transcript_task_id"] == submitted["task_id"]
        other = set_current_user_id(999)
        try:
            assert service.transcript_availability([("topic-1", "audio-1")]) == {}
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
        other = set_current_user_id(909)
        try:
            with pytest.raises(ResearchNoteAudioAnalysisError, match="无权访问"):
                service.retry(submitted["task_id"], owner_id="user:404")
            retried = service.retry(submitted["task_id"], owner_id="user:303")
        finally:
            reset_current_user_id(other)
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


class _CacheTranscriber:
    configured = True

    def __init__(self, *, provider: str = "test-asr", model: str = "test-model", text: str = "共享原始逐字稿", fail: bool = False):
        self.provider = provider
        self.model = model
        self.text = text
        self.fail = fail
        self.calls = 0

    def __call__(self, _path, _name):
        self.calls += 1
        if self.fail:
            raise ResearchNoteAudioAnalysisError("模拟转写失败")
        return self.text


class _TwoAudioResearchNoteService:
    def get_note(self, topic_id: str):
        return {
            "topic_id": topic_id,
            "title": "公司录音合集",
            "created_at": "2026-08-24T09:00:00+08:00",
            "files": [
                {"file_id": "audio-1", "name": "公司交流一.mp3", "size": 5, "asset_kind": "audio"},
                {"file_id": "audio-2", "name": "公司交流二.mp3", "size": 5, "asset_kind": "audio"},
            ],
        }


def _cache_test_analyzer(_transcripts, title, _focus):
    return {"title": title, "executive_summary": "缓存测试纪要"}


def _submit_and_wait(service, item=("topic-1", "audio-1")):
    submitted = service.submit([item])
    service._executor.shutdown(wait=True)
    return service.get(submitted["task_id"])


def test_new_task_reuses_persistent_raw_transcript_cache(tmp_path: Path) -> None:
    transcriber = _CacheTranscriber(text="同一录音只应付费转写一次")
    http_calls = []
    service = ResearchNoteAudioAnalysisTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic, _file: "https://example.test/audio",
        http_get=lambda *_args, **_kwargs: http_calls.append(1) or _FakeResponse(),
        transcriber=transcriber,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )

    first = _submit_and_wait(service)
    restarted_service = ResearchNoteAudioAnalysisTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic, _file: "https://example.test/audio",
        http_get=lambda *_args, **_kwargs: http_calls.append(1) or _FakeResponse(),
        transcriber=transcriber,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    second = _submit_and_wait(restarted_service)

    assert first["status"] == second["status"] == "completed"
    assert first["transcript_artifacts"][0]["cache_hit"] is False
    assert second["transcript_artifacts"][0]["cache_hit"] is True
    assert first["transcript_artifacts"][0]["transcript_sha256"] == second["transcript_artifacts"][0]["transcript_sha256"]
    assert transcriber.calls == 1
    assert len(http_calls) == 1
    assert restarted_service.transcript(second["task_id"], "audio-1")["text"] == "同一录音只应付费转写一次"
    cache_payload = json.loads(next((tmp_path / "transcript_cache").rglob("*.json")).read_text(encoding="utf-8"))
    assert "owner_id" not in cache_payload
    assert "task_id" not in cache_payload
    assert "topic_id" not in cache_payload
    assert "file_id" not in cache_payload
    assert "缓存测试纪要" not in json.dumps(cache_payload, ensure_ascii=False)


def test_transcript_cache_never_crosses_file_or_provider(tmp_path: Path) -> None:
    provider_a = _CacheTranscriber(provider="provider-a", model="model-1", text="提供方A逐字稿")
    service_a = ResearchNoteAudioAnalysisTaskService(
        service_factory=_TwoAudioResearchNoteService,
        resolver=lambda _topic, file_id: f"https://example.test/{file_id}",
        http_get=lambda *_args, **_kwargs: _FakeResponse(),
        transcriber=provider_a,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    first_file = _submit_and_wait(service_a, ("topic-1", "audio-1"))
    service_a._executor = ThreadPoolExecutor(max_workers=1)
    second_file = _submit_and_wait(service_a, ("topic-1", "audio-2"))
    service_a._executor = ThreadPoolExecutor(max_workers=1)
    other_topic = _submit_and_wait(service_a, ("topic-2", "audio-1"))

    provider_b = _CacheTranscriber(provider="provider-b", model="model-1", text="提供方B逐字稿")
    service_b = ResearchNoteAudioAnalysisTaskService(
        service_factory=_TwoAudioResearchNoteService,
        resolver=lambda _topic, file_id: f"https://example.test/{file_id}",
        http_get=lambda *_args, **_kwargs: _FakeResponse(),
        transcriber=provider_b,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    other_provider = _submit_and_wait(service_b, ("topic-1", "audio-1"))

    assert first_file["transcript_artifacts"][0]["cache_hit"] is False
    assert second_file["transcript_artifacts"][0]["cache_hit"] is False
    assert other_topic["transcript_artifacts"][0]["cache_hit"] is False
    assert other_provider["transcript_artifacts"][0]["cache_hit"] is False
    assert provider_a.calls == 3
    assert provider_b.calls == 1
    assert service_b.transcript(other_provider["task_id"], "audio-1")["text"] == "提供方B逐字稿"


def test_corrupt_transcript_cache_self_heals_by_retranscribing(tmp_path: Path) -> None:
    transcriber = _CacheTranscriber(text="可校验逐字稿")
    service = ResearchNoteAudioAnalysisTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic, _file: "https://example.test/audio",
        http_get=lambda *_args, **_kwargs: _FakeResponse(),
        transcriber=transcriber,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    _submit_and_wait(service)
    cache_path = next((tmp_path / "transcript_cache").rglob("*.json"))
    cache_path.write_text('{"transcript":"tampered"}', encoding="utf-8")

    service._executor = ThreadPoolExecutor(max_workers=1)
    healed = _submit_and_wait(service)

    assert healed["status"] == "completed"
    assert healed["transcript_artifacts"][0]["cache_hit"] is False
    assert transcriber.calls == 2
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["transcript"] == "可校验逐字稿"
    assert payload["char_count"] == len(payload["transcript"])


def test_failed_transcription_is_never_cached(tmp_path: Path) -> None:
    failing = _CacheTranscriber(fail=True)
    failed_service = ResearchNoteAudioAnalysisTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic, _file: "https://example.test/audio",
        http_get=lambda *_args, **_kwargs: _FakeResponse(),
        transcriber=failing,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    failed = _submit_and_wait(failed_service)

    assert failed["status"] == "failed"
    assert list((tmp_path / "transcript_cache").rglob("*.json")) == []

    working = _CacheTranscriber(text="失败后重新识别成功")
    recovered_service = ResearchNoteAudioAnalysisTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic, _file: "https://example.test/audio",
        http_get=lambda *_args, **_kwargs: _FakeResponse(),
        transcriber=working,
        analyzer=_cache_test_analyzer,
        task_root=tmp_path,
        workers=1,
    )
    recovered = _submit_and_wait(recovered_service)

    assert recovered["status"] == "completed"
    assert recovered["transcript_artifacts"][0]["cache_hit"] is False
    assert failing.calls == working.calls == 1
    assert len(list((tmp_path / "transcript_cache").rglob("*.json"))) == 1


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _StatusResponse(_JsonResponse):
    def __init__(self, status_code, payload=None, *, text=""):
        super().__init__(payload or {})
        self.status_code = status_code
        self.text = text or json.dumps(payload or {}, ensure_ascii=False)
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class _SequencedRequestSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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


@pytest.mark.parametrize(
    "transient_failure",
    [
        requests.ConnectionError("connection reset"),
        requests.Timeout("read timeout"),
        _StatusResponse(429, {"message": "rate limited"}),
        _StatusResponse(503, {"message": "temporarily unavailable"}),
    ],
)
def test_aliyun_request_retries_only_transient_failures(monkeypatch, transient_failure) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-test-key")
    monkeypatch.setenv("DASHSCOPE_ASR_REQUEST_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("DASHSCOPE_ASR_REQUEST_BACKOFF_SEC", "0")
    session = _SequencedRequestSession([
        transient_failure,
        _StatusResponse(200, {"output": {"task_id": "aliyun-task-1"}}),
    ])
    transcriber = AliyunDashScopeAudioTranscriber(session=session)

    payload = transcriber._request_json("GET", "https://dashscope.test/api/v1/tasks/T1")

    assert payload["output"]["task_id"] == "aliyun-task-1"
    assert len(session.calls) == 2


def test_aliyun_request_does_not_retry_other_4xx_and_redacts_error(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-test-key")
    monkeypatch.setenv("DASHSCOPE_ASR_REQUEST_MAX_ATTEMPTS", "4")
    session = _SequencedRequestSession([
        _StatusResponse(400, text='{"api_key":"sk-sensitive-value","message":"bad request"}'),
        _StatusResponse(200, {"unexpected": "must not be called"}),
    ])
    transcriber = AliyunDashScopeAudioTranscriber(session=session)

    with pytest.raises(ResearchNoteAudioAnalysisError) as captured:
        transcriber._request_json("POST", "https://dashscope.test/api/v1/services/audio/asr/transcription")

    assert len(session.calls) == 1
    assert "sk-sensitive-value" not in str(captured.value)
    assert "<redacted>" in str(captured.value)


def test_aliyun_request_stops_after_configured_transient_attempts(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-test-key")
    monkeypatch.setenv("DASHSCOPE_ASR_REQUEST_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("DASHSCOPE_ASR_REQUEST_BACKOFF_SEC", "0")
    session = _SequencedRequestSession([
        requests.ConnectionError("first reset"),
        requests.ConnectionError("second reset"),
        _StatusResponse(200, {"unexpected": "must not be called"}),
    ])
    transcriber = AliyunDashScopeAudioTranscriber(session=session)

    with pytest.raises(ResearchNoteAudioAnalysisError, match="ConnectionError"):
        transcriber._request_json("GET", "https://dashscope.test/api/v1/tasks/T1")

    assert len(session.calls) == 2
