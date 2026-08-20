# -*- coding: utf-8 -*-
"""Schemas for the model-assisted one-stop data acquisition workbench."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DataAcquisitionPlanRequest(BaseModel):
    request: str = Field(..., min_length=2, max_length=4000)


class DataAcquisitionRunRequest(DataAcquisitionPlanRequest):
    plan: Optional[Dict[str, Any]] = None


class DataAcquisitionTask(BaseModel):
    id: str
    source: str
    resource: str
    label: str
    reason: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    fields: List[str] = Field(default_factory=list)


class DataAcquisitionPlan(BaseModel):
    title: str
    objective: str
    tasks: List[DataAcquisitionTask]
    output_formats: List[str] = Field(default_factory=lambda: ["json", "csv", "xlsx", "zip"])
    caveats: List[str] = Field(default_factory=list)
    model: str
    generated_at: str
