"""Explicit arithmetic and point-in-time cases; no invented market acceptance."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json

import pytest

from src.services.jev_cb_t0_engine import CBT0Config, build_cb_state, run_cb_t0_backtest
from src.services.jev_cb_decisions_client import JevCBDecisionsClient, validate_cb_state
from src.services.jev_decisions_client import JevClientError, JevDecisionsClient


def cfg(**kwargs):
    return CBT0Config(**({"lookback_events": 2, "decision_interval_seconds": 1,
                         "latency_ms": 0, "order_lots": 1, "participation_rate": 1,
                         "commission_bps": 0, "slippage_bps": 0,
                         "restore_base_at_close": False, "restore_base_at_evaluation_end": False} | kwargs))


def events(prices=None, start="2026-01-05T09:30:00+08:00", unit="bonds"):
    prices = prices or [100] * 8
    time = datetime.fromisoformat(start)
    return [{"time": (time + timedelta(seconds=i)).isoformat(), "sequence": i,
             "last_price": price, "bid_price": price, "ask_price": price,
             "bid_volume": 10000, "ask_volume": 10000, "trade_volume": 10000,
             "quantity_unit": unit, "volume_kind": "incremental",
             "event_type": "trade", "trading_status": "trading"} for i, price in enumerate(prices)]


def decision(action="HOLD", **kwargs):
    return {"action": action, "status": "ok", "confidence": .9,
            "probabilities": {name: .8 if name == action else .1 for name in ("BUY", "SELL", "HOLD")},
            "latency_ms": 0} | kwargs


def run_sequence(raw, actions, config=None, **kwargs):
    replies = iter(actions)
    return run_cb_t0_backtest(raw, config or cfg(), lambda _: decision(next(replies, "HOLD")), **kwargs)


def test_initial_rounding_preserves_exact_400000_equity_without_purchase():
    result = run_sequence(events([123.4] * 3), [])
    summary = result["summary"]
    assert summary["initial_base_quantity"] == 1620
    assert summary["initial_cash_after_rounding"] == pytest.approx(200000 + 200000 - 1620 * 123.4)
    assert result["equity"][0]["equity"] == pytest.approx(400000)
    assert result["fills"] == []
    assert summary["incremental_t_pnl"] == pytest.approx(0)


def test_sell_existing_base_then_repurchase_generates_exact_incremental_t_profit():
    raw = events([100, 100, 110, 110, 100, 100])
    result = run_sequence(raw, ["SELL", "HOLD", "BUY"])
    assert [(fill["side"], fill["quantity"], fill["price"]) for fill in result["fills"]] == [
        ("SELL", 10, 110), ("BUY", 10, 100)]
    assert result["summary"]["inventory"] == 2000
    assert result["summary"]["cash"] == 200100
    assert result["summary"]["incremental_t_pnl"] == 100
    assert result["summary"]["sell_tax"] == 0
    assert result["fills"][0]["time"] != result["orders"][0]["signal_time"]


def test_cash_difference_is_not_mislabeled_profit_when_inventory_differs():
    result = run_sequence(events([100, 100, 110, 120]), ["SELL"])
    summary = result["summary"]
    assert summary["cash_difference"] == 1100
    assert summary["inventory_difference"] == -10
    assert summary["inventory_deviation_value"] == -1200
    assert summary["incremental_t_pnl"] == -100


def test_partial_fills_apply_volume_depth_caps_and_minimum_commission_once_per_order():
    raw = events([100] * 6)
    for row in raw:
        row["trade_volume"] = 101
    raw[4]["trade_volume"] = 0
    result = run_sequence(raw, ["BUY"], cfg(order_lots=3, participation_rate=.1, min_commission=5))
    assert [row["quantity"] for row in result["fills"]] == [10, 10, 10]
    assert [row["commission"] for row in result["fills"]] == [5, 0, 0]
    assert result["orders"][0]["status"] == "filled"
    assert result["summary"]["incremental_t_pnl"] == -5
    assert result["summary"]["cash"] == 196995


def test_sparse_events_expire_orders_without_fabricated_ticks_or_fills():
    raw = events([100] * 3)
    raw[2]["time"] = "2026-01-05T09:31:00+08:00"
    result = run_sequence(raw, ["BUY"], cfg(latency_ms=1000, order_ttl_ms=1000))
    assert result["fills"] == []
    assert result["orders"][0]["end_reason"] == "ttl_expired"
    assert len(result["equity"]) == 3
    assert result["data_quality"]["synthetic_event_count"] == 0


def test_model_latency_is_counted_in_next_real_event_eligibility():
    raw = events([100] * 6)
    result = run_cb_t0_backtest(raw, cfg(latency_ms=1000), lambda _: decision("BUY", latency_ms=2500))
    assert result["fills"][0]["time"] == raw[4]["time"]
    assert result["orders"][0]["eligible_at"] == "2026-01-05T09:30:03.500000+08:00"


@pytest.mark.parametrize("action,confidence", [("BUY", .9), ("HOLD", .9), ("BUY", .1)])
def test_one_second_decisions_wait_for_the_previous_real_model_response(action, confidence):
    raw = events([100] * 8)
    result = run_cb_t0_backtest(raw, cfg(), lambda _: decision(action, confidence=confidence, latency_ms=1100))
    assert [s["time"] for s in result["signals"]] == [raw[i]["time"] for i in (1, 3, 5, 7)]
    assert result["execution_version"] == "event-execution-v2-serial-model"
    if action == "BUY" and confidence == .9:
        assert result["fills"][0]["time"] == raw[3]["time"]
    else:
        assert result["fills"] == []


def test_thirty_second_cadence_is_unchanged_by_shorter_model_response_latency():
    raw = events([100] * 93)
    result = run_cb_t0_backtest(raw, cfg(decision_interval_seconds=30), lambda _: decision(latency_ms=1100))
    assert [s["time"] for s in result["signals"]] == [raw[i]["time"] for i in (1, 31, 61, 91)]


def test_explicit_idealized_latency_mode_keeps_the_requested_cadence():
    raw = events([100] * 5)
    result = run_cb_t0_backtest(raw, cfg(use_model_latency=False), lambda _: decision(latency_ms=1100))
    assert [s["time"] for s in result["signals"]] == [raw[i]["time"] for i in (1, 2, 3, 4)]


def test_quote_interval_crossing_latency_is_not_prorated_or_used():
    raw = events([100] * 5)
    for i, row in enumerate(raw):
        row["event_type"] = "quote"
        row["volume_interval_start"] = (datetime.fromisoformat(row["time"]) - timedelta(seconds=1)).isoformat()
    result = run_sequence(raw, ["BUY"], cfg(latency_ms=1000))
    assert result["fills"][0]["time"] == raw[3]["time"]
    assert result["data_quality"]["blocked_reasons"]["volume_interval_overlaps_latency"] == 1


@pytest.mark.parametrize("mutation,reason", [
    ({"trading_status": "halted"}, "not_continuous_trading"),
    ({"event_type": "quote"}, "missing_volume_interval"),
    ({"trade_volume": 0}, "no_verified_incremental_volume"),
    ({"bid_price": None, "ask_price": None}, "missing_order_book"),
    ({"ask_volume": 5}, "insufficient_cash_inventory_or_participation"),
])
def test_missing_or_nonexecutable_liquidity_never_fills(mutation, reason):
    raw = events([100] * 3)
    raw[2].update(mutation)
    result = run_sequence(raw, ["BUY"])
    assert result["fills"] == []
    assert result["data_quality"]["blocked_reasons"][reason] == 1


def test_conservative_trade_fallback_requires_explicit_choice():
    raw = events([100] * 3)
    for row in raw:
        row["bid_price"] = row["ask_price"] = None
    result = run_sequence(raw, ["BUY"], cfg(fill_mode="conservative_trade", slippage_bps=2))
    assert result["fills"][0]["price_source"] == "conservative_trade_assumption"
    assert result["fills"][0]["price"] == pytest.approx(100.02)


def test_target_inventory_mode_can_sell_base_without_shorting_or_borrowing():
    result = run_sequence(events([100] * 5), ["SELL", "BUY"], cfg(inventory_mode="target_inventory"))
    assert [row["quantity"] for row in result["fills"]] == [1000, 2000]
    assert result["summary"]["inventory"] == 3000
    assert result["summary"]["cash"] == 100000
    assert all(row["cash"] >= 0 and row["inventory"] >= 0 for row in result["equity"])


def test_restoration_submits_normal_delayed_orders_and_reports_unfilled_gap():
    raw = events([100] * 5, start="2026-01-05T14:54:57+08:00")
    result = run_sequence(raw, ["SELL"], cfg(restore_base_at_close=True))
    assert result["orders"][-1]["reason"] == "restore_base_at_close"
    assert result["summary"]["inventory_difference"] == 0
    assert result["summary"]["restore_gaps"] == []
    partial = run_sequence(raw[:-1], ["SELL"], cfg(restore_base_at_close=True))
    assert len(partial["fills"]) == 1
    assert partial["summary"]["restore_gaps"][0]["quantity_gap"] == -10
    assert partial["orders"][-1]["end_reason"] == "no_further_real_event"


def test_decision_clock_uses_elapsed_time_not_event_count_and_is_bounded():
    raw = events([100] * 8)
    result = run_cb_t0_backtest(raw, cfg(decision_interval_seconds=3, max_model_calls=1), lambda _: decision())
    assert result["summary"]["model_calls"] == 1
    assert [row["time"] for row in result["signals"]] == [raw[1]["time"], raw[4]["time"], raw[7]["time"]]
    assert result["summary"]["model_call_limit_reached"] is True
    assert result["signals"][-1]["status"] == "budget_limit"


def test_factor_asof_availability_and_staleness_prevent_future_leakage():
    raw = events([100] * 8)
    context = {"underlying": [{"asof": raw[0]["time"], "available_at": raw[4]["time"],
                                "values": {"return_30s": .1}}]}
    absent = build_cb_state(raw[:3], cfg(context_max_age_seconds=5), contexts=context)
    assert absent["factors"]["underlying"]["status"] == "missing"
    fresh = build_cb_state(raw[:5], cfg(context_max_age_seconds=5), contexts=context)
    assert fresh["factors"]["underlying"]["values"] == {"return_30s": .1}
    stale = build_cb_state(raw, cfg(context_max_age_seconds=5), contexts=context)
    assert stale["factors"]["underlying"] == {"status": "stale", "age_seconds": 7, "values": {}}
    future = deepcopy(context)
    future["underlying"].append({"asof": "2026-01-06T09:30:00+08:00", "available_at": "2026-01-06T09:30:00+08:00",
                                 "values": {"return_30s": 999}})
    assert build_cb_state(raw[:5], cfg(context_max_age_seconds=5), contexts=future) == fresh
    with pytest.raises(TypeError):
        fresh["factors"]["underlying"]["values"]["return_30s"] = 100


def test_orderbook_ablation_removes_model_fields_but_preserves_executor_quotes():
    raw = events([100] * 5)
    seen = []
    no_book = cfg(factors=("ohlcv",))
    result = run_cb_t0_backtest(raw, no_book, lambda state: seen.append(state) or decision("BUY"))
    assert all(set(row) == {"age_seconds", "price", "volume", "event_type", "trade_age_seconds"} for row in seen[0]["normalized_events"])
    assert all(value["status"] == "disabled" for value in seen[0]["factors"].values())
    assert result["fills"][0]["price_source"] == "best_ask"
    assert validate_cb_state(seen[0])["factor_groups"] == ["ohlcv"]


def test_execution_parameters_and_identity_never_enter_model_state():
    raw = events([100] * 3)
    first = build_cb_state(raw, cfg())
    second = build_cb_state(raw, cfg(symbol="999999.SH", initial_cash=300000, initial_base_value=100000,
                                    min_commission=10, slippage_bps=99, latency_ms=5000, order_lots=7))
    assert first == second
    assert not any(key in json.dumps(first) for key in ("999999", "200000", "initial_cash", "2026-"))


def test_evaluation_split_preserves_warmup_and_excludes_end_timestamp():
    raw = events([100, 110, 120, 130, 140, 150])
    seen = []
    result = run_cb_t0_backtest(raw, cfg(evaluation_start=raw[2]["time"], evaluation_end=raw[4]["time"]),
                              lambda state: seen.append(state) or decision("BUY"))
    assert [row["time"] for row in result["equity"]] == [raw[2]["time"], raw[3]["time"]]
    assert result["equity"][0]["equity"] == pytest.approx(400000)
    assert result["summary"]["initial_base_price"] == 120
    assert seen[0]["normalized_events"][0]["price"] == 1
    assert seen[0]["normalized_events"][-1]["price"] == pytest.approx(120 / 110)
    assert all(row["time"] < raw[4]["time"] for row in result["fills"])


def test_us_simulation_requires_explicit_currency_account_and_share_units():
    with pytest.raises(ValueError):
        cfg(instrument_type="us_equity")
    config = cfg(instrument_type="us_equity", currency="USD", exchange_timezone="America/New_York",
                 simulation_account="unlevered_margin", lot_size=1, price_tick=.01, session_close="16:00:00")
    raw = events([100] * 3, start="2012-06-21T09:30:00-04:00", unit="shares")
    result = run_sequence(raw, ["BUY"], config)
    assert result["summary"]["currency"] == "USD"
    assert result["summary"]["initial_equity"] == 400000
    assert result["fills"][0]["quantity"] == 1
    assert any("cash-account settlement" in item for item in result["data_quality"]["warnings"])


@pytest.mark.parametrize("mutation", [{"last_price": float("nan")}, {"quantity_unit": "unknown"},
                                       {"volume_kind": "cumulative"}, {"bid_price": 200},
                                       {"time": "2026-01-05"}])
def test_invalid_or_unverified_events_are_rejected(mutation):
    raw = events([100] * 3)
    raw[1].update(mutation)
    with pytest.raises(ValueError):
        run_sequence(raw, [])


def test_cb_client_uses_new_questions_and_strict_protocol_without_weakening_old_client():
    state = build_cb_state(events([100] * 3), cfg())
    client = JevCBDecisionsClient(api_key="unit-test-key")
    assert client.cache_version == state["schema"]
    assert client._prepare_state(state)["schema"] == "cb-t0-factors-v1"
    assert "backward-asof" in client._build_questions()["action"]["instructions"]
    with pytest.raises(JevClientError):
        JevDecisionsClient(api_key="unit-test-key")._prepare_state(state)
    for extra in ({"cash": 400000}, {"symbol": "113677.SH"}, {"news": "text"}):
        with pytest.raises(JevClientError):
            validate_cb_state(dict(state) | extra)
    corrupted = json.loads(json.dumps(state))
    corrupted["normalized_events"][0]["volume"] = float("nan")
    with pytest.raises(JevClientError):
        validate_cb_state(corrupted)


def test_same_timestamp_events_have_explicit_sequence_and_global_indices():
    raw = events([100] * 4)
    raw[2]["time"] = raw[1]["time"]
    result = run_sequence(raw, ["BUY"])
    assert result["signals"][0]["event_index"] == 1
    assert result["orders"][0]["signal_event_index"] == 1
    assert result["fills"][0]["event_index"] == 2
    assert result["fills"][0]["sequence"] == 2
    assert result["fills"][0]["time"] == result["signals"][0]["time"]


def test_stale_quotes_and_stale_trade_fallback_do_not_generate_fills():
    raw = events([100] * 3)
    raw[2]["quote_age_ms"] = 5001
    result = run_sequence(raw, ["BUY"])
    assert not result["fills"]
    assert result["data_quality"]["blocked_reasons"]["stale_order_book"] == 1
    raw[2]["last_trade_age_ms"] = 30001
    result = run_sequence(raw, ["BUY"], cfg(fill_mode="conservative_trade"))
    assert not result["fills"]
    assert result["data_quality"]["blocked_reasons"]["stale_trade_price"] == 1


def test_real_time_returns_book_imbalance_microprice_and_true_vwap():
    raw = events([100, 101, 102, 103, 104, 105, 106, 107])
    raw[-1].update(bid_price=106.99, ask_price=107.01, bid_volume=300, ask_volume=100,
                   quote_age_ms=2, last_trade_age_ms=900)
    state = build_cb_state(raw, cfg(lookback_events=8))
    values = state["indicators"]
    assert values["return_5s"] == pytest.approx(107 / 102 - 1)
    assert values["return_15s"] is None
    assert values["order_imbalance"] == .5
    assert values["microprice_gap"] == pytest.approx(107.005 / 107 - 1, abs=1e-10)
    assert values["window_vwap_gap"] == pytest.approx(107 / 103.5 - 1)
    assert values["volume_to_mean"] == 1
    assert state["normalized_events"][-1]["trade_age_seconds"] == .9
    assert validate_cb_state(state)["indicators"] == values
    no_book = build_cb_state(raw, cfg(lookback_events=8, factors=("ohlcv",)))
    assert not {"order_imbalance", "microprice_gap", "spread_bps"} & set(no_book["indicators"])


def test_aggregated_quotes_need_actual_notional_for_vwap():
    raw = events([100, 110])
    for row in raw:
        row["event_type"] = "quote"
    state = build_cb_state(raw, cfg())
    assert state["indicators"]["window_vwap_gap"] is None
    for row in raw:
        row["trade_notional"] = 102 * row["trade_volume"]
    state = build_cb_state(raw, cfg())
    assert state["indicators"]["window_vwap_gap"] == pytest.approx(110 / 102 - 1)


def test_evaluation_end_restore_does_not_change_exchange_session_close():
    raw = events([100] * 8)
    result = run_sequence(raw, ["SELL"], cfg(evaluation_start=raw[0]["time"], evaluation_end=raw[7]["time"],
                                             restore_base_at_evaluation_end=True, restore_before_seconds=2))
    assert result["orders"][-1]["reason"] == "restore_base_at_evaluation_end"
    assert result["fills"][-1]["time"] == raw[6]["time"]
    assert result["summary"]["inventory_difference"] == 0
    assert result["summary"]["restore_gaps"] == []
    raw[6]["trade_volume"] = 0
    missing = run_sequence(raw, ["SELL"], cfg(evaluation_start=raw[0]["time"], evaluation_end=raw[7]["time"],
                                              restore_base_at_evaluation_end=True, restore_before_seconds=2))
    assert missing["summary"]["inventory_difference"] == -10
    assert missing["summary"]["restore_gaps"][0]["reason"] == "no_executable_restoration_before_evaluation_end"
