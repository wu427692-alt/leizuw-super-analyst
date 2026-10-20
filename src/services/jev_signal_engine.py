"""Trailing OHLCV signals and a deterministic, close-to-next-open cash simulator.

No prices, fills, or model decisions are obtained by this module. Missing exchange
metadata is reported, never inferred from a security name or a generic 10% rule.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from statistics import mean, pstdev
from typing import Any, Callable, Mapping, Sequence

ACTIONS = ("BUY", "SELL", "HOLD")
SHANGHAI = ZoneInfo("Asia/Shanghai")


class FrozenDict(dict):
    """JSON-compatible immutable feature snapshot (including nested values)."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("OHLCV features are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self


def _freeze(value):
    if isinstance(value, dict):
        return FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _number(value: Any, name: str, minimum: float = 0, maximum: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} is outside its allowed range")
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in its allowed range")
    return value


def _date(value: Any) -> str:
    if isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str):
        raise ValueError("bar and configuration dates must be ISO dates")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("bar and configuration dates must be ISO dates") from None
    if parsed.isoformat() != value:
        raise ValueError("bar and configuration dates must be ISO dates")
    return value


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) == 10:
        return _date(value)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("bar time must be an ISO timestamp") from None
    else:
        raise ValueError("bar time must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI).isoformat()


@dataclass(frozen=True)
class BacktestConfig:
    symbol: str
    start_date: str
    end_date: str
    lookback_bars: int = 60
    initial_cash: float = 100000
    confidence_threshold: float = 0.65
    commission_bps: float = 3
    min_commission: float = 5
    sell_tax_bps: float = 5
    slippage_bps: float = 5
    lot_size: int = 100
    max_holding_bars: int | None = 60
    decision_interval: int = 1
    corporate_action_policy: str = "reject_factor_changes"
    timeframe: str = "1min"
    position_pct: float = 1.0
    settlement_mode: str = "t1"
    stop_loss_pct: float = 0
    take_profit_pct: float = 0

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol is required")
        if _date(self.start_date) > _date(self.end_date):
            raise ValueError("start_date must not be after end_date")
        _integer(self.lookback_bars, "lookback_bars", 20, 252)
        _integer(self.lot_size, "lot_size", 1, 1000000)
        _integer(self.decision_interval, "decision_interval", 1, 252)
        if self.max_holding_bars is not None:
            _integer(self.max_holding_bars, "max_holding_bars", 1, 10000)
        _number(self.initial_cash, "initial_cash", 0.01, 1e12)
        _number(self.confidence_threshold, "confidence_threshold", 0, 1)
        for name in ("commission_bps", "sell_tax_bps", "slippage_bps"):
            _number(getattr(self, name), name, 0, 1000)
        _number(self.min_commission, "min_commission", 0, 1e8)
        _number(self.position_pct, "position_pct", 0.1, 1)
        _number(self.stop_loss_pct, "stop_loss_pct", 0, 100)
        _number(self.take_profit_pct, "take_profit_pct", 0, 1000)
        if self.timeframe not in ("1min", "5min", "15min", "1d"):
            raise ValueError("timeframe must be 1min, 5min, 15min or 1d")
        if self.settlement_mode not in ("t1", "research_t0"):
            raise ValueError("settlement_mode must be t1 or research_t0")
        if self.corporate_action_policy != "reject_factor_changes":
            raise ValueError("only reject_factor_changes is supported")


@dataclass(frozen=True)
class _Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_factor: float | None
    limit_up: float | bool | None
    limit_down: float | bool | None
    suspended: bool | None


def _bars(rows: Sequence[Mapping[str, Any]]) -> tuple[_Bar, ...]:
    result = []
    previous = ""
    for row in rows:
        when = _timestamp(row.get("time", row.get("date")))
        if when <= previous:
            raise ValueError("bars must have unique, strictly increasing dates")
        previous = when
        prices = {key: _number(row.get(key), key, 1e-12, 1e12)
                  for key in ("open", "high", "low", "close")}
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(
                prices["open"], prices["close"]) <= prices["high"]:
            raise ValueError("OHLC price geometry is invalid")
        suspended = row.get("suspended")
        if suspended is not None and not isinstance(suspended, bool):
            raise ValueError("suspended must be boolean or missing")
        limits = {}
        for key in ("limit_up", "limit_down"):
            value = row.get(key)
            limits[key] = value if value is None or isinstance(value, bool) else _number(
                value, key, 1e-12, 1e12)
        factor = row.get("adj_factor")
        result.append(_Bar(when, **prices,
                           volume=_number(row.get("volume"), "volume", 0, 1e18),
                           adj_factor=None if factor is None else _number(factor, "adj_factor", 1e-12, 1e12),
                           suspended=suspended, **limits))
    return tuple(result)


def _features(window: Sequence[_Bar], timeframe: str = "1min") -> dict:
    closes = [bar.close for bar in window]
    volumes = [bar.volume for bar in window]
    anchor, average_volume = closes[0], mean(volumes)
    normalized = [{key: round(getattr(bar, key) / anchor, 10)
                   for key in ("open", "high", "low", "close")} | {
                       "volume": round(bar.volume / average_volume, 10) if average_volume else 0.0}
                  for bar in window]
    indicators = {}
    for n in (1, 5, 10, 20):
        if len(closes) > n:
            indicators[f"return_{n}"] = closes[-1] / closes[-1 - n] - 1
    for n in (5, 20, 60):
        if len(closes) >= n:
            indicators[f"close_to_ma{n}"] = closes[-1] / mean(closes[-n:]) - 1
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))][-14:]
    gains = sum(max(delta, 0) for delta in changes)
    losses = sum(max(-delta, 0) for delta in changes)
    indicators["rsi14"] = 0.5 if gains + losses == 0 else gains / (gains + losses)
    ranges = [max(window[i].high - window[i].low,
                  abs(window[i].high - closes[i - 1]), abs(window[i].low - closes[i - 1]))
              for i in range(1, len(window))][-14:]
    indicators["atr14_to_close"] = mean(ranges) / closes[-1]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))][-20:]
    indicators["volatility20"] = pstdev(returns) if len(returns) > 1 else 0
    indicators["close_to_trailing_high"] = closes[-1] / max(bar.high for bar in window) - 1
    indicators["volume_to_ma20"] = volumes[-1] / mean(volumes[-20:]) if sum(volumes[-20:]) else 0
    return _freeze({"timeframe": timeframe, "lookback_bars": len(window), "normalized_bars": normalized,
                    "indicators": {key: round(value, 10) for key, value in indicators.items()}})


def build_features(bars: Sequence[Mapping[str, Any]], lookback_bars: int = 60,
                   timeframe: str = "1min") -> dict:
    """Build an immutable snapshot from the supplied trailing history only.

    Caller must truncate at the decision close; no symbol, calendar date, news,
    company identity, account state, or execution costs enter the model state.
    """
    _integer(lookback_bars, "lookback_bars", 20, 252)
    parsed = _bars(bars)
    if len(parsed) < lookback_bars:
        raise ValueError("insufficient trailing bars")
    window = parsed[-lookback_bars:]
    _check_factors(window)
    return _features(window, timeframe)


def _check_factors(bars: Sequence[_Bar]) -> None:
    factors = [bar.adj_factor for bar in bars if bar.adj_factor is not None]
    if factors and any(not math.isclose(factor, factors[0], rel_tol=1e-10) for factor in factors):
        raise ValueError("corporate_action_factor_change: raw-share simulation requires a constant-factor window")


def _decision(raw: Any, threshold: float) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid decision")
    status = raw.get("status", "ok")
    if status not in ("ok", "uncertain", "unavailable"):
        raise ValueError("invalid decision status")
    if status == "unavailable":
        return _unavailable()
    action = raw.get("action")
    if action not in ACTIONS:
        raise ValueError("invalid decision action")
    confidence = _number(raw.get("confidence"), "confidence", 0, 1)
    probabilities = raw.get("probabilities")
    if not isinstance(probabilities, Mapping) or set(probabilities) != set(ACTIONS):
        raise ValueError("invalid decision probabilities")
    probabilities = {key: _number(probabilities[key], "probability", 0, 1) for key in ACTIONS}
    if abs(sum(probabilities.values()) - 1) > 0.02 + 1e-9:
        raise ValueError("invalid decision probability sum")
    if probabilities[action] + 1e-6 < max(probabilities.values()):
        raise ValueError("selected action is not the maximum probability")
    if confidence < threshold:
        status = "uncertain"
    usage = raw.get("usage") or {}
    clean_usage = {}
    if not isinstance(usage, Mapping):
        raise ValueError("invalid usage")
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if key in usage:
            clean_usage[key] = _integer(usage[key], key, 0, 2**53 - 1)
    if "cost" in usage:
        clean_usage["cost"] = _number(usage["cost"], "usage cost", 0)
    if "cost_is_estimate" in usage:
        if not isinstance(usage["cost_is_estimate"], bool):
            raise ValueError("invalid cost estimate flag")
        clean_usage["cost_is_estimate"] = usage["cost_is_estimate"]
    # Only fixed labels cross the callback boundary; arbitrary model prose and
    # exception messages cannot leak into persisted results.
    return {"action": action, "status": status, "confidence": confidence,
            "probabilities": probabilities, "labels": ["price_volume_only"], "usage": clean_usage}


def _unavailable() -> dict:
    return {"action": "HOLD", "status": "unavailable", "confidence": None,
            "probabilities": {}, "labels": ["model_unavailable_no_action"], "usage": {}}


def _fixed_decision(action: str) -> dict:
    return {"action": action, "status": "ok", "confidence": 1.0,
            "probabilities": {key: float(key == action) for key in ACTIONS}, "labels": [], "usage": {}}


class _Account:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.cash = float(config.initial_cash)
        self.shares = 0
        self.entry_index = None
        self.entry_date = None
        self.forced_exit_reason = None
        self.entry_cost = 0.0
        self.pending = None
        self.trades = []
        self.equity = []
        self.costs = Counter()
        self.blocked = Counter()
        self.peak = float(config.initial_cash)
        self.closed_pnls = []
        self.rule_exit_count = 0

    def execute_open(self, bar: _Bar, index: int):
        signal = self.pending
        self.pending = None  # Orders expire after this open; never invent a later fill.
        if signal is None:
            return
        action = signal["execution_action"]
        reason = self._blocked_reason(bar, action, index)
        if reason:
            signal["fill_status"] = reason
            self.blocked[reason] += 1
            return
        direction = 1 if action == "BUY" else -1
        fill_price = bar.open * (1 + direction * self.config.slippage_bps / 10000)
        boundary = bar.limit_up if action == "BUY" else bar.limit_down
        if boundary is not None and not isinstance(boundary, bool) and (
                (action == "BUY" and fill_price > boundary + 1e-8)
                or (action == "SELL" and fill_price < boundary - 1e-8)):
            signal["fill_status"] = "slippage_beyond_price_limit"
            self.blocked["slippage_beyond_price_limit"] += 1
            return
        commission_rate = self.config.commission_bps / 10000
        if action == "BUY":
            budget = self.cash * self.config.position_pct
            lots = math.floor(budget / (fill_price * (1 + commission_rate)) / self.config.lot_size)
            shares = lots * self.config.lot_size
            while shares > 0 and shares * fill_price + max(
                    self.config.min_commission, shares * fill_price * commission_rate) > budget + 1e-8:
                shares -= self.config.lot_size
            if shares <= 0:
                signal["fill_status"] = "insufficient_cash"
                self.blocked["insufficient_cash"] += 1
                return
        else:
            shares = self.shares
        gross = shares * fill_price
        commission = max(self.config.min_commission, gross * commission_rate)
        tax = gross * self.config.sell_tax_bps / 10000 if action == "SELL" else 0
        slippage = shares * abs(fill_price - bar.open)
        if action == "SELL" and self.cash + gross - commission - tax < -1e-8:
            signal["fill_status"] = "insufficient_cash_for_sell_costs"
            self.blocked["insufficient_cash_for_sell_costs"] += 1
            return
        if action == "BUY":
            self.cash -= gross + commission
            self.shares = shares
            self.entry_index = index
            self.entry_date = bar.date[:10]
            self.entry_cost = gross + commission
        else:
            self.cash += gross - commission - tax
            self.shares = 0
            self.entry_index = None
            self.entry_date = None
            self.forced_exit_reason = None
        trade = {"date": bar.date, "signal_date": signal["date"], "action": action,
                 "shares": shares, "price": fill_price, "gross": gross, "commission": commission,
                 "sell_tax": tax, "slippage_cost": slippage, "cash_after": self.cash,
                 "position_after": self.shares, "reason": signal["execution_reason"]}
        if action == "SELL":
            trade["pnl"] = gross - commission - tax - self.entry_cost
            self.closed_pnls.append(trade["pnl"])
            self.entry_cost = 0
            if signal["execution_reason"] in ("max_holding_rule", "stop_loss_rule", "take_profit_rule"):
                self.rule_exit_count += 1
        self.trades.append(trade)
        self.costs.update(commission=commission, sell_tax=tax, slippage_cost=slippage)
        signal["fill_status"], signal["fill_date"] = "filled", bar.date

    def _blocked_reason(self, bar: _Bar, action: str, index: int) -> str | None:
        if bar.suspended:
            return "suspended"
        if bar.volume <= 0:
            return "zero_volume"
        boundary = bar.limit_up if action == "BUY" else bar.limit_down
        if boundary is True:
            return "limit_up" if action == "BUY" else "limit_down"
        if boundary is not None and not isinstance(boundary, bool):
            if (action == "BUY" and bar.open >= boundary - 1e-8) or (
                    action == "SELL" and bar.open <= boundary + 1e-8):
                return "limit_up" if action == "BUY" else "limit_down"
        if (action == "SELL" and self.config.settlement_mode == "t1"
                and self.entry_date is not None and bar.date[:10] <= self.entry_date):
            return "t_plus_one"
        if action == "SELL" and not self.shares:
            return "no_position"
        if action == "BUY" and self.shares:
            return "already_in_position"
        return None

    def mark_close(self, bar: _Bar):
        equity = self.cash + self.shares * bar.close
        self.peak = max(self.peak, equity)
        self.equity.append({"date": bar.date, "equity": equity, "cash": self.cash,
                            "position_shares": self.shares, "close": bar.close,
                            "drawdown_pct": (equity / self.peak - 1) * 100,
                            "realized_pnl": sum(self.closed_pnls),
                            "unrealized_pnl": self.shares * bar.close - self.entry_cost,
                            "total_pnl": equity - self.config.initial_cash})

    def schedule(self, signal: dict, index: int, has_next_bar: bool, holding_rule: bool):
        action, reason = "HOLD", signal["status"]
        if holding_rule and self.shares:
            return_pct = (self.equity[-1]["close"] * self.shares / self.entry_cost - 1) * 100
            if self.forced_exit_reason is None:
                if self.config.stop_loss_pct > 0 and return_pct <= -self.config.stop_loss_pct:
                    self.forced_exit_reason = "stop_loss_rule"
                elif self.config.take_profit_pct > 0 and return_pct >= self.config.take_profit_pct:
                    self.forced_exit_reason = "take_profit_rule"
                elif (self.config.max_holding_bars is not None and self.entry_index is not None
                      and index - self.entry_index + 1 >= self.config.max_holding_bars):
                    self.forced_exit_reason = "max_holding_rule"
        if holding_rule and self.forced_exit_reason:
            action, reason = "SELL", self.forced_exit_reason
        elif signal["status"] == "ok":
            if signal["action"] == "BUY" and not self.shares:
                action, reason = "BUY", "signal"
            elif signal["action"] == "SELL" and self.shares:
                action, reason = "SELL", "signal"
            else:
                reason = "hold_or_target_already_met"
        signal.update(execution_action=action, execution_reason=reason, fill_status="no_order")
        if action != "HOLD":
            signal["fill_status"] = "pending" if has_next_bar else "no_next_bar"
            if has_next_bar:
                self.pending = signal

    def summary(self, signals: list[dict], model_calls: int = 0, warmup: int = 0) -> dict:
        last = self.equity[-1]
        return {"initial_cash": self.config.initial_cash, "final_equity": last["equity"],
                "total_return_pct": (last["equity"] / self.config.initial_cash - 1) * 100,
                "max_drawdown_pct": -min(point["drawdown_pct"] for point in self.equity),
                "trade_count": len(self.trades), "closed_trade_count": len(self.closed_pnls),
                "win_rate_pct": (100 * sum(pnl > 0 for pnl in self.closed_pnls) / len(self.closed_pnls)
                                 if self.closed_pnls else None),
                "cash": self.cash, "position_shares": self.shares,
                "position_market_value": self.shares * last["close"],
                "total_costs": sum(self.costs.values()),
                "commission": self.costs["commission"], "sell_tax": self.costs["sell_tax"],
                "slippage_cost": self.costs["slippage_cost"], "model_calls": model_calls,
                "unavailable_count": sum(row["status"] == "unavailable" for row in signals),
                "uncertain_count": sum(row["status"] == "uncertain" for row in signals),
                "blocked_order_count": sum(self.blocked.values()), "blocked_reasons": dict(self.blocked),
                "rule_exit_count": self.rule_exit_count, "warmup_skipped_bars": warmup,
                "latest_signal": signals[-1] if signals else None}


def run_backtest(bars: list[dict], config: BacktestConfig, decide: Callable[[dict], dict],
                 progress: Callable[[dict], None] | None = None) -> dict:
    """Walk forward; all external decision failures become explicit no-action rows."""
    parsed = _bars(bars)
    selected = [index for index, bar in enumerate(parsed)
                if config.start_date <= bar.date[:10] <= config.end_date]
    if not selected:
        raise ValueError("no bars in the requested date interval")
    if config.timeframe != "1d" and any(len(bar.date) == 10 for bar in parsed):
        raise ValueError("minute simulations require genuine timestamped bars")
    # Do not let data beyond the selected interval affect factors or quality.
    relevant = parsed[max(0, selected[0] - config.lookback_bars + 1):selected[-1] + 1]
    _check_factors(relevant)
    missing = {field: sum(getattr(bar, field) is None for bar in relevant)
               for field in ("adj_factor", "suspended", "limit_up", "limit_down")}
    warnings = []
    if missing["adj_factor"]:
        warnings.append("复权因子不完整，除权除息与原始价格收益尚未核验。")
    if any(missing[key] for key in ("suspended", "limit_up", "limit_down")):
        warnings.append("停牌或涨跌停资料不完整，不能视为完整的 A 股可成交性模拟。")
    quality = {"corporate_action_policy": config.corporate_action_policy,
               "adjustment_factors_complete": missing["adj_factor"] == 0,
               "execution_flags_complete": all(missing[key] == 0 for key in ("suspended", "limit_up", "limit_down")),
               "missing_fields": missing, "warnings": warnings}
    accounts = {key: _Account(config) for key in ("jev", "buy_hold", "ma5_20")}
    signal_rows = {key: [] for key in accounts}
    model_calls = eligible_count = warmup = 0
    for offset, index in enumerate(selected):
        bar = parsed[index]
        has_next = offset < len(selected) - 1
        for account in accounts.values():
            account.execute_open(bar, index)
            account.mark_close(bar)
        if index + 1 < config.lookback_bars:
            warmup += 1
        else:
            window = parsed[index - config.lookback_bars + 1:index + 1]
            if eligible_count % config.decision_interval == 0:
                model_calls += 1
                try:
                    model_signal = _decision(decide(_features(window, config.timeframe)), config.confidence_threshold)
                except Exception:
                    # Upstream exception text may contain keys or arbitrary bodies.
                    model_signal = _unavailable()
                model_signal["date"] = bar.date
                signal_rows["jev"].append(model_signal)
            else:
                model_signal = {"date": bar.date, "action": "HOLD", "status": "not_called",
                                "confidence": None, "probabilities": {},
                                "labels": ["decision_interval"], "usage": {}}
                signal_rows["jev"].append(model_signal)
            accounts["jev"].schedule(model_signal, index, has_next, holding_rule=True)
            close_values = [item.close for item in window]
            baseline_actions = {"buy_hold": "BUY", "ma5_20": (
                "BUY" if mean(close_values[-5:]) > mean(close_values[-20:]) else "SELL")}
            for name, action in baseline_actions.items():
                signal = _fixed_decision(action)
                signal.update(date=bar.date, labels=[name])
                signal_rows[name].append(signal)
                # Baselines retain their named strategy; all fill/cost rules are
                # identical, but do not inherit Jev's separate forced-exit rule.
                accounts[name].schedule(signal, index, has_next, holding_rule=False)
            eligible_count += 1
        if progress is not None:
            progress({"processed_bars": offset + 1, "total_bars": len(selected),
                      "decision_count": model_calls, "date": bar.date,
                      "equity_point": dict(accounts["jev"].equity[-1]),
                      "latest_signal": dict(signal_rows["jev"][-1]) if signal_rows["jev"] else None,
                      "signal_updates": [dict(row) for row in signal_rows["jev"][-2:]],
                      "latest_trade": dict(accounts["jev"].trades[-1]) if accounts["jev"].trades else None})
    account = accounts["jev"]
    return {"summary": account.summary(signal_rows["jev"], model_calls, warmup),
            "equity": account.equity, "signals": signal_rows["jev"], "trades": account.trades,
            "baselines": {name: {"summary": accounts[name].summary(signal_rows[name], warmup=warmup),
                                  "equity": accounts[name].equity, "trades": accounts[name].trades}
                          for name in ("buy_hold", "ma5_20")},
            "data_quality": quality,
            "methodology": [
                "模型只接收截至当前收盘的量价窗口与相对指标，不接收证券名称、日期、账户、新闻或基本面。",
                "本根收盘形成的信号，最早在下一根已提供行情的开盘尝试成交。",
                "只做多现金账户，按设定仓位与整手数量买入；不加杠杆、做空或发送真实订单。",
                f"结算假设：{config.settlement_mode}。t1 在上海日期的次日解锁卖出；research_t0 仅供研究，不能视为 A 股实际规则。",
                "所有策略采用相同滑点、佣金、最低佣金与卖出税；0 成交量、停牌和已知方向涨跌停阻止成交。",
                "买入持有与 MA5/20 使用相同成交约束，不继承 Jev 的额外持有时长或止盈止损退出规则。",
                "持有时长、止盈和止损在收盘触发，下一根尝试卖出；受 T+1 或涨跌停阻止时保留退出意图。",
                "模型不确定或不可用时不创建模型委托，规则退出会单独标记。",
                "有复权因子时要求区间内一致；资料缺失会标记，未重建分红、拆股和退市现金流。",
                "期末持仓按最后收盘估值，不虚构平仓；最后一个信号没有下一根行情时不成交。",
                "分钟 K 线不能还原逐笔流动性和排队成交；费用与交易规则是可调假设。",
                "成交记录的时间是所用 K 线的时间标签；本模拟未计模型网络延迟，仅用滑点近似执行影响，也未限制成交量参与率。",
                "置信度不是盈利概率。历史回测仍受模型预训练和反复调参偏差影响，应另留后续区间检验。",
            ]}
