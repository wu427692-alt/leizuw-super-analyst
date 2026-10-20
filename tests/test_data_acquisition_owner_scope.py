"""Offline package ownership checks, including real executor thread boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import zipfile

import pytest

from src.request_identity import (
    current_owner_id,
    reset_current_user_id,
    set_current_user_id,
)
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService
from src.services.data_acquisition_task_service import DataAcquisitionTaskService


@contextmanager
def as_user(user_id):
    token = set_current_user_id(user_id)
    try:
        yield
    finally:
        reset_current_user_id(token)


class OfflineFinancial:
    def query(self, **kwargs):
        return {"rows": [{"ts_code": "603306.SH", "close": 42.5}]}


@pytest.fixture
def package_factory(tmp_path, monkeypatch):
    # Never use the real runtime config, database, providers or AI planner.
    monkeypatch.setattr(
        "src.services.data_acquisition_service.get_config",
        lambda: SimpleNamespace(database_path=str(tmp_path / "unused.db")),
    )
    monkeypatch.setenv("DATA_ACQUISITION_MAX_WORKERS", "1")

    def no_network(*args, **kwargs):
        raise AssertionError("Ownership tests must not call a network provider")

    monkeypatch.setattr("requests.sessions.Session.request", no_network)

    def factory():
        return DataAcquisitionService(
            planner=SimpleNamespace(model="offline-test", api_key=""),
            financial=OfflineFinancial(),
            monitor=object(),
            cninfo=object(),
            output_root=tmp_path / "packages",
        )

    return factory


@pytest.fixture
def confirmed_plan():
    return {
        "title": "离线取数验收",
        "tasks": [{"id": "daily", "source": "tushare", "resource": "daily",
                   "params": {"ts_code": "603306.SH"}}],
    }


def test_background_packages_keep_submitter_identity_across_reused_thread(
    package_factory, confirmed_plan, tmp_path,
):
    observed = []

    def observed_factory():
        observed.append((threading.get_ident(), current_owner_id()))
        return package_factory()

    manager = DataAcquisitionTaskService(
        service_factory=observed_factory, task_root=tmp_path / "tasks",
    )
    release = threading.Event()
    # Queue both requests before the worker starts: neither may read the later
    # caller's identity, and the reused executor thread must return to no owner.
    blocker = manager._executor.submit(release.wait, 5)
    try:
        with as_user(11):
            first = manager.submit("获取603306日线", confirmed_plan)
        with as_user(22):
            second = manager.submit("获取603306日线", confirmed_plan)
        release.set()
        assert blocker.result(timeout=5)
        worker_identity = manager._executor.submit(current_owner_id).result(timeout=10)
        assert worker_identity is None
        # Submission now validates selectors synchronously *before* queuing.
        # Both that caller-side check and the reused worker must keep identity.
        caller_checks = [(thread, owner) for thread, owner in observed[1:] if thread == threading.get_ident()]
        worker_checks = [(thread, owner) for thread, owner in observed[1:] if thread != threading.get_ident()]
        assert [owner for _, owner in caller_checks] == ["user:11", "user:22"]
        assert [owner for _, owner in worker_checks] == ["user:11", "user:22"]
        assert len({thread for thread, _ in worker_checks}) == 1

        for user_id, submitted, other in ((11, first, second), (22, second, first)):
            with as_user(user_id):
                task = manager.get(submitted["task_id"])
                assert task["status"] == "completed"
                assert task["result"]["summary"]["row_count"] == 1
                assert "owner_id" not in task["result"]
                service = package_factory()
                job = service.get_job(task["job_id"])
                assert "owner_id" not in job
                assert service.package_path(job["job_id"]).is_file()
                assert [item["job_id"] for item in service.list_jobs()["items"]] == [job["job_id"]]
                with pytest.raises(DataAcquisitionError):
                    manager.get(other["task_id"])
                with zipfile.ZipFile(service.package_path(job["job_id"])) as archive:
                    exported = json.loads(archive.read("manifest.json"))
                    assert "owner_id" not in exported
                stored = json.loads((service.output_root / job["job_id"] / "manifest.json").read_text())
                assert stored["owner_id"] == f"user:{user_id}"

        with as_user(None):
            assert package_factory().list_jobs() == {"items": [], "total": 0}
    finally:
        release.set()
        manager._executor.shutdown(wait=True)


def test_sync_packages_are_filtered_before_limit_and_cannot_be_downloaded_by_another_user(
    package_factory, confirmed_plan,
):
    service = package_factory()
    with as_user(11):
        own = service.run("获取603306日线", confirmed_plan)
    with as_user(22):
        other = service.run("获取603306日线", confirmed_plan)
        with pytest.raises(DataAcquisitionError):
            service.get_job(own["job_id"])
        with pytest.raises(DataAcquisitionError):
            service.package_path(own["job_id"])
    with as_user(11):
        listed = service.list_jobs(limit=1)
        assert [item["job_id"] for item in listed["items"]] == [own["job_id"]]
        assert "owner_id" not in listed["items"][0]
        with pytest.raises(DataAcquisitionError):
            service.get_job(other["job_id"])
        with pytest.raises(DataAcquisitionError):
            service.package_path(other["job_id"])


def test_legacy_unowned_packages_remain_only_in_legacy_admin_scope(package_factory):
    service = package_factory()
    job_id = "legacy-package-123"
    directory = service.output_root / job_id
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"job_id": job_id, "title": "旧包"}))
    (directory / f"{job_id}.zip").write_bytes(b"legacy-archive")

    with as_user(11):
        assert service.list_jobs() == {"items": [], "total": 0}
        with pytest.raises(DataAcquisitionError):
            service.get_job(job_id)
        with pytest.raises(DataAcquisitionError):
            service.package_path(job_id)
    with as_user(0):
        assert service.get_job(job_id)["title"] == "旧包"
        assert service.package_path(job_id).is_file()
        assert service.list_jobs()["total"] == 1


def test_package_without_readable_owner_metadata_is_not_downloadable(package_factory):
    service = package_factory()
    for index, contents in enumerate((None, "{broken", "[]")):
        job_id = f"incomplete-package-{index}"
        directory = service.output_root / job_id
        directory.mkdir()
        (directory / f"{job_id}.zip").write_bytes(b"private-archive")
        if contents is not None:
            (directory / "manifest.json").write_text(contents)
        with as_user(11), pytest.raises(DataAcquisitionError):
            service.package_path(job_id)


def write_legacy_task(service, job_id, owner, suffix="first", **updates):
    task_root = service.output_root / ".tasks"
    task_root.mkdir(exist_ok=True)
    task_id = f"acq-legacy-{suffix}"
    state = {"task_id": task_id, "owner_id": owner, "status": "completed",
             "job_id": job_id, "result": {"job_id": job_id}}
    state.update(updates)
    (task_root / f"{task_id}.json").write_text(json.dumps(state))


def make_legacy_package(service, job_id):
    directory = service.output_root / job_id
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"job_id": job_id, "title": "旧用户数据包"}))
    (directory / f"{job_id}.zip").write_bytes(b"legacy-archive")
    return directory


def test_legacy_package_owner_is_proven_read_only_from_completed_task_ledger(package_factory):
    service = package_factory()
    job_id = "legacy-user-package"
    directory = make_legacy_package(service, job_id)
    write_legacy_task(service, job_id, "user:11")
    before = {path: path.read_bytes() for path in service.output_root.rglob("*") if path.is_file()}

    with as_user(11):
        assert service.get_job(job_id)["title"] == "旧用户数据包"
        assert service.package_path(job_id).is_file()
        assert service.list_jobs()["items"][0]["job_id"] == job_id
        assert "owner_id" not in service.get_job(job_id)
    for user_id in (22, 0, None):
        with as_user(user_id):
            assert service.list_jobs() == {"items": [], "total": 0}
            with pytest.raises(DataAcquisitionError):
                service.get_job(job_id)
            with pytest.raises(DataAcquisitionError):
                service.package_path(job_id)
    assert {path: path.read_bytes() for path in before} == before
    assert "owner_id" not in json.loads((directory / "manifest.json").read_text())


@pytest.mark.parametrize("conflict", ["other-owner", "mismatched-job", "interrupted", "invalid-owner"])
def test_legacy_ambiguous_ownership_fails_closed(package_factory, conflict):
    service = package_factory()
    job_id = "legacy-conflict-package"
    make_legacy_package(service, job_id)
    write_legacy_task(service, job_id, "user:11")
    if conflict == "other-owner":
        write_legacy_task(service, job_id, "user:22", suffix="second")
    elif conflict == "mismatched-job":
        write_legacy_task(service, job_id, "user:11", suffix="second", result={"job_id": "different-package"})
    elif conflict == "interrupted":
        write_legacy_task(service, job_id, "user:11", suffix="second", status="running")
    else:
        write_legacy_task(service, job_id, ["user:11"], suffix="second")
    for user_id in (11, 22, 0):
        with as_user(user_id):
            assert service.list_jobs() == {"items": [], "total": 0}
            with pytest.raises(DataAcquisitionError):
                service.package_path(job_id)


def test_failed_worker_does_not_leak_identity_to_next_task(package_factory, confirmed_plan, tmp_path):
    def factory():
        service = package_factory()
        if current_owner_id() == "user:11":
            def fail(*args, **kwargs):
                raise DataAcquisitionError("离线失败样本")
            service.run = fail
        return service

    manager = DataAcquisitionTaskService(service_factory=factory, task_root=tmp_path / "tasks")
    try:
        with as_user(11):
            first = manager.submit("获取603306日线", confirmed_plan)
        with as_user(22):
            second = manager.submit("获取603306日线", confirmed_plan)
        assert manager._executor.submit(current_owner_id).result(timeout=10) is None
        with as_user(11):
            assert manager.get(first["task_id"])["status"] == "failed"
            assert package_factory().list_jobs() == {"items": [], "total": 0}
        with as_user(22):
            task = manager.get(second["task_id"])
            assert task["status"] == "completed"
            assert package_factory().package_path(task["job_id"]).is_file()
    finally:
        manager._executor.shutdown(wait=True)


def test_api_jobs_and_download_enforce_user_identity_after_background_execution(
    package_factory, confirmed_plan, monkeypatch,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.middlewares import auth
    from api.v1.endpoints import data_acquisition

    manager = DataAcquisitionTaskService(service_factory=package_factory)
    monkeypatch.setattr(data_acquisition, "DataAcquisitionService", package_factory)
    monkeypatch.setattr(data_acquisition, "DataAcquisitionTaskService", SimpleNamespace(get_instance=lambda: manager))
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "user_access_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_session", lambda cookie: cookie == "offline-admin")
    accounts = {"offline-a": SimpleNamespace(id=11, display_name="用户甲"),
                "offline-b": SimpleNamespace(id=22, display_name="用户乙")}
    monkeypatch.setattr(auth, "UserAccountService", lambda: SimpleNamespace(account_for_session=accounts.get))
    app = FastAPI()
    app.include_router(data_acquisition.router, prefix="/api/v1/data-acquisition")
    app.add_middleware(auth.AuthMiddleware)
    base = "/api/v1/data-acquisition"
    try:
        # Real auth middleware, endpoint functions and service/executor; only
        # session lookup and upstream data are offline fixtures. No app lifespan.
        with TestClient(app) as client:
            headers = {}

            def get(path):
                return client.get(f"{base}{path}", headers=headers)

            assert get("/jobs").status_code == 401
            headers = {"Cookie": f"{auth.USER_COOKIE_NAME}=offline-a"}
            submitted = client.post(f"{base}/run-async", headers=headers,
                                    json={"request": "获取603306日线", "plan": confirmed_plan})
            assert submitted.status_code == 202
            task_id = submitted.json()["task_id"]
            assert manager._executor.submit(current_owner_id).result(timeout=10) is None
            task = get(f"/tasks/{task_id}")
            assert task.status_code == 200
            assert task.json()["status"] == "completed"
            job_id = task.json()["job_id"]
            assert "owner_id" not in task.text
            assert get("/jobs").json()["items"][0]["job_id"] == job_id
            assert get(f"/jobs/{job_id}").status_code == 200
            downloaded = get(f"/jobs/{job_id}/download")
            assert downloaded.status_code == 200
            with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
                assert "owner_id" not in json.loads(archive.read("manifest.json"))

            headers = {"Cookie": f"{auth.USER_COOKIE_NAME}=offline-b"}
            assert get("/jobs").json() == {"items": [], "total": 0}
            assert get(f"/tasks/{task_id}").status_code == 404
            assert get(f"/jobs/{job_id}").status_code == 404
            assert get(f"/jobs/{job_id}/download").status_code == 404

            headers = {}
            assert get(f"/jobs/{job_id}/download").status_code == 401
            headers = {"Cookie": f"{auth.COOKIE_NAME}=offline-admin"}
            assert get(f"/jobs/{job_id}/download").status_code == 404
            legacy_id = "legacy-admin-package"
            make_legacy_package(package_factory(), legacy_id)
            assert get(f"/jobs/{legacy_id}/download").status_code == 200
    finally:
        manager._executor.shutdown(wait=True)
