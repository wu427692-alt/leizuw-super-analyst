# -*- coding: utf-8 -*-
"""Request schemas for the DeepSeek essay-radar API."""

from pydantic import BaseModel, Field


class EssayBackfillRequest(BaseModel):
    days: int = Field(30, ge=1, le=3650)


class EssayRetryRequest(BaseModel):
    start_worker: bool = True


class EssayDailyReportRunRequest(BaseModel):
    report_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    force: bool = False
