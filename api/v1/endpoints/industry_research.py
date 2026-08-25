# -*- coding: utf-8 -*-
"""48-hour industry/company research workspace API."""

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.industry_research import IndustryResearchProjectRequest
from src.services.industry_research_service import IndustryResearchError, IndustryResearchService

router = APIRouter()


def _error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": "industry_research_error", "message": str(exc)[:500]})


@router.get("/methodology", summary="读取极速行业调研方法")
def methodology():
    return IndustryResearchService.methodology()


@router.get("/blueprint", summary="从本地全渠道数据生成课题证据蓝图")
def blueprint(
    topic: str = Query(min_length=2, max_length=200),
    lookback_days: int = Query(default=730, ge=30, le=3650),
):
    try:
        return IndustryResearchService().blueprint(topic, lookback_days=lookback_days)
    except IndustryResearchError as exc:
        raise _error(exc, 422)


@router.post("/projects", status_code=202, summary="创建本人后台行业调研课题")
def create_project(request: IndustryResearchProjectRequest):
    try:
        return IndustryResearchService().create_project(request.model_dump())
    except IndustryResearchError as exc:
        raise _error(exc, 422)


@router.get("/projects", summary="读取本人行业调研课题")
def list_projects(limit: int = Query(default=30, ge=1, le=100)):
    return IndustryResearchService().list_projects(limit)


@router.get("/projects/{project_id}", summary="读取本人行业调研课题详情")
def project_detail(project_id: str):
    project = IndustryResearchService().get_project(project_id)
    if project is None:
        raise _error(IndustryResearchError("行业调研课题不存在"), 404)
    return project
