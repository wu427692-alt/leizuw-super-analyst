"""Verify paid-work boundaries and result accounting without network traffic."""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from api.v1.schemas.jev_convertible_t import JevEventRequest
from src.repositories.jev_backtest_repo import JevReplayRepository
from src.services.jev_event_service import JevEventService
from tests.test_jev_backtest_service import TemporaryDB, ManualPool, FakeClient


INSTRUMENT = {"symbol": "NVDA", "currency": "USD", "quantity_unit": "shares", "market": "XNAS",
              "lot_size": 1, "instrument_type": "us_equity", "exchange_timezone": "America/New_York",
              "price_tick": .01, "session_close": "16:00:00", "simulation_account": "unlevered_margin",
              "dataset_id": "test-nvda"}


def config(**patch):
    return JevEventRequest(request_id=uuid4(), symbol="NVDA", dataset_id="test-nvda",
        start_date="2025-09-16", end_date="2025-09-16", lookback_events=20,
        decision_interval_seconds=10, order_quantity=100,
        factors=["ohlcv", "orderbook"], **patch).model_dump(mode="json")


def dataset():
    start = datetime.fromisoformat("2025-09-16T09:30:01-04:00")
    events = [{"time": (start + timedelta(seconds=i)).isoformat(), "sequence": i,
        "last_price": 100 + i / 1000, "bid_price": 99.99 + i / 1000, "ask_price": 100.01 + i / 1000,
        "bid_volume": 1000, "ask_volume": 1000, "trade_volume": 10000 + i,
        "event_type": "quote", "trading_status": "trading", "volume_kind": "incremental",
        "quantity_unit": "shares", "volume_interval_start": (start + timedelta(seconds=i-1)).isoformat()}
        for i in range(65)]
    return events, {}, {"instrument": INSTRUMENT, "warnings": []}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.jev_cb_data.instruments", lambda: [INSTRUMENT])
    db = TemporaryDB(tmp_path / "events.sqlite")
    client = FakeClient()
    client.cache_version = "cb-t0-factors-v1"
    result = JevEventService(repo=JevReplayRepository(db), loader=lambda _: dataset(), client_factory=lambda: client)
    result.pool.shutdown(wait=True)
    result.pool = ManualPool()
    result.test_client = client
    yield result
    db.engine.dispose()


def finish(service, request, owner="event:a"):
    task = service.submit(owner, request)
    service.pool.drain()
    return service.repo.get(owner, task["task_id"])


def test_completed_result_preserves_total_capital_and_scopes_reads(service):
    request = config()
    task = finish(service, request)
    assert task["status"] == "completed", task.get("error")
    assert task["config"]["currency"] == "USD"
    assert task["result"]["summary"]["initial_total_equity"] == 400000
    assert task["result"]["summary"]["incremental_t_pnl"] == pytest.approx(
        task["result"]["summary"]["final_equity"] - task["result"]["summary"]["hold_baseline_equity"])
    assert service.repo.get("event:b", task["task_id"]) is None
    assert service.repo.get("a", task["task_id"]) is None
    assert service.submit("event:a", request)["task_id"] == task["task_id"]
    assert not service.pool.jobs


def test_execution_only_changes_reuse_model_cache(service):
    first = finish(service, config())
    second = finish(service, config(commission_bps=4, slippage_bps=5))
    assert first["status"] == second["status"] == "completed"
    assert first["usage"]["requests"] > 0
    assert second["usage"]["requests"] == 0
    assert second["usage"]["cached"] == first["usage"]["requests"]
    assert second["usage"]["cost_usd"] == 0


def test_source_validation_precedes_credentials_and_paid_requests(service):
    from src.services.jev_cb_data import CBDataError
    service.loader = lambda _: (_ for _ in ()).throw(CBDataError("units"))
    service.client_factory = lambda: pytest.fail("No credential needed when data fail")
    task = finish(service, config())
    assert task["status"] == "failed"
    assert task["usage"]["requests"] == 0
    assert "单位" in task["error"]


def test_budget_and_cancel_stop_subsequent_calls(service):
    task = finish(service, config(max_cost_usd=.000001))
    assert task["status"] == "failed"
    assert not service.test_client.calls
    request = config()
    queued = service.submit("event:a", request)
    cancelled = service.cancel("event:a", queued["task_id"])
    service.pool.drain()
    assert cancelled["status"] == "cancelled"
    assert not service.test_client.calls


def test_unexpected_failure_does_not_leak_error_details(service):
    service.client_factory = lambda: (_ for _ in ()).throw(RuntimeError("private-debug-value"))
    task = finish(service, config())
    assert task["status"] == "failed"
    assert "private-debug-value" not in str(task)


def test_account_mode_cannot_silently_change_us_settlement_assumptions(service):
    with pytest.raises(ValueError, match="账户模拟"):
        service.submit("event:a", config(account_mode="cash_t0"))


def test_schema_requires_factors_and_aware_chronological_evaluation_window():
    with pytest.raises(ValueError):
        config(evaluation_start="2025-09-16T10:00:00", evaluation_end="2025-09-16T11:00:00")
    with pytest.raises(ValueError):
        config(evaluation_start="2025-09-16T12:00:00-04:00", evaluation_end="2025-09-16T11:00:00-04:00")


@pytest.mark.parametrize("boundary", ["reserve", "completion"])
def test_accepted_cancel_wins_before_paid_reserve_or_completion(service, monkeypatch, boundary):
    update = service.repo.update
    injected = []

    def cancel_before_write(owner, task_id, patch, **kwargs):
        target = (patch.get("message") == "Jev 正在判断当前因子" if boundary == "reserve"
                  else patch.get("status") == "completed")
        if target and not injected:
            injected.append(True)
            assert service.cancel(owner, task_id)["cancel_requested"]
        return update(owner, task_id, patch, **kwargs)

    monkeypatch.setattr(service.repo, "update", cancel_before_write)
    task = finish(service, config())
    assert injected and task["status"] == "cancelled"
    assert task.get("result") is None
    assert task["usage"]["requests"] == len(service.test_client.calls)
    assert task["usage"]["cost_usd"] == pytest.approx(len(service.test_client.calls) * service.test_client.cost)
    assert task["usage"]["cost_is_estimate"] is False
    if boundary == "reserve":
        assert not service.test_client.calls
    else:
        assert service.test_client.calls


def test_event_cancel_keeps_the_real_charge_of_a_reserved_in_flight_call(service):
    task = service.submit("event:a", config())
    service.test_client.on_decide = lambda: service.cancel("event:a", task["task_id"])
    service.pool.drain()
    stopped = service.repo.get("event:a", task["task_id"])
    assert stopped["status"] == "cancelled"
    assert stopped["usage"]["requests"] == len(service.test_client.calls) == 1
    assert stopped["usage"]["cost_usd"] == pytest.approx(service.test_client.cost)
    assert stopped["usage"]["cost_is_estimate"] is False
