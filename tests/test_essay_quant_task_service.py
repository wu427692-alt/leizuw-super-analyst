import json
import os
import threading
import time

import pytest

from src.config import Config
from src.request_identity import reset_current_user_id, set_current_user_id
from src.services.essay_quant_service import EssayQuantError, EssayQuantService
from src.services.essay_quant_task_service import EssayQuantTaskManager
from src.storage import DatabaseManager, EssayQuantRunRecord, EssayQuantTaskRecord


@pytest.fixture()
def task_db(tmp_path):
    previous = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "essay-quant-tasks.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    EssayQuantTaskManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        EssayQuantTaskManager.reset_instance()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if previous is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous


def _wait_for_status(manager, task_id, status, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get_task(task_id)
        if task and task["status"] == status:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {status}")


def test_background_task_captures_owner_and_keeps_results_private(task_db, monkeypatch):
    executed = threading.Event()

    def fake_run(self, rule, **_kwargs):
        assert self.owner_id == "user:101"
        result = {"rule": rule, "summary": {}, "generated_at": "2026-08-22T02:00:00Z"}
        with self.db.get_session() as session:
            row = EssayQuantRunRecord(
                owner_id=self.owner_id,
                rule_json=json.dumps(rule),
                result_json=json.dumps(result),
                source_hash="test-owner-hash",
                event_count=0,
                mature_event_count=0,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            run_id = row.id
        executed.set()
        return {**result, "run_id": run_id}

    monkeypatch.setattr(EssayQuantService, "run", fake_run)
    manager = EssayQuantTaskManager(db=task_db, worker_count=1)
    user_one = set_current_user_id(101)
    try:
        submitted = manager.submit({"name": "用户一研究", "lookback_days": 365})
        assert submitted["status"] in {"queued", "running"}
        assert executed.wait(1.0)
        completed = _wait_for_status(manager, submitted["task_id"], "completed")
        assert completed["result_run_id"]
        assert EssayQuantService(db=task_db).get_run(completed["result_run_id"])["run_id"] == completed["result_run_id"]
    finally:
        reset_current_user_id(user_one)

    user_two = set_current_user_id(202)
    try:
        assert manager.get_task(submitted["task_id"]) is None
        assert manager.list_tasks()["total"] == 0
        with pytest.raises(EssayQuantError, match="不存在"):
            EssayQuantService(db=task_db).get_run(completed["result_run_id"])
    finally:
        reset_current_user_id(user_two)
        manager.stop()


def test_start_recovers_interrupted_task_for_its_original_owner(task_db, monkeypatch):
    with task_db.get_session() as session:
        session.add(EssayQuantTaskRecord(
            task_id="recover-me",
            owner_id="user:303",
            status="running",
            progress=42,
            message="旧进程执行中",
            request_json=json.dumps({"rule": {"name": "恢复任务"}, "max_symbols": 30}),
        ))
        session.commit()

    def fake_run(self, rule, **_kwargs):
        assert self.owner_id == "user:303"
        with self.db.get_session() as session:
            row = EssayQuantRunRecord(
                owner_id=self.owner_id,
                rule_json=json.dumps(rule),
                result_json=json.dumps({"rule": rule, "summary": {}}),
                source_hash="recovered-hash",
                event_count=0,
                mature_event_count=0,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return {"run_id": row.id}

    monkeypatch.setattr(EssayQuantService, "run", fake_run)
    manager = EssayQuantTaskManager(db=task_db, worker_count=1)
    manager.start()
    token = set_current_user_id(303)
    try:
        task = _wait_for_status(manager, "recover-me", "completed")
        assert task["progress"] == 100
        assert task["result_run_id"]
    finally:
        reset_current_user_id(token)
        manager.stop()
