from typing import List, Optional

from pydantic import BaseModel, Field


class EssayQuantRuleRequest(BaseModel):
    name: str = Field(default="小作文多头事件策略", min_length=1, max_length=120)
    source_query: str = Field(default="", max_length=200)
    signal_direction: str = Field(default="bullish", pattern="^(bullish|bearish|all)$")
    lookback_days: int = Field(default=365, ge=30, le=1825)
    holding_periods: List[int] = Field(default_factory=lambda: [5, 10, 20], min_length=1, max_length=3)
    first_mention_only: bool = False
    first_mention_window_days: int = Field(default=180, ge=30, le=730)
    min_importance: int = Field(default=60, ge=0, le=100)
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    benchmark_code: str = Field(default="000300.SH", max_length=16)
    portfolio_size: int = Field(default=10, ge=2, le=30)
    enabled: bool = True


class EssayQuantRunRequest(EssayQuantRuleRequest):
    rule_id: Optional[int] = None
    refresh_prices: bool = True
    max_symbols: int = Field(default=30, ge=2, le=60)
