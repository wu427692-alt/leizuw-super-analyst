"""Published sample reads never query private jobs or start paid work."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.v1.endpoints import jev_backtest
from src.services import jev_event_service as service_module
from tests.test_jev_event_api import client_for


def sample(task_id):
    return {"task_id": task_id, "visibility": "public_sample", "status": "completed", "progress": 100,
            "created_at": "2026-09-20T00:00:00Z", "message": "公开样本已完成", "owner": "event:integration",
            "config": {"symbol": "NVDA", "currency": "USD", "request_id": "private-request", "owner_id": 99},
            "usage": {"requests": 2, "cached": 0, "cost_usd": .001, "api_key": "test-private"},
            "result": {"summary": {"incremental_t_pnl": -1, "owner": "private-owner"},
                       "equity": [{"timestamp": "2025-09-16T10:00:00-04:00", "event_index": 1, "equity": 400000,
                                   "owner": "private-owner", "price": {"owner": "nested-private"}}],
                       "events": [], "signals": [], "trades": [],
                       "market_data": {"path": "/private/source"},
                       "data_quality": {"warnings": ["真实样本"], "private_path": "/private"},
                       "methodology": ["没有未来数据。"]}}


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_CB_DATA_DIR", str(tmp_path))
    task_id = str(uuid4())
    (tmp_path / "research.json").write_text(json.dumps({"status": "completed", "rounds": [{"task_id": task_id}]}))
    result_dir = tmp_path / "research-results"
    result_dir.mkdir()
    path = result_dir / f"{task_id}.json"
    path.write_text(json.dumps(sample(task_id)))
    return tmp_path, task_id, path


def test_public_projection_excludes_private_metadata_at_every_level(published):
    _, task_id, _ = published
    result = service_module.research_round(task_id)
    assert result["task_id"] == task_id
    assert result["visibility"] == "public_sample"
    assert result["result"]["summary"]["incremental_t_pnl"] == -1
    serialized = json.dumps(result)
    for sensitive in ("owner", "request_id", "api_key", "/private", "nested-private"):
        assert sensitive not in serialized


def test_only_manifest_listed_uuids_with_explicit_published_identity_are_readable(published):
    root, task_id, path = published
    unknown = str(uuid4())
    (path.parent / f"{unknown}.json").write_text(json.dumps(sample(unknown)))
    assert service_module.research_round(unknown) is None
    for invalid in ("../research", "../../private", "not-a-uuid"):
        assert service_module.research_round(invalid) is None
    for changes in ({"task_id": unknown}, {"visibility": "private"}, {"status": "running"}):
        path.write_text(json.dumps({**sample(task_id), **changes}))
        assert service_module.research_round(task_id) is None
    (root / "research.json").write_text("not-json")
    assert service_module.research_round(task_id) is None


def test_rejects_symlinked_result_file_and_directory(published, tmp_path):
    root, task_id, path = published
    payload = path.read_bytes()
    outside = root / "private.json"
    outside.write_bytes(payload)
    path.unlink()
    path.symlink_to(outside)
    assert service_module.research_round(task_id) is None
    path.unlink()
    path.parent.rmdir()
    other = root / "other-results"
    other.mkdir()
    (other / path.name).write_bytes(payload)
    path.parent.symlink_to(other, target_is_directory=True)
    assert service_module.research_round(task_id) is None


def test_rejects_oversized_or_malformed_files_and_manifest_symlink(published, monkeypatch):
    root, task_id, path = published
    monkeypatch.setattr(service_module, "_RESEARCH_RESULT_MAX_BYTES", 50)
    assert service_module.research_round(task_id) is None
    monkeypatch.setattr(service_module, "_RESEARCH_RESULT_MAX_BYTES", 32000000)
    path.write_text("{")
    assert service_module.research_round(task_id) is None
    manifest = root / "research.json"
    manifest.rename(root / "actual.json")
    manifest.symlink_to(root / "actual.json")
    assert service_module.research_round(task_id) is None


def test_authenticated_users_share_samples_but_keep_private_task_ownership(published, monkeypatch):
    _, task_id, _ = published
    client, service = client_for(monkeypatch)
    monkeypatch.setattr(jev_backtest, "_resolve_request_user", lambda request: (
        SimpleNamespace(id=int(request.headers.get("X-Test-User", "1")), status="approved"), "user"))
    service.repo.get.side_effect = lambda owner, _: {"task_id": task_id} if owner == "event:user:1" else None
    for user in ("1", "2"):
        response = client.get(f"/event/research/rounds/{task_id}", headers={"X-Test-User": user})
        assert response.status_code == 200
        assert response.json()["visibility"] == "public_sample"
        assert "no-store" in response.headers["cache-control"]
    service.repo.get.assert_not_called()
    assert client.get(f"/event/tasks/{task_id}", headers={"X-Test-User": "1"}).status_code == 200
    assert client.get(f"/event/tasks/{task_id}", headers={"X-Test-User": "2"}).status_code == 404
    service.submit.assert_not_called()
    service.cancel.assert_not_called()


def test_sample_endpoint_requires_login_and_rejects_unlisted_uuid(published, monkeypatch):
    _, task_id, _ = published
    client, service = client_for(monkeypatch, False)
    assert client.get(f"/event/research/rounds/{task_id}").status_code == 401
    client, service = client_for(monkeypatch, True)
    assert client.get(f"/event/research/rounds/{uuid4()}").status_code == 404
    assert client.get("/event/research/rounds/not-a-uuid").status_code == 422
    service.repo.get.assert_not_called()
    service.submit.assert_not_called()
