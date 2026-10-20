"""Paid-request and clock boundaries using real paper ledger, no external IO."""
from concurrent.futures import Future
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.services import jev_live_t_service as service
from src.services.jev_decisions_client import JevClientError
from src.services.jev_live_t_engine import LiveTConfig


START = datetime.fromisoformat("2026-09-21T10:00:00+08:00")


def answer(action="HOLD", cost=.00001):
    return {"action": action, "confidence": .9, "status": "ok", "latency_ms": 0,
            "probabilities": {a: .8 if a == action else .1 for a in ("BUY", "SELL", "HOLD")},
            "usage": {"cost": cost, "cost_is_estimate": False}}


class Clock:
    second = 0.

    def sleep(self, seconds):
        self.second += seconds

    def iso(self):
        return (START + timedelta(seconds=self.second)).isoformat()


class Store:
    def __init__(self):
        self.records = []
        self.on_save = None

    def save(self, owner, run):
        assert owner == "user:123"
        self.records.append(deepcopy(run))
        if self.on_save:
            self.on_save(run)


class Source:
    symbol = "sh603306"

    def __init__(self, clock):
        self.clock = clock
        self.closed = False
        self.on_poll = None

    def poll(self):
        if self.on_poll:
            self.on_poll()
        stamp = self.clock.iso()
        volume = 100000 + int(self.clock.second) * 10000
        q = {"source_time": stamp, "received_at": stamp, "last_price": 100,
             "bid_price": 100, "ask_price": 100, "bid_volume": 100000,
             "ask_volume": 100000, "cumulative_volume": volume,
             "cumulative_amount": volume * 100, "volume_unit": "shares",
             "trading_status": "trading", "source": "synthetic_offline"}
        return {"quotes": {self.symbol: q}}

    def close(self):
        self.closed = True


class Client:
    model = "synthetic-offline-model"
    input_price_per_million = .042
    timeout = 2

    def __init__(self, outcome=None):
        self.states = []
        self.outcome = outcome or (lambda: answer())

    def decide(self, state):
        self.states.append(deepcopy(state))
        return self.outcome()


class Executor:
    def __init__(self, **kwargs):
        self.shutdown_args = None

    def submit(self, fn, state):
        future = Future()
        try:
            future.set_result(fn(state))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        self.shutdown_args = kwargs


@pytest.fixture
def setup(monkeypatch):
    clock, store = Clock(), Store()
    source = Source(clock)
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: clock.second, sleep=clock.sleep))
    monkeypatch.setattr(service, "now", clock.iso)
    monkeypatch.setattr(service, "session_status", lambda _: "trading")
    monkeypatch.setattr(service, "ThreadPoolExecutor", Executor)

    def run(client=None, **patch):
        return service.run_live_paper(
            LiveTConfig(initial_base_value=200000, initial_cash=100000, order_quantity=100),
            owner="user:123", store=store, source=source, client=client or Client(),
            **({"duration_seconds": 35, "warmup_seconds": 30, "lookback_events": 2,
                "max_model_requests": 1} | patch))
    return SimpleNamespace(clock=clock, store=store, source=source, run=run)


def test_reservation_is_persisted_before_dispatch_and_actual_charge_settles_once(setup):
    def outcome():
        usage = setup.store.records[-1]["usage"]
        assert usage["requests"] == 1 and usage["cost_usd"] > 0
        assert usage["cost_is_estimate"] is True
        return answer(cost=.000017)
    client = Client(outcome)
    result = setup.run(client)
    assert len(client.states) == 1 and result["usage"]["requests"] == 1
    assert result["usage"]["cost_usd"] == pytest.approx(.000017)
    assert result["usage"]["cost_is_estimate"] is False
    assert result["usage"]["unconfirmed_requests"] == 0
    assert result["stop_reason"] == "model_request_limit"
    assert setup.source.closed and not result["trades"]
    assert client.states[0]["instrument_type"] == "a_share"
    assert "603306" not in str(client.states) and "initial_cash" not in str(client.states)


@pytest.mark.parametrize("failure", [RuntimeError("private_failure_text"), JevClientError("timeout")])
def test_unknown_paid_outcome_retains_reservation_stops_and_never_retries(setup, failure):
    def outcome():
        raise failure
    client = Client(outcome)
    result = setup.run(client)
    assert result["status"] == "failed" and len(client.states) == 1
    assert result["usage"]["requests"] == 1 and result["usage"]["cost_usd"] > 0
    assert result["usage"]["cost_is_estimate"] is True
    assert result["usage"]["unconfirmed_requests"] == 1
    assert "private_failure_text" not in str(result)
    assert not result["trades"]


def test_protocol_error_keeps_verified_provider_charge_once(setup):
    def outcome():
        raise JevClientError("invalid_probabilities", {"cost": .00003, "cost_is_estimate": False})
    result = setup.run(Client(outcome))
    assert result["status"] == "failed"
    assert result["usage"]["cost_usd"] == pytest.approx(.00003)
    assert result["usage"]["cost_is_estimate"] is False
    assert result["usage"]["unconfirmed_requests"] == 0


@pytest.mark.parametrize("expired", ["deadline", "session"])
def test_poll_crossing_stop_boundary_cannot_apply_pending_fill(setup, monkeypatch, expired):
    session_open = True
    def poll():
        nonlocal session_open
        if setup.clock.second == 31:
            if expired == "deadline":
                setup.clock.second = 40
            else:
                session_open = False
    setup.source.on_poll = poll
    monkeypatch.setattr(service, "session_status", lambda _: "trading" if session_open else "closed")
    result = setup.run(Client(lambda: answer("BUY")))
    assert len(result["signals"]) == 1  # A valid order existed before slow poll.
    assert result["trades"] == []
    assert result["account"]["total_shares"] == 2000
    assert result["equity"][-1]["timestamp"] == (START + timedelta(seconds=30)).isoformat()
    assert result["stop_reason"] == ("duration_complete" if expired == "deadline" else "continuous_session_ended")


def test_reservation_write_crossing_deadline_does_not_dispatch_or_charge(setup):
    def save(run):
        if run["usage"]["requests"]:
            setup.clock.second = 40
    setup.store.on_save = save
    client = Client()
    result = setup.run(client)
    assert client.states == []
    assert result["usage"]["requests"] == result["usage"]["cost_usd"] == 0
    assert result["usage"]["cost_is_estimate"] is False
    assert result["stop_reason"] == "duration_complete"


@pytest.mark.parametrize("outcome", ["success", "provider_error", "unknown"])
def test_inflight_response_at_stop_only_settles_cost_never_submits_order(setup, monkeypatch, outcome):
    observed = []
    class Pending:
        def done(self):
            return False

        def result(self, *, timeout):
            observed.append(timeout)
            if outcome == "provider_error":
                raise JevClientError("invalid_choice", {"cost": .000023, "cost_is_estimate": False})
            if outcome == "unknown":
                raise TimeoutError("private_timeout_text")
            return answer("BUY", .000023)

        def cancel(self):
            return False

    class PendingExecutor(Executor):
        def submit(self, fn, state):
            return Pending()

    monkeypatch.setattr(service, "ThreadPoolExecutor", PendingExecutor)
    result = setup.run()
    assert len(observed) == 1 and observed[0] >= 0
    assert result["signals"] == result["trades"] == []
    assert result["usage"]["requests"] == 1
    if outcome == "unknown":
        assert result["usage"]["cost_usd"] > 0
        assert result["usage"]["cost_is_estimate"] is True
        assert result["usage"]["unconfirmed_requests"] == 1
        assert result["status"] == "failed"
    else:
        assert result["usage"]["cost_usd"] == pytest.approx(.000023)
        assert result["usage"]["cost_is_estimate"] is False
        assert result["status"] == ("failed" if outcome == "provider_error" else "completed")


def test_executor_rejection_before_dispatch_leaves_no_phantom_charge(setup, monkeypatch):
    class RejectExecutor(Executor):
        def submit(self, *args):
            raise RuntimeError("executor unavailable")
    monkeypatch.setattr(service, "ThreadPoolExecutor", RejectExecutor)
    result = setup.run()
    assert result["status"] == "failed"
    assert result["usage"]["requests"] == result["usage"]["cost_usd"] == 0
    assert result["usage"]["unconfirmed_requests"] == 0


def test_paid_budget_reservation_prevents_model_call(setup):
    client = Client()
    result = setup.run(client, max_cost_usd=.000000001)
    assert client.states == [] and result["usage"]["requests"] == 0
    assert result["stop_reason"] == "model_budget_reserved"


def test_buy_then_sell_projection_keeps_t1_frozen_shares_and_all_fees(setup):
    actions = iter(("BUY", "SELL"))
    result = setup.run(Client(lambda: answer(next(actions))), max_model_requests=2, duration_seconds=40)
    assert [t["action"] for t in result["trades"]] == ["BUY", "SELL"]
    account = result["account"]
    assert account["total_shares"] == 2000
    assert account["today_frozen_shares"] == 100
    assert account["sellable_yesterday_shares"] == 1900
    assert account["inventory_gap_shares"] == 0
    assert account["total_fees"] == pytest.approx(15.2)
    assert account["incremental_pnl"] == pytest.approx(-15.2)
    assert account["closed_t_net_pnl"] == pytest.approx(-15.2)
    assert account["cost_reduction_per_initial_share"] == pytest.approx(-15.2 / 2000)
    assert account["actual_cost_basis"] is None


def continuous_sink(records):
    def save(run, checkpoint):
        records.append((deepcopy(run), deepcopy(checkpoint)))
    return save


def test_continuous_ignores_old_duration_and_request_caps_until_manual_stop(setup):
    records = []
    client = Client()
    result = setup.run(client, continuous=True, checkpoint_sink=continuous_sink(records),
                       stop_requested=lambda: setup.clock.second >= 40)
    assert result["stop_reason"] == "manual_stop" and result["status"] == "stopped"
    assert len(client.states) > 1
    assert setup.clock.second >= 40  # Old finite fixture ends at 35 seconds.
    assert result["config"]["duration_seconds"] is None
    assert result["config"]["max_model_requests"] is None
    assert result["config"]["continuous"] is True
    assert setup.store.records == []  # Atomic joint sink replaces the old writer.
    assert records[-1][0]["run_id"] == result["run_id"]


def test_continuous_pause_makes_no_provider_calls_and_rewarms_on_reopen(setup, monkeypatch):
    records, polled, decided = [], [], []
    setup.source.on_poll = lambda: polled.append(setup.clock.second)
    monkeypatch.setattr(service, "session_status", lambda _: "closed" if 32 <= setup.clock.second < 45 else "trading")
    def outcome():
        decided.append(setup.clock.second)
        return answer()
    result = setup.run(Client(outcome), continuous=True, checkpoint_sink=continuous_sink(records),
                       stop_requested=lambda: setup.clock.second >= 80)
    assert all(not 32 <= at < 45 for at in polled)
    assert all(not 32 <= at < 75 for at in decided)
    paused = [row for row, _ in records if row["status"] == "paused"]
    assert paused and all(row["account"]["initial_equity"] == 300000 for row in paused)
    assert len({row["run_id"] for row, _ in records}) == 1
    assert result["usage"]["cost_usd"] == pytest.approx(len(decided) * .00001)


def test_continuous_manual_stop_caps_settlement_and_preserves_unknown_reserve(setup, monkeypatch):
    records, waits = [], []
    class Pending:
        def done(self):
            return False

        def result(self, *, timeout):
            waits.append(timeout)
            raise TimeoutError("unobserved_provider_outcome")

        def cancel(self):
            return False

    class PendingExecutor(Executor):
        def submit(self, fn, state):
            return Pending()
    monkeypatch.setattr(service, "ThreadPoolExecutor", PendingExecutor)
    result = setup.run(continuous=True, checkpoint_sink=continuous_sink(records),
                       stop_requested=lambda: setup.clock.second >= 31)
    assert waits and max(waits) <= 2
    assert result["status"] == "stopped" and result["stop_reason"] == "manual_stop"
    assert result["usage"]["cost_usd"] > 0
    assert result["usage"]["cost_is_estimate"] is True
    assert result["usage"]["unconfirmed_requests"] == 1
    assert result["paid_request"] is None
    assert result["signals"] == result["trades"] == []


def test_continuous_resume_keeps_account_trades_costs_and_identity(setup):
    records = []
    result = setup.run(Client(lambda: answer("BUY")), continuous=True,
                       checkpoint_sink=continuous_sink(records),
                       stop_requested=lambda: setup.clock.second >= 34)
    assert result["trades"] and result["account"]["today_frozen_shares"] > 0
    first, checkpoint = records[-1]
    resumed = setup.run(continuous=True, checkpoint_sink=continuous_sink(records),
                        resume_run=first, engine_checkpoint=checkpoint, stop_requested=lambda: True)
    assert resumed["run_id"] == first["run_id"]
    assert resumed["account"] == first["account"]
    assert resumed["trades"] == first["trades"]
    assert resumed["signals"] == first["signals"]
    assert resumed["usage"] == {**first["usage"], "observed_decision_interval_ms": None}
    assert first["usage"]["observed_decision_interval_ms"] == 2000.0


def test_recovery_retains_unsettled_paid_reservation_exactly_once_without_retry(setup):
    records = []
    setup.run(continuous=True, checkpoint_sink=continuous_sink(records),
              stop_requested=lambda: setup.clock.second >= 33)
    reserved, checkpoint = next((run, checkpoint) for run, checkpoint in records if run.get("paid_request"))
    new_records, client = [], Client()
    resumed = setup.run(client, continuous=True, checkpoint_sink=continuous_sink(new_records),
                        resume_run=reserved, engine_checkpoint=checkpoint, stop_requested=lambda: True)
    assert client.states == []
    assert resumed["usage"]["requests"] == reserved["usage"]["requests"]
    assert resumed["usage"]["cost_usd"] == reserved["usage"]["cost_usd"]
    assert resumed["usage"]["cost_is_estimate"] is True
    assert resumed["usage"]["unconfirmed_requests"] == 1
    second = setup.run(continuous=True, checkpoint_sink=continuous_sink(new_records),
                       resume_run=new_records[-1][0], engine_checkpoint=new_records[-1][1],
                       stop_requested=lambda: True)
    assert second["usage"]["unconfirmed_requests"] == 1


def test_resume_cannot_reset_or_raise_the_existing_budget(setup):
    records = []
    setup.run(continuous=True, checkpoint_sink=continuous_sink(records), stop_requested=lambda: True)
    original, checkpoint = records[-1]
    with pytest.raises(ValueError, match="resume configuration"):
        setup.run(continuous=True, checkpoint_sink=continuous_sink(records), max_cost_usd=.2,
                  resume_run=original, engine_checkpoint=checkpoint, stop_requested=lambda: True)
    original["usage"]["cost_usd"] = original["config"]["max_cost_usd"]
    client = Client()
    result = setup.run(client, continuous=True, checkpoint_sink=continuous_sink(records),
                       resume_run=original, engine_checkpoint=checkpoint)
    assert result["stop_reason"] == "model_budget_reached" and client.states == []
    assert result["usage"]["cost_usd"] == .1


def test_continuous_source_failures_stop_once_and_disable_without_paid_retry(setup):
    records, polls = [], []
    def fail():
        polls.append(setup.clock.second)
        raise ValueError("provider_failure")
    setup.source.on_poll = fail
    result = setup.run(continuous=True, checkpoint_sink=continuous_sink(records))
    assert len(polls) == 3
    assert result["status"] == "failed" and result["stop_reason"] == "quote_failures"
    assert result["usage"]["requests"] == 0


def test_manual_stop_during_slow_quote_poll_cannot_fill_an_existing_order(setup):
    records = []
    def slow_poll():
        if setup.clock.second == 31:
            setup.clock.second += 6
    setup.source.on_poll = slow_poll
    result = setup.run(Client(lambda: answer("BUY")), continuous=True,
                       checkpoint_sink=continuous_sink(records),
                       stop_requested=lambda: setup.clock.second >= 32)
    assert result["stop_reason"] == "manual_stop"
    assert result["trades"] == [] and len(result["signals"]) == 1
    assert setup.clock.second - 32 < 10


def test_stop_after_atomic_reservation_but_before_dispatch_refunds_unused_reserve(setup):
    records, should_stop = [], [False]
    def sink(run, checkpoint):
        records.append((deepcopy(run), deepcopy(checkpoint)))
        if run.get("paid_request"):
            should_stop[0] = True
    client = Client()
    result = setup.run(client, continuous=True, checkpoint_sink=sink,
                       stop_requested=lambda: should_stop[0])
    assert client.states == []
    assert result["stop_reason"] == "manual_stop"
    assert result["usage"]["requests"] == result["usage"]["cost_usd"] == 0
    assert result["paid_request"] is None
