# -*- coding: utf-8 -*-
"""Schemas for the unified financial-data API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class FinancialDataQueryRequest(BaseModel):
    source: Literal["tushare", "zsxq", "monitor", "cninfo", "tianyancha"]
    resource: str = Field(..., min_length=1, max_length=64)
    params: Dict[str, Any] = Field(default_factory=dict)
    fields: Optional[Union[str, List[str]]] = None


class TushareQueryRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)
    fields: Optional[Union[str, List[str]]] = None


class ResearchNoteImportRequest(BaseModel):
    group_id: Optional[str] = Field(None, max_length=32)
    group_name: Optional[str] = Field(None, max_length=100)
    topics: Optional[List[Dict[str, Any]]] = None
    mcp_page: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_payload_choice(self):
        if (self.topics is None) == (self.mcp_page is None):
            raise ValueError("provide exactly one of topics or mcp_page")
        return self


class ResearchNoteItem(BaseModel):
    topic_id: str
    group_id: str
    group_name: str
    title: str
    content: Optional[str] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    topic_type: str
    text_type: Optional[str] = None
    digested: bool
    sticky: bool
    symbols: List[str] = Field(default_factory=list)
    files: List[Dict[str, Any]] = Field(default_factory=list)
    images: List[Dict[str, Any]] = Field(default_factory=list)
    asset_summary: Dict[str, Any] = Field(default_factory=dict)
    ai_eligible: bool = True
    counts: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    synced_at: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


class ResearchNoteListResponse(BaseModel):
    items: List[ResearchNoteItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class ResearchNoteAudioDownloadItem(BaseModel):
    topic_id: str = Field(..., min_length=1, max_length=64)
    file_id: str = Field(..., min_length=1, max_length=128)


class ResearchNoteAudioBatchDownloadRequest(BaseModel):
    items: List[ResearchNoteAudioDownloadItem] = Field(..., min_length=1, max_length=100)


class ResearchNoteAudioAnalysisRequest(BaseModel):
    items: List[ResearchNoteAudioDownloadItem] = Field(..., min_length=1, max_length=20)
    title: Optional[str] = Field(None, max_length=160)
    focus: Optional[str] = Field(None, max_length=500)
    hotwords: List[str] = Field(default_factory=list, max_length=100)
    speaker_count: Optional[int] = Field(None, ge=2, le=20)


class ResearchNoteImportResponse(BaseModel):
    received: int
    saved: int
    created: int
    updated: int
    unchanged: int
    has_more: Optional[bool] = None
    next_end_time: Optional[str] = None
    analysis_queue: Optional[Dict[str, int]] = None


class ZsxqHistoryBackfillRequest(BaseModel):
    years: int = Field(1, ge=1, le=2, description="历史同步范围，只支持 1 年或 2 年")
