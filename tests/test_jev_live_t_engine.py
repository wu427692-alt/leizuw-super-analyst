"""Deterministic paper-account arithmetic; never calls quotes, models or brokers."""
from datetime import datetime, timedelta

import pytest

from src.services.jev_live_t_engine import LiveTConfig, LiveTEngine


START = datetime.fromisoformat("2026-09-21T10:00:00+08:00")


def quote(second=0, price=100, **patch):
    now = START + timedelta(seconds=second)
    return {"source_time": now.isoformat(), "received_at": now.isoformat(),
            "last_price": price, "bid_price": price, "ask_price": price,
            "bid_volume": 100000, "ask_volume": 100000,
            "cumulative_volume": 100000 + int(second) * 10000,
            "volume_unit": "shares", "trading_status": "trading"} | patch


def config(**patch):
    return LiveTConfig(**({"initial_base_value": 200000, "initial_cash": 100000,
                          "order_quantity": 100, "commission_bps": 0, "min_commission": 0,
                          "sell_tax_bps": 0, "transfer_fee_bps": 0,
                          "participation_rate": 1, "max_inventory_deviation_ratio": 1} | patch))


def decision(action="BUY", confidence=.9, **patch):
    return {"action": action, "confidence": confidence, "status": "ok",
            "probabilities": {a: .8 if a == action else .1 for a in ("BUY", "SELL", "HOLD")},
            "latency_ms": 0} | patch


def submit(engine, action, *, requested=0, responded=None, event_id=None, **patch):
    return engine.submit_decision(decision(action, **patch), signal_event_id=event_id or engine._event_id,
                                  requested_at=(START + timedelta(seconds=requested)).isoformat(),
                                  responded_at=(START + timedelta(seconds=requested if responded is None else responded)).isoformat())


def test_initial_eight_million_base_and_four_million_cash_are_conserved_without_purchase():
    engine = LiveTEngine()
    result = engine.step(quote(price=84.27))
    assert result["reason"] == "initialized_no_fill"
    summary = result["snapshot"]
    assert summary["initial_base_quantity"] == 94900
    assert summary["equity"] == pytest.approx(12000000)
    assert summary["cash"] == pytest.approx(12000000 - 94900 * 84.27)
    assert summary["sellable_shares"] == 94900 and summary["frozen_shares"] == 0
    assert summary["fill_count"] == summary["commission"] == 0


def test_sell_then_buy_closed_pair_includes_both_fees_without_inventing_historical_cost():
    engine = LiveTEngine(config(commission_bps=3, min_commission=5, sell_tax_bps=5, transfer_fee_bps=.1))
    engine.step(quote())
    submit(engine, "SELL")
    sold = engine.step(quote(1))["fill"]
    assert sold["commission"] == 5 and sold["sell_tax"] == 5 and sold["transfer_fee"] == .1
    submit(engine, "BUY", requested=1)
    bought = engine.step(quote(2, price=99))["fill"]
    assert bought["commission"] == 5 and bought["sell_tax"] == 0
    summary = engine.snapshot()
    assert summary["closed_t_net_pnl"] == pytest.approx(84.801)
    assert summary["net_increment"] == pytest.approx(84.801)
    assert summary["closed_t_cost_reduction_per_initial_share"] == pytest.approx(84.801 / 2000)
    assert summary["open_t_mark_to_market_pnl"] == pytest.approx(0, abs=1e-8)
    assert summary["sellable_shares"] == 1900 and summary["frozen_shares"] == 100
    assert "historical acquisition cost is unknown" in " ".join(engine.result()["methodology"])


def test_t1_never_sells_today_purchases_and_releases_them_only_on_a_later_date():
    engine = LiveTEngine(config(order_quantity=2000, initial_cash=500000))
    engine.step(quote()); submit(engine, "BUY")
    engine.step(quote(1))
    assert engine.snapshot()["inventory"] == 4000
    submit(engine, "SELL", requested=1); engine.step(quote(2))
    assert engine.snapshot()["sellable_shares"] == 0 and engine.snapshot()["frozen_shares"] == 2000
    submit(engine, "SELL", requested=2)
    assert engine.step(quote(3))["fill"] is None
    next_day = START + timedelta(days=1)
    result = engine.step(quote(4, source_time=next_day.isoformat(), received_at=next_day.isoformat(), cumulative_volume=10))
    assert result["reason"] == "new_day_volume_baseline_no_fill"
    assert result["snapshot"]["sellable_shares"] == 2000 and result["snapshot"]["frozen_shares"] == 0
    engine.submit_decision(decision("SELL"), signal_event_id=result["event_id"],
                           requested_at=next_day.isoformat(), responded_at=next_day.isoformat())
    fill = engine.step(quote(5, source_time=(next_day + timedelta(seconds=1)).isoformat(),
                             received_at=(next_day + timedelta(seconds=1)).isoformat(), cumulative_volume=10010))["fill"]
    assert fill["quantity"] == 2000 and fill["inventory_after"] == 0


def test_buy_first_pair_can_close_using_yesterday_shares_while_new_shares_stay_frozen():
    engine = LiveTEngine(config()); engine.step(quote())
    submit(engine, "BUY"); engine.step(quote(1, price=99))
    submit(engine, "SELL", requested=1); engine.step(quote(2, price=100))
    summary = engine.snapshot()
    assert summary["closed_t_net_pnl"] == 100
    assert summary["inventory"] == 2000 and summary["sellable_shares"] == 1900 and summary["frozen_shares"] == 100


def test_actual_response_latency_and_entire_interval_must_precede_fill():
    engine = LiveTEngine(config()); engine.step(quote())
    order = submit(engine, "BUY", responded=1.1)["order"]
    assert order["eligible_at"].endswith("10:00:01.100000+08:00")
    assert engine.step(quote(1))["fill"] is None
    assert engine.step(quote(2))["fill"] is None
    assert engine.step(quote(3))["fill"]["quantity"] == 100


def test_delayed_three_second_source_snapshots_use_source_volume_interval_not_poll_time():
    engine = LiveTEngine(config(order_ttl_ms=20000))
    engine.step(quote(received_at=(START + timedelta(seconds=3)).isoformat()))
    submit(engine, "BUY", requested=3, responded=4.1)
    for second in (3, 6):
        assert engine.step(quote(second, received_at=(START + timedelta(seconds=second + 3)).isoformat()))["fill"] is None
    fill = engine.step(quote(9, received_at=(START + timedelta(seconds=12)).isoformat()))["fill"]
    assert fill["source_time"] == (START + timedelta(seconds=9)).isoformat()
    assert fill["received_at"] == (START + timedelta(seconds=12)).isoformat()


def test_quotes_arriving_while_model_runs_do_not_change_its_anchor_or_backfill_execution():
    engine = LiveTEngine(config()); first = engine.step(quote())
    engine.step(quote(1))
    submit(engine, "BUY", requested=0, responded=2, event_id=first["event_id"])
    assert engine.step(quote(2))["fill"] is None
    assert engine.step(quote(3))["fill"] is not None


@pytest.mark.parametrize("patch,reason", [
    ({"source_time": (START + timedelta(seconds=1)).isoformat()}, "future_source_timestamp"),
    ({"received_at": (START + timedelta(seconds=11)).isoformat()}, "stale_quote"),
    ({"last_price": float("nan")}, "invalid_quote"),
    ({"ask_volume": float("inf")}, "invalid_quote"),
    ({"volume_unit": "lots"}, "invalid_quote"),
    ({"bid_price": 101}, "invalid_quote"),
])
def test_unverified_stale_future_or_nonfinite_quotes_cannot_initialize_or_trade(patch, reason):
    engine = LiveTEngine(config())
    answer = engine.step(quote(**patch))
    assert not answer["accepted"] and answer["reason"] == reason
    assert not engine.snapshot()["initialized"] and not engine.result()["fills"]


def test_duplicate_old_and_regressed_counter_quotes_do_not_advance_or_fill():
    engine = LiveTEngine(config()); engine.step(quote()); submit(engine, "BUY")
    assert engine.step(quote())["reason"] == "duplicate_source_timestamp"
    assert engine.step(quote(-1))["reason"] == "out_of_order_quote"
    assert engine.step(quote(1, cumulative_volume=99999))["reason"] == "cumulative_volume_regressed"
    assert engine.snapshot()["fill_count"] == 0
    result = engine.step(quote(2, cumulative_volume=100100))
    assert result["incremental_volume"] == 100 and result["fill"]["quantity"] == 100


def test_gap_resets_volume_baseline_and_expires_order_without_catching_up_old_volume():
    engine = LiveTEngine(config()); engine.step(quote()); submit(engine, "BUY")
    gap = engine.step(quote(30, cumulative_volume=900000))
    assert gap["reason"] == "gap_resync_no_fill" and gap["incremental_volume"] is None
    assert engine.result()["orders"][0]["end_reason"] == "quote_gap"
    submit(engine, "BUY", requested=30)
    result = engine.step(quote(31, cumulative_volume=900099))
    assert result["incremental_volume"] == 99 and result["fill"] is None


def test_order_ttl_uses_arrival_time_and_never_fills_a_late_snapshot():
    engine = LiveTEngine(config(order_ttl_ms=1000)); engine.step(quote()); submit(engine, "BUY")
    assert engine.step(quote(2))["fill"] is None
    assert engine.result()["orders"][0]["end_reason"] == "order_ttl_expired"


def test_only_matched_shares_count_towards_realized_cost_reduction():
    engine = LiveTEngine(config(order_quantity=200)); engine.step(quote())
    submit(engine, "SELL"); engine.step(quote(1))
    submit(engine, "BUY", requested=1); engine.step(quote(2, price=99, ask_volume=100))
    summary = engine.snapshot()
    assert summary["net_increment"] == 200
    assert summary["closed_t_net_pnl"] == 100 and summary["closed_t_matched_quantity"] == 100
    assert summary["open_t_mark_to_market_pnl"] == 100 and summary["inventory_gap"] == -100
    assert summary["closed_t_cost_reduction_per_initial_share"] == .05


def test_losing_closed_roundtrip_reports_negative_cost_reduction():
    engine = LiveTEngine(config()); engine.step(quote())
    submit(engine, "SELL"); engine.step(quote(1))
    submit(engine, "BUY", requested=1); engine.step(quote(2, price=101))
    assert engine.snapshot()["closed_t_net_pnl"] == -100
    assert engine.snapshot()["closed_t_cost_reduction_per_initial_share"] == -.05


def test_partial_fills_respect_depth_participation_round_lots_and_one_commission_minimum():
    engine = LiveTEngine(config(order_quantity=300, participation_rate=.1, min_commission=5))
    engine.step(quote()); submit(engine, "BUY")
    fills = [engine.step(quote(i, cumulative_volume=100000 + 1001 * i, ask_volume=150))["fill"] for i in (1, 2, 3)]
    assert [f["quantity"] for f in fills] == [100, 100, 100]
    assert [f["commission"] for f in fills] == [5, 0, 0]
    assert engine.result()["orders"][0]["status"] == "filled"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_twenty_percent_inventory_deviation_is_a_hard_cap(side):
    engine = LiveTEngine(config(order_quantity=1000, max_inventory_deviation_ratio=.2))
    engine.step(quote()); submit(engine, side)
    assert engine.step(quote(1))["fill"]["quantity"] == 400
    assert engine.step(quote(2))["fill"] is None
    assert abs(engine.snapshot()["inventory_gap"]) == 400


def test_insufficient_cash_including_minimum_fees_never_borrows():
    engine = LiveTEngine(config(initial_cash=10000, min_commission=5))
    engine.step(quote()); submit(engine, "BUY")
    assert engine.step(quote(1))["fill"] is None
    assert engine.snapshot()["cash"] == 10000


def test_slippage_crosses_ask_and_rounds_adversely_to_one_cent():
    engine = LiveTEngine(config(slippage_bps=1)); engine.step(quote())
    submit(engine, "BUY")
    fill = engine.step(quote(1, ask_price=100.02))["fill"]
    assert fill["price"] == 100.04
    assert fill["slippage_cost"] == pytest.approx(2)


def test_supplied_price_band_blocks_adverse_slippage_beyond_limit():
    engine = LiveTEngine(config(slippage_bps=1)); engine.step(quote())
    submit(engine, "BUY")
    assert engine.step(quote(1, limit_up=100, limit_down=90))["fill"] is None


@pytest.mark.parametrize("clock", ["09:25:00", "11:30:00", "12:00:00", "14:57:00", "15:00:00"])
def test_caller_trading_flag_cannot_enable_auction_or_lunch_fills(clock):
    engine = LiveTEngine(config())
    when = f"2026-09-21T{clock}+08:00"
    result = engine.step(quote(source_time=when, received_at=when))
    assert not result["accepted"] and result["reason"] == "initial_quote_not_trading"


def test_order_is_cancelled_at_auction_boundary_even_if_caller_says_trading():
    engine = LiveTEngine(config())
    when = "2026-09-21T14:56:59+08:00"
    event = engine.step(quote(source_time=when, received_at=when))
    engine.submit_decision(decision(), signal_event_id=event["event_id"], requested_at=when, responded_at=when)
    result = engine.step(quote(1, source_time="2026-09-21T14:57:00+08:00", received_at="2026-09-21T14:57:00+08:00"))
    assert result["fill"] is None
    assert engine.result()["orders"][0]["end_reason"] == "outside_continuous_trading"


@pytest.mark.parametrize("action,confidence,status", [("HOLD", .9, "ok"), ("BUY", .1, "ok"), ("SELL", .9, "uncertain")])
def test_hold_or_below_threshold_or_uncertain_never_submits_order(action, confidence, status):
    engine = LiveTEngine(config()); engine.step(quote())
    assert submit(engine, action, confidence=confidence, status=status)["order"] is None
    assert engine.step(quote(1))["fill"] is None


def test_response_to_an_already_stale_signal_does_not_open_an_order():
    engine = LiveTEngine(config()); engine.step(quote())
    assert submit(engine, "BUY", responded=11)["reason"] == "stale_signal_quote"


def test_unmatched_sale_cash_is_not_profit_and_stop_does_not_fabricate_repurchase():
    engine = LiveTEngine(config()); engine.step(quote()); submit(engine, "SELL")
    engine.step(quote(1)); engine.step(quote(2, price=105))
    submit(engine, "BUY", requested=2)
    before = engine.snapshot()
    assert before["cash"] == 110000
    assert before["net_increment"] == -500 and before["closed_t_net_pnl"] == 0
    stopped = engine.stop("trial_finished", (START + timedelta(seconds=2)).isoformat())
    assert stopped["stopped"] and stopped["inventory_gap"] == -100
    assert stopped["pending_order"] is None and stopped["fill_count"] == 1
    assert engine.result()["orders"][-1]["status"] == "cancelled"
    assert engine.step(quote(3))["reason"] == "engine_stopped"
    assert submit(engine, "BUY", requested=3)["reason"] == "engine_stopped"


def test_result_copies_do_not_allow_mutating_the_ledger():
    engine = LiveTEngine(config()); event = engine.step(quote())
    event["snapshot"]["cash"] = -999
    result = engine.result(); result["equity"][0]["cash"] = -777
    assert engine.snapshot()["cash"] == 100000
    assert engine.result()["equity"][0]["cash"] == 100000


def test_config_rejects_invalid_lots_nonfinite_costs_or_disabled_t1_tick_contract():
    for patch in ({"order_quantity": 101}, {"commission_bps": float("nan")}, {"lot_size": 1}, {"price_tick": .001}):
        with pytest.raises(ValueError):
            config(**patch)


@pytest.mark.parametrize("action", ["BUY", "SELL"])
@pytest.mark.parametrize("confidence,expected", [(.58, 500), (.25, 200), (.09, 100), (0, 100), (1, 1000)])
def test_confidence_sizes_whole_lots_without_changing_buy_sell_direction(action, confidence, expected):
    engine = LiveTEngine(config(order_quantity=1000, confidence_sizing=True))
    engine.step(quote())
    submitted = submit(engine, action, confidence=confidence)
    assert submitted["reason"] == "order_submitted"
    assert submitted["order"]["side"] == action
    assert submitted["order"]["quantity"] == expected
    signal = submitted["signal"]
    assert signal["action"] == action and signal["status"] == "ok"
    assert signal["confidence"] == confidence and signal["requested_quantity"] == expected
    fill = engine.step(quote(1))["fill"]
    assert fill["side"] == action and fill["quantity"] == expected
    assert engine.result()["config"]["confidence_sizing"] is True


@pytest.mark.parametrize("confidence", [0, .58, 1])
def test_confidence_sizing_never_turns_hold_into_an_order(confidence):
    engine = LiveTEngine(config(order_quantity=1000, confidence_sizing=True))
    engine.step(quote())
    submitted = submit(engine, "HOLD", confidence=confidence)
    assert submitted["order"] is None and submitted["reason"] == "hold"
    assert submitted["signal"]["requested_quantity"] == 0
    assert engine.step(quote(1))["fill"] is None


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.001, 1.001, True])
def test_confidence_sizing_rejects_invalid_confidence_before_order(confidence):
    engine = LiveTEngine(config(order_quantity=1000, confidence_sizing=True))
    engine.step(quote())
    submitted = submit(engine, "BUY", confidence=confidence)
    assert submitted["reason"] == "invalid_decision_or_timing" and submitted["order"] is None
    assert engine.step(quote(1))["fill"] is None


def test_default_mode_keeps_old_threshold_and_fixed_quantity():
    engine = LiveTEngine(config(order_quantity=1000))
    assert engine.config.confidence_sizing is False
    engine.step(quote())
    refused = submit(engine, "BUY", confidence=.58)
    assert refused["reason"] == "uncertain" and refused["order"] is None
    assert refused["signal"]["requested_quantity"] == 0
    engine.step(quote(1))
    accepted = submit(engine, "BUY", confidence=.65, requested=1)
    assert accepted["order"]["quantity"] == 1000


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_confidence_sizing_does_not_relax_inventory_deviation_cap(side):
    engine = LiveTEngine(config(order_quantity=1000, confidence_sizing=True,
                               max_inventory_deviation_ratio=.2))
    engine.step(quote())
    submitted = submit(engine, side, confidence=.58)
    assert submitted["order"]["quantity"] == 500
    assert engine.step(quote(1))["fill"]["quantity"] == 400
    assert engine.step(quote(2))["fill"] is None
    assert abs(engine.snapshot()["inventory_gap"]) == 400


def test_confidence_sizing_never_sells_today_purchases_and_can_only_use_yesterday_shares():
    engine = LiveTEngine(config(initial_base_value=10000, initial_cash=100000,
                               order_quantity=1000, confidence_sizing=True))
    engine.step(quote())
    submit(engine, "BUY", confidence=.09)
    assert engine.step(quote(1))["fill"]["quantity"] == 100
    assert engine.snapshot()["frozen_shares"] == 100
    submit(engine, "SELL", confidence=1, requested=1)
    assert engine.step(quote(2))["fill"]["quantity"] == 100
    assert engine.snapshot()["sellable_shares"] == 0
    assert engine.snapshot()["frozen_shares"] == 100
    assert engine.step(quote(3))["fill"] is None


def test_confidence_minimum_lot_does_not_override_cash_or_fee_availability():
    engine = LiveTEngine(config(initial_cash=10000, min_commission=5,
                               order_quantity=1000, confidence_sizing=True))
    engine.step(quote())
    assert submit(engine, "BUY", confidence=0)["order"]["quantity"] == 100
    assert engine.step(quote(1))["fill"] is None
    assert engine.snapshot()["cash"] == 10000


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_confidence_sizing_option_is_a_real_boolean(value):
    with pytest.raises(ValueError):
        config(confidence_sizing=value)
