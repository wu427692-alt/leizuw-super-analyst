"""Owner-scoped background transcription and memo generation for selected audio."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4
import zipfile

import requests
from json_repair import repair_json

from src.config import get_config
from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.repositories.research_note_repo import ResearchNoteRepository
from src.research_note_fingerprint import research_note_information_hash
from src.request_identity import current_owner_id
from src.services.financial_data_service import FinancialDataValidationError, ResearchNoteService
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncService


logger = logging.getLogger(__name__)
_TASK_ID_RE = re.compile(r"^audio-analysis-[A-Za-z0-9_-]{8,80}$")
_ACTIVE_STATUSES = {"queued", "running"}


class ResearchNoteAudioAnalysisError(RuntimeError):
    """A selected-audio analysis task cannot be created or read."""


class OpenAICompatibleAudioTranscriber:
    """Transcribe one audio file through an OpenAI-compatible ASR endpoint."""

    def __init__(self, *, session: Optional[requests.Session] = None) -> None:
        self.api_key = str(
            os.getenv("AUDIO_TRANSCRIPTION_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        ).strip()
        self.base_url = str(
            os.getenv("AUDIO_TRANSCRIPTION_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = str(os.getenv("AUDIO_TRANSCRIPTION_MODEL") or "").strip()
        self.language = str(os.getenv("AUDIO_TRANSCRIPTION_LANGUAGE") or "zh").strip()
        self.timeout = max(60, min(int(os.getenv("AUDIO_TRANSCRIPTION_TIMEOUT_SEC", "900")), 3600))
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def __call__(self, path: Path, filename: str) -> str:
        if not self.configured:
            raise ResearchNoteAudioAnalysisError(
                "语音转写服务尚未配置：请在服务器设置 AUDIO_TRANSCRIPTION_API_KEY 和 AUDIO_TRANSCRIPTION_MODEL"
            )
        with path.open("rb") as source:
            response = self.session.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (filename, source, "application/octet-stream")},
                data={"model": self.model, "language": self.language, "response_format": "json"},
                timeout=self.timeout,
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            detail = str(getattr(response, "text", "") or "")[:300]
            raise ResearchNoteAudioAnalysisError(f"语音转写失败：{detail or type(exc).__name__}") from exc
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if not text:
            raise ResearchNoteAudioAnalysisError("语音转写服务没有返回有效文本")
        return text


class AliyunDashScopeAudioTranscriber:
    """Transcribe long recordings with Aliyun Model Studio's async file API."""

    _PENDING_STATUSES = {"PENDING", "RUNNING", "QUEUED"}

    def __init__(self, *, session: Optional[requests.Session] = None) -> None:
        self.api_key = str(
            os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("LLM_DASHSCOPE_API_KEY")
            or ""
        ).strip()
        self.base_url = str(
            os.getenv("DASHSCOPE_ASR_BASE_URL") or "https://dashscope.aliyuncs.com"
        ).rstrip("/")
        self.model = str(
            os.getenv("DASHSCOPE_ASR_MODEL")
            or os.getenv("AUDIO_TRANSCRIPTION_MODEL")
            or "qwen-audio-3.0-asr-flash-filetrans"
        ).strip()
        self.language = str(os.getenv("AUDIO_TRANSCRIPTION_LANGUAGE") or "zh").strip()
        self.diarization_enabled = str(
            os.getenv("DASHSCOPE_ASR_DIARIZATION_ENABLED") or "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        raw_speaker_count = str(os.getenv("DASHSCOPE_ASR_SPEAKER_COUNT") or "").strip()
        self.speaker_count = int(raw_speaker_count) if raw_speaker_count.isdigit() else None
        self.poll_interval = max(
            1.0, min(float(os.getenv("DASHSCOPE_ASR_POLL_INTERVAL_SEC", "3")), 30.0)
        )
        self.timeout = max(
            60, min(int(os.getenv("AUDIO_TRANSCRIPTION_TIMEOUT_SEC", "7200")), 43200)
        )
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    @property
    def provider(self) -> str:
        return "aliyun_dashscope"

    def transcribe_url(
        self,
        source_url: str,
        filename: str,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        hotwords: Optional[Iterable[str]] = None,
        speaker_count: Optional[int] = None,
    ) -> str:
        if not self.configured:
            raise ResearchNoteAudioAnalysisError(
                "阿里云语音识别尚未配置：请在管理员设置中填写 DASHSCOPE_API_KEY"
            )
        if not str(source_url or "").startswith(("http://", "https://", "oss://")):
            raise ResearchNoteAudioAnalysisError(f"《{filename}》缺少阿里云可读取的录音地址")

        parameters: Dict[str, Any] = {
            "channel_id": [0],
            "language_hints": [self.language] if self.language else ["zh"],
            "diarization_enabled": self.diarization_enabled,
        }
        requested_speakers = speaker_count or self.speaker_count
        if self.diarization_enabled and requested_speakers and 2 <= requested_speakers <= 100:
            parameters["speaker_count"] = requested_speakers
        vocabulary = {
            str(term).strip()[:80]: 5
            for term in (hotwords or [])
            if str(term).strip()
        }
        if vocabulary:
            parameters["vocabulary"] = dict(list(vocabulary.items())[:100])
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        if progress:
            progress("submitting", f"正在向阿里云提交《{filename}》")
        payload = self._request_json(
            "POST",
            f"{self.base_url}/api/v1/services/audio/asr/transcription",
            headers=headers,
            json={
                "model": self.model,
                "input": {"file_urls": [source_url]},
                "parameters": parameters,
            },
            timeout=(10, 60),
        )
        output = payload.get("output") if isinstance(payload, dict) else None
        task_id = str((output or {}).get("task_id") or "").strip()
        if not task_id:
            raise ResearchNoteAudioAnalysisError(
                f"阿里云未返回转写任务编号：{self._error_detail(payload)}"
            )

        deadline = time.monotonic() + self.timeout
        last_status = "PENDING"
        while time.monotonic() < deadline:
            if progress:
                label = "阿里云正在排队" if last_status in {"PENDING", "QUEUED"} else "阿里云正在识别"
                progress(last_status.lower(), f"{label}《{filename}》")
            task = self._request_json(
                "GET",
                f"{self.base_url}/api/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=(10, 60),
            )
            task_output = task.get("output") if isinstance(task, dict) else None
            if not isinstance(task_output, dict):
                raise ResearchNoteAudioAnalysisError(
                    f"阿里云转写任务返回格式无效：{self._error_detail(task)}"
                )
            last_status = str(task_output.get("task_status") or "").upper()
            if last_status in self._PENDING_STATUSES:
                time.sleep(self.poll_interval)
                continue
            if last_status != "SUCCEEDED":
                raise ResearchNoteAudioAnalysisError(
                    f"阿里云转写任务失败（{last_status or 'UNKNOWN'}）：{self._error_detail(task_output)}"
                )
            results = task_output.get("results") or []
            result = next(
                (item for item in results if isinstance(item, dict) and item.get("subtask_status") == "SUCCEEDED"),
                None,
            )
            if not result:
                failed = next((item for item in results if isinstance(item, dict)), {})
                raise ResearchNoteAudioAnalysisError(
                    f"阿里云无法读取《{filename}》：{self._error_detail(failed)}"
                )
            transcription_url = str(result.get("transcription_url") or "").strip()
            if not transcription_url:
                raise ResearchNoteAudioAnalysisError("阿里云转写完成，但未返回结果文件地址")
            if progress:
                progress("fetching", f"正在取回《{filename}》的转写结果")
            transcript_payload = self._request_json(
                "GET", transcription_url, headers={}, timeout=(10, 120)
            )
            text = self._format_transcript(transcript_payload)
            if not text:
                raise ResearchNoteAudioAnalysisError("阿里云语音识别没有返回有效文本")
            return text
        raise ResearchNoteAudioAnalysisError(f"阿里云转写《{filename}》超时，请稍后重试")

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        response: Optional[requests.Response] = None
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            detail = str(getattr(response, "text", "") or "")[:300]
            raise ResearchNoteAudioAnalysisError(
                f"阿里云语音识别请求失败：{detail or type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict):
            raise ResearchNoteAudioAnalysisError("阿里云语音识别返回格式无效")
        return payload

    @staticmethod
    def _error_detail(payload: Any) -> str:
        if not isinstance(payload, dict):
            return "未知错误"
        return str(
            payload.get("message")
            or payload.get("code")
            or (payload.get("output") or {}).get("message")
            or "未知错误"
        )[:300]

    @staticmethod
    def _format_transcript(payload: Dict[str, Any]) -> str:
        lines: List[str] = []
        for transcript in payload.get("transcripts") or []:
            if not isinstance(transcript, dict):
                continue
            sentences = transcript.get("sentences") or []
            for sentence in sentences:
                if not isinstance(sentence, dict):
                    continue
                text = str(sentence.get("text") or "").strip()
                if not text:
                    continue
                begin_ms = max(0, int(sentence.get("begin_time") or 0))
                timestamp = AliyunDashScopeAudioTranscriber._format_timestamp(begin_ms)
                speaker = sentence.get("speaker_id")
                speaker_text = f" 说话人{int(speaker) + 1}" if isinstance(speaker, int) else ""
                lines.append(f"[{timestamp}]{speaker_text}：{text}")
            if not sentences:
                text = str(transcript.get("text") or "").strip()
                if text:
                    lines.append(text)
        return "\n".join(lines).strip()

    @staticmethod
    def _format_timestamp(milliseconds: int) -> str:
        seconds = milliseconds // 1000
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class ConfiguredAudioTranscriber:
    """Select Aliyun by default while preserving the generic ASR fallback."""

    def __init__(self, *, session: Optional[requests.Session] = None) -> None:
        requested = str(os.getenv("AUDIO_TRANSCRIPTION_PROVIDER") or "auto").strip().lower()
        aliyun = AliyunDashScopeAudioTranscriber(session=session)
        generic = OpenAICompatibleAudioTranscriber(session=session)
        if requested in {"aliyun", "aliyun_dashscope", "dashscope"}:
            self.backend: Any = aliyun
        elif requested in {"openai", "openai_compatible"}:
            self.backend = generic
        else:
            self.backend = aliyun if aliyun.configured else generic

    @property
    def configured(self) -> bool:
        return bool(getattr(self.backend, "configured", False))

    @property
    def provider(self) -> str:
        return str(getattr(self.backend, "provider", "openai_compatible"))

    def transcribe(
        self,
        path: Path,
        filename: str,
        source_url: str,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        hotwords: Optional[Iterable[str]] = None,
        speaker_count: Optional[int] = None,
    ) -> str:
        transcribe_url = getattr(self.backend, "transcribe_url", None)
        if callable(transcribe_url):
            return transcribe_url(
                source_url,
                filename,
                progress=progress,
                hotwords=hotwords,
                speaker_count=speaker_count,
            )
        return self.backend(path, filename)


class DeepSeekAudioMemoAnalyzer:
    """Turn one or more transcripts into a traceable investment-research memo."""

    def __init__(self, *, session: Optional[requests.Session] = None) -> None:
        config = get_config()
        keys = list(getattr(config, "deepseek_api_keys", None) or [])
        self.api_key = str((keys[0] if keys else getattr(config, "deepseek_api_key", "")) or "").strip()
        self.base_url = str(
            os.getenv("ESSAY_ANALYSIS_DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        ).rstrip("/")
        self.model = str(os.getenv("AUDIO_MEMO_ANALYSIS_MODEL") or os.getenv("ESSAY_ANALYSIS_MODEL") or "").strip()
        self.timeout = max(30, min(int(os.getenv("AUDIO_MEMO_ANALYSIS_TIMEOUT_SEC", "300")), 900))
        self.chunk_chars = max(4000, min(int(os.getenv("AUDIO_MEMO_CHUNK_CHARS", "12000")), 30000))
        self.max_chunks = max(1, min(int(os.getenv("AUDIO_MEMO_MAX_CHUNKS_PER_FILE", "12")), 30))
        self.max_retries = max(1, min(int(os.getenv("AUDIO_MEMO_ANALYSIS_MAX_RETRIES", "3")), 5))
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def __call__(self, transcripts: List[Dict[str, Any]], title: str, focus: str) -> Dict[str, Any]:
        if not self.configured:
            raise ResearchNoteAudioAnalysisError("DeepSeek 文本分析服务尚未配置")
        extracted: List[Dict[str, Any]] = []
        for source in transcripts:
            text = str(source.get("transcript") or "")
            chunks = self._line_chunks(text)
            if len(chunks) > self.max_chunks:
                raise ResearchNoteAudioAnalysisError(
                    f"录音《{source.get('filename')}》转写文本过长，请提高 AUDIO_MEMO_MAX_CHUNKS_PER_FILE 后重试"
                )
            for index, chunk in enumerate(chunks or [""]):
                extracted.append(self._call_json(
                    "你是严谨的中国资本市场会议纪要分析员。只能依据转写文本，不补造数字、公司或观点。"
                    "提取本段的核心事实、观点、业绩或市值预测、产业链信息、分歧和待核验事项。"
                    "每条事实必须保留原文时间戳与说话人，无法定位就明确写未定位。"
                    "严格返回JSON对象，字段为summary、facts、opinions、forecasts、companies、risks、follow_ups、evidence，数组最多各10条。",
                    {
                        "source_file": source.get("filename"),
                        "part": f"{index + 1}/{max(len(chunks), 1)}",
                        "transcript": chunk,
                    },
                    max_tokens=3200,
                ))
        result = self._call_json(
            "你是资深中国资本市场研究员。根据多个录音分段提取结果生成一份可追溯的录音纪要。"
            "必须区分事实、发言者观点、预测和传闻；合并重复项，保留数字的时间与口径；不构成投资建议。"
            "严格返回JSON对象，字段：title、executive_summary、meeting_context、key_conclusions、industry_chain、"
            "company_mentions、financial_forecasts、catalysts、risks、disagreements、follow_ups、transcript_quality、"
            "evidence_index、speaker_views、monitoring_items。"
            "其中company_mentions为{name,ts_code,evidence,view}数组，financial_forecasts为{subject,period,metric,value,evidence}数组，"
            "evidence_index为{claim,source_file,timestamp,speaker,category,confidence}数组；"
            "speaker_views为{speaker,summary,key_points}数组；monitoring_items为{item,metric,time_window,trigger,evidence}数组。"
            "其余复数字段均为字符串数组。executive_summary写300-600字，结论必须具体。",
            {
                "requested_title": title,
                "focus": focus,
                "sources": [{key: source.get(key) for key in ("filename", "topic_id", "file_id", "note_title", "created_at")} for source in transcripts],
                "extracted_parts": extracted,
            },
            max_tokens=8000,
        )
        result["model"] = self.model
        return result

    def _line_chunks(self, text: str) -> List[str]:
        """Keep ASR timestamps and speaker turns intact across analysis chunks."""
        chunks: List[str] = []
        current: List[str] = []
        current_chars = 0
        for line in str(text or "").splitlines() or [str(text or "")]:
            line_chars = len(line) + 1
            if current and current_chars + line_chars > self.chunk_chars:
                chunks.append("\n".join(current))
                current, current_chars = [], 0
            current.append(line)
            current_chars += line_chars
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _call_json(self, system: str, payload: Dict[str, Any], *, max_tokens: int) -> Dict[str, Any]:
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            # Audio memo extraction needs deterministic JSON, not a long hidden
            # reasoning trace that can consume the entire completion budget.
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        last_reason = "empty_content"
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=request_payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                response_payload = response.json()
                choice = response_payload["choices"][0]
                message = choice["message"]
                raw = str(message.get("content") or "").strip()
                finish_reason = str(choice.get("finish_reason") or "").strip()
                if not raw:
                    last_reason = "output_truncated" if finish_reason == "length" else "empty_content"
                    logger.warning(
                        "DeepSeek audio memo returned no content attempt=%s/%s finish_reason=%s reasoning_chars=%s",
                        attempt,
                        self.max_retries,
                        finish_reason or "unknown",
                        len(str(message.get("reasoning_content") or "")),
                    )
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    break
                parsed = json.loads(repair_json(raw, return_objects=False))
                if not isinstance(parsed, dict):
                    last_reason = "invalid_json_root"
                    raise ValueError(last_reason)
                return parsed
            except requests.RequestException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                last_reason = f"http_{status_code}" if status_code else type(exc).__name__
                retryable = status_code in {408, 409, 425, 429} or (status_code is not None and status_code >= 500)
                logger.warning(
                    "DeepSeek audio memo request failed attempt=%s/%s reason=%s",
                    attempt,
                    self.max_retries,
                    last_reason,
                )
                if not retryable or attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_reason = type(exc).__name__
                logger.warning(
                    "DeepSeek audio memo response could not be parsed attempt=%s/%s reason=%s",
                    attempt,
                    self.max_retries,
                    last_reason,
                )
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))
        if last_reason in {"empty_content", "output_truncated"}:
            raise ResearchNoteAudioAnalysisError("DeepSeek 暂未返回完整纪要，系统已自动重试；可稍后从后台再次重试")
        raise ResearchNoteAudioAnalysisError("DeepSeek 纪要生成暂时不可用，系统已自动重试；请稍后再试")


class ResearchNoteAudioAnalysisTaskService:
    """Download selected audio temporarily, transcribe it, and persist report artifacts."""

    _instance: Optional["ResearchNoteAudioAnalysisTaskService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        service_factory: Callable[[], ResearchNoteService] = ResearchNoteService,
        resolver: Optional[Callable[[str, str], str]] = None,
        http_get: Callable[..., Any] = requests.get,
        transcriber: Optional[Callable[[Path, str], str]] = None,
        analyzer: Optional[Callable[[List[Dict[str, Any]], str, str], Dict[str, Any]]] = None,
        task_root: Optional[Path] = None,
        workers: Optional[int] = None,
        owner_getter: Callable[[], Optional[str]] = current_owner_id,
    ) -> None:
        database_path = Path(os.getenv("DATABASE_PATH", "./data/stock_analysis.db")).resolve()
        configured_root = os.getenv("RESEARCH_NOTE_AUDIO_ANALYSIS_ROOT", "").strip()
        self.task_root = Path(task_root or configured_root or database_path.parent / "research_note_audio_analysis").resolve()
        self.output_root = self.task_root / "outputs"
        self.temp_root = self.task_root / "temporary"
        for directory in (self.task_root, self.output_root, self.temp_root):
            directory.mkdir(parents=True, exist_ok=True)
        self._service_factory = service_factory
        self._should_index = service_factory is ResearchNoteService
        self._resolver = resolver or (lambda topic_id, file_id: ZsxqMcpSyncService().resolve_media_url_sync(topic_id, "files", file_id))
        self._http_get = http_get
        self._transcriber = transcriber or ConfiguredAudioTranscriber()
        self._analyzer = analyzer or DeepSeekAudioMemoAnalyzer()
        self._owner_getter = owner_getter
        self.max_files = max(1, min(int(os.getenv("AUDIO_ANALYSIS_MAX_FILES", "8")), 20))
        self.max_file_bytes = max(1, int(os.getenv("AUDIO_TRANSCRIPTION_MAX_FILE_MB", "512"))) * 1024 * 1024
        self.retention_hours = max(24, min(int(os.getenv("AUDIO_ANALYSIS_RETENTION_HOURS", "168")), 720))
        worker_count = workers if workers is not None else int(os.getenv("AUDIO_ANALYSIS_WORKERS", "1"))
        self._executor = ThreadPoolExecutor(max_workers=max(1, min(worker_count, 2)), thread_name_prefix="audio-analysis")
        self._lock = threading.RLock()
        self._resume_interrupted_tasks()
        self._cleanup_expired_tasks()

    @classmethod
    def get_instance(cls) -> "ResearchNoteAudioAnalysisTaskService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def capability(self) -> Dict[str, Any]:
        transcription_configured = bool(getattr(self._transcriber, "configured", True))
        analysis_configured = bool(getattr(self._analyzer, "configured", True))
        return {
            "configured": transcription_configured and analysis_configured,
            "transcription_configured": transcription_configured,
            "analysis_configured": analysis_configured,
            "transcription_provider": str(getattr(self._transcriber, "provider", "custom")),
            "max_files": self.max_files,
            "max_file_mb": self.max_file_bytes // 1024 // 1024,
            "message": "可提交后台录音纪要任务" if transcription_configured else "需要管理员配置阿里云百炼 API Key 后启用语音转写",
        }

    def submit(
        self,
        items: Iterable[Tuple[str, str]],
        *,
        title: str = "",
        focus: str = "",
        hotwords: Optional[Iterable[str]] = None,
        speaker_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        selected = list(dict.fromkeys((str(topic).strip(), str(file_id).strip()) for topic, file_id in items))
        if not selected:
            raise ResearchNoteAudioAnalysisError("至少选择一个录音文件")
        if len(selected) > self.max_files:
            raise ResearchNoteAudioAnalysisError(f"单次最多分析 {self.max_files} 个录音文件")
        capability = self.capability()
        if not capability["configured"]:
            raise ResearchNoteAudioAnalysisError(str(capability["message"]))
        now = datetime.now(timezone.utc)
        task_id = f"audio-analysis-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
        normalized_hotwords = list(dict.fromkeys(
            str(value).strip()[:80] for value in (hotwords or []) if str(value).strip()
        ))[:100]
        normalized_speakers = int(speaker_count) if speaker_count and 2 <= int(speaker_count) <= 20 else None
        state = {
            "task_id": task_id, "owner_id": self._owner_getter(), "status": "queued", "phase": "queued",
            "progress": 0, "message": "已进入后台队列，可以离开当前页面", "title": title.strip()[:160],
            "focus": focus.strip()[:500], "total_files": len(selected), "completed_files": 0,
            "items": [{"topic_id": topic_id, "file_id": file_id} for topic_id, file_id in selected],
            "hotwords": normalized_hotwords, "speaker_count": normalized_speakers, "retry_count": 0,
            "transcript_artifacts": [], "indexed": False, "library_topic_id": None,
            "current_filename": None, "result": None, "download_urls": {}, "error": None,
            "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=self.retention_hours)).isoformat(),
        }
        self._write_state(state)
        self._executor.submit(self._run, task_id, selected)
        return self._public_state(state)

    def retry(self, task_id: str) -> Dict[str, Any]:
        state = self._read_state(task_id)
        self._assert_owner(state)
        if state.get("status") in _ACTIVE_STATUSES:
            return self._public_state(state)
        selected = self._selected_from_state(state)
        if not selected:
            raise ResearchNoteAudioAnalysisError("旧任务缺少可恢复的录音清单，请重新选择录音提交")
        capability = self.capability()
        if not capability["configured"]:
            raise ResearchNoteAudioAnalysisError(str(capability["message"]))
        self._update(
            task_id,
            status="queued",
            phase="queued",
            progress=0,
            completed_files=len(state.get("transcript_artifacts") or []),
            current_filename=None,
            message="已重新进入后台队列，可以离开当前页面",
            error=None,
            result=None,
            download_urls={},
            retry_count=int(state.get("retry_count") or 0) + 1,
        )
        self._executor.submit(self._run, task_id, selected)
        return self.get(task_id)

    def transcript(self, task_id: str, file_id: str) -> Dict[str, Any]:
        state = self._read_state(task_id)
        self._assert_owner(state)
        artifact = next(
            (item for item in state.get("transcript_artifacts") or [] if str(item.get("file_id")) == str(file_id)),
            None,
        )
        if not artifact:
            raise ResearchNoteAudioAnalysisError("逐字稿不存在或尚未生成")
        path = self.output_root / task_id / str(artifact.get("artifact_name") or "")
        if not path.is_file():
            raise ResearchNoteAudioAnalysisError("逐字稿文件不存在或已过期")
        text = path.read_text(encoding="utf-8")
        return {
            "file_id": str(file_id),
            "filename": artifact.get("filename"),
            "text": text,
            "lines": self._parse_transcript_lines(text),
        }

    def list_tasks(self, limit: int = 20) -> Dict[str, Any]:
        owner_id = self._owner_getter()
        rows = []
        for path in sorted(self.task_root.glob("audio-analysis-*.json"), reverse=True):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if state.get("owner_id") == owner_id:
                rows.append(self._public_state(state, include_result=False))
            if len(rows) >= max(1, min(int(limit), 50)):
                break
        return {"items": rows, "total": len(rows)}

    def get(self, task_id: str) -> Dict[str, Any]:
        state = self._read_state(task_id)
        self._assert_owner(state)
        return self._public_state(state)

    def download(self, task_id: str, artifact: str) -> Tuple[Path, str, str]:
        state = self._read_state(task_id)
        self._assert_owner(state)
        if state.get("status") != "completed":
            raise ResearchNoteAudioAnalysisError("录音纪要仍在后台生成")
        formats = {"zip": ("bundle.zip", "application/zip"), "md": ("report.md", "text/markdown; charset=utf-8"), "docx": ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), "json": ("result.json", "application/json")}
        suffix, media_type = formats.get(str(artifact).lower(), formats["zip"])
        path = self.output_root / task_id / suffix
        if not path.is_file():
            raise ResearchNoteAudioAnalysisError("录音纪要下载文件不存在或已过期")
        title = self._safe_name(str((state.get("result") or {}).get("title") or "录音纪要"))
        return path, f"{title}.{artifact if artifact in formats else 'zip'}", media_type

    def _run(self, task_id: str, selected: List[Tuple[str, str]]) -> None:
        work_dir = self.temp_root / task_id
        output_dir = self.output_root / task_id
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            state = self._read_state(task_id)
            hotwords = list(state.get("hotwords") or [])
            speaker_count = state.get("speaker_count")
            service = self._service_factory()
            transcripts: List[Dict[str, Any]] = []
            transcript_artifacts: List[Dict[str, Any]] = []
            cached_artifacts = {
                str(item.get("file_id") or ""): item
                for item in (state.get("transcript_artifacts") or [])
                if isinstance(item, dict) and item.get("artifact_name")
            }
            for index, (topic_id, file_id) in enumerate(selected):
                note = service.get_note(topic_id)
                asset = next((item for item in note.get("files", []) if str(item.get("file_id") or "") == file_id), None)
                if asset is None or asset.get("asset_kind") != "audio":
                    raise FinancialDataValidationError(f"录音附件不存在：{topic_id}/{file_id}")
                filename = self._safe_name(str(asset.get("name") or f"录音-{file_id}"))
                declared_size = int(asset.get("size") or 0)
                if declared_size > self.max_file_bytes:
                    raise ResearchNoteAudioAnalysisError(f"《{filename}》超过单文件 {self.max_file_bytes // 1024 // 1024}MB 转写限制")
                cached = cached_artifacts.get(file_id)
                cached_path = output_dir / str((cached or {}).get("artifact_name") or "")
                if cached and cached_path.is_file():
                    transcript = cached_path.read_text(encoding="utf-8")
                    transcripts.append({
                        "filename": filename,
                        "topic_id": topic_id,
                        "file_id": file_id,
                        "note_title": note.get("title"),
                        "created_at": note.get("created_at"),
                        "transcript": transcript,
                    })
                    transcript_artifacts.append(dict(cached))
                    self._update(
                        task_id,
                        status="running",
                        phase="transcribing",
                        progress=18 + int((index + 1) / len(selected) * 55),
                        completed_files=index + 1,
                        transcript_artifacts=transcript_artifacts,
                        current_filename=filename,
                        message=f"已复用逐字稿 {index + 1}/{len(selected)} · {filename}",
                    )
                    continue
                self._update(task_id, status="running", phase="downloading", progress=5 + int(index / len(selected) * 55), current_filename=filename, message=f"正在读取录音 {index + 1}/{len(selected)} · {filename}")
                local_path = work_dir / f"{index + 1:02d}-{filename}"
                source_url = self._resolver(topic_id, file_id)
                response = self._http_get(source_url, stream=True, timeout=(10, 300))
                loaded = 0
                try:
                    response.raise_for_status()
                    with local_path.open("wb") as target:
                        for chunk in response.iter_content(chunk_size=256 * 1024):
                            if not chunk:
                                continue
                            target.write(chunk)
                            loaded += len(chunk)
                            if loaded > self.max_file_bytes:
                                raise ResearchNoteAudioAnalysisError(f"《{filename}》超过单文件转写限制")
                finally:
                    response.close()
                file_progress_base = 12 + int(index / len(selected) * 58)
                self._update(task_id, phase="transcribing", progress=file_progress_base, message=f"正在语音转写 {index + 1}/{len(selected)} · {filename}")

                def update_transcription(_status: str, message: str) -> None:
                    self._update(
                        task_id,
                        phase="transcribing",
                        progress=file_progress_base,
                        current_filename=filename,
                        message=f"{message}（{index + 1}/{len(selected)}）",
                    )

                transcribe = getattr(self._transcriber, "transcribe", None)
                if callable(transcribe):
                    transcript = transcribe(
                        local_path,
                        filename,
                        source_url,
                        progress=update_transcription,
                        hotwords=self._task_hotwords(hotwords, filename, note),
                        speaker_count=speaker_count,
                    )
                else:
                    transcript = self._transcriber(local_path, filename)
                local_path.unlink(missing_ok=True)
                transcript_name = f"{index + 1:02d}-{Path(filename).stem}.txt"
                (output_dir / transcript_name).write_text(transcript, encoding="utf-8")
                transcript_artifacts.append({
                    "file_id": file_id,
                    "topic_id": topic_id,
                    "filename": filename,
                    "artifact_name": transcript_name,
                    "line_count": len(str(transcript).splitlines()),
                })
                transcripts.append({"filename": filename, "topic_id": topic_id, "file_id": file_id, "note_title": note.get("title"), "created_at": note.get("created_at"), "transcript": transcript})
                self._update(task_id, completed_files=index + 1, transcript_artifacts=transcript_artifacts, progress=18 + int((index + 1) / len(selected) * 55))
            self._update(task_id, phase="analyzing", progress=76, current_filename=None, message="正在交叉整理转写内容并生成录音纪要")
            requested_title = str(state.get("title") or "").strip() or f"{transcripts[0]['note_title'] or Path(transcripts[0]['filename']).stem} · 录音纪要"
            result = self._analyzer(transcripts, requested_title, str(state.get("focus") or ""))
            result["title"] = str(result.get("title") or requested_title).strip()[:180]
            result["generated_at"] = datetime.now(timezone.utc).isoformat()
            result["source_files"] = [{key: item.get(key) for key in ("filename", "topic_id", "file_id", "note_title", "created_at")} for item in transcripts]
            markdown = self._render_markdown(result)
            library_topic_id = self._index_completed_memo(task_id, result, markdown)
            result["library_topic_id"] = library_topic_id
            result["indexed"] = bool(library_topic_id)
            markdown = self._render_markdown(result)
            (output_dir / "report.md").write_text(markdown, encoding="utf-8")
            (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            self._write_docx(output_dir / "report.docx", result["title"], markdown)
            with zipfile.ZipFile(output_dir / "bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in output_dir.iterdir():
                    if path.name != "bundle.zip":
                        archive.write(path, arcname=("转写文本/" + path.name) if path.suffix == ".txt" else path.name)
            urls = {fmt: f"/api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/download?format={fmt}" for fmt in ("zip", "md", "docx", "json")}
            self._update(task_id, status="completed", phase="completed", progress=100, message="录音纪要已生成，并写入统一检索与情报库", result=result, download_urls=urls, error=None, indexed=bool(library_topic_id), library_topic_id=library_topic_id)
        except Exception as exc:  # noqa: BLE001 - background task failures must stay visible.
            logger.exception("Audio analysis task failed task_id=%s", task_id)
            message = str(exc).strip()[:500] or f"录音分析失败：{type(exc).__name__}"
            self._update(task_id, status="failed", phase="failed", progress=100, message=message, current_filename=None, error=message)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _render_markdown(result: Dict[str, Any]) -> str:
        lines = [f"# {result.get('title') or '录音纪要'}", "", f"> 生成时间：{result.get('generated_at') or '—'}", "", "## 核心摘要", "", str(result.get("executive_summary") or "")]
        sections = [("会议背景", "meeting_context"), ("核心结论", "key_conclusions"), ("产业链脉络", "industry_chain"), ("业绩与估值预测", "financial_forecasts"), ("催化因素", "catalysts"), ("风险与反例", "risks"), ("分歧", "disagreements"), ("说话人观点", "speaker_views"), ("证据索引", "evidence_index"), ("跟踪清单", "monitoring_items"), ("后续跟踪", "follow_ups")]
        companies = result.get("company_mentions") or []
        if companies:
            lines += ["", "## 公司与标的", ""] + [f"- **{item.get('name', '未命名')}**：{item.get('view') or ''}（依据：{item.get('evidence') or '未标注'}）" if isinstance(item, dict) else f"- {item}" for item in companies]
        for label, key in sections:
            value = result.get(key)
            if not value:
                continue
            lines += ["", f"## {label}", ""]
            if isinstance(value, list):
                for item in value:
                    lines.append(f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}")
            else:
                lines.append(str(value))
        lines += ["", "## 转写质量说明", "", str(result.get("transcript_quality") or "未提供"), "", "## 来源录音", ""]
        lines += [f"- {item.get('filename')} · {item.get('note_title') or '原帖子未命名'}" for item in result.get("source_files") or [] if isinstance(item, dict)]
        lines += ["", "---", "本报告由语音转写与 AI 整理生成，可能存在识别误差；数字、公司名称与结论须回听原录音核验，不构成投资建议。", ""]
        return "\n".join(lines)

    @staticmethod
    def _write_docx(path: Path, title: str, markdown: str) -> None:
        paragraphs = []
        for line in markdown.splitlines():
            text = line.lstrip("#- ").strip()
            if not text:
                continue
            paragraphs.append(f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')
        document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + "".join(paragraphs) + '<w:sectPr/></w:body></w:document>'
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr("word/document.xml", document)

    def _read_state(self, task_id: str) -> Dict[str, Any]:
        path = self._task_path(task_id)
        if not path.is_file():
            raise ResearchNoteAudioAnalysisError("录音分析任务不存在或已过期")
        try:
            with self._lock:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResearchNoteAudioAnalysisError("录音分析任务状态暂时不可读") from exc

    def _update(self, task_id: str, **changes: Any) -> None:
        state = self._read_state(task_id)
        state.update(changes)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_state(state)

    def _write_state(self, state: Dict[str, Any]) -> None:
        path = self._task_path(str(state["task_id"]))
        temporary = path.with_suffix(".json.tmp")
        with self._lock:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    def _task_path(self, task_id: str) -> Path:
        if not _TASK_ID_RE.fullmatch(str(task_id or "")):
            raise ResearchNoteAudioAnalysisError("无效的录音分析任务编号")
        return self.task_root / f"{task_id}.json"

    def _assert_owner(self, state: Dict[str, Any]) -> None:
        if state.get("owner_id") != self._owner_getter():
            raise ResearchNoteAudioAnalysisError("录音分析任务不存在或无权访问")

    @staticmethod
    def _public_state(state: Dict[str, Any], *, include_result: bool = True) -> Dict[str, Any]:
        result = {key: value for key, value in state.items() if key not in {"owner_id", "items"}}
        result["transcript_artifacts"] = [
            {key: value for key, value in item.items() if key != "artifact_name"}
            for item in (state.get("transcript_artifacts") or [])
            if isinstance(item, dict)
        ]
        if not include_result:
            result["result"] = None
        return result

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", Path(value).name).strip(" ._")
        return cleaned[:180] or "录音"

    @staticmethod
    def _selected_from_state(state: Dict[str, Any]) -> List[Tuple[str, str]]:
        return [
            (str(item.get("topic_id") or ""), str(item.get("file_id") or ""))
            for item in (state.get("items") or [])
            if isinstance(item, dict) and item.get("topic_id") and item.get("file_id")
        ]

    @staticmethod
    def _task_hotwords(explicit: Iterable[str], filename: str, note: Dict[str, Any]) -> List[str]:
        candidates = list(explicit) + [Path(filename).stem, str(note.get("title") or "")]
        terms: List[str] = []
        for candidate in candidates:
            for value in re.split(r"[\s,，、/|：:；;（）()\[\]【】]+", str(candidate)):
                cleaned = value.strip()
                if 2 <= len(cleaned) <= 80 and cleaned not in terms:
                    terms.append(cleaned)
        return terms[:100]

    @staticmethod
    def _parse_transcript_lines(text: str) -> List[Dict[str, str]]:
        pattern = re.compile(r"^\[(?P<timestamp>\d{2}:\d{2}:\d{2})\](?:\s*(?P<speaker>[^：:]+))?[：:]\s*(?P<text>.*)$")
        lines: List[Dict[str, str]] = []
        for raw_line in str(text or "").splitlines():
            match = pattern.match(raw_line.strip())
            if match:
                lines.append({
                    "timestamp": match.group("timestamp"),
                    "speaker": (match.group("speaker") or "未标注").strip(),
                    "text": match.group("text").strip(),
                })
            elif raw_line.strip():
                lines.append({"timestamp": "", "speaker": "未标注", "text": raw_line.strip()})
        return lines

    def _index_completed_memo(self, task_id: str, result: Dict[str, Any], markdown: str) -> Optional[str]:
        """Publish one generated memo into both shared retrieval surfaces."""
        if not self._should_index:
            return None
        try:
            digest = hashlib.sha1(task_id.encode("utf-8")).hexdigest()[:20]
            topic_id = f"audio-memo-{digest}"
            generated = datetime.now(timezone.utc).replace(tzinfo=None)
            title = str(result.get("title") or "AI录音纪要")[:500]
            symbols = sorted({
                self._normalize_symbol(str(item.get("ts_code") or ""))
                for item in (result.get("company_mentions") or [])
                if isinstance(item, dict) and self._normalize_symbol(str(item.get("ts_code") or ""))
            })
            files = [{
                "name": str(item.get("filename") or "录音"),
                "file_id": str(item.get("file_id") or ""),
                "source_topic_id": str(item.get("topic_id") or ""),
                "asset_kind": "audio",
            } for item in (result.get("source_files") or []) if isinstance(item, dict)]
            content_hash = research_note_information_hash(
                title=title,
                content=markdown,
                files=files,
                images=[],
            )
            ResearchNoteRepository().upsert_notes([{
                "topic_id": topic_id,
                "group_id": "ai-audio-memo",
                "group_name": "AI录音纪要",
                "title": title,
                "content": markdown,
                "author_id": "deepseek-audio-memo",
                "author_name": "DeepSeek录音研究员",
                "topic_type": "audio_memo",
                "text_type": "article",
                "digested": True,
                "sticky": False,
                "symbol_codes": ",".join(symbols),
                "files_json": json.dumps(files, ensure_ascii=False),
                "images_json": "[]",
                "counts_json": "{}",
                "raw_payload": json.dumps({"task_id": task_id, "result": result}, ensure_ascii=False, default=str),
                "content_hash": content_hash,
                "created_at": generated,
                "modified_at": generated,
                "synced_at": generated,
            }])
            InvestmentMonitorRepository().upsert_events([{
                "source_key": "audio.memo.ai",
                "source_name": "AI录音纪要",
                "source_type": "essay",
                "external_id": task_id,
                "event_type": "audio_memo",
                "perspective": "institution",
                "title": title,
                "summary": str(result.get("executive_summary") or markdown)[:10000],
                "symbols": symbols,
                "sentiment": "neutral",
                "importance_score": 72,
                "confidence_score": 0.68,
                "tags": ["录音纪要", "AI转写", "待回听核验"],
                "actors": [str(item.get("name")) for item in (result.get("company_mentions") or []) if isinstance(item, dict) and item.get("name")],
                "metrics": {"channel": "audio_memo", "evidence_level": "ai_transcript", "library_topic_id": topic_id},
                "raw_payload": {"task_id": task_id, "result": result},
                "event_at": generated,
                "ingested_at": generated,
            }])
            return topic_id
        except Exception:  # noqa: BLE001 - report generation must survive an index outage.
            logger.exception("Could not index completed audio memo task_id=%s", task_id)
            return None

    @staticmethod
    def _normalize_symbol(value: str) -> str:
        symbol = str(value or "").strip().upper()
        if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", symbol):
            return symbol
        if re.fullmatch(r"\d{6}", symbol):
            if symbol.startswith(("6", "9")):
                return f"{symbol}.SH"
            if symbol.startswith(("0", "3")):
                return f"{symbol}.SZ"
            return f"{symbol}.BJ"
        return ""

    def _resume_interrupted_tasks(self) -> None:
        for path in self.task_root.glob("audio-analysis-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if state.get("status") in _ACTIVE_STATUSES:
                selected = self._selected_from_state(state)
                if selected:
                    state.update({"status": "queued", "phase": "resuming", "message": "服务已恢复，正在续跑录音分析", "error": None})
                    self._write_state(state)
                    self._executor.submit(self._run, str(state["task_id"]), selected)
                else:
                    state.update({"status": "failed", "phase": "interrupted", "progress": 100, "message": "旧任务缺少恢复清单，请重新选择录音提交", "error": "旧任务缺少恢复清单，请重新选择录音提交"})
                    self._write_state(state)

    def _cleanup_expired_tasks(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        for path in self.task_root.glob("audio-analysis-*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                updated = datetime.fromisoformat(str(state.get("updated_at") or ""))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
            except (OSError, ValueError):
                continue
            if state.get("status") in _ACTIVE_STATUSES or updated >= cutoff:
                continue
            shutil.rmtree(self.output_root / str(state.get("task_id") or ""), ignore_errors=True)
            path.unlink(missing_ok=True)
