"""Typed Jev direction classifier for anonymous event data and PIT factors."""
from __future__ import annotations

import math
from typing import Mapping

from src.services.jev_cb_t0_engine import CONTEXT_NAMES, FACTOR_NAMES, SCHEMA
from src.services.jev_decisions_client import JevClientError, JevDecisionsClient, _finite, _tokens

GROUPS = {"ohlcv", "stock", "sector", "market", "orderbook", "conversion_premium"}


def validate_cb_state(state):
    required = {"schema", "instrument_type", "sampling", "factor_groups", "lookback_events", "window_seconds",
                "normalized_events", "indicators", "factors"}
    if (not isinstance(state, Mapping) or set(state) != required or state["schema"] != SCHEMA
            or state["sampling"] != "event" or state["instrument_type"] not in ("convertible_bond", "us_equity", "a_share")):
        raise JevClientError("invalid_price_volume_state")
    groups = state["factor_groups"]
    if (not isinstance(groups, (list, tuple)) or any(not isinstance(value, str) for value in groups)
            or "ohlcv" not in groups or not set(groups) <= GROUPS or len(groups) != len(set(groups))):
        raise JevClientError("invalid_price_volume_state")
    count = _tokens(state["lookback_events"])
    rows = state["normalized_events"]
    if not 2 <= count <= 252 or not isinstance(rows, (list, tuple)) or len(rows) != count:
        raise JevClientError("invalid_price_volume_state")
    window = _finite(state["window_seconds"], 0, 1e9)
    expected = {"age_seconds", "price", "volume", "event_type", "trade_age_seconds"}
    if "orderbook" in groups:
        expected |= {"bid", "ask", "spread_bps", "quote_age_seconds"}
    clean_rows, prior_age = [], math.inf
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected or row["event_type"] not in ("trade", "quote"):
            raise JevClientError("invalid_price_volume_state")
        clean = {"age_seconds": _finite(row["age_seconds"], 0, 1e9),
                 "price": _finite(row["price"], 1e-12, 1e12), "volume": _finite(row["volume"], 0, 1e12),
                 "event_type": row["event_type"]}
        clean["trade_age_seconds"] = None if row["trade_age_seconds"] is None else _finite(row["trade_age_seconds"], 0, 1e21)
        if clean["age_seconds"] > prior_age:
            raise JevClientError("invalid_price_volume_state")
        prior_age = clean["age_seconds"]
        if "orderbook" in groups:
            clean["quote_age_seconds"] = None if row["quote_age_seconds"] is None else _finite(row["quote_age_seconds"], 0, 1e21)
            if row["bid"] is None and row["ask"] is None and row["spread_bps"] is None:
                clean.update(bid=None, ask=None, spread_bps=None)
            else:
                bid, ask = _finite(row["bid"], 1e-12, 1e12), _finite(row["ask"], 1e-12, 1e12)
                spread = _finite(row["spread_bps"], 0, 1e12)
                if bid > ask or not math.isclose(spread, (ask - bid) / clean["price"] * 10000, abs_tol=1e-4):
                    raise JevClientError("invalid_price_volume_state")
                clean.update(bid=bid, ask=ask, spread_bps=spread)
        clean_rows.append(clean)
    if (not math.isclose(clean_rows[0]["price"], 1, abs_tol=1e-9)
            or not math.isclose(clean_rows[0]["age_seconds"], window, abs_tol=1e-9)
            or clean_rows[-1]["age_seconds"] != 0):
        raise JevClientError("invalid_price_volume_state")
    indicators = state["indicators"]
    indicator_names = {"window_return", "last_event_return", "event_volatility", "volume_to_mean", "window_vwap_gap",
                       "return_5s", "return_15s", "return_30s", "return_60s"}
    if "orderbook" in groups:
        indicator_names |= {"spread_bps", "order_imbalance", "microprice_gap"}
    if not isinstance(indicators, Mapping) or set(indicators) != indicator_names:
        raise JevClientError("invalid_price_volume_state")
    required_numbers = {"window_return", "last_event_return", "event_volatility"}
    clean_indicators = {}
    for key, value in indicators.items():
        if value is None and key not in required_numbers:
            clean_indicators[key] = None
        else:
            lower = 0 if key in ("event_volatility", "volume_to_mean", "spread_bps") else -1
            clean_indicators[key] = _finite(value, lower, 1 if key == "order_imbalance" else 1e12)
    factors = state["factors"]
    if not isinstance(factors, Mapping) or set(factors) != set(CONTEXT_NAMES):
        raise JevClientError("invalid_price_volume_state")
    clean_factors = {}
    for name, item in factors.items():
        if (not isinstance(item, Mapping) or set(item) != {"status", "age_seconds", "values"}
                or item["status"] not in ("fresh", "missing", "stale", "disabled")
                or not isinstance(item["values"], Mapping) or not set(item["values"]) <= FACTOR_NAMES):
            raise JevClientError("invalid_price_volume_state")
        enabled = name in groups if name != "underlying" else "stock" in groups or "conversion_premium" in groups
        if (enabled == (item["status"] == "disabled") or (item["status"] != "fresh" and item["values"])
                or (item["status"] == "fresh" and not item["values"])):
            raise JevClientError("invalid_price_volume_state")
        if name == "underlying" and (("conversion_premium" not in groups and "conversion_premium" in item["values"])
                                     or ("stock" not in groups and set(item["values"]) - {"conversion_premium"})):
            raise JevClientError("invalid_price_volume_state")
        age = None if item["age_seconds"] is None else _finite(item["age_seconds"], 0, 1e12)
        if item["status"] in ("fresh", "stale") and age is None:
            raise JevClientError("invalid_price_volume_state")
        clean_factors[name] = {"status": item["status"], "age_seconds": age,
                               "values": {key: _finite(value, -1e6, 1e6) for key, value in item["values"].items()}}
    return {"schema": SCHEMA, "instrument_type": state["instrument_type"], "sampling": "event",
            "factor_groups": sorted(groups), "lookback_events": count, "window_seconds": window,
            "normalized_events": clean_rows, "indicators": clean_indicators, "factors": clean_factors}


class JevCBDecisionsClient(JevDecisionsClient):
    cache_version = SCHEMA

    def _prepare_state(self, features):
        return validate_cb_state(features)

    def _build_questions(self):
        return {"action": {"type": "choice", "instructions": (
            "Classify short-horizon price direction using anonymous normalized real-event price/volume, "
            "the enabled order-book observations, and backward-asof factors only. Factor groups marked "
            "missing, stale or disabled provide no numeric evidence. Do not infer identities, absolute "
            "dates, news or account positions. BUY predicts positive direction, SELL negative, HOLD no "
            "clear edge. The executor controls all inventory and costs; probabilities are classifications, "
            "not calibrated profit forecasts. Consider spread, sparse event timing and contradictory contexts."),
            "criteria": {"BUY": "Positive short-horizon direction", "SELL": "Negative short-horizon direction",
                         "HOLD": "No clear directional advantage in the available evidence"}}}

    def _normalize(self, raw):
        result = super()._normalize(raw)
        result["labels"] = ["price_volume_factors"]
        return result
