"""Enriched live runner boundaries: independent collection, no stale paid dispatch."""
from concurrent.futures import Future
from copy import deepcopy
import json

import pytest

from src.services import jev_live_t_service as service
from src.services.jev_live_context_client import JevLiveContextDecisionsClient, SCHEMA
from tests.test_jev_live_t_service import Client, Executor, answer, setup  # noqa: F401


class ContextSource:
    def __init__(self, clock):
        self.clock = clock
        self.calls = 0
        self.patch = lambda snapshot: None

    def poll(self):
        self.calls += 1
        stamp = self.clock.iso()
        snapshot = {"context": {
            "cpo": {"status": "fresh", "source": "synthetic_public_cpo", "source_time": stamp,
                    "available_at": stamp, "age_seconds": 0, "values": {"return_since_prev_close": .001,
                    "breadth_up": .6, "member_count": 50, "coverage_ratio": .9}},
            "news": {"status": "fresh", "source": "synthetic_public_news", "fetched_at": stamp, "items": [{
                "title": "Public event", "summary": "Public factual description", "source": "synthetic_public_news",
                "url": None, "published_at": stamp, "available_at": stamp}]}}}
        self.patch(snapshot)
        return snapshot


class BudgetClient(Client):
    def __init__(self, outcome=None):
        super().__init__(outcome)
        self.bodies = []

    def build_budget_body(self, state):
        body = {"model": self.model, "state": state,
                "questions": JevLiveContextDecisionsClient(api_key="")._build_questions()}
        self.bodies.append(deepcopy(body))
        return body


@pytest.fixture
def enriched(setup):
    original = setup.source.poll
    setup.index_patch = lambda quotes: None

    def poll():
        batch = original()
        quote = batch["quotes"][setup.source.symbol]
        for name in ("sh000300", "sh000852"):
            batch["quotes"][name] = {**quote, "pre_close": 100}
        setup.index_patch(batch["quotes"])
        return batch

    setup.source.poll = poll
    setup.context_source = ContextSource(setup.clock)
    original_run = setup.run
    setup.run = lambda client=None, **kwargs: original_run(
        client or BudgetClient(), enriched_context=True, context_source=setup.context_source, **kwargs)
    return setup


def test_all_public_components_pass_to_model_with_real_budget_and_dispatch_snapshot(enriched):
    seen = []

    def outcome():
        persisted = enriched.store.records[-1]
        assert persisted["last_model_context"] is None  # Not marked before executor accepts it.
        seen.append(persisted["usage"]["cost_usd"])
        return answer()

    client = BudgetClient(outcome)
    result = enriched.run(client, max_cost_usd=10)
    assert len(client.states) == 1 and len(client.bodies) == 1
    payload = client.states[0]
    assert payload["schema"] == SCHEMA
    assert payload["target"] == {"symbol": "603306.SH", "name": "华懋科技"}
    assert all(item["status"] == "fresh" for item in payload["context"].values())
    assert payload["base_state"]["factors"]["market"]["status"] == "disabled"
    assert result["last_model_context"] == {"decision_at": payload["decision_at"], "context": payload["context"]}
    assert seen[0] == pytest.approx(len(json.dumps(client.bodies[0], ensure_ascii=False).encode()) * .042 / 1e6)
    assert result["usage"]["cost_usd"] == pytest.approx(.00001)
    assert not any(key in str(payload) for key in ("initial_cash", "user:123", "initial_base_value"))
    assert result["config"]["max_cost_usd"] == 10
    assert result["config"]["duration_seconds"] == 35 and result["config"]["max_model_requests"] == 1


@pytest.mark.parametrize("component", ["hs300", "zz1000", "cpo", "news"])
def test_any_missing_or_stale_component_defers_model_but_continues_quotes(enriched, component):
    if component in ("hs300", "zz1000"):
        code = {"hs300": "sh000300", "zz1000": "sh000852"}[component]
        enriched.index_patch = lambda quotes: quotes.pop(code)
    else:
        def patch(snapshot):
            item = snapshot["context"][component]
            item["status"] = "stale"
            item["items" if component == "news" else "values"] = [] if component == "news" else {}
        enriched.context_source.patch = patch
    client = BudgetClient()
    result = enriched.run(client)
    assert client.states == [] and result["usage"]["requests"] == 0
    assert result["last_model_context"] is None
    assert len(result["equity"]) >= 30
    assert result["stop_reason"] == "context_not_ready"
    assert result["status"] == "stopped"


def test_slow_external_refresh_cannot_block_quotes_pending_fill_or_paid_settlement(enriched, monkeypatch):
    pending = []
    shutdown = []

    class SplitExecutor(Executor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.is_context = kwargs["thread_name_prefix"] == "jev-live-context"
            self.count = 0

        def submit(self, fn, state):
            self.count += 1
            if self.is_context and enriched.clock.second >= 31:
                future = Future()  # Context fetch remains unfinished for whole run.
                pending.append(future)
                return future
            return super().submit(fn, state)

        def shutdown(self, **kwargs):
            shutdown.append((self.is_context, kwargs))
            super().shutdown(**kwargs)

    monkeypatch.setattr(service, "ThreadPoolExecutor", SplitExecutor)
    result = enriched.run(BudgetClient(lambda: answer("BUY", cost=.000025)))
    assert len(pending) == 1 and pending[0].cancelled()
    assert result["usage"]["requests"] == 1 and result["usage"]["cost_usd"] == pytest.approx(.000025)
    assert len(result["trades"]) == 1
    assert result["account"]["today_frozen_shares"] == 100
    assert len(result["equity"]) >= 32
    assert (True, {"wait": False, "cancel_futures": True}) in shutdown


def test_context_refresh_failure_keeps_quotes_and_never_fabricates_news_or_buys(enriched):
    enriched.context_source.patch = lambda _: (_ for _ in ()).throw(ValueError("private_provider_failure"))
    client = BudgetClient()
    result = enriched.run(client)
    assert len(result["equity"]) >= 30 and client.states == []
    assert result["context"]["cpo"]["status"] == result["context"]["news"]["status"] == "error"
    assert result["context"]["news"]["items"] == []
    assert "private_provider_failure" not in str(result)


@pytest.mark.parametrize("boundary", ["deadline", "quote_stale", "context_stale"])
def test_reservation_write_overrun_cannot_dispatch_stale_or_late_request(enriched, boundary):
    def save(run):
        if run["usage"]["requests"]:
            enriched.clock.second += {"deadline": 20, "quote_stale": 11, "context_stale": 16}[boundary]
    enriched.store.on_save = save
    client = BudgetClient()
    result = enriched.run(client, duration_seconds=90 if boundary != "deadline" else 35)
    assert client.states == []
    assert result["usage"]["requests"] == result["usage"]["cost_usd"] == 0
    assert result["last_model_context"] is None


def test_rejected_executor_does_not_advertise_context_was_sent(enriched, monkeypatch):
    class SplitExecutor(Executor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.is_context = kwargs["thread_name_prefix"] == "jev-live-context"

        def submit(self, fn, state):
            if not self.is_context:
                raise RuntimeError("synthetic rejected submission")
            return super().submit(fn, state)

    monkeypatch.setattr(service, "ThreadPoolExecutor", SplitExecutor)
    result = enriched.run()
    assert result["status"] == "failed"
    assert result["usage"]["requests"] == result["usage"]["cost_usd"] == 0
    assert result["last_model_context"] is None


def test_budget_above_ten_usd_rejected_without_extending_run_or_making_requests(enriched):
    client = BudgetClient()
    with pytest.raises(ValueError):
        enriched.run(client, max_cost_usd=10.01)
    assert client.states == [] and enriched.context_source.calls == 0


def test_write_failure_after_expired_reservation_cannot_double_refund(enriched):
    pending_failure = False
    failed = False

    def save(run):
        nonlocal pending_failure, failed
        if not failed and run["usage"]["requests"]:
            enriched.clock.second += 11
            pending_failure = True
        elif pending_failure:
            pending_failure, failed = False, True
            raise OSError("synthetic rollback save error")

    enriched.store.on_save = save
    client = BudgetClient()
    result = enriched.run(client, duration_seconds=90)
    assert result["status"] == "failed" and client.states == []
    assert result["usage"]["requests"] == result["usage"]["cost_usd"] == 0
    assert result["usage"]["unconfirmed_requests"] == 0
    assert result["last_model_context"] is None


def test_budget_fallback_for_injected_client_uses_enriched_question(enriched):
    client = Client()
    observed = []

    def outcome():
        body = {"state": client.states[-1],
                "questions": JevLiveContextDecisionsClient(api_key="")._build_questions()}
        expected = len(json.dumps(body, ensure_ascii=False).encode()) * client.input_price_per_million / 1e6
        observed.append((enriched.store.records[-1]["usage"]["cost_usd"], expected))
        return answer()

    client.outcome = outcome
    result = enriched.run(client)
    assert len(observed) == 1
    assert observed[0][0] == pytest.approx(observed[0][1])
    assert result["usage"]["requests"] == 1
