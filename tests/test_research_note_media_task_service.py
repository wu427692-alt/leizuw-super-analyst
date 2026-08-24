from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile, ZIP_STORED

from src.services.research_note_media_task_service import ResearchNoteMediaTaskService


class _FakeResearchNoteService:
    def get_note(self, topic_id: str):
        return {
            "topic_id": topic_id,
            "title": "低空经济录音合集",
            "group_name": "调研纪要",
            "created_at": "2026-08-24T09:00:00+08:00",
            "files": [
                {"file_id": "audio-1", "name": "行业交流.mp3", "size": 5, "asset_kind": "audio"},
                {"file_id": "audio-2", "name": "行业交流.mp3", "size": 6, "asset_kind": "audio"},
            ],
        }


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield self.payload

    def close(self) -> None:
        return None


def test_selected_audio_runs_in_background_and_persists_downloadable_progress(tmp_path: Path) -> None:
    payloads = {"audio-1": b"first", "audio-2": b"second"}
    service = ResearchNoteMediaTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic_id, file_id: f"https://example.test/{file_id}",
        http_get=lambda url, **_kwargs: _FakeResponse(payloads[url.rsplit("/", 1)[-1]]),
        task_root=tmp_path,
        workers=1,
    )

    submitted = service.submit([("topic-1", "audio-1"), ("topic-1", "audio-2")])
    assert submitted["status"] == "queued"
    assert submitted["progress"] == 0
    service._executor.shutdown(wait=True)  # Wait only in the deterministic unit test.

    completed = service.get(submitted["task_id"])
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["completed_files"] == 2
    assert completed["downloaded_bytes"] == 11
    assert completed["archive_bytes"] > 0
    assert completed["download_url"].endswith(f"/{submitted['task_id']}/download")

    archive_path, filename = service.download(submitted["task_id"])
    assert filename.startswith("知识星球录音_已选2个_")
    with ZipFile(archive_path) as archive:
        assert archive.getinfo("录音文件/行业交流.mp3").compress_type == ZIP_STORED
        assert archive.read("录音文件/行业交流.mp3") == b"first"
        assert archive.read("录音文件/行业交流_2.mp3") == b"second"
        manifest = json.loads(archive.read("下载清单.json"))
        assert [item["size"] for item in manifest] == [5, 6]


def test_missing_audio_becomes_visible_failed_task(tmp_path: Path) -> None:
    service = ResearchNoteMediaTaskService(
        service_factory=_FakeResearchNoteService,
        resolver=lambda _topic_id, _file_id: "https://example.test/missing",
        http_get=lambda *_args, **_kwargs: _FakeResponse(b"unused"),
        task_root=tmp_path,
        workers=1,
    )

    submitted = service.submit([("topic-1", "missing")])
    service._executor.shutdown(wait=True)

    failed = service.get(submitted["task_id"])
    assert failed["status"] == "failed"
    assert failed["phase"] == "failed"
    assert "录音附件不存在" in failed["message"]
