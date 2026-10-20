"""Execution risks: immutable trailing states, real timestamps, T+1 and costs."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from src.services.jev_signal_engine import BacktestConfig, build_features, run_backtest


def bars(count=30, day="2026-01-05", price=10.0):
    start = datetime.fromisoformat(day + "T09:30:00+08:00")
    return [{"time": (start + timedelta(minutes=index)).isoformat(),
             "open": price, "high": price + 0.2, "low": price - 0.2,
             "close": price, "volume": 100000, "adj_factor": 1,
             "limit_up": False, "limit_down": False, "suspended": False}
            for index in range(count)]


def config(**kwargs):
    return BacktestConfig(**({"symbol": "000001", "start_date": "2026-01-05", "end_date": "2026-01-06",
                              "lookback_bars": 20, "max_holding_bars": None} | kwargs))


def decision(action="BUY", confidence=0.9):
    return {"status": "ok", "action": action, "confidence": confidence,
            "probabilities": {key: 0.8 if key == action else 0.1 for key in ("BUY", "SELL", "HOLD")}}


def test_trailing_features_are_immutable_anonymous_and_scale_invariant():
    raw = bars(21)
    original = deepcopy(raw)
    features = build_features(raw[:20], 20)
    assert set(features) == {"timeframe", "lookback_bars", "normalized_bars", "indicators"}
    with pytest.raises(TypeError):
        features["normalized_bars"][0]["close"] = 999
    with pytest.raises(TypeError):
        features["indicators"]["rsi14"] = 999
    scaled = deepcopy(raw[:20])
    for row in scaled:
        for name in ("open", "high", "low", "close"):
            row[name] *= 4
        row["volume"] *= 100
    assert build_features(scaled, 20) == features
    raw[-1].update(open=500, high=500, low=500, close=500)
    assert build_features(raw[:20], 20) == features
    assert original[:20] == raw[:20]


def test_walk_forward_never_exposes_future_or_fills_same_bar():
    raw = bars(25)
    snapshots = []

    def decide(state):
        snapshots.append(state)
        return decision()

    result = run_backtest(raw, config(), decide)
    assert snapshots[0] == build_features(raw[:20], 20)
    first = result["trades"][0]
    assert first["signal_date"] == raw[19]["time"]
    assert first["date"] == raw[20]["time"]
    assert first["date"] > first["signal_date"]
    assert result["summary"]["model_calls"] == 6
    assert result["summary"]["position_market_value"] > 0
    assert result["summary"]["final_equity"] == pytest.approx(
        result["summary"]["cash"] + result["summary"]["position_market_value"])
    assert result["baselines"]["buy_hold"]["trades"][0]["date"] == first["date"]


def test_final_signal_does_not_invent_a_next_bar_fill():
    result = run_backtest(bars(20), config(), lambda _: decision())
    assert result["trades"] == []
    assert result["signals"][-1]["fill_status"] == "no_next_bar"
    assert result["summary"]["final_equity"] == 100000
    assert result["baselines"]["buy_hold"]["trades"] == []


def test_minute_t_plus_one_uses_shanghai_calendar_date():
    raw = bars(23) + bars(3, day="2026-01-06")
    sequence = iter([decision()] + [decision("SELL")] * 20)
    result = run_backtest(raw, config(), lambda _: next(sequence))
    trades = result["trades"]
    assert [row["action"] for row in trades] == ["BUY", "SELL"]
    assert trades[1]["date"] == raw[23]["time"]
    assert result["summary"]["blocked_reasons"]["t_plus_one"] == 2
    assert all(trade["date"][:10] > trades[0]["date"][:10] for trade in trades[1:])


def test_t0_is_explicit_and_fees_reconcile_to_cash_and_pnl():
    sequence = iter([decision(), decision("SELL"), decision("HOLD")])
    result = run_backtest(bars(22), config(initial_cash=10005, commission_bps=0, slippage_bps=0,
                                          min_commission=5, sell_tax_bps=5, settlement_mode="research_t0"),
                          lambda _: next(sequence))
    assert [row["shares"] for row in result["trades"]] == [1000, 1000]
    assert result["summary"]["final_equity"] == 9990
    assert result["summary"]["total_costs"] == 15
    assert result["trades"][1]["pnl"] == -15
    assert result["equity"][-1]["realized_pnl"] == -15
    assert result["equity"][-1]["unrealized_pnl"] == 0
    assert result["equity"][-1]["total_pnl"] == -15
    assert any("research_t0" in item for item in result["methodology"])


@pytest.mark.parametrize("field,value,reason", [("suspended", True, "suspended"),
                                                ("volume", 0, "zero_volume"),
                                                ("limit_up", True, "limit_up"),
                                                ("limit_up", 10, "limit_up")])
def test_blocked_open_never_creates_fake_fills(field, value, reason):
    raw = bars(21)
    raw[20][field] = value
    result = run_backtest(raw, config(), lambda _: decision())
    assert result["trades"] == []
    assert result["signals"][0]["fill_status"] == reason
    assert result["summary"]["blocked_reasons"][reason] == 1


def test_stop_and_holding_rules_remain_latched_through_t1():
    raw = bars(24) + bars(2, day="2026-01-06")
    sequence = iter([decision()] + [decision("HOLD")] * 20)
    result = run_backtest(raw, config(max_holding_bars=1), lambda _: next(sequence))
    assert result["trades"][1]["date"] == raw[24]["time"]
    assert result["trades"][1]["reason"] == "max_holding_rule"
    assert result["summary"]["rule_exit_count"] == 1
    assert result["summary"]["blocked_reasons"]["t_plus_one"] == 3


def test_stop_loss_is_latched_even_if_price_recovers_before_unlock():
    raw = bars(24) + bars(2, day="2026-01-06")
    raw[21].update(open=9, high=9.1, low=8.9, close=9)
    sequence = iter([decision()] + [decision("HOLD")] * 20)
    result = run_backtest(raw, config(stop_loss_pct=5), lambda _: next(sequence))
    assert result["trades"][1]["reason"] == "stop_loss_rule"
    assert result["trades"][1]["date"] == raw[24]["time"]


def test_uncertain_unavailable_nan_and_exceptions_are_counted_as_no_action():
    replies = [decision(confidence=0.2), {"status": "unavailable"}, decision(confidence=float("nan"))]
    sequence = iter(replies)
    result = run_backtest(bars(22), config(), lambda _: next(sequence))
    assert result["summary"]["uncertain_count"] == 1
    assert result["summary"]["unavailable_count"] == 2
    assert result["trades"] == []

    def failure(_):
        raise RuntimeError("must-never-leak-secret")

    failed = run_backtest(bars(20), config(), failure)
    assert "must-never-leak-secret" not in str(failed)
    assert failed["summary"]["unavailable_count"] == 1
    assert failed["summary"]["model_calls"] == 1


def test_corporate_actions_rejected_and_missing_flags_declared():
    raw = bars(22)
    raw[21]["adj_factor"] = 2
    with pytest.raises(ValueError, match="corporate_action_factor_change"):
        run_backtest(raw, config(), lambda _: decision())
    raw = [{key: value for key, value in row.items() if key not in (
        "adj_factor", "limit_up", "limit_down", "suspended")} for row in bars(20)]
    quality = run_backtest(raw, config(), lambda _: decision())["data_quality"]
    assert not quality["execution_flags_complete"]
    assert not quality["adjustment_factors_complete"]
    assert len(quality["warnings"]) == 2


def test_invalid_prices_time_order_config_and_fake_minutes_are_rejected():
    for invalid in (float("nan"), float("inf"), -1, True):
        raw = bars(20)
        raw[0]["close"] = invalid
        with pytest.raises(ValueError):
            run_backtest(raw, config(), lambda _: decision())
    raw = bars(20)
    raw[1]["time"] = raw[0]["time"]
    with pytest.raises(ValueError, match="increasing"):
        run_backtest(raw, config(), lambda _: decision())
    with pytest.raises(ValueError):
        config(confidence_threshold=1.1)
    with pytest.raises(ValueError, match="timestamped"):
        run_backtest([dict(bars(1)[0], time="2026-01-05")], config(), lambda _: decision())


def test_interval_skips_are_explicit_and_progress_contains_replay_profit():
    updates = []
    result = run_backtest(bars(25), config(decision_interval=2, position_pct=0.5),
                          lambda _: decision(), updates.append)
    assert result["summary"]["model_calls"] == 3
    assert sum(row["status"] == "not_called" for row in result["signals"]) == 3
    assert result["summary"]["position_shares"] < 6000
    assert len(updates) == 25
    assert updates[-1]["equity_point"]["total_pnl"] == result["equity"][-1]["total_pnl"]
    assert updates[-1]["latest_trade"]["date"] == result["trades"][-1]["date"]


def test_execution_parameters_do_not_enter_model_state_or_change_features():
    first, second = [], []
    run_backtest(bars(22), config(), lambda state: first.append(state) or decision())
    run_backtest(bars(22), config(position_pct=0.2, confidence_threshold=0.95, slippage_bps=20,
                                settlement_mode="research_t0", stop_loss_pct=5, max_holding_bars=1),
                 lambda state: second.append(state) or decision())
    assert first == second


@pytest.mark.parametrize("timeframe", ["1min", "5min", "15min"])
def test_supported_minute_timeframes_preserve_feature_identity(timeframe):
    seen = []
    result = run_backtest(bars(20), config(timeframe=timeframe),
                          lambda state: seen.append(state) or decision())
    assert seen[0]["timeframe"] == timeframe
    assert result["trades"] == []


def test_slippage_cannot_create_a_fill_beyond_supplied_price_limit():
    raw = bars(21)
    raw[20]["limit_up"] = 10.001
    result = run_backtest(raw, config(slippage_bps=5), lambda _: decision())
    assert result["trades"] == []
    assert result["signals"][0]["fill_status"] == "slippage_beyond_price_limit"


def test_baseline_execution_costs_and_warmup_match_jev_when_actions_match():
    result = run_backtest(bars(25), config(), lambda _: decision())
    baseline = result["baselines"]["buy_hold"]
    assert baseline["trades"] == result["trades"]
    assert baseline["equity"] == result["equity"]
    assert baseline["summary"]["model_calls"] == 0


def test_period_end_ignores_next_day_and_does_not_execute_outside_range():
    raw = bars(20) + bars(2, day="2026-01-06")
    result = run_backtest(raw, config(end_date="2026-01-05"), lambda _: decision())
    assert result["trades"] == []
    assert result["signals"][-1]["fill_status"] == "no_next_bar"
    assert len(result["equity"]) == 20


def test_cash_account_cannot_overdraw_to_pay_sell_fees_after_extreme_gap():
    raw = bars(22)
    raw[21].update(open=.001, high=.001, low=.001, close=.001)
    sequence = iter([decision(), decision("SELL"), decision("HOLD")])
    result = run_backtest(raw, config(initial_cash=10005, commission_bps=0, min_commission=5,
                                      slippage_bps=0, settlement_mode="research_t0"), lambda _: next(sequence))
    assert result["summary"]["cash"] >= 0
    assert len(result["trades"]) == 1
    assert result["signals"][1]["fill_status"] == "insufficient_cash_for_sell_costs"
