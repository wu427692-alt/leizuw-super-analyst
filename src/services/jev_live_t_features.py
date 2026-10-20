"""Reuse the existing anonymous event features without the T0 execution engine."""
from dataclasses import dataclass

from src.services.jev_cb_t0_engine import build_cb_state
from src.services.jev_cb_decisions_client import validate_cb_state


@dataclass(frozen=True)
class LiveFeatureConfig:
    instrument_type: str = "a_share"
    exchange_timezone: str = "Asia/Shanghai"
    factors: tuple = ("ohlcv", "orderbook", "market")
    lookback_events: int = 20
    context_max_age_seconds: float = 15


def build_live_state(events, market_context, lookback_events=20, *, include_legacy_market=True):
    factors = ("ohlcv", "orderbook", "market") if include_legacy_market else ("ohlcv", "orderbook")
    return validate_cb_state(build_cb_state(events, LiveFeatureConfig(lookback_events=lookback_events, factors=factors),
                                           {"market": market_context}))
