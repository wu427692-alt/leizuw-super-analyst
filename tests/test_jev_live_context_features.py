"""Live context features use observable timestamps, without fabricated windows."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from src.services.jev_live_context_features import LiveContextTracker
from src.services.jev_decisions_client import JevClientError
from tests.test_jev_live_context_client import state as wire_state


START = datetime.fromisoformat("2026-09-21T13:00:00+08:00")


def iso(seconds):
    return (START + timedelta(seconds=seconds)).isoformat()


def batch(second, *, price=100, received=None):
    quote = {"source_time": iso(second), "received_at": iso(second if received is None else received),
             "last_price": price, "pre_close": 100, "trading_status": "trading"}
    return {"quotes": {"sh000300": deepcopy(quote), "sh000852": deepcopy(quote)}}


def external(second=60):
    numeric = {"status": "fresh", "source": "public_cpo", "source_time": iso(second),
               "available_at": iso(second), "age_seconds": 0,
               "values": {"return_since_prev_close": .01, "breadth_up": .6, "coverage_ratio": .9, "member_count": 10}}
    news = {"status": "fresh", "source": "public_news", "fetched_at": iso(second), "items": [{
        "title": "Public news", "summary": "", "source": "public_news", "url": None,
        "published_at": iso(second-60), "available_at": iso(second)}]}
    return {"context": {"cpo": numeric, "news": news}}


def test_index_30_60_returns_require_real_trailing_windows_and_use_current_stock_relative_return():
    tracker = LiveContextTracker()
    for second in range(0, 61, 10):
        tracker.observe_indices(batch(second, price=100+second), iso(second))
        values = tracker.view(iso(second))["hs300"]["values"]
        assert ("return_30s" in values) is (second >= 30)
        assert ("return_60s" in values) is (second >= 60)
    values = tracker.view(iso(60), stock_return_30s=.1)["hs300"]["values"]
    assert values["return_30s"] == pytest.approx(160/130-1)
    assert values["return_60s"] == pytest.approx(.6)
    assert values["relative_return"] == pytest.approx(.1-(160/130-1))
    assert "relative_return" not in tracker.numeric["hs300"]["values"]  # View does not mutate history.


def test_real_source_gap_resets_windows_and_no_nearest_interpolation():
    tracker = LiveContextTracker()
    for second in (0, 10, 20, 30):
        tracker.observe_indices(batch(second, price=100+second), iso(second))
    assert "return_30s" in tracker.view(iso(30))["hs300"]["values"]
    tracker.observe_indices(batch(50, price=150), iso(50))
    assert "return_30s" not in tracker.view(iso(50))["hs300"]["values"]
    for second in (60, 70, 80):
        tracker.observe_indices(batch(second, price=100+second), iso(second))
    values = tracker.view(iso(80))["hs300"]["values"]
    assert values["return_30s"] == pytest.approx(180/150-1)
    assert "return_60s" not in values


@pytest.mark.parametrize("change", [
    {"source_time": iso(11)}, {"received_at": iso(11)}, {"received_at": iso(8)},
    {"last_price": float("nan")}, {"last_price": True}, {"pre_close": 0}, {"trading_status": "halted"},
])
def test_future_or_invalid_index_quote_is_not_model_evidence(change):
    tracker = LiveContextTracker()
    quotes = batch(9)
    quotes["quotes"]["sh000300"].update(change)
    tracker.observe_indices(quotes, iso(10))
    item = tracker.view(iso(10))["hs300"]
    assert item["status"] != "fresh" and item["values"] == {}
    assert tracker.histories["hs300"] == []


def test_indices_age_at_dispatch_not_only_at_fetch_and_do_not_resurrect_after_out_of_order():
    tracker = LiveContextTracker()
    tracker.observe_indices(batch(10), iso(10))
    assert tracker.view(iso(25))["hs300"]["status"] == "fresh"
    assert tracker.view(iso(25.001))["hs300"]["status"] == "stale"
    assert tracker.view(iso(25.001))["hs300"]["values"] == {}
    tracker.observe_indices(batch(9), iso(10))
    assert tracker.view(iso(10))["hs300"]["status"] == "error"
    assert len(tracker.histories["hs300"]) == 1


def test_cpo_age_and_news_fetch_age_are_reassessed_and_views_are_independent():
    tracker = LiveContextTracker()
    tracker.observe_external(external())
    assert tracker.view(iso(75))["cpo"]["status"] == "fresh"
    assert tracker.view(iso(75.001))["cpo"]["values"] == {}
    assert tracker.view(iso(240))["news"]["status"] == "fresh"
    assert tracker.view(iso(240.001))["news"]["items"] == []
    old = tracker.view(iso(60))
    old["news"]["items"][0]["title"] = "mutated"
    assert tracker.view(iso(60))["news"]["items"][0]["title"] == "Public news"


@pytest.mark.parametrize("patch", [
    {"published_at": iso(61)}, {"available_at": iso(61)},
    {"published_at": iso(-21601)}, {"available_at": iso(-1)},
])
def test_unavailable_future_or_overage_news_removed_before_model(patch):
    tracker = LiveContextTracker()
    value = external()
    value["context"]["news"]["items"][0].update(patch)
    tracker.observe_external(value)
    assert tracker.view(iso(60))["news"]["items"] == []


def test_model_state_is_strict_new_protocol_and_unknown_external_fields_cannot_escape():
    tracker = LiveContextTracker()
    tracker.observe_indices(batch(60), iso(60))
    value = external()
    tracker.observe_external(value)
    candidate = tracker.model_state(wire_state()["base_state"], iso(60))
    assert candidate["schema"] == "jev-live-context-v1"
    assert set(candidate["context"]) == {"hs300", "zz1000", "cpo", "news"}
    value["context"]["cpo"]["values"]["portfolio"] = 200000
    tracker.observe_external(value)
    with pytest.raises(JevClientError):
        tracker.model_state(wire_state()["base_state"], iso(60))
