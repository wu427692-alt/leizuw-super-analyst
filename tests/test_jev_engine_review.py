"""Independent economic invariants and event-order regressions for minute replay."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from src.services.jev_signal_engine import BacktestConfig, run_backtest


def bar(stamp, price):
    return {"date": stamp, "open": price, "high": price, "low": price, "close": price,
            "volume": 100000, "adj_factor": 1, "limit_up": False,
            "limit_down": False, "suspended": False}


def warmup():
    start = datetime.fromisoformat("2026-01-02T10:00:00+08:00")
    return [bar((start + timedelta(minutes=i)).isoformat(), 10) for i in range(20)]


def reply(action):
    return {"action": action, "status": "ok", "confidence": 1,
            "probabilities": {name: float(name == action) for name in ("BUY", "SELL", "HOLD")}}


def settings(**overrides):
    return BacktestConfig(**{
        "symbol": "600000.SH", "start_date": "2026-01-05", "end_date": "2026-01-06",
        "lookback_bars": 20, "initial_cash": 10010, "commission_bps": 0,
        "min_commission": 5, "sell_tax_bps": 5, "slippage_bps": 0,
        "max_holding_bars": None, **overrides,
    })


def test_hand_calculated_round_trip_reentry_and_new_inventory_t1():
    """Selling old stock at an open must not unlock stock bought later that day."""
    rows = warmup() + [
        bar("2026-01-05T09:30:00+08:00", 10),
        bar("2026-01-05T09:31:00+08:00", 10),
        bar("2026-01-05T09:32:00+08:00", 10),
        bar("2026-01-06T09:30:00+08:00", 11),
        bar("2026-01-06T09:31:00+08:00", 9),
        bar("2026-01-06T09:32:00+08:00", 10),
    ]
    choices = iter(["BUY", "SELL", "SELL", "BUY", "SELL", "HOLD"])
    result = run_backtest(rows, settings(), lambda _: reply(next(choices)))
    trades = result["trades"]
    assert [trade["action"] for trade in trades] == ["BUY", "SELL", "BUY"]
    assert [trade["shares"] for trade in trades] == [1000, 1000, 1200]
    # Hand ledger: 10010 - 10000 - 5 + 11000 - 5 - 5.5 - 10800 - 5.
    assert result["summary"]["cash"] == pytest.approx(189.5)
    assert result["summary"]["final_equity"] == pytest.approx(12189.5)
    assert trades[1]["pnl"] == pytest.approx(984.5)
    assert result["equity"][-1]["unrealized_pnl"] == pytest.approx(1195)
    assert result["summary"]["blocked_reasons"]["t_plus_one"] == 2
    assert trades[1]["date"] < trades[2]["date"]
    for point in result["equity"]:
        assert point["realized_pnl"] + point["unrealized_pnl"] == pytest.approx(point["total_pnl"])
        assert point["cash"] + point["position_shares"] * point["close"] == pytest.approx(point["equity"])


def test_future_price_changes_cannot_change_earlier_decisions_or_ledger():
    rows = warmup() + [bar(f"2026-01-05T09:{30+i:02d}:00+08:00", 10+i) for i in range(6)]
    changed = deepcopy(rows)
    changed[-1].update(open=100, high=200, low=1, close=150, volume=1)
    snapshots = [[], []]

    def decide(position):
        def callback(features):
            snapshots[position].append(features)
            return reply("BUY")
        return callback

    original = run_backtest(rows, settings(), decide(0))
    altered = run_backtest(changed, settings(), decide(1))
    assert snapshots[0][:-1] == snapshots[1][:-1]
    assert original["equity"][:-1] == altered["equity"][:-1]
    assert original["trades"] == altered["trades"]


def test_exit_rules_keep_running_between_sparse_model_decisions():
    rows = warmup() + [bar(f"2026-01-05T09:{30+i:02d}:00+08:00", price)
                       for i, price in enumerate([10, 10, 8, 7])]
    calls = []
    result = run_backtest(rows, settings(settlement_mode="research_t0", decision_interval=60,
                                         stop_loss_pct=5),
                          lambda features: calls.append(features) or reply("BUY"))
    assert len(calls) == 1
    assert [trade["action"] for trade in result["trades"]] == ["BUY", "SELL"]
    assert result["trades"][1]["price"] == 7  # gap loss, never fill at the 5% threshold
    assert result["trades"][1]["reason"] == "stop_loss_rule"
    assert result["trades"][1]["pnl"] == pytest.approx(-3013.5)
    assert result["summary"]["final_equity"] == pytest.approx(6996.5)


def test_final_mark_preserves_open_position_without_charging_a_fictional_exit():
    rows = warmup() + [bar("2026-01-05T09:30:00+08:00", 10),
                       bar("2026-01-05T09:31:00+08:00", 10)]
    result = run_backtest(rows, settings(), lambda _: reply("BUY"))
    assert len(result["trades"]) == 1
    assert result["summary"]["position_shares"] == 1000
    assert result["summary"]["commission"] == 5
    assert result["summary"]["sell_tax"] == 0
    assert result["summary"]["final_equity"] == 10005
    assert result["summary"]["closed_trade_count"] == 0
