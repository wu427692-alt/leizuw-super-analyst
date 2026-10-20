"""Real-event, unlevered inventory T+0 replay; never manufactures ticks or fills.

CB defaults follow SSE same-day turnover, ten-bond lots and 0.001 price ticks.
US research uses an explicitly identified unlevered margin-style cash ledger;
it is not an implementation of cash-account settlement or broker eligibility.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from statistics import mean, pstdev
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.services.jev_signal_engine import _decision, _freeze, _integer, _number, _unavailable

SCHEMA = "cb-t0-factors-v1"
EXECUTION_VERSION = "event-execution-v2-serial-model"
FACTOR_NAMES = frozenset({"return_1s", "return_5s", "return_30s", "return_60s", "return_300s",
                          "vwap_gap", "relative_volume", "realized_volatility", "order_imbalance",
                          "breadth", "limit_up_ratio", "conversion_premium"})
CONTEXT_NAMES = ("underlying", "sector", "market")


@dataclass(frozen=True)
class CBT0Config:
    symbol: str = "113677.SH"
    initial_base_value: float = 200000
    initial_cash: float = 200000
    lot_size: int = 10
    order_lots: int = 10
    inventory_mode: str = "fixed_lots"
    buy_target_ratio: float = 1.5
    sell_target_ratio: float = .5
    max_inventory_ratio: float = 2
    confidence_threshold: float = .65
    latency_ms: float = 1000
    order_ttl_ms: float = 10000
    participation_rate: float = .1
    commission_bps: float = 1
    min_commission: float = 0
    other_fee_bps: float = 0
    slippage_bps: float = 2
    restore_base_at_close: bool = True
    restore_base_at_evaluation_end: bool = True
    session_close: str = "15:00:00"
    restore_before_seconds: int = 300
    lookback_events: int = 60
    decision_interval_seconds: float = 30
    context_max_age_seconds: float = 60
    max_quote_age_ms: float = 5000
    max_trade_age_ms: float = 30000
    fill_mode: str = "quote_required"
    max_model_calls: int = 1000
    instrument_type: str = "convertible_bond"
    currency: str = "CNY"
    exchange_timezone: str = "Asia/Shanghai"
    price_tick: float = .001
    simulation_account: str = "cash_cb"
    use_model_latency: bool = True
    evaluation_start: str | None = None
    evaluation_end: str | None = None
    factors: tuple = ("ohlcv", "stock", "sector", "market", "orderbook", "conversion_premium")

    def __post_init__(self):
        if self.instrument_type not in ("convertible_bond", "us_equity"):
            raise ValueError("unsupported instrument_type")
        if self.instrument_type == "convertible_bond":
            if (self.currency, self.exchange_timezone, self.simulation_account, self.lot_size) != (
                    "CNY", "Asia/Shanghai", "cash_cb", 10):
                raise ValueError("CB replay requires CNY, Shanghai, cash_cb and ten-bond lots")
        elif (self.currency, self.exchange_timezone, self.simulation_account, self.lot_size) != (
                "USD", "America/New_York", "unlevered_margin", 1):
            raise ValueError("US replay requires explicit USD, New York, unlevered_margin and one-share lots")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("symbol is required")
        _number(self.initial_base_value, "initial_base_value", 0, 1e12)
        _number(self.initial_cash, "initial_cash", .01, 1e12)
        if (not isinstance(self.factors, (list, tuple)) or "ohlcv" not in self.factors
                or len(set(self.factors)) != len(self.factors)
                or not set(self.factors) <= {"ohlcv", "stock", "sector", "market", "orderbook", "conversion_premium"}):
            raise ValueError("invalid enabled factor groups")
        _integer(self.order_lots, "order_lots", 1, 1000000)
        _integer(self.lookback_events, "lookback_events", 2, 252)
        _integer(self.max_model_calls, "max_model_calls", 1, 100000)
        _integer(self.restore_before_seconds, "restore_before_seconds", 1, 3600)
        _number(self.confidence_threshold, "confidence_threshold", 0, 1)
        _number(self.participation_rate, "participation_rate", .000001, 1)
        _number(self.price_tick, "price_tick", .000001, 1)
        _number(self.max_inventory_ratio, "max_inventory_ratio", 1, 10)
        for key in ("buy_target_ratio", "sell_target_ratio"):
            _number(getattr(self, key), key, 0, self.max_inventory_ratio)
        if self.buy_target_ratio < self.sell_target_ratio:
            raise ValueError("buy target must be at least sell target")
        for key in ("latency_ms", "slippage_bps", "commission_bps", "other_fee_bps", "min_commission"):
            _number(getattr(self, key), key, 0, 1e6)
        _number(self.order_ttl_ms, "order_ttl_ms", 1, 86400000)
        _number(self.decision_interval_seconds, "decision_interval_seconds", .001, 86400)
        _number(self.context_max_age_seconds, "context_max_age_seconds", .001, 86400)
        _number(self.max_quote_age_ms, "max_quote_age_ms", 0, 86400000)
        _number(self.max_trade_age_ms, "max_trade_age_ms", 0, 86400000)
        if self.fill_mode not in ("quote_required", "conservative_trade"):
            raise ValueError("invalid fill_mode")
        if self.inventory_mode not in ("fixed_lots", "target_inventory"):
            raise ValueError("invalid inventory_mode")
        if (not isinstance(self.restore_base_at_close, bool) or not isinstance(self.use_model_latency, bool)
                or not isinstance(self.restore_base_at_evaluation_end, bool)):
            raise ValueError("rule switches must be boolean")
        if bool(self.evaluation_start) != bool(self.evaluation_end):
            raise ValueError("both evaluation timestamps are required")
        if self.evaluation_start and _ts(self.evaluation_start, self.exchange_timezone) >= _ts(self.evaluation_end, self.exchange_timezone):
            raise ValueError("invalid evaluation interval")
        try:
            closing = time.fromisoformat(self.session_close)
            if closing.tzinfo is not None:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("invalid session_close") from None


def _ts(value, timezone):
    if not isinstance(value, str) or len(value) <= 10:
        raise ValueError("events and factors require real ISO timestamps")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid event or factor timestamp") from None
    if stamp.tzinfo is None:
        raise ValueError("timestamps must include their actual timezone")
    return stamp.astimezone(ZoneInfo(timezone))


@dataclass(frozen=True)
class Event:
    stamp: datetime
    sequence: int
    last_price: float
    trade_volume: float
    bid_price: float | None
    ask_price: float | None
    bid_volume: float | None
    ask_volume: float | None
    interval_start: datetime | None
    event_type: str
    trading_status: str
    limit_up: float | None
    limit_down: float | None
    quote_age_ms: float | None
    last_trade_age_ms: float | None
    trade_notional: float | None


def _parse_events(rows, config):
    parsed, prior = [], None
    unit = "bonds" if config.instrument_type == "convertible_bond" else "shares"
    for i, row in enumerate(rows):
        stamp = _ts(row.get("time"), config.exchange_timezone)
        sequence = _integer(row.get("sequence", i), "sequence", 0, 2**53 - 1)
        key = (stamp, sequence)
        if prior is not None and key <= prior:
            raise ValueError("events must be strictly ordered by real time and sequence")
        prior = key
        if row.get("volume_kind") != "incremental" or row.get("quantity_unit") != unit:
            raise ValueError("verified incremental volume with the instrument's quantity unit is required")
        kind, status = row.get("event_type"), row.get("trading_status")
        if kind not in ("trade", "quote") or status not in ("trading", "halted", "auction", "closed"):
            raise ValueError("event type and trading status must be explicit")
        values = {}
        for name in ("bid_price", "ask_price", "limit_up", "limit_down", "bid_volume", "ask_volume"):
            value = row.get(name)
            values[name] = None if value is None else _number(value, name, 0 if name.endswith("volume") else 1e-12, 1e15)
        if (values["bid_price"] is None) != (values["ask_price"] is None):
            raise ValueError("both best bid and ask are required when quotes are supplied")
        if values["bid_price"] is not None and values["bid_price"] > values["ask_price"]:
            raise ValueError("crossed quotes are invalid")
        for name in ("quote_age_ms", "last_trade_age_ms", "trade_notional"):
            value = row.get(name)
            values[name] = None if value is None else _number(value, name, 0, 1e24)
        if values["trade_notional"] is not None and (row.get("trade_volume", 0) > 0) != (values["trade_notional"] > 0):
            raise ValueError("incremental trade notional and volume must agree on zero activity")
        start = row.get("volume_interval_start")
        start = _ts(start, config.exchange_timezone) if start is not None else None
        if start is not None and start >= stamp:
            raise ValueError("volume interval must start before its event")
        parsed.append(Event(stamp, sequence, _number(row.get("last_price"), "last_price", 1e-12, 1e12),
                            _number(row.get("trade_volume"), "trade_volume", 0, 1e18),
                            interval_start=start, event_type=kind, trading_status=status, **values))
    return tuple(parsed)


def _parse_contexts(contexts, config):
    contexts = contexts or {}
    if not isinstance(contexts, Mapping) or not set(contexts) <= set(CONTEXT_NAMES):
        raise ValueError("invalid context names")
    result, assumed = {}, 0
    for name in CONTEXT_NAMES:
        rows = []
        for raw in contexts.get(name, []):
            asof = _ts(raw.get("asof"), config.exchange_timezone)
            available = _ts(raw.get("available_at", raw.get("asof")), config.exchange_timezone)
            if available < asof:
                raise ValueError("factor available_at cannot precede its asof timestamp")
            assumed += "available_at" not in raw
            values = raw.get("values")
            if not isinstance(values, Mapping) or not values or not set(values) <= FACTOR_NAMES:
                raise ValueError("invalid factor names")
            rows.append((asof, available, {key: _number(value, "factor", -1e6, 1e6)
                                           for key, value in values.items()}))
        rows.sort(key=lambda item: (item[1], item[0]))
        availability, best_rows, best = [], [], None
        for row in rows:
            if best is None or (row[0], row[1]) >= (best[0], best[1]):
                best = row
            availability.append(row[1])
            best_rows.append(best)
        result[name] = (tuple(availability), tuple(best_rows))
    return result, assumed


def _state(window, contexts, config):
    latest, anchor = window[-1].stamp, window[0].last_price
    average_volume = mean(row.trade_volume for row in window)
    normalized = []
    for row in window:
        normalized.append({"age_seconds": (latest - row.stamp).total_seconds(),
                           "price": row.last_price / anchor,
                           "volume": row.trade_volume / average_volume if average_volume else 0,
                           "bid": row.bid_price / anchor if row.bid_price is not None else None,
                           "ask": row.ask_price / anchor if row.ask_price is not None else None,
                           "spread_bps": ((row.ask_price - row.bid_price) / row.last_price * 10000
                                          if row.bid_price is not None else None),
                           "event_type": row.event_type,
                           "trade_age_seconds": row.last_trade_age_ms / 1000 if row.last_trade_age_ms is not None else None,
                           "quote_age_seconds": row.quote_age_ms / 1000 if row.quote_age_ms is not None else None})
    if "orderbook" not in config.factors:
        for row in normalized:
            for key in ("bid", "ask", "spread_bps", "quote_age_seconds"):
                row.pop(key)
    prices = [row.last_price for row in window]
    returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    indicators = {"window_return": prices[-1] / prices[0] - 1,
                  "last_event_return": prices[-1] / prices[-2] - 1,
                  "event_volatility": pstdev(returns) if len(returns) > 1 else 0,
                  "volume_to_mean": window[-1].trade_volume / average_volume if average_volume else None}
    for seconds in (5, 15, 30, 60):
        target = latest - timedelta(seconds=seconds)
        previous = next((event for event in reversed(window) if event.stamp <= target), None)
        indicators[f"return_{seconds}s"] = prices[-1] / previous.last_price - 1 if previous else None
    notional = volume = 0.
    complete_notional = True
    for event in window:
        volume += event.trade_volume
        if event.trade_notional is not None:
            notional += event.trade_notional
        elif event.event_type == "trade":
            notional += event.last_price * event.trade_volume
        elif event.trade_volume:
            complete_notional = False
    indicators["window_vwap_gap"] = prices[-1] / (notional / volume) - 1 if complete_notional and volume and notional else None
    if "orderbook" in config.factors:
        last = window[-1]
        depth = None if last.bid_volume is None or last.ask_volume is None else last.bid_volume + last.ask_volume
        valid_book = last.bid_price is not None and depth is not None and depth > 0
        indicators["spread_bps"] = normalized[-1]["spread_bps"]
        indicators["order_imbalance"] = (last.bid_volume - last.ask_volume) / depth if valid_book else None
        indicators["microprice_gap"] = ((last.ask_price * last.bid_volume + last.bid_price * last.ask_volume)
                                        / depth / ((last.bid_price + last.ask_price) / 2) - 1) if valid_book else None
    factors = {}
    for name in CONTEXT_NAMES:
        enabled = (name in config.factors if name != "underlying"
                   else "stock" in config.factors or "conversion_premium" in config.factors)
        if not enabled:
            factors[name] = {"status": "disabled", "age_seconds": None, "values": {}}
            continue
        availability, best_rows = contexts[name]
        index = bisect_right(availability, latest) - 1
        if index < 0:
            factors[name] = {"status": "missing", "age_seconds": None, "values": {}}
            continue
        row = best_rows[index]
        age = (latest - row[0]).total_seconds()
        fresh = age <= config.context_max_age_seconds
        values = dict(row[2]) if fresh else {}
        if name == "underlying":
            if "conversion_premium" not in config.factors:
                values.pop("conversion_premium", None)
            if "stock" not in config.factors:
                values = {key: value for key, value in values.items() if key == "conversion_premium"}
        factors[name] = {"status": "fresh" if fresh and values else "stale" if not fresh else "missing",
                         "age_seconds": age, "values": values}
    # Rounding applies only to anonymous model features, never to execution data.
    for row in normalized:
        for key, value in row.items():
            if isinstance(value, float):
                places = 4 if key == "spread_bps" else 6 if key in ("volume", "age_seconds", "trade_age_seconds", "quote_age_seconds") else 10
                row[key] = round(value, places)
    for key, value in indicators.items():
        if value is not None:
            indicators[key] = round(value, 4 if key == "spread_bps" else 6 if key == "volume_to_mean" else 10)
    for item in factors.values():
        if item["age_seconds"] is not None:
            item["age_seconds"] = round(item["age_seconds"], 6)
        item["values"] = {key: round(value, 10) for key, value in item["values"].items()}
    return _freeze({"schema": SCHEMA, "instrument_type": config.instrument_type, "sampling": "event",
                    "factor_groups": sorted(config.factors), "lookback_events": len(window), "window_seconds": round((latest - window[0].stamp).total_seconds(), 6),
                    "normalized_events": normalized,
                    "indicators": indicators,
                    "factors": factors})


def build_cb_state(events, config=None, contexts=None):
    config = config or CBT0Config()
    parsed = _parse_events(events, config)
    if len(parsed) < config.lookback_events:
        raise ValueError("insufficient actual events for lookback")
    factors, _ = _parse_contexts(contexts, config)
    return _state(parsed[-config.lookback_events:], factors, config)


def run_cb_t0_backtest(events, config: CBT0Config, decide, contexts=None, progress=None):
    parsed = _parse_events(events, config)
    factors, assumed_availability = _parse_contexts(contexts, config)
    evaluation_start = _ts(config.evaluation_start, config.exchange_timezone) if config.evaluation_start else None
    evaluation_end = _ts(config.evaluation_end, config.exchange_timezone) if config.evaluation_end else None
    if evaluation_end:
        parsed = tuple(event for event in parsed if event.stamp < evaluation_end)
    first_index = next((i for i, event in enumerate(parsed) if event.trading_status == "trading"
                        and (evaluation_start is None or event.stamp >= evaluation_start)), None)
    if first_index is None:
        raise ValueError("no tradable valuation event")
    first = parsed[first_index]
    base_quantity = math.floor(config.initial_base_value / first.last_price / config.lot_size) * config.lot_size
    base_cash = config.initial_cash + config.initial_base_value - base_quantity * first.last_price
    initial_equity = config.initial_cash + config.initial_base_value
    cash, inventory, peak = base_cash, base_quantity, initial_equity
    equity, signals, orders, fills, restore_gaps = [], [], [], [], []
    totals, blocked, quality = Counter(), Counter(), Counter()
    pending = None
    last_model_time = None
    model_available_at = None
    previous_day = first.stamp.date()
    target_base = base_quantity or math.floor(config.initial_cash / first.last_price / config.lot_size) * config.lot_size
    max_inventory = math.floor(target_base * config.max_inventory_ratio / config.lot_size) * config.lot_size

    def expire(reason):
        nonlocal pending
        if pending is not None:
            pending["status"] = "partially_filled_expired" if pending["filled_quantity"] else "expired"
            pending["end_reason"] = reason
            pending = None

    def create_order(event, side, quantity, reason, model_latency=0):
        nonlocal pending
        quantity = math.floor(quantity / config.lot_size) * config.lot_size
        if quantity <= 0:
            return None
        latency = max(config.latency_ms, model_latency if config.use_model_latency else 0)
        eligible = event.stamp + timedelta(milliseconds=latency)
        order = {"order_id": len(orders) + 1, "signal_time": event.stamp.isoformat(), "signal_sequence": event.sequence,
                 "event_index": index, "signal_event_index": index,
                 "eligible_at": eligible.isoformat(), "expires_at": (eligible + timedelta(milliseconds=config.order_ttl_ms)).isoformat(),
                 "side": side, "quantity": quantity, "filled_quantity": 0, "status": "pending",
                 "reason": reason, "commission_paid": 0., "gross_filled": 0.}
        orders.append(order)
        pending = order
        return order["order_id"]

    def attempt_fill(event):
        nonlocal cash, inventory, pending
        if pending is None:
            return
        if event.stamp > datetime.fromisoformat(pending["expires_at"]):
            expire("ttl_expired")
            return
        eligible = datetime.fromisoformat(pending["eligible_at"])
        # Even latency=0 can never fill at the event which produced the signal.
        if (event.stamp, event.sequence) <= (datetime.fromisoformat(pending["signal_time"]), pending["signal_sequence"]) or event.stamp < eligible:
            return
        reason = None
        if event.trading_status != "trading" or event.stamp.time().replace(tzinfo=None) >= time.fromisoformat(config.session_close):
            reason = "not_continuous_trading"
        elif event.trade_volume <= 0:
            reason = "no_verified_incremental_volume"
        elif event.event_type == "quote" and event.interval_start is None:
            reason = "missing_volume_interval"
        elif event.interval_start is not None and event.interval_start < eligible:
            reason = "volume_interval_overlaps_latency"
        if reason:
            blocked[reason] += 1
            return
        side = pending["side"]
        reference = event.ask_price if side == "BUY" else event.bid_price
        source = "best_ask" if side == "BUY" else "best_bid"
        if event.quote_age_ms is not None and event.quote_age_ms > config.max_quote_age_ms:
            reference = None
            if config.fill_mode == "quote_required":
                blocked["stale_order_book"] += 1
                return
        if reference is None:
            if config.fill_mode != "conservative_trade":
                blocked["missing_order_book"] += 1
                return
            if event.last_trade_age_ms is not None and event.last_trade_age_ms > config.max_trade_age_ms:
                blocked["stale_trade_price"] += 1
                return
            reference, source = event.last_price, "conservative_trade_assumption"
        price = reference * (1 + (1 if side == "BUY" else -1) * config.slippage_bps / 10000)
        ticks = price / config.price_tick
        price = (math.ceil(ticks - 1e-9) if side == "BUY" else math.floor(ticks + 1e-9)) * config.price_tick
        if price <= 0 or (event.limit_up is not None and price > event.limit_up + 1e-9) or (
                event.limit_down is not None and price < event.limit_down - 1e-9):
            blocked["price_outside_supplied_limits"] += 1
            return
        capacity = math.floor(event.trade_volume * config.participation_rate / config.lot_size) * config.lot_size
        depth = None if source == "conservative_trade_assumption" else event.ask_volume if side == "BUY" else event.bid_volume
        if depth is not None:
            capacity = min(capacity, math.floor(depth / config.lot_size) * config.lot_size)
        else:
            quality["attempts_without_depth"] += 1
        quantity = min(capacity, pending["quantity"] - pending["filled_quantity"],
                       max_inventory - inventory if side == "BUY" else inventory)
        quantity = math.floor(quantity / config.lot_size) * config.lot_size
        if side == "BUY":
            rate, other_rate = config.commission_bps / 10000, config.other_fee_bps / 10000
            max_gross = min((cash + pending["commission_paid"] - config.min_commission) / (1 + other_rate),
                            (cash + pending["commission_paid"] - pending["gross_filled"] * rate) / (1 + rate + other_rate))
            quantity = min(quantity, max(0, math.floor(max_gross / price / config.lot_size) * config.lot_size))
        commission = other_fee = 0.
        while quantity > 0:
            gross = quantity * price
            commission = max(config.min_commission, (pending["gross_filled"] + gross) * config.commission_bps / 10000) - pending["commission_paid"]
            other_fee = gross * config.other_fee_bps / 10000
            change = -gross - commission - other_fee if side == "BUY" else gross - commission - other_fee
            if cash + change >= -1e-8:
                break
            quantity -= config.lot_size
        if quantity <= 0:
            blocked["insufficient_cash_inventory_or_participation"] += 1
            return
        gross = quantity * price
        cash += -gross - commission - other_fee if side == "BUY" else gross - commission - other_fee
        inventory += quantity if side == "BUY" else -quantity
        pending["filled_quantity"] += quantity
        pending["gross_filled"] += gross
        pending["commission_paid"] += commission
        slippage = quantity * abs(price - reference)
        if source == "conservative_trade_assumption":
            quality["conservative_trade_fills"] += 1
        totals.update(commission=commission, other_fees=other_fee, slippage_cost=slippage)
        fills.append({"time": event.stamp.isoformat(), "event_index": index, "sequence": event.sequence, "order_id": pending["order_id"], "side": side,
                      "quantity": quantity, "price": price, "gross": gross, "commission": commission,
                      "other_fee": other_fee, "sell_tax": 0, "slippage_cost": slippage,
                      "cash_after": cash, "inventory_after": inventory, "position_after": inventory,
                      "fee": commission + other_fee, "price_source": source, "reason": pending["reason"]})
        pending["status"] = "filled" if pending["filled_quantity"] == pending["quantity"] else "partially_filled"
        if pending["status"] == "filled":
            pending = None

    for index in range(first_index, len(parsed)):
        event = parsed[index]
        day = event.stamp.date()
        if day != previous_day:
            if config.restore_base_at_close and inventory != base_quantity:
                restore_gaps.append({"date": previous_day.isoformat(), "quantity_gap": inventory - base_quantity,
                                     "reason": "no_executable_restoration_before_session_end"})
            expire("session_ended")
            previous_day = day
        attempt_fill(event)
        session_end = datetime.combine(day, time.fromisoformat(config.session_close), tzinfo=event.stamp.tzinfo)
        close_restore = config.restore_base_at_close and event.stamp >= session_end - timedelta(seconds=config.restore_before_seconds)
        evaluation_restore = (config.restore_base_at_evaluation_end and evaluation_end is not None
                              and event.stamp >= evaluation_end - timedelta(seconds=config.restore_before_seconds))
        restoration = close_restore or evaluation_restore
        if restoration:
            reason = "restore_base_at_evaluation_end" if evaluation_restore else "restore_base_at_close"
            deadline = min(session_end, evaluation_end) if evaluation_restore else session_end
            if pending is not None and pending["reason"] not in ("restore_base_at_close", "restore_base_at_evaluation_end"):
                expire("superseded_by_restoration")
            if inventory != base_quantity and pending is None and event.stamp < deadline:
                create_order(event, "BUY" if inventory < base_quantity else "SELL",
                             abs(inventory - base_quantity), reason)
        elif (event.trading_status == "trading" and index + 1 >= config.lookback_events
              and (model_available_at is None or event.stamp >= model_available_at)
              and (last_model_time is None or (event.stamp - last_model_time).total_seconds() >= config.decision_interval_seconds)):
            last_model_time = event.stamp
            if totals["model_calls"] >= config.max_model_calls:
                signal = _unavailable()
                signal["status"] = "budget_limit"
                quality["model_call_limit_reached"] = 1
            else:
                totals["model_calls"] += 1
                try:
                    raw = decide(_state(parsed[index - config.lookback_events + 1:index + 1], factors, config))
                    signal = _decision(raw, config.confidence_threshold)
                    signal["latency_ms"] = _number(raw.get("latency_ms", 0), "model_latency", 0, 86400000)
                    model_available_at = event.stamp + timedelta(
                        milliseconds=signal["latency_ms"] if config.use_model_latency else 0)
                except Exception:
                    signal = _unavailable()
            signal.update(time=event.stamp.isoformat(), event_index=index, sequence=event.sequence, execution_reason="no_action")
            if signal["status"] == "ok" and signal["action"] != "HOLD":
                if pending is not None:
                    signal["execution_reason"] = "existing_order_pending"
                else:
                    side = signal["action"]
                    if config.inventory_mode == "fixed_lots":
                        quantity = config.order_lots * config.lot_size
                    else:
                        ratio = config.buy_target_ratio if side == "BUY" else config.sell_target_ratio
                        target = math.floor(target_base * ratio / config.lot_size) * config.lot_size
                        quantity = max(0, target - inventory if side == "BUY" else inventory - target)
                    quantity = min(quantity, max_inventory - inventory if side == "BUY" else inventory)
                    signal["order_id"] = create_order(event, side, quantity, "model_direction", signal.get("latency_ms", 0))
                    signal["execution_reason"] = "order_submitted" if signal["order_id"] else "inventory_target_met"
            signals.append(signal)
        value = cash + inventory * event.last_price
        baseline_value = base_cash + base_quantity * event.last_price
        peak = max(peak, value)
        point = {"time": event.stamp.isoformat(), "event_index": index, "equity": value,
                 "baseline_equity": baseline_value, "incremental_t_pnl": value - baseline_value,
                 "cash": cash, "inventory": inventory, "inventory_difference": inventory - base_quantity,
                 "inventory_deviation_value": (inventory - base_quantity) * event.last_price,
                 "drawdown_pct": (value / peak - 1) * 100, "last_price": event.last_price}
        equity.append(point)
        if progress:
            progress({"processed_events": index - first_index + 1, "total_events": len(parsed) - first_index,
                      "decision_count": totals["model_calls"], "time": event.stamp.isoformat(),
                      "equity_point": dict(point), "latest_signal": signals[-1] if signals else None,
                      "latest_fill": fills[-1] if fills else None,
                      "event": {"time": event.stamp.isoformat(), "last_price": event.last_price,
                                "bid_price": event.bid_price, "ask_price": event.ask_price,
                                "trade_volume": event.trade_volume, "event_type": event.event_type,
                                "quote_age_ms": event.quote_age_ms, "last_trade_age_ms": event.last_trade_age_ms}})
    if (config.restore_base_at_close or (config.restore_base_at_evaluation_end and evaluation_end)) and inventory != base_quantity:
        restore_gaps.append({"date": previous_day.isoformat(), "quantity_gap": inventory - base_quantity,
                             "reason": "no_executable_restoration_before_evaluation_end" if config.restore_base_at_evaluation_end and evaluation_end else "no_executable_restoration_before_data_end"})
    expire("no_further_real_event")
    last = equity[-1]
    warnings = ["Recorded order books and tape do not reproduce queue position, hidden liquidity or market impact.",
                "Commission, slippage and other fees are configurable research assumptions, not broker-verified charges."]
    if any(event.bid_price is None for event in parsed):
        warnings.append("Some events have no best bid/ask; exact execution cannot be claimed.")
    if any(event.bid_volume is None or event.ask_volume is None for event in parsed):
        warnings.append("Some best-quote sizes are missing; trade-volume participation alone does not prove available queue depth.")
    if any(event.limit_up is None or event.limit_down is None for event in parsed):
        warnings.append("Exchange price-band metadata is incomplete; full exchange-rule coverage is not claimed.")
    if any(event.quote_age_ms is None for event in parsed):
        warnings.append("Some quote ages are absent; contemporaneous order-book freshness is unverified.")
    if any(event.last_trade_age_ms is not None and event.last_trade_age_ms > config.max_trade_age_ms for event in parsed):
        warnings.append("Some last-trade marks are stale; displayed last-price valuations are not guaranteed executable prices.")
    if assumed_availability:
        warnings.append("Some factor availability timestamps are assumed equal to asof; true publication latency is unverified.")
    if config.instrument_type == "us_equity":
        warnings.append("USD research account is not a CNY conversion; cash-account settlement, margin eligibility, SEC/FINRA fees and corporate actions are not fully reconstructed.")
    return {"execution_version": EXECUTION_VERSION,
            "summary": {"initial_equity": initial_equity, "initial_base_quantity": base_quantity,
                        "initial_base_price": first.last_price, "initial_cash_after_rounding": base_cash,
                        "final_equity": last["equity"], "baseline_final_equity": last["baseline_equity"],
                        "incremental_t_pnl": last["incremental_t_pnl"],
                        "incremental_t_return_pct": last["incremental_t_pnl"] / initial_equity * 100,
                        "total_return_pct": (last["equity"] / initial_equity - 1) * 100,
                        "max_drawdown_pct": -min(row["drawdown_pct"] for row in equity),
                        "cash": cash, "inventory": inventory, "inventory_difference": inventory - base_quantity,
                        "inventory_deviation_value": last["inventory_deviation_value"], "cash_difference": cash - base_cash,
                        "commission": totals["commission"], "other_fees": totals["other_fees"], "sell_tax": 0,
                        "slippage_cost": totals["slippage_cost"], "filled_order_count": sum(order["filled_quantity"] > 0 for order in orders),
                        "fill_count": len(fills), "model_calls": totals["model_calls"],
                        "unavailable_count": sum(row["status"] == "unavailable" for row in signals),
                        "uncertain_count": sum(row["status"] == "uncertain" for row in signals),
                        "model_call_limit_reached": bool(quality["model_call_limit_reached"]),
                        "restore_gaps": restore_gaps, "currency": config.currency,
                        "instrument_type": config.instrument_type, "simulation_account": config.simulation_account},
            "equity": equity, "signals": signals, "orders": orders, "fills": fills,
            "baseline": {"quantity": base_quantity, "cash": base_cash, "initial_price": first.last_price},
            "data_quality": {"warnings": warnings, "blocked_reasons": dict(blocked),
                             "factor_availability_assumed_count": assumed_availability,
                             "fill_mode": config.fill_mode, "statistics": dict(quality),
                             "real_event_count": len(parsed), "synthetic_event_count": 0},
            "methodology": [
                "Initial base inventory is valued, not purchased: lot-rounding residual joins cash and total initial equity is conserved.",
                "Incremental T P&L equals actual equity minus unchanged base inventory plus unchanged initial cash; excess cash alone is not profit.",
                "No shorting or borrowing. Existing base inventory can be sold and repurchased on the same day.",
                ("Only later real events after max(configured, reported model latency) may fill. Sparse time intervals are not interpolated."
                 if config.use_model_latency else
                 "Idealized response assumption: reported model latency is disabled; only configured latency and later real events constrain fills."),
                "With model latency enabled, judgments are serial: the next request waits until both the cadence and the previous response availability permit it.",
                "Best ask/bid with adverse tick-rounded slippage and verified incremental-volume participation cap; supplied depth and price bands constrain fills.",
                "A quote interval overlapping the order latency is unusable because its within-interval trade timing is unknown.",
                "Minimum commission accrues per simulated order across partial fills. No stock stamp tax is applied.",
                "Session-end or configured research-window-end restoration submits ordinary delayed, liquidity-constrained orders; no liquidation is fabricated and missing fills remain visible inventory gaps.",
                "Context factors require backward asof and available_at timestamps; stale or missing values are never replaced with zero.",
                "Model sees anonymous event-normalized prices, volume and PIT factors; identity, account balances and execution parameters are excluded.",
                "Replay is historical research; pretrained-model information, parameter selection and genuine out-of-sample performance remain separate questions.",
            ]}
