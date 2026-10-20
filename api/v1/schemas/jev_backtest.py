"""Validated minute replay requests; execution settings never enter model state."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JevBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: UUID
    symbol: str = Field(pattern=r"^(?:[036]\d{5})(?:\.(?:SH|SZ|BJ))?$")
    start_date: date
    end_date: date
    timeframe: Literal["1min", "5min", "15min"] = "1min"
    data_source: Literal["sina", "tushare"] = "sina"
    lookback_bars: int = Field(60, ge=20, le=252)
    initial_cash: float = Field(100000, gt=0, le=100000000)
    confidence_threshold: float = Field(.65, ge=0, le=1)
    commission_bps: float = Field(3, ge=0, le=1000)
    min_commission: float = Field(5, ge=0, le=10000)
    sell_tax_bps: float = Field(5, ge=0, le=1000)
    slippage_bps: float = Field(5, ge=0, le=1000)
    max_holding_bars: int = Field(60, ge=1, le=10000)
    decision_interval: int = Field(1, ge=1, le=252)
    position_pct: float = Field(1, ge=.1, le=1)
    settlement_mode: Literal["t1", "research_t0"] = "t1"
    stop_loss_pct: float = Field(0, ge=0, le=100)
    take_profit_pct: float = Field(0, ge=0, le=100)
    refresh_prices: bool = True
    max_cost_usd: float = Field(.2, gt=0, le=.5)

    @model_validator(mode="after")
    def valid_range(self):
        if not 0 <= (self.end_date - self.start_date).days <= 31:
            raise ValueError("回测区间须为 0–31 天，最多 2000 根评估行情")
        if self.end_date > date.today():
            raise ValueError("回测结束日期不能在未来")
        return self
