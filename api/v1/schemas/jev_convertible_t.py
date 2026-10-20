"""Explicit, bounded submissions for event-time Jev research."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JevEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: UUID
    symbol: str = Field(pattern=r"^(?:[A-Z]{1,8}|\d{6}(?:\.(?:SH|SZ))?)$")
    dataset_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    account_mode: Literal["unlevered_margin", "cash_t0"] | None = None
    start_date: date
    end_date: date
    event_granularity: Literal["tick", "1s"] = "1s"
    base_position_value: float = Field(200000, ge=0, le=100000000)
    initial_cash: float = Field(200000, gt=0, le=100000000)
    order_quantity: int = Field(100, ge=1, le=100000)
    decision_interval_seconds: float = Field(30, ge=1, le=3600)
    lookback_events: int = Field(60, ge=20, le=252)
    confidence_threshold: float = Field(.65, ge=0, le=1)
    latency_ms: int = Field(1000, ge=0, le=60000)
    use_model_latency: bool = True
    order_ttl_ms: int = Field(10000, ge=100, le=300000)
    participation_rate: float = Field(.1, gt=0, le=.25)
    commission_bps: float = Field(1, ge=0, le=100)
    min_commission: float = Field(0, ge=0, le=1000)
    slippage_bps: float = Field(2, ge=0, le=100)
    other_fee_bps: float = Field(0, ge=0, le=100)
    restore_base_at_close: bool = True
    factors: list[Literal["ohlcv", "stock", "sector", "market", "orderbook", "conversion_premium"]] = Field(
        default_factory=lambda: ["ohlcv", "orderbook"])
    max_cost_usd: float = Field(.2, gt=0, le=.5)
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self):
        if not 0 <= (self.end_date - self.start_date).days <= 31:
            raise ValueError("研究区间须为 0–31 天")
        if self.end_date > date.today():
            raise ValueError("结束日期不能在未来")
        if "ohlcv" not in self.factors or len(self.factors) != len(set(self.factors)):
            raise ValueError("必须包含量价，因子不可重复")
        if bool(self.evaluation_start) != bool(self.evaluation_end):
            raise ValueError("评估起止时间必须同时填写")
        if self.evaluation_start:
            if self.evaluation_start.tzinfo is None or self.evaluation_end.tzinfo is None:
                raise ValueError("评估时间必须包含时区")
            if self.evaluation_end <= self.evaluation_start:
                raise ValueError("评估结束时间须晚于开始时间")
            if not (self.start_date <= self.evaluation_start.date() <= self.evaluation_end.date() <= self.end_date):
                raise ValueError("评估时间须在数据日期区间内")
        return self
