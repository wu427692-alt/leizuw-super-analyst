"""Arrival-driven A-share paper ledger. No broker, network, or model calls."""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import math
import json
import re
from threading import RLock
from typing import Mapping
from zoneinfo import ZoneInfo

from src.services.jev_signal_engine import _decision, _integer, _number

EXECUTION_VERSION = "ashare-live-paper-t1-v1"
CHECKPOINT_SCHEMA = "jev-live-ledger-checkpoint-v1"
CHECKPOINT_MAX_BYTES = 8 * 1024 * 1024
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _stamp(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None:
            raise ValueError
        return result.astimezone(SHANGHAI)
    except (TypeError, ValueError):
        raise ValueError("invalid_timestamp") from None


def _continuous(stamp):
    clock = stamp.time().replace(tzinfo=None)
    return stamp.weekday() < 5 and (time(9, 30) <= clock < time(11, 30) or time(13) <= clock < time(14, 57))


def _reason(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,64}", value) else "stopped"


@dataclass(frozen=True)
class LiveTConfig:
    symbol: str = "603306.SH"
    initial_base_value: float = 8000000
    initial_cash: float = 4000000
    order_quantity: int = 1000
    confidence_threshold: float = .65
    confidence_sizing: bool = False
    commission_bps: float = 3
    min_commission: float = 5
    sell_tax_bps: float = 5
    transfer_fee_bps: float = .1
    slippage_bps: float = 0
    participation_rate: float = .1
    latency_ms: float = 0
    order_ttl_ms: float = 10000
    max_quote_age_ms: float = 10000
    max_gap_ms: float = 15000
    max_inventory_deviation_ratio: float = .2
    lot_size: int = 100
    price_tick: float = .01

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("invalid_symbol")
        if self.lot_size != 100 or self.price_tick != .01:
            raise ValueError("ashare_lot_and_tick_are_fixed")
        _number(self.initial_base_value, "initial_base_value", 0, 1e12)
        _number(self.initial_cash, "initial_cash", .01, 1e12)
        _integer(self.order_quantity, "order_quantity", 100, 100000000)
        if self.order_quantity % 100:
            raise ValueError("order_quantity_requires_round_lots")
        _number(self.confidence_threshold, "confidence_threshold", 0, 1)
        if not isinstance(self.confidence_sizing, bool):
            raise ValueError("confidence_sizing_requires_boolean")
        _number(self.participation_rate, "participation_rate", .000001, 1)
        _number(self.max_inventory_deviation_ratio, "max_inventory_deviation_ratio", 0, 1)
        for name in ("commission_bps", "sell_tax_bps", "transfer_fee_bps", "slippage_bps"):
            _number(getattr(self, name), name, 0, 1000)
        _number(self.min_commission, "min_commission", 0, 10000)
        _number(self.latency_ms, "latency_ms", 0, 60000)
        for name in ("order_ttl_ms", "max_quote_age_ms", "max_gap_ms"):
            _number(getattr(self, name), name, 1, 3600000)


@dataclass(frozen=True)
class _Quote:
    source_time: datetime
    received_at: datetime
    last_price: float
    bid_price: float
    ask_price: float
    bid_volume: int
    ask_volume: int
    cumulative_volume: int
    trading_status: str
    limit_up: float | None
    limit_down: float | None


def _quote(raw):
    if not isinstance(raw, Mapping) or raw.get("volume_unit") != "shares":
        raise ValueError("verified_share_units_required")
    status = raw.get("trading_status")
    if status not in ("trading", "halted", "auction", "closed"):
        raise ValueError("explicit_trading_status_required")
    values = {name: _number(raw.get(name), name, .000001, 1e8)
              for name in ("last_price", "bid_price", "ask_price")}
    if values["bid_price"] > values["ask_price"]:
        raise ValueError("crossed_quote")
    for name in ("bid_volume", "ask_volume", "cumulative_volume"):
        values[name] = _integer(raw.get(name), name, 0, 2**53 - 1)
    for name in ("limit_up", "limit_down"):
        values[name] = None if raw.get(name) is None else _number(raw[name], name, .000001, 1e8)
    if values["limit_up"] is not None and values["limit_down"] is not None and values["limit_down"] > values["limit_up"]:
        raise ValueError("invalid_price_limits")
    return _Quote(_stamp(raw.get("source_time")), _stamp(raw.get("received_at")),
                  trading_status=status, **values)


_TOTAL_FIELDS = {"commission", "sell_tax", "transfer_fees", "slippage_cost"}
_ORDER_FIELDS = {"order_id", "signal_event_id", "signal_source_time", "requested_at", "responded_at",
                 "eligible_at", "expires_at", "side", "quantity", "filled_quantity", "gross_filled",
                 "commission_paid", "status"}


def _shape(value, fields, optional=()):
    if not isinstance(value, dict) or not fields <= set(value) or not set(value) <= fields | set(optional):
        raise ValueError("checkpoint_invalid_fields")


def _checkpoint_json(value):
    try:
        body = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode("utf-8")) > CHECKPOINT_MAX_BYTES:
            raise ValueError
        return json.loads(body)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("invalid_or_oversized_checkpoint_json") from None


def _equal(left, right):
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=.0001)


def _lot(value, minimum=100):
    _integer(value, "quantity", minimum, 2**53 - 1)
    if value % 100:
        raise ValueError("checkpoint_non_round_lot")


def _quality(value):
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError("checkpoint_quality_invalid")
    for key, count in value.items():
        if _reason(key) != key:
            raise ValueError("checkpoint_quality_invalid")
        _integer(count, "quality_count", 0, 2**53 - 1)


def _order(row):
    _shape(row, _ORDER_FIELDS, {"end_reason"})
    for name in ("order_id", "signal_event_id"):
        _integer(row[name], name, 1, 2**53 - 1)
    _lot(row["quantity"]); _lot(row["filled_quantity"], 0)
    if row["filled_quantity"] > row["quantity"] or row["side"] not in ("BUY", "SELL"):
        raise ValueError("checkpoint_order_quantity_invalid")
    for name in ("gross_filled", "commission_paid"):
        _number(row[name], name, 0, 1e24)
    if row["status"] not in ("pending", "partially_filled", "filled", "expired", "partially_filled_expired",
                              "cancelled", "partially_filled_cancelled"):
        raise ValueError("checkpoint_order_status_invalid")
    times = [_stamp(row[name]) for name in ("signal_source_time", "requested_at", "responded_at", "eligible_at", "expires_at")]
    if times != sorted(times):
        raise ValueError("checkpoint_order_clock_invalid")
    if "end_reason" in row and _reason(row["end_reason"]) != row["end_reason"]:
        raise ValueError("checkpoint_order_reason_invalid")


def _history_row(kind, row, equity_fields, config):
    if kind == "orders":
        _order(row)
    elif kind == "signals":
        fields = {"action", "status", "confidence", "probabilities", "labels", "usage", "signal_event_id",
                  "source_time", "requested_at", "responded_at", "latency_ms", "execution_reason", "requested_quantity"}
        _shape(row, fields, {"order_id"})
        normalized = _decision(row, 0 if config.confidence_sizing else config.confidence_threshold)
        if any(row[key] != value for key, value in normalized.items()):
            raise ValueError("checkpoint_signal_invalid")
        _integer(row["signal_event_id"], "signal_event_id", 1, 2**53 - 1)
        if "order_id" in row:
            _integer(row["order_id"], "order_id", 1, 2**53 - 1)
        _lot(row["requested_quantity"], 0)
        _number(row["latency_ms"], "latency_ms", 0, 3600000)
        times = [_stamp(row[name]) for name in ("source_time", "requested_at", "responded_at")]
        if times != sorted(times) or _reason(row["execution_reason"]) != row["execution_reason"]:
            raise ValueError("checkpoint_signal_clock_invalid")
    elif kind == "fills":
        fields = {"source_time", "received_at", "event_id", "order_id", "side", "quantity", "price", "gross",
                  "commission", "sell_tax", "transfer_fee", "total_fee", "slippage_cost", "cash_after",
                  "inventory_after", "sellable_after", "frozen_after", "price_source"}
        _shape(row, fields)
        for name in ("event_id", "order_id"):
            _integer(row[name], name, 1, 2**53 - 1)
        _lot(row["quantity"])
        for name in ("inventory_after", "sellable_after", "frozen_after"):
            _lot(row[name], 0)
        for name in ("price", "gross", "commission", "sell_tax", "transfer_fee", "total_fee", "slippage_cost", "cash_after"):
            _number(row[name], name, .000001 if name == "price" else 0, 1e24)
        if (row["side"] not in ("BUY", "SELL") or row["price_source"] != ("snapshot_best_ask" if row["side"] == "BUY" else "snapshot_best_bid")
                or row["inventory_after"] != row["sellable_after"] + row["frozen_after"]
                or not _equal(row["gross"], row["quantity"] * row["price"])
                or not _equal(row["total_fee"], row["commission"] + row["sell_tax"] + row["transfer_fee"])
                or _stamp(row["source_time"]) > _stamp(row["received_at"])):
            raise ValueError("checkpoint_fill_invalid")
    elif kind == "roundtrips":
        fields = {"quantity", "opening_side", "opened_at", "closed_at", "same_day", "buy_price", "sell_price", "total_fees", "net_pnl"}
        _shape(row, fields)
        _lot(row["quantity"])
        for name in ("buy_price", "sell_price", "total_fees"):
            _number(row[name], name, 0 if name == "total_fees" else .000001, 1e24)
        _number(row["net_pnl"], "net_pnl", -1e24, 1e24)
        opened, closed = _stamp(row["opened_at"]), _stamp(row["closed_at"])
        if (row["opening_side"] not in ("BUY", "SELL") or opened > closed
                or not isinstance(row["same_day"], bool) or row["same_day"] != (opened.date() == closed.date())
                or not _equal(row["net_pnl"], row["quantity"] * (row["sell_price"] - row["buy_price"]) - row["total_fees"])):
            raise ValueError("checkpoint_roundtrip_invalid")
    elif kind == "equity":
        _shape(row, equity_fields)
        if row["initialized"] is not True or row["symbol"] != config.symbol or row["currency"] != "CNY" or row["stopped"] is not False:
            raise ValueError("checkpoint_equity_identity_invalid")
        for name in ("event_id", "fill_count"):
            _integer(row[name], name, 0, 2**53 - 1)
        for name in ("initial_base_quantity", "inventory", "sellable_shares", "frozen_shares", "closed_t_matched_quantity"):
            _lot(row[name], 0)
        if row["inventory"] != row["sellable_shares"] + row["frozen_shares"]:
            raise ValueError("checkpoint_equity_inventory_invalid")
        for name in ("quote_age_ms", "initial_equity", "initial_base_price", "initial_cash_after_rounding", "equity", "baseline_equity",
                     "cash", "commission", "sell_tax", "transfer_fees", "slippage_cost"):
            _number(row[name], name, 0, 1e24)
        for name in ("total_pnl", "net_increment", "net_increment_pct", "inventory_gap", "inventory_gap_value",
                     "closed_t_net_pnl", "open_t_mark_to_market_pnl"):
            _number(row[name], name, -1e24, 1e24)
        if row["closed_t_cost_reduction_per_initial_share"] is not None:
            _number(row["closed_t_cost_reduction_per_initial_share"], "cost_reduction", -1e24, 1e24)
        if _stamp(row["time"]) > _stamp(row["received_at"]) or row["stopped_at"] is not None or row["stop_reason"] is not None:
            raise ValueError("checkpoint_equity_clock_invalid")
        if row["last_reject_reason"] is not None and _reason(row["last_reject_reason"]) != row["last_reject_reason"]:
            raise ValueError("checkpoint_equity_reason_invalid")
        _quality(row["quality_counts"])
        if row["pending_order"] is not None:
            _order(row["pending_order"])


class LiveTEngine:
    """Single paper account; arrival and response methods are atomic per instance."""

    def __init__(self, config: LiveTConfig | None = None):
        self.config = config or LiveTConfig()
        self._lock = RLock()
        self._last = None
        self._event_id = 0
        self._next_order_id = 1
        self._fill_count = 0
        self._anchors = {}
        self._base_quantity = self._base_cash = self._base_price = 0
        self._cash = self._inventory = self._sellable = self._frozen = 0
        self._pending = None
        self._open_legs = deque()
        self._closed_profit = self._closed_quantity = 0
        self._totals, self._quality = Counter(), Counter()
        self._last_reject = None
        self._stopped_at = self._stop_reason = None
        self._equity, self._signals, self._orders, self._fills, self._roundtrips = [], [], [], [], []

    def _expire(self, reason):
        if self._pending is not None:
            self._pending.update(status="partially_filled_expired" if self._pending["filled_quantity"] else "expired",
                                 end_reason=reason)
            self._pending = None

    def _reject(self, reason):
        self._quality[reason] += 1
        self._last_reject = reason
        return {"accepted": False, "reason": reason, "event_id": None,
                "incremental_volume": None, "fill": None, "snapshot": self.snapshot()}

    def step(self, quote: Mapping) -> dict:
        """Consume one genuinely received source snapshot; no interpolation."""
        with self._lock:
            if self._stopped_at is not None:
                return self._reject("engine_stopped")
            try:
                current = _quote(quote)
            except (TypeError, ValueError, OverflowError):
                return self._reject("invalid_quote")
            if current.source_time > current.received_at:
                return self._reject("future_source_timestamp")
            if (current.received_at - current.source_time).total_seconds() * 1000 > self.config.max_quote_age_ms:
                return self._reject("stale_quote")
            previous = self._last
            if previous is not None:
                if current.source_time == previous.source_time:
                    return self._reject("duplicate_source_timestamp")
                if current.source_time < previous.source_time or current.received_at <= previous.received_at:
                    return self._reject("out_of_order_quote")
                if current.source_time.date() == previous.source_time.date() and current.cumulative_volume < previous.cumulative_volume:
                    return self._reject("cumulative_volume_regressed")
            elif current.trading_status != "trading" or not _continuous(current.source_time):
                return self._reject("initial_quote_not_trading")
            if (previous is not None and current.source_time.date() != previous.source_time.date()
                    and (current.trading_status != "trading" or not _continuous(current.source_time))):
                return self._reject("new_day_quote_not_trading")
            self._event_id += 1
            self._anchors[self._event_id] = current
            self._last = current
            self._last_reject = None
            volume = None
            reason = "accepted"
            if previous is None:
                self._base_price = current.last_price
                self._base_quantity = math.floor(self.config.initial_base_value / current.last_price / 100) * 100
                self._base_cash = self.config.initial_cash + self.config.initial_base_value - self._base_quantity * current.last_price
                self._cash, self._inventory = self._base_cash, self._base_quantity
                self._sellable, self._frozen = self._base_quantity, 0
                reason = "initialized_no_fill"
            elif current.source_time.date() != previous.source_time.date():
                self._sellable, self._frozen = self._inventory, 0
                self._expire("new_trading_date")
                reason = "new_day_volume_baseline_no_fill"
            elif max((current.source_time - previous.source_time).total_seconds(),
                     (current.received_at - previous.received_at).total_seconds()) * 1000 > self.config.max_gap_ms:
                self._expire("quote_gap")
                reason = "gap_resync_no_fill"
                self._quality[reason] += 1
            else:
                volume = current.cumulative_volume - previous.cumulative_volume
            if not _continuous(current.source_time) or current.trading_status != "trading":
                self._expire("outside_continuous_trading")
            fill = self._attempt_fill(current, previous, volume)
            point = self.snapshot()
            point["event_id"] = self._event_id
            self._equity.append(deepcopy(point))
            return {"accepted": True, "reason": reason, "event_id": self._event_id,
                    "incremental_volume": volume, "fill": deepcopy(fill), "snapshot": point}

    def submit_decision(self, decision: Mapping, *, signal_event_id: int,
                        requested_at: str, responded_at: str) -> dict:
        """Submit an already returned decision; caller owns all model/network work."""
        with self._lock:
            if self._stopped_at is not None:
                return {"accepted": False, "reason": "engine_stopped", "order": None}
            try:
                _integer(signal_event_id, "signal_event_id", 1, 2**53 - 1)
                anchor = self._anchors.get(signal_event_id)
                requested, responded = _stamp(requested_at), _stamp(responded_at)
                if anchor is None or requested < anchor.received_at or responded < requested:
                    raise ValueError
                normalized = _decision(decision, 0 if self.config.confidence_sizing else self.config.confidence_threshold)
                reported = _number(decision.get("latency_ms", 0), "latency_ms", 0, 3600000)
            except (TypeError, ValueError, OverflowError):
                return {"accepted": False, "reason": "invalid_decision_or_timing", "order": None}
            latency = max(reported, (responded - requested).total_seconds() * 1000)
            quantity = 0
            if normalized["status"] == "ok" and normalized["action"] != "HOLD":
                quantity = self.config.order_quantity
                if self.config.confidence_sizing:
                    # Confidence changes only the requested lot size. Direction
                    # remains the model's choice, even at zero confidence; the
                    # existing cash, sellable, depth and risk caps still apply.
                    quantity = max(self.config.lot_size, math.floor(
                        self.config.order_quantity * normalized["confidence"] / self.config.lot_size
                    ) * self.config.lot_size)
            signal = {**normalized, "signal_event_id": signal_event_id,
                      "requested_quantity": quantity,
                      "source_time": anchor.source_time.isoformat(), "requested_at": requested.isoformat(),
                      "responded_at": responded.isoformat(), "latency_ms": latency, "execution_reason": "no_action"}
            self._signals.append(signal)
            reason = None
            if normalized["status"] != "ok" or normalized["action"] == "HOLD":
                reason = normalized["status"] if normalized["status"] != "ok" else "hold"
            elif (anchor.trading_status != "trading" or self._last.trading_status != "trading"
                  or not _continuous(anchor.source_time) or not _continuous(responded)):
                reason = "not_trading"
            elif (responded - anchor.source_time).total_seconds() * 1000 > self.config.max_quote_age_ms:
                reason = "stale_signal_quote"
            elif responded.date() != anchor.source_time.date():
                reason = "signal_crossed_trading_date"
            elif any(s["signal_event_id"] == signal_event_id for s in self._signals[:-1]):
                reason = "duplicate_signal_event"
            elif self._pending is not None:
                reason = "existing_order_pending"
            if reason:
                signal["execution_reason"] = reason
                return {"accepted": True, "reason": reason, "order": None, "signal": deepcopy(signal)}
            eligible = requested + timedelta(milliseconds=max(self.config.latency_ms, latency))
            order = {"order_id": self._next_order_id, "signal_event_id": signal_event_id,
                     "signal_source_time": anchor.source_time.isoformat(), "requested_at": requested.isoformat(),
                     "responded_at": responded.isoformat(), "eligible_at": eligible.isoformat(),
                     "expires_at": (eligible + timedelta(milliseconds=self.config.order_ttl_ms)).isoformat(),
                     "side": normalized["action"], "quantity": quantity,
                     "filled_quantity": 0, "gross_filled": 0., "commission_paid": 0., "status": "pending"}
            self._next_order_id += 1
            self._orders.append(order)
            self._pending = order
            signal.update(order_id=order["order_id"], execution_reason="order_submitted")
            return {"accepted": True, "reason": "order_submitted", "order": deepcopy(order), "signal": deepcopy(signal)}

    def cancel_pending(self, reason="cancelled_by_operator"):
        with self._lock:
            order = self._pending
            self._expire(_reason(reason))
            if order is not None:
                order["status"] = "partially_filled_cancelled" if order["filled_quantity"] else "cancelled"

    def stop(self, reason: str, at: str):
        with self._lock:
            when = _stamp(at)
            if self._last is not None and when < self._last.received_at:
                raise ValueError("stop_precedes_last_received_quote")
            self.cancel_pending(reason)
            self._stopped_at, self._stop_reason = when.isoformat(), _reason(reason)
            return self.snapshot()

    def _attempt_fill(self, quote, previous, volume):
        order = self._pending
        if order is None:
            return None
        if quote.received_at > _stamp(order["expires_at"]):
            self._expire("order_ttl_expired")
            return None
        eligible = _stamp(order["eligible_at"])
        reason = None
        if self._event_id <= order["signal_event_id"] or quote.received_at <= _stamp(order["responded_at"]):
            reason = "requires_next_new_quote"
        elif quote.trading_status != "trading" or not _continuous(quote.source_time):
            reason = "not_trading"
        elif volume is None or volume <= 0:
            reason = "no_verified_interval_volume"
        elif previous is None or previous.source_time < eligible:
            reason = "volume_interval_overlaps_response"
        if reason:
            self._quality[reason] += 1
            return None
        buy = order["side"] == "BUY"
        ref, depth = (quote.ask_price, quote.ask_volume) if buy else (quote.bid_price, quote.bid_volume)
        unrounded = ref * (1 + (1 if buy else -1) * self.config.slippage_bps / 10000)
        price = (math.ceil(unrounded / .01 - 1e-9) if buy else math.floor(unrounded / .01 + 1e-9)) * .01
        if price <= 0 or (quote.limit_up is not None and price > quote.limit_up + 1e-8) or (
                quote.limit_down is not None and price < quote.limit_down - 1e-8):
            self._quality["price_outside_supplied_limits"] += 1
            return None
        deviation = math.floor(self._base_quantity * self.config.max_inventory_deviation_ratio / 100) * 100
        position_room = self._base_quantity + deviation - self._inventory if buy else self._inventory - max(0, self._base_quantity - deviation)
        quantity = min(order["quantity"] - order["filled_quantity"],
                       math.floor(volume * self.config.participation_rate / 100) * 100,
                       math.floor(depth / 100) * 100, position_room,
                       position_room if buy else self._sellable)
        quantity = max(0, math.floor(quantity / 100) * 100)
        if buy:
            rate, transfer_rate = self.config.commission_bps / 10000, self.config.transfer_fee_bps / 10000
            maximum_gross = min((self._cash + order["commission_paid"] - self.config.min_commission) / (1 + transfer_rate),
                                (self._cash + order["commission_paid"] - order["gross_filled"] * rate) / (1 + rate + transfer_rate))
            quantity = min(quantity, max(0, math.floor(maximum_gross / price / 100) * 100))
        while quantity:
            gross = price * quantity
            commission = max(self.config.min_commission, (order["gross_filled"] + gross) * self.config.commission_bps / 10000) - order["commission_paid"]
            tax = 0 if buy else gross * self.config.sell_tax_bps / 10000
            transfer = gross * self.config.transfer_fee_bps / 10000
            change = (-gross if buy else gross) - commission - tax - transfer
            if self._cash + change >= -1e-8:
                break
            quantity -= 100
        if not quantity:
            self._quality["insufficient_cash_sellable_depth_volume_or_inventory_room"] += 1
            return None
        self._cash += change
        self._inventory += quantity if buy else -quantity
        if buy:
            self._frozen += quantity
        else:
            self._sellable -= quantity
        order["filled_quantity"] += quantity
        order["gross_filled"] += gross
        order["commission_paid"] += commission
        order["status"] = "filled" if order["filled_quantity"] == order["quantity"] else "partially_filled"
        slip = quantity * abs(price - ref)
        self._totals.update(commission=commission, sell_tax=tax, transfer_fees=transfer, slippage_cost=slip)
        fill = {"source_time": quote.source_time.isoformat(), "received_at": quote.received_at.isoformat(),
                "event_id": self._event_id, "order_id": order["order_id"], "side": order["side"],
                "quantity": quantity, "price": price, "gross": gross, "commission": commission,
                "sell_tax": tax, "transfer_fee": transfer, "total_fee": commission + tax + transfer,
                "slippage_cost": slip, "cash_after": self._cash, "inventory_after": self._inventory,
                "sellable_after": self._sellable, "frozen_after": self._frozen,
                "price_source": "snapshot_best_ask" if buy else "snapshot_best_bid"}
        self._fills.append(fill)
        self._fill_count += 1
        self._pair(fill)
        if order["status"] == "filled":
            self._pending = None
        return fill

    def _pair(self, fill):
        remaining = fill["quantity"]
        fee_per_share = fill["total_fee"] / remaining
        while remaining and self._open_legs and self._open_legs[0]["side"] != fill["side"]:
            opening = self._open_legs[0]
            quantity = min(remaining, opening["quantity"])
            buy_price = fill["price"] if fill["side"] == "BUY" else opening["price"]
            sell_price = opening["price"] if fill["side"] == "BUY" else fill["price"]
            fees = quantity * (fee_per_share + opening["fee_per_share"])
            profit = quantity * (sell_price - buy_price) - fees
            self._closed_profit += profit
            self._closed_quantity += quantity
            self._roundtrips.append({"quantity": quantity, "opening_side": opening["side"],
                "opened_at": opening["source_time"], "closed_at": fill["source_time"],
                "same_day": _stamp(opening["source_time"]).date() == _stamp(fill["source_time"]).date(),
                "buy_price": buy_price, "sell_price": sell_price, "total_fees": fees, "net_pnl": profit})
            remaining -= quantity
            opening["quantity"] -= quantity
            if not opening["quantity"]:
                self._open_legs.popleft()
        if remaining:
            self._open_legs.append({"side": fill["side"], "quantity": remaining, "price": fill["price"],
                                    "fee_per_share": fee_per_share, "source_time": fill["source_time"]})

    def snapshot(self):
        with self._lock:
            initial = self.config.initial_base_value + self.config.initial_cash
            initialized = self._last is not None
            price = self._last.last_price if initialized else None
            equity = self._cash + self._inventory * price if initialized else None
            baseline = self._base_cash + self._base_quantity * price if initialized else None
            increment = equity - baseline if initialized else None
            return {"initialized": initialized, "symbol": self.config.symbol, "currency": "CNY",
                    "stopped": self._stopped_at is not None, "stopped_at": self._stopped_at, "stop_reason": self._stop_reason,
                    "time": self._last.source_time.isoformat() if initialized else None,
                    "received_at": self._last.received_at.isoformat() if initialized else None,
                    "quote_age_ms": (self._last.received_at - self._last.source_time).total_seconds() * 1000 if initialized else None,
                    "initial_equity": initial, "initial_base_quantity": self._base_quantity if initialized else None,
                    "initial_base_price": self._base_price if initialized else None,
                    "initial_cash_after_rounding": self._base_cash if initialized else None,
                    "equity": equity, "baseline_equity": baseline,
                    "total_pnl": equity - initial if initialized else None,
                    "net_increment": increment, "net_increment_pct": increment / initial * 100 if initialized else None,
                    "cash": self._cash if initialized else None, "inventory": self._inventory,
                    "inventory_gap": self._inventory - self._base_quantity,
                    "inventory_gap_value": (self._inventory - self._base_quantity) * price if initialized else None,
                    "sellable_shares": self._sellable, "frozen_shares": self._frozen,
                    "closed_t_net_pnl": self._closed_profit, "closed_t_matched_quantity": self._closed_quantity,
                    "closed_t_cost_reduction_per_initial_share": self._closed_profit / self._base_quantity if self._base_quantity else None,
                    "open_t_mark_to_market_pnl": increment - self._closed_profit if initialized else None,
                    "commission": self._totals["commission"], "sell_tax": self._totals["sell_tax"],
                    "transfer_fees": self._totals["transfer_fees"], "slippage_cost": self._totals["slippage_cost"],
                    "pending_order": deepcopy(self._pending), "fill_count": self._fill_count,
                    "last_reject_reason": self._last_reject, "quality_counts": dict(self._quality)}

    def compact_history(self, max_records=2000):
        """Bound display history without changing cumulative account state or FIFO legs."""
        _integer(max_records, "max_records", 1, 2000)
        with self._lock:
            for name in ("_equity", "_signals", "_orders", "_fills", "_roundtrips"):
                setattr(self, name, getattr(self, name)[-max_records:])
            self._anchors = {key: value for key, value in self._anchors.items()
                             if key > self._event_id - 200}

    def export_checkpoint(self):
        """Return a private, versioned JSON snapshot, never a pickle or credentials."""
        with self._lock:
            self.compact_history()
            last = None
            if self._last is not None:
                last = asdict(self._last)
                last.update(source_time=self._last.source_time.isoformat(),
                            received_at=self._last.received_at.isoformat(), volume_unit="shares")
            state = {key: getattr(self, "_" + key) for key in (
                "event_id", "next_order_id", "fill_count", "base_quantity", "base_cash", "base_price",
                "cash", "inventory", "sellable", "frozen", "closed_profit", "closed_quantity",
                "last_reject", "stop_reason")}
            state.update(stopped_at=_stamp(self._stopped_at).isoformat() if self._stopped_at else None,
                         pending_order_id=self._pending["order_id"] if self._pending else None,
                         totals={key: self._totals[key] for key in _TOTAL_FIELDS}, quality=dict(self._quality))
            result = {"schema": CHECKPOINT_SCHEMA, "execution_version": EXECUTION_VERSION,
                      "config": asdict(self.config), "state": state, "last_quote": last,
                      "open_legs": list(self._open_legs),
                      "history": {name: getattr(self, "_" + name)
                                  for name in ("equity", "signals", "orders", "fills", "roundtrips")}}
            return _checkpoint_json(result)

    @classmethod
    def from_checkpoint(cls, config, checkpoint):
        """Resume the same account; cancel pending orders and discard old model anchors."""
        if not isinstance(config, LiveTConfig):
            raise ValueError("checkpoint_requires_config")
        data = _checkpoint_json(checkpoint)
        _shape(data, {"schema", "execution_version", "config", "state", "last_quote", "open_legs", "history"})
        if data["schema"] != CHECKPOINT_SCHEMA or data["execution_version"] != EXECUTION_VERSION:
            raise ValueError("unsupported_checkpoint_version")
        try:
            saved_config = LiveTConfig(**data["config"])
        except (TypeError, ValueError):
            raise ValueError("invalid_checkpoint_config") from None
        if asdict(saved_config) != asdict(config):
            raise ValueError("checkpoint_config_mismatch")
        result = cls(config)
        state = data["state"]
        state_fields = {"event_id", "next_order_id", "fill_count", "base_quantity", "base_cash", "base_price",
                        "cash", "inventory", "sellable", "frozen", "closed_profit", "closed_quantity",
                        "last_reject", "stop_reason", "stopped_at", "pending_order_id", "totals", "quality"}
        _shape(state, state_fields)
        for key in ("event_id", "fill_count", "base_quantity", "inventory", "sellable", "frozen", "closed_quantity"):
            _integer(state[key], key, 0, 2**53 - 1)
        _integer(state["next_order_id"], "next_order_id", 1, 2**53 - 1)
        for key in ("base_quantity", "inventory", "sellable", "frozen", "closed_quantity"):
            if state[key] % config.lot_size:
                raise ValueError("checkpoint_non_round_lot")
        for key in ("base_cash", "base_price", "cash"):
            _number(state[key], key, 0, 1e24)
        _number(state["closed_profit"], "closed_profit", -1e24, 1e24)
        if state["inventory"] != state["sellable"] + state["frozen"]:
            raise ValueError("checkpoint_inventory_mismatch")
        _shape(state["totals"], _TOTAL_FIELDS)
        for key, value in state["totals"].items():
            _number(value, key, 0, 1e24)
        _quality(state["quality"])
        for key in ("last_reject", "stop_reason"):
            if state[key] is not None and _reason(state[key]) != state[key]:
                raise ValueError("checkpoint_invalid_reason")
        if (state["stopped_at"] is None) != (state["stop_reason"] is None):
            raise ValueError("checkpoint_stop_mismatch")
        stopped = None if state["stopped_at"] is None else _stamp(state["stopped_at"])
        if data["last_quote"] is not None:
            _shape(data["last_quote"], set(_Quote.__dataclass_fields__) | {"volume_unit"})
            result._last = _quote(data["last_quote"])
            if (result._last.source_time > result._last.received_at
                    or (result._last.received_at - result._last.source_time).total_seconds() * 1000 > config.max_quote_age_ms
                    or state["event_id"] < 1 or state["base_price"] <= 0
                    or (stopped is not None and stopped < result._last.received_at)):
                raise ValueError("checkpoint_quote_or_clock_mismatch")
            expected_base = math.floor(config.initial_base_value / state["base_price"] / config.lot_size) * config.lot_size
            if state["base_quantity"] != expected_base or not _equal(
                    state["base_cash"] + state["base_quantity"] * state["base_price"],
                    config.initial_base_value + config.initial_cash):
                raise ValueError("checkpoint_initial_equity_mismatch")
        _shape(data["history"], {"equity", "signals", "orders", "fills", "roundtrips"})
        for name, rows in data["history"].items():
            if not isinstance(rows, list) or len(rows) > 2000:
                raise ValueError("checkpoint_history_limit")
            for row in rows:
                _history_row(name, row, set(result.snapshot()) | {"event_id"}, config)
            setattr(result, "_" + name, rows)
        if not isinstance(data["open_legs"], list):
            raise ValueError("checkpoint_open_legs_invalid")
        net_quantity, open_cash, sides = 0, 0., set()
        for leg in data["open_legs"]:
            _shape(leg, {"side", "quantity", "price", "fee_per_share", "source_time"})
            if leg["side"] not in ("BUY", "SELL"):
                raise ValueError("checkpoint_open_leg_side")
            _lot(leg["quantity"])
            _number(leg["price"], "price", .000001, 1e8)
            _number(leg["fee_per_share"], "fee_per_share", 0, 1e8)
            if result._last is None or _stamp(leg["source_time"]) > result._last.source_time:
                raise ValueError("checkpoint_open_leg_clock")
            direction = 1 if leg["side"] == "BUY" else -1
            net_quantity += direction * leg["quantity"]
            open_cash += (-direction * leg["price"] - leg["fee_per_share"]) * leg["quantity"]
            sides.add(leg["side"])
        if (len(sides) > 1 or net_quantity != state["inventory"] - state["base_quantity"]
                or not _equal(state["cash"], state["base_cash"] + state["closed_profit"] + open_cash)):
            raise ValueError("checkpoint_fifo_account_mismatch")
        if (state["fill_count"] < len(result._fills)
                or state["closed_quantity"] < sum(row["quantity"] for row in result._roundtrips)):
            raise ValueError("checkpoint_cumulative_counts_mismatch")
        for name in _TOTAL_FIELDS:
            fill_name = {"sell_tax": "sell_tax", "transfer_fees": "transfer_fee"}.get(name, name)
            if state["totals"][name] + .000001 < sum(row[fill_name] for row in result._fills):
                raise ValueError("checkpoint_cumulative_fees_mismatch")
        order_ids = [row["order_id"] for row in result._orders]
        if order_ids != sorted(set(order_ids)):
            raise ValueError("checkpoint_order_ids_invalid")
        referenced_ids = order_ids + [row["order_id"] for row in result._fills]
        referenced_ids += [row["order_id"] for row in result._signals if "order_id" in row]
        if referenced_ids and max(referenced_ids) >= state["next_order_id"]:
            raise ValueError("checkpoint_order_sequence_invalid")
        events = [row["event_id"] for row in result._equity + result._fills]
        events += [row["signal_event_id"] for row in result._signals + result._orders]
        if events and max(events) > state["event_id"]:
            raise ValueError("checkpoint_event_sequence_invalid")
        if result._last is not None:
            if not result._equity:
                raise ValueError("checkpoint_initialized_history_missing")
            latest = result._equity[-1]
            if (latest["event_id"] != state["event_id"] or _stamp(latest["time"]) != result._last.source_time
                    or _stamp(latest["received_at"]) != result._last.received_at):
                raise ValueError("checkpoint_latest_quote_mismatch")
            ledger_names = {"cash": "cash", "inventory": "inventory", "sellable_shares": "sellable",
                            "frozen_shares": "frozen", "initial_base_quantity": "base_quantity",
                            "initial_cash_after_rounding": "base_cash", "initial_base_price": "base_price",
                            "closed_t_net_pnl": "closed_profit", "closed_t_matched_quantity": "closed_quantity",
                            "fill_count": "fill_count"}
            if any(not _equal(latest[key], state[value]) for key, value in ledger_names.items()):
                raise ValueError("checkpoint_latest_ledger_mismatch")
            if not _equal(latest["equity"], state["cash"] + state["inventory"] * result._last.last_price):
                raise ValueError("checkpoint_latest_equity_mismatch")
        pending = state["pending_order_id"]
        active_orders = [row for row in result._orders if row["status"] in ("pending", "partially_filled")]
        if pending is not None:
            _integer(pending, "pending_order_id", 1, state["next_order_id"] - 1)
        if ([row["order_id"] for row in active_orders] != ([] if pending is None else [pending])):
            raise ValueError("checkpoint_pending_mismatch")
        if result._last is None and (any(state[name] for name in (
                "event_id", "fill_count", "base_quantity", "base_cash", "base_price", "cash", "inventory",
                "sellable", "frozen", "closed_profit", "closed_quantity")) or state["next_order_id"] != 1
                or any(data["history"].values()) or data["open_legs"] or any(state["totals"].values())):
            raise ValueError("checkpoint_uninitialized_account_mismatch")
        for name in state_fields - {"totals", "quality", "stopped_at", "stop_reason", "pending_order_id"}:
            setattr(result, "_" + name, state[name])
        result._totals, result._quality = Counter(state["totals"]), Counter(state["quality"])
        result._open_legs = deque(data["open_legs"])
        result._pending = active_orders[0] if active_orders else None
        result.cancel_pending("restored_checkpoint")
        # Old asynchronous responses cannot regain their pre-restart quote anchor.
        result._anchors = {}
        result._stopped_at = result._stop_reason = None
        return result

    def result(self):
        with self._lock:
            return {"execution_version": EXECUTION_VERSION, "config": asdict(self.config), "summary": self.snapshot(),
                    "equity": deepcopy(self._equity), "signals": deepcopy(self._signals), "orders": deepcopy(self._orders),
                    "fills": deepcopy(self._fills), "roundtrips": deepcopy(self._roundtrips),
                    "methodology": [
                        "Paper simulation only: no broker connection or live orders.",
                        ("Model confidence scales requested BUY/SELL quantity down to whole lots with a one-lot minimum; it never changes direction or makes HOLD an order."
                         if self.config.confidence_sizing else
                         "Model confidence below the configured threshold blocks orders; accepted BUY/SELL signals use the configured fixed quantity."),
                        "Initial inventory represents yesterday's shares valued at the first valid quote; its historical acquisition cost is unknown.",
                        "Same-day purchases remain frozen until a later trading date; sales consume only previously sellable inventory.",
                        "Snapshot fills are conservative assumptions, not verified executions or a reconstruction of exchange queues.",
                        "Only a later genuine source quote with its entire volume interval after response eligibility may fill; gaps resynchronize without fills.",
                        "Prices cross the displayed book with adverse tick rounding; verified share-volume participation, depth, cash and inventory limits constrain fills.",
                        "Commission minima accrue once per paper order across partial fills; tax and transfer fees are explicit configurable assumptions.",
                        "Incremental equity is measured against unchanged initial shares plus cash; extra sale proceeds alone are not profit.",
                        "FIFO opposite-side matched quantities realize paper round-trip P&L net of both legs' fees; unmatched inventory remains marked to market.",
                        "Cost reduction per initial share is only closed paper round-trip profit divided by initial shares, not an invented historical holding cost.",
                        "No mandatory repurchase, guaranteed profit, or fabricated closing trade is applied; unavailable price limits remain unverified.",
                        "The quote provider must verify the current exchange trading date; corporate actions and historical acquisition costs are not reconstructed.",
                    ]}
