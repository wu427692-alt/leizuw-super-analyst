"""Private bounded JSON checkpoints preserve the ledger across restart and days."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from src.services import jev_live_t_engine as module
from src.services.jev_live_t_engine import LiveTEngine
from tests.test_jev_live_t_engine import START, config, decision, quote, submit


def active_account():
    value = config(order_quantity=1000, confidence_sizing=True, commission_bps=3,
                   min_commission=5, sell_tax_bps=5, transfer_fee_bps=.1)
    engine = LiveTEngine(value)
    engine.step(quote())
    submit(engine, "SELL", confidence=.58)
    engine.step(quote(1))
    submit(engine, "BUY", requested=1, confidence=.25)
    engine.step(quote(2, price=99))
    return engine


def account(snapshot):
    return {key: snapshot[key] for key in (
        "initial_equity", "initial_base_quantity", "initial_base_price", "initial_cash_after_rounding",
        "cash", "inventory", "sellable_shares", "frozen_shares", "closed_t_net_pnl", "closed_t_matched_quantity",
        "commission", "sell_tax", "transfer_fees", "slippage_cost", "fill_count", "baseline_equity", "net_increment")}


def test_json_checkpoint_restores_money_base_fifo_fees_and_history_without_shared_objects():
    engine = active_account()
    checkpoint = json.loads(json.dumps(engine.export_checkpoint(), allow_nan=False))
    restored = LiveTEngine.from_checkpoint(engine.config, checkpoint)
    assert account(restored.snapshot()) == account(engine.snapshot())
    for name in ("signals", "orders", "fills", "equity", "roundtrips"):
        assert restored.result()[name] == engine.result()[name]
    assert restored.export_checkpoint()["open_legs"] == checkpoint["open_legs"]
    checkpoint["state"]["cash"] = -999
    assert restored.snapshot()["cash"] >= 0 and engine.snapshot()["cash"] >= 0
    # A later matching leg proves the restored FIFO still calculates both legs' fees.
    restored.step(quote(3, price=98))
    submit(restored, "BUY", requested=3, confidence=.3)
    restored.step(quote(4, price=98))
    assert restored.snapshot()["inventory_gap"] == 0
    assert restored.snapshot()["closed_t_matched_quantity"] == 500
    assert restored.snapshot()["closed_t_net_pnl"] > engine.snapshot()["closed_t_net_pnl"]


def test_restore_cancels_pending_without_fills_and_rejects_pre_restart_model_anchor():
    engine = active_account()
    pending = submit(engine, "BUY", requested=2, confidence=.09)["order"]
    before = engine.snapshot()
    restored = LiveTEngine.from_checkpoint(engine.config, engine.export_checkpoint())
    assert restored.snapshot()["pending_order"] is None
    assert restored.snapshot()["fill_count"] == before["fill_count"]
    last_order = restored.result()["orders"][-1]
    assert last_order["order_id"] == pending["order_id"]
    assert last_order["status"] == "cancelled" and last_order["end_reason"] == "restored_checkpoint"
    assert engine.snapshot()["pending_order"] is not None  # Source account is untouched.
    stale_response = submit(restored, "BUY", requested=2, responded=2.1)
    assert stale_response["reason"] == "invalid_decision_or_timing"
    assert restored.step(quote(3))["fill"] is None
    accepted = submit(restored, "BUY", requested=3, confidence=.09)
    assert accepted["order"]["order_id"] > pending["order_id"]


def test_partial_pending_restores_as_partial_cancel_with_existing_fills_preserved():
    engine = LiveTEngine(config(order_quantity=1000, confidence_sizing=True))
    engine.step(quote()); submit(engine, "BUY", confidence=.58)
    engine.step(quote(1, ask_volume=100))
    restored = LiveTEngine.from_checkpoint(engine.config, engine.export_checkpoint())
    assert restored.result()["orders"][-1]["status"] == "partially_filled_cancelled"
    assert restored.result()["orders"][-1]["filled_quantity"] == 100
    assert restored.snapshot()["frozen_shares"] == 100
    assert restored.step(quote(2))["fill"] is None


def test_manual_stop_checkpoint_can_restart_same_account_without_cash_reset():
    engine = active_account()
    engine.stop("manual_stop", (START + timedelta(seconds=3)).isoformat())
    checkpoint = engine.export_checkpoint()
    assert checkpoint["state"]["stopped_at"] is not None
    restored = LiveTEngine.from_checkpoint(engine.config, checkpoint)
    assert not restored.snapshot()["stopped"] and restored.snapshot()["stop_reason"] is None
    assert account(restored.snapshot()) == account(engine.snapshot())
    before = restored.snapshot()
    assert restored.step(quote(4, price=99))["reason"] == "accepted"
    assert restored.snapshot()["cash"] == before["cash"]
    assert restored.snapshot()["inventory"] == before["inventory"]


def test_uninitialized_checkpoint_can_start_later_and_preserves_stop_without_fake_inventory():
    engine = LiveTEngine(config())
    engine.stop("manual_stop", START.isoformat())
    restored = LiveTEngine.from_checkpoint(engine.config, engine.export_checkpoint())
    assert restored.snapshot()["initialized"] is False
    assert restored.step(quote())["reason"] == "initialized_no_fill"
    assert restored.snapshot()["equity"] == 300000


@pytest.mark.parametrize("hour,minute,day", [(9, 25, 1), (15, 0, 1), (10, 0, 5)])
def test_next_day_nontrading_quote_cannot_unlock_frozen_inventory(hour, minute, day):
    engine = active_account()
    restored = LiveTEngine.from_checkpoint(engine.config, engine.export_checkpoint())
    stamp = (START + timedelta(days=day)).replace(hour=hour, minute=minute)
    result = restored.step(quote(source_time=stamp.isoformat(), received_at=stamp.isoformat(),
                                 cumulative_volume=1, trading_status="trading"))
    assert result["accepted"] is False and result["reason"] == "new_day_quote_not_trading"
    assert restored.snapshot()["frozen_shares"] == engine.snapshot()["frozen_shares"]
    assert restored.snapshot()["time"] == engine.snapshot()["time"]


def test_first_real_next_trading_day_quote_unlocks_but_cannot_fill_or_reset_basis():
    engine = active_account()
    restored = LiveTEngine.from_checkpoint(engine.config, engine.export_checkpoint())
    before = restored.snapshot()
    stamp = START + timedelta(days=1)
    result = restored.step(quote(source_time=stamp.isoformat(), received_at=stamp.isoformat(),
                                 cumulative_volume=100, last_price=99, bid_price=99, ask_price=99))
    assert result["reason"] == "new_day_volume_baseline_no_fill"
    assert result["fill"] is None and result["incremental_volume"] is None
    after = restored.snapshot()
    assert after["frozen_shares"] == 0 and after["sellable_shares"] == before["inventory"]
    for key in ("cash", "initial_equity", "initial_base_quantity", "initial_base_price", "initial_cash_after_rounding",
                "closed_t_net_pnl", "commission", "fill_count"):
        assert after[key] == before[key]
    restored.submit_decision(decision("SELL", confidence=.09), signal_event_id=result["event_id"],
                              requested_at=stamp.isoformat(), responded_at=stamp.isoformat())
    stamp += timedelta(seconds=1)
    fill = restored.step(quote(source_time=stamp.isoformat(), received_at=stamp.isoformat(), cumulative_volume=10100))["fill"]
    assert fill["quantity"] == 100


def test_compaction_preserves_cumulative_counts_and_unique_ids_across_restore():
    engine = LiveTEngine(config())
    engine.step(quote())
    for i in range(1, 7):
        submit(engine, "SELL" if i % 2 else "BUY", requested=i-1)
        engine.step(quote(i, price=100 if i % 2 else 99))
    before = account(engine.snapshot())
    engine.compact_history(max_records=2)
    result = engine.result()
    assert all(len(result[key]) <= 2 for key in ("equity", "signals", "orders", "fills", "roundtrips"))
    assert account(engine.snapshot()) == before and engine.snapshot()["fill_count"] == 6
    checkpoint = engine.export_checkpoint()
    restored = LiveTEngine.from_checkpoint(engine.config, checkpoint)
    restored.step(quote(7))
    submitted = submit(restored, "SELL", requested=7)
    assert submitted["order"]["order_id"] == 7
    restored.step(quote(8))
    assert restored.snapshot()["fill_count"] == 7
    assert restored.snapshot()["closed_t_net_pnl"] == before["closed_t_net_pnl"]


def test_compaction_keeps_all_fifo_legs_and_only_recent_anchors():
    engine = LiveTEngine(config())
    engine.step(quote())
    for i in range(1, 5):
        submit(engine, "SELL", requested=i-1)
        engine.step(quote(i))
    for i in range(5, 210):
        engine.step(quote(i))
    engine.compact_history(2)
    assert len(engine._anchors) == 200
    assert len(engine.export_checkpoint()["open_legs"]) == 4
    restored = LiveTEngine.from_checkpoint(engine.config, engine.export_checkpoint())
    assert restored.snapshot()["inventory_gap"] == -400
    assert restored.snapshot()["fill_count"] == 4


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(schema="unknown"),
    lambda c: c.update(password="private"),
    lambda c: c["state"].update(cash=float("nan")),
    lambda c: c["state"].update(cash="10000"),
    lambda c: c["state"].update(cash=c["state"]["cash"]+100),
    lambda c: c["state"].update(inventory=c["state"]["inventory"]+100),
    lambda c: c["state"].update(base_cash=c["state"]["base_cash"]+100),
    lambda c: c["state"].update(next_order_id=1),
    lambda c: c["state"].update(fill_count=0),
    lambda c: c["state"].update(pending_order_id=1),
    lambda c: c["open_legs"][0].update(quantity=99),
    lambda c: c["open_legs"][0].update(api_key="private"),
    lambda c: c["history"]["fills"][0].update(price=float("inf")),
    lambda c: c["history"]["fills"][0].update(gross=1),
    lambda c: c["history"]["orders"][0].update(status="fake"),
    lambda c: c["history"]["signals"][0].update(requested_quantity=True),
    lambda c: c["history"]["equity"][0].update(inventory=-100),
])
def test_checkpoint_rejects_unknown_private_nonfinite_or_inconsistent_ledger(mutate):
    engine = active_account()
    checkpoint = engine.export_checkpoint()
    mutate(checkpoint)
    with pytest.raises(ValueError):
        LiveTEngine.from_checkpoint(engine.config, checkpoint)


def test_checkpoint_requires_identical_configuration_including_confidence_mode():
    engine = active_account()
    for modified in (replace(engine.config, confidence_sizing=False), replace(engine.config, initial_cash=1),
                     replace(engine.config, symbol="600000.SH")):
        with pytest.raises(ValueError, match="checkpoint_config_mismatch"):
            LiveTEngine.from_checkpoint(modified, engine.export_checkpoint())


def test_checkpoint_byte_limit_is_enforced_without_dropping_open_legs(monkeypatch):
    engine = active_account()
    before = deepcopy(engine._open_legs)
    monkeypatch.setattr(module, "CHECKPOINT_MAX_BYTES", 100)
    with pytest.raises(ValueError, match="oversized"):
        engine.export_checkpoint()
    assert engine._open_legs == before
