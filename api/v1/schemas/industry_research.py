"""Request schemas for owner-scoped industry research projects."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class IndustryResearchProjectRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    research_type: Literal["industry", "company"] = "industry"
    objective: Optional[str] = Field(default=None, max_length=2000)
    lookback_days: int = Field(default=730, ge=30, le=3650)
    query_terms: List[str] = Field(default_factory=list, max_length=15)

