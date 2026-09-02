from __future__ import annotations

from unittest.mock import patch

from src.services.industry_research_service import IndustryResearchService
from src.services.research_note_audio_analysis_service import ResearchNoteAudioAnalysisError
from src.storage import DatabaseManager


def _snapshot() -> dict:
    created_at = {
        1: "2026-08-28T09:00:00Z",
        2: "not-a-date",
        3: "2026-08-30T15:00:00Z",
        # Latest source recording still precedes the frozen snapshot time.
        4: "2026-08-31T06:59:00Z",
    }
    candidates = [
        {
            "topic_id": f"source-topic-{index}",
            "file_id": f"audio-{index}",
            "filename": f"华懋科技录音{index}.mp3",
            "created_at": created_at[index],
        }
        for index in range(1, 5)
    ]
    return {
        "topic": "华懋科技",
        "research_type": "company",
        "subject": {"name": "华懋科技", "symbol": "603306.SH", "resolved": True},
        "query_terms": ["华懋科技", "603306.SH"],
        "lookback_days": 730,
        "collected_at": "2026-08-31T07:00:00",
        "cutoff": "2024-09-01T07:00:00",
        "audio_candidates": candidates,
        "audio_pipeline": {"status": "pending", "candidate_count": 4},
        "coverage": [{
            "key": "audio_transcripts",
            "name": "相关录音转写",
            "status": "missing",
            "count": 0,
            "evidence_level": "ai_transcript",
            "candidates": 4,
        }],
        "source_status": [],
        "evidence": [],
        "totals": {"evidence": 0, "evidence_stored": 0, "audio_candidates": 4},
        "financial_series": [{"period": str(index)} for index in range(4)],
        "market_series": [{"date": f"2026-08-{index:02d}"} for index in range(1, 31)],
        "valuation_series": [],
        "ownership_governance": [],
        "capital_market_activity": [],
        "filing_documents": [{"title": "2026年半年报"}],
        "broker_report_documents": [],
        "web_documents": [],
        "concept_context": {},
        "industry_peer_matrix": {},
        "media_gallery": [],
        "companies": [{"symbol": "603306.SH", "name": "华懋科技"}],
    }


class _RetryCompletesAudioService:
    def __init__(self) -> None:
        self.retry_calls = []
        self.submit_kwargs = {}

    @staticmethod
    def capability():
        return {"configured": True, "transcription_provider": "aliyun_dashscope"}

    def submit(self, *_args, **kwargs):
        self.submit_kwargs = dict(kwargs)
        return {"task_id": "audio-task-retry", "status": "failed", "message": "临时网络错误"}

    def retry(self, task_id, *, owner_id=None):
        self.retry_calls.append((task_id, owner_id))
        return {
            "task_id": task_id,
            "status": "completed",
            "retry_count": 1,
            "result": {
                "title": "华懋科技录音纪要",
                "executive_summary": "四个录音均已完成转写。",
                "generated_at": "2026-08-31T07:10:00",
            },
        }

    @staticmethod
    def get(*_args, **_kwargs):
        raise AssertionError("terminal states should not be polled again")

    @staticmethod
    def transcript(task_id, file_id, *, owner_id=None):
        assert task_id == "audio-task-retry"
        assert owner_id == "user:7"
        return {"text": f"{file_id} 已完成逐字稿"}


class _RetryStillFailsAudioService(_RetryCompletesAudioService):
    @staticmethod
    def submit(*_args, **_kwargs):
        return {"task_id": "audio-task-partial", "status": "failed", "message": "第四个录音连接中断"}

    def retry(self, task_id, *, owner_id=None):
        self.retry_calls.append((task_id, owner_id))
        return {
            "task_id": task_id,
            "status": "failed",
            "retry_count": 1,
            "message": "第四个录音自动续跑后仍失败",
        }

    @staticmethod
    def transcript(task_id, file_id, *, owner_id=None):
        assert task_id == "audio-task-partial"
        assert owner_id == "user:7"
        if file_id == "audio-4":
            raise ResearchNoteAudioAnalysisError("逐字稿不存在或尚未生成")
        return {"text": f"{file_id} 已完成逐字稿"}


class _SixAudioService(_RetryCompletesAudioService):
    def __init__(self) -> None:
        super().__init__()
        self.submitted = []

    def submit(self, selected, **_kwargs):
        self.submitted = list(selected)
        return {
            "task_id": "audio-task-six", "status": "completed",
            "result": {
                "title": "华懋科技六个录音纪要",
                "executive_summary": "六个严格匹配录音均已完成转写。",
                "generated_at": "2026-08-31T07:10:00",
            },
        }

    @staticmethod
    def transcript(task_id, file_id, *, owner_id=None):
        assert task_id == "audio-task-six"
        assert owner_id == "user:7"
        return {"text": f"{file_id} 已完成逐字稿"}


def test_industry_audio_failure_retries_once_with_explicit_owner_and_completes(tmp_path) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'audio-retry.db'}")
    audio_service = _RetryCompletesAudioService()
    try:
        with patch(
            "src.services.industry_research_service.ResearchNoteAudioAnalysisTaskService.get_instance",
            return_value=audio_service,
        ):
            result = IndustryResearchService(db).transcribe_relevant_audio(
                _snapshot(), owner_id="user:7", objective="核验经营预期",
            )
    finally:
        DatabaseManager.reset_instance()

    assert audio_service.retry_calls == [("audio-task-retry", "user:7")]
    assert audio_service.submit_kwargs["generate_memo"] is False
    assert result["audio_pipeline"]["status"] == "completed"
    assert result["audio_pipeline"]["retry_attempted"] is True
    assert result["audio_pipeline"]["transcribed_count"] == 4
    assert result["totals"]["audio_transcripts"] == 4
    assert next(item for item in result["coverage"] if item["key"] == "audio_transcripts")["status"] == "covered"
    evidence = next(item for item in result["evidence"] if item["kind"] == "audio_transcript")
    assert evidence["source"] == "阿里云语音转写"
    assert evidence["url"].endswith("/transcripts/audio-1")
    assert evidence["date"] == "2026-08-31T06:59:00Z"
    assert evidence["processed_at"] == "2026-08-31T07:10:00"
    assert evidence["date"] != evidence["processed_at"]
    assert result["audio_pipeline"]["source_date"] == evidence["date"]
    assert result["audio_pipeline"]["processed_at"] == evidence["processed_at"]
    assert result["data_quality"]["metrics"]["future_dated_items"] == 0


def test_default_audio_limit_transcribes_all_six_and_records_selection_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("INDUSTRY_RESEARCH_AUDIO_MAX_FILES", raising=False)
    snapshot = _snapshot()
    for index in (5, 6):
        snapshot["audio_candidates"].append({
            "topic_id": f"source-topic-{index}",
            "file_id": f"audio-{index}",
            "filename": f"华懋科技录音{index}.mp3",
            "created_at": "2026-08-30T12:00:00Z",
        })
    snapshot["totals"]["audio_candidates"] = 6
    snapshot["audio_pipeline"].update({
        "candidate_count": 6, "selected_count": 6, "deferred_count": 0, "max_files": 8,
    })
    audio_service = _SixAudioService()
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'audio-six.db'}")
    try:
        with patch(
            "src.services.industry_research_service.ResearchNoteAudioAnalysisTaskService.get_instance",
            return_value=audio_service,
        ):
            result = IndustryResearchService(db).transcribe_relevant_audio(
                snapshot, owner_id="user:7", objective="核验经营预期",
            )
    finally:
        DatabaseManager.reset_instance()

    assert len(audio_service.submitted) == 6
    assert result["audio_pipeline"] | {
        "candidate_count": 6, "selected_count": 6, "deferred_count": 0,
        "transcribed_count": 6, "max_files": 8,
    } == result["audio_pipeline"]
    assert result["audio_pipeline"]["status"] == "completed"
    audio_plan = next(item for item in result["source_plan"] if item["key"] == "audio_transcripts")
    assert audio_plan["status"] == "covered"
    assert audio_plan["candidate_count"] == audio_plan["selected_count"] == audio_plan["transcribed_count"] == 6
    assert audio_plan["deferred_count"] == 0


def test_audio_budget_completion_discloses_deferred_candidates_without_false_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("INDUSTRY_RESEARCH_AUDIO_MAX_FILES", raising=False)
    snapshot = _snapshot()
    for index in range(5, 11):
        snapshot["audio_candidates"].append({
            "topic_id": f"source-topic-{index}",
            "file_id": f"audio-{index}",
            "filename": f"华懋科技候选录音{index}.mp3",
            "created_at": "2026-08-30T12:00:00Z",
        })
    snapshot["totals"]["audio_candidates"] = 10
    snapshot["audio_pipeline"].update({
        "candidate_count": 10, "selected_count": 8, "deferred_count": 2, "max_files": 8,
    })
    audio_service = _SixAudioService()
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'audio-budget.db'}")
    try:
        with patch(
            "src.services.industry_research_service.ResearchNoteAudioAnalysisTaskService.get_instance",
            return_value=audio_service,
        ):
            result = IndustryResearchService(db).transcribe_relevant_audio(
                snapshot, owner_id="user:7", objective="核验经营预期",
            )
    finally:
        DatabaseManager.reset_instance()

    assert len(audio_service.submitted) == 8
    pipeline = result["audio_pipeline"]
    assert pipeline["status"] == "completed"
    assert pipeline["candidate_count"] == 10
    assert pipeline["selected_count"] == pipeline["transcribed_count"] == 8
    assert pipeline["deferred_count"] == 2
    assert "本报告只使用已转写样本" in pipeline["message"]
    assert "严格匹配录音仅部分完成转写" not in result["data_quality"]["critical_gaps"]
    assert any("候选未纳入本次样本" in item for item in result["data_quality"]["warnings"])
    audio_plan = next(item for item in result["source_plan"] if item["key"] == "audio_transcripts")
    assert audio_plan["status"] == "covered"
    evidence = next(item for item in result["evidence"] if item["kind"] == "audio_transcript")
    assert evidence["processing_status"] == "completed"


def test_industry_audio_second_failure_keeps_partial_transcripts_and_limits_quality(tmp_path) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'audio-partial.db'}")
    audio_service = _RetryStillFailsAudioService()
    try:
        with patch(
            "src.services.industry_research_service.ResearchNoteAudioAnalysisTaskService.get_instance",
            return_value=audio_service,
        ):
            result = IndustryResearchService(db).transcribe_relevant_audio(
                _snapshot(), owner_id="user:7", objective="核验经营预期",
            )
    finally:
        DatabaseManager.reset_instance()

    assert audio_service.retry_calls == [("audio-task-partial", "user:7")]
    assert result["audio_pipeline"]["status"] == "partial"
    assert result["audio_pipeline"]["retry_attempted"] is True
    assert result["audio_pipeline"]["transcribed_count"] == 3
    assert result["audio_pipeline"]["failed_count"] == 1
    assert result["totals"]["audio_transcripts"] == 3
    coverage = next(item for item in result["coverage"] if item["key"] == "audio_transcripts")
    assert coverage["status"] == "partial"
    assert coverage["count"] == 3
    evidence = next(item for item in result["evidence"] if item["kind"] == "audio_transcript")
    assert evidence["processing_status"] == "partial"
    assert evidence["transcript_file_count"] == 3
    assert evidence["date"] == "2026-08-30T15:00:00Z"
    assert evidence["processed_at"]
    assert "audio-1 已完成逐字稿" in evidence["summary"]
    assert "audio-4" not in evidence["summary"]
    assert result["data_quality"]["status"] != "ready"
    assert "严格匹配录音仅部分完成转写" in result["data_quality"]["critical_gaps"]
