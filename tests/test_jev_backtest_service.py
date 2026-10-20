"""Paid replay job boundaries, using a temporary DB and no network/credentials."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.repositories.jev_backtest_repo import JevReplayRepository
from src.services.jev_backtest_service import JevBacktestService
from src.services.jev_signal_engine import BacktestConfig


class TemporaryDB:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        self.sessions = sessionmaker(bind=self.engine)

    @contextmanager
    def get_session(self):
        session = self.sessions()
        try:
            yield session
        finally:
            session.close()


class ManualPool:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((fn, args))

    def drain(self):
        while self.jobs:
            fn, args = self.jobs.pop(0)
            fn(*args)


def request(**overrides):
    config = BacktestConfig(symbol="000001", start_date="2026-01-05", end_date="2026-01-05",
                            lookback_bars=20, max_holding_bars=60)
    return asdict(config) | {"request_id": str(uuid4()), "max_cost_usd": .05, "refresh_prices": True} | overrides


def sample_bars(count=25, changing=False):
    start = datetime.fromisoformat("2026-01-05T09:30:00+08:00")
    return [{"date": (start + timedelta(minutes=i)).isoformat(),
             "open": 10, "high": 10.2, "low": 9.8, "close": 10,
             "volume": 100000 + (i * i if changing else 0),
             "adj_factor": 1, "limit_up": False, "limit_down": False, "suspended": False}
            for i in range(count)]


class FakeClient:
    model = "Qwen/Qwen3.5-9B"
    endpoint = "https://jev.worldseek-ai.com/v1/systemone"
    input_price_per_million = .01

    def __init__(self):
        self.calls = []
        self.on_decide = None
        self.cost = .0000042

    def decide(self, state):
        self.calls.append(state)
        if self.on_decide:
            self.on_decide()
        return {"action": "BUY", "status": "ok", "confidence": .9,
                "probabilities": {"BUY": .8, "SELL": .1, "HOLD": .1},
                "usage": {"input_tokens": 100, "output_tokens": 0,
                          "cost": self.cost, "cost_is_estimate": False}}


@pytest.fixture
def service(tmp_path):
    db = TemporaryDB(tmp_path / "replays.sqlite")
    repo = JevReplayRepository(db)
    client = FakeClient()
    service = JevBacktestService(repo=repo, loader=lambda _: (sample_bars(), {"warnings": []}),
                                 client_factory=lambda: client)
    service.pool.shutdown(wait=True)
    service.pool = ManualPool()
    service.test_client = client
    yield service
    db.engine.dispose()


def finish(service, owner, config):
    task = service.submit(owner, config)
    service.pool.drain()
    return service.repo.get(owner, task["task_id"])


def test_duplicate_submissions_charge_once_and_do_not_resubmit_completed_job(service):
    config = request()
    task = service.submit("owner-a", config)
    duplicate = service.submit("owner-a", config)
    assert duplicate["task_id"] == task["task_id"]
    assert len(service.pool.jobs) == 1
    service.pool.drain()
    done = service.repo.get("owner-a", task["task_id"])
    assert done["status"] == "completed"
    assert done["usage"]["requests"] == 1
    assert done["usage"]["cached"] == 5
    assert len(service.test_client.calls) == 1
    duplicate = service.submit("owner-a", config)
    assert duplicate["status"] == "completed"
    assert service.pool.jobs == []


def test_same_request_id_with_changed_parameters_is_rejected(service):
    config = request()
    service.submit("owner-a", config)
    with pytest.raises(ValueError, match="同一请求编号"):
        service.submit("owner-a", config | {"position_pct": .5})


def test_concurrent_repeated_requests_create_exactly_one_worker(service):
    config = request()
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: service.submit("owner-a", config), range(12)))
    assert len({job["task_id"] for job in jobs}) == 1
    assert len(service.pool.jobs) == 1
    service.pool.drain()
    assert len(service.test_client.calls) == 1


def test_one_active_paid_job_per_owner_and_other_owner_can_run(service):
    service.submit("owner-a", request())
    with pytest.raises(ValueError, match="已有回测"):
        service.submit("owner-a", request())
    service.submit("owner-b", request())
    service.pool.drain()
    assert len(service.test_client.calls) == 2


def test_changed_execution_conditions_reuse_paid_decisions(service):
    first = finish(service, "owner-a", request())
    second = finish(service, "owner-a", request(position_pct=.5, confidence_threshold=.95,
                                                commission_bps=9, min_commission=20,
                                                sell_tax_bps=9, slippage_bps=15,
                                                max_holding_bars=1, stop_loss_pct=1, take_profit_pct=1,
                                                settlement_mode="research_t0"))
    assert first["status"] == second["status"] == "completed"
    assert len(service.test_client.calls) == 1
    assert second["usage"]["requests"] == 0
    assert second["usage"]["cached"] == 6
    assert second["usage"]["cost_usd"] == 0
    assert second["result"]["summary"]["trade_count"] == 0


def test_owner_scopes_history_lookup_cancel_and_paid_cache(service):
    task = finish(service, "owner-a", request())
    assert service.repo.get("owner-b", task["task_id"]) is None
    assert service.repo.list("owner-b") == []
    assert service.cancel("owner-b", task["task_id"]) is None
    other = finish(service, "owner-b", request())
    assert other["usage"]["requests"] == 1
    assert len(service.test_client.calls) == 2


def test_queued_cancel_never_reaches_loader_client_or_paid_model(service):
    touched = []
    service.loader = lambda _: touched.append("loader")
    task = service.submit("owner-a", request())
    assert service.cancel("owner-a", task["task_id"])["status"] == "cancelled"
    service.pool.drain()
    assert touched == []
    assert service.test_client.calls == []


def test_running_cancel_stops_after_in_flight_request_and_keeps_charge(service):
    task = service.submit("owner-a", request())
    service.test_client.on_decide = lambda: service.cancel("owner-a", task["task_id"])
    service.pool.drain()
    stopped = service.repo.get("owner-a", task["task_id"])
    assert stopped["status"] == "cancelled"
    assert stopped["usage"]["requests"] == 1
    assert stopped["usage"]["cost_usd"] == pytest.approx(service.test_client.cost)
    assert "result" not in stopped
    assert stopped["progress"] < 100
    assert len(service.test_client.calls) == 1


def test_budget_exhaustion_stops_explicitly_before_any_unreserved_request(service):
    stopped = finish(service, "owner-a", request(max_cost_usd=.00001))
    assert stopped["status"] == "failed"
    assert "预算上限" in stopped["message"]
    assert stopped["usage"]["requests"] == 0
    assert service.test_client.calls == []
    assert "result" not in stopped
    assert stopped["progress"] < 100


def test_actual_charge_over_budget_stops_without_more_calls_and_keeps_cache(service):
    service.test_client.cost = .01
    stopped = finish(service, "owner-a", request(max_cost_usd=.002))
    assert stopped["status"] == "failed"
    assert "实际费用" in stopped["message"]
    assert stopped["usage"]["requests"] == 1
    assert stopped["usage"]["cost_usd"] == pytest.approx(.01)
    replay = finish(service, "owner-a", request(max_cost_usd=.02))
    assert replay["status"] == "completed"
    assert replay["usage"]["requests"] == 0
    assert len(service.test_client.calls) == 1


def test_client_failure_aborts_instead_of_continuing_as_success(service):
    from src.services.jev_decisions_client import JevClientError

    def fail():
        raise JevClientError("timeout")

    service.test_client.on_decide = fail
    task = finish(service, "owner-a", request())
    assert task["status"] == "failed"
    assert "timeout" in task["message"]
    assert len(service.test_client.calls) == 1
    assert task["usage"]["cost_is_estimate"] is True
    assert task["usage"]["cost_usd"] > 0


def test_model_or_endpoint_change_invalidates_paid_cache(service):
    finish(service, "owner-a", request())
    service.test_client.model = "typesafe/jev-other-version"
    second = finish(service, "owner-a", request())
    service.test_client.endpoint = "https://alternate.example/v1/systemone"
    third = finish(service, "owner-a", request())
    assert second["usage"]["requests"] == third["usage"]["requests"] == 1
    assert len(service.test_client.calls) == 3


def test_unexpected_client_failure_is_not_swallowed_into_completed_job(service):
    def fail():
        raise RuntimeError("unexpected failure with secret upstream detail")

    service.test_client.on_decide = fail
    task = finish(service, "owner-a", request())
    assert task["status"] == "failed"
    assert len(service.test_client.calls) == 1
    assert "secret upstream detail" not in str(task)


def test_third_party_value_error_text_is_never_exposed(service):
    def fail(_):
        raise ValueError("SECRET_KEY or raw HTTP response body")

    service.loader = fail
    task = finish(service, "owner-a", request())
    assert task["status"] == "failed"
    assert "SECRET_KEY" not in str(task)
    assert "raw HTTP" not in str(task)


def test_superseded_stale_worker_cannot_resume_paid_calls(service):
    import time
    from src.repositories.jev_backtest_repo import JevReplayTask

    service.loader = lambda _: (sample_bars(changing=True), {"warnings": []})
    task = service.submit("owner-a", request())
    replacement = []

    def supersede_running_worker():
        service.test_client.on_decide = None
        with service.repo.db.get_session() as session:
            row = session.get(JevReplayTask, task["task_id"])
            row.updated_at = time.time() - 1900
            session.commit()
        replacement.append(service.submit("owner-a", request()))

    service.test_client.on_decide = supersede_running_worker
    fn, args = service.pool.jobs.pop(0)
    fn(*args)
    old = service.repo.get("owner-a", task["task_id"])
    assert old["status"] == "failed"
    assert "result" not in old
    assert len(service.test_client.calls) == 1
    assert service.repo.get("owner-a", replacement[0]["task_id"])["status"] == "queued"


def test_cancel_racing_with_completion_preserves_completed_message(service, monkeypatch):
    task = service.submit("owner-a", request())
    service.repo.update("owner-a", task["task_id"], {"status": "running"}, expected_status="queued")
    real_update = service.repo.update

    def complete_before_cancel(owner, task_id, patch, **kwargs):
        if patch.get("cancel_requested"):
            real_update(owner, task_id, {"status": "completed", "progress": 100, "message": "完成"})
        return real_update(owner, task_id, patch, **kwargs)

    monkeypatch.setattr(service.repo, "update", complete_before_cancel)
    reply = service.cancel("owner-a", task["task_id"])
    stored = service.repo.get("owner-a", task["task_id"])
    assert reply is not None
    assert stored["status"] == reply["status"] == "completed"
    assert stored["message"] == "完成"
    assert not stored.get("cancel_requested")


@pytest.mark.parametrize("boundary", ["reserve", "completion"])
def test_accepted_cancel_wins_before_paid_reserve_or_completion(service, monkeypatch, boundary):
    update = service.repo.update
    injected = []

    def cancel_before_write(owner, task_id, patch, **kwargs):
        target = (patch.get("message") == "Jev 正在判断当前量价窗口" if boundary == "reserve"
                  else patch.get("status") == "completed")
        if target and not injected:
            injected.append(True)
            assert service.cancel(owner, task_id)["cancel_requested"]
        return update(owner, task_id, patch, **kwargs)

    monkeypatch.setattr(service.repo, "update", cancel_before_write)
    task = finish(service, "owner-a", request())
    assert injected and task["status"] == "cancelled"
    assert task.get("result") is None
    assert task["usage"]["requests"] == len(service.test_client.calls)
    assert task["usage"]["cost_usd"] == pytest.approx(len(service.test_client.calls) * service.test_client.cost)
    assert task["usage"]["cost_is_estimate"] is False
    if boundary == "reserve":
        assert not service.test_client.calls
    else:
        assert service.test_client.calls


def test_repository_rechecks_cancellation_after_an_optimistic_write_conflict(service, monkeypatch):
    from sqlalchemy.orm import Query

    task, _ = service.repo.create("owner-a", request())
    task_id = task["task_id"]
    service.repo.update("owner-a", task_id, {"status": "running"}, expected_status="queued")
    query_update = Query.update
    injected = []

    def cancel_between_read_and_write(query, *args, **kwargs):
        if not injected:
            injected.append(True)
            assert service.cancel("owner-a", task_id)["cancel_requested"]
        return query_update(query, *args, **kwargs)

    monkeypatch.setattr(Query, "update", cancel_between_read_and_write)
    completed = service.repo.update("owner-a", task_id, {"status": "completed"},
                                    expected_status="running", require_not_cancelled=True)
    stored = service.repo.get("owner-a", task_id)
    assert completed is None
    assert stored["status"] == "running" and stored["cancel_requested"]
