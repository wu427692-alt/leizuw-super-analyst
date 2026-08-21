# -*- coding: utf-8 -*-
"""Request models for source registration and unified event ingestion."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MonitorSyncRequest(BaseModel):
    categories: Optional[List[str]] = None


class DragonTigerSyncRequest(BaseModel):
    start_date: str
    end_date: str


class AnnouncementSyncRequest(BaseModel):
    start_date: str
    end_date: str
    symbols: List[str] = Field(default_factory=list, max_length=100)
    categories: List[str] = Field(default_factory=list, max_length=26)
    keyword: str = Field("", max_length=100)
    max_pages: int = Field(20, ge=1, le=100)


class AnnouncementPackageRequest(BaseModel):
    event_ids: List[int] = Field(min_length=1, max_length=20)
    include_text: bool = True


class MonitoringSourceCreate(BaseModel):
    source_key: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    adapter_type: str = "api"
    provider: str = "external"
    category: str = "news"
    enabled: bool = True
    poll_interval_seconds: int = Field(300, ge=10, le=86400)
    config: Dict[str, Any] = Field(default_factory=dict)


class ExternalMonitoringEvent(BaseModel):
    external_id: Optional[str] = None
    event_type: str = "news"
    perspective: str = "investor"
    title: str = Field(min_length=1, max_length=500)
    summary: str = ""
    url: Optional[str] = None
    symbols: List[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    importance_score: int = Field(50, ge=0, le=100)
    confidence_score: float = Field(0.5, ge=0, le=1)
    tags: List[str] = Field(default_factory=list)
    actors: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    event_at: Optional[str] = None


class ExternalEventBatch(BaseModel):
    events: List[ExternalMonitoringEvent] = Field(min_length=1, max_length=1000)
