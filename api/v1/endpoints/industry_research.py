# -*- coding: utf-8 -*-
"""Rapid background industry/company research workspace API."""

import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from api.v1.schemas.industry_research import IndustryResearchProjectRequest
from src.services.industry_research_report_export_service import IndustryResearchReportExportService
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
    research_type: str = Query(default="industry", pattern="^(industry|company)$"),
):
    try:
        return IndustryResearchService().blueprint(
            topic,
            lookback_days=lookback_days,
            research_type=research_type,
        )
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


@router.get("/projects/{project_id}/download", summary="下载本人行业或公司调研报告")
def project_download(
    project_id: str,
    format: str = Query(default="docx", pattern="^(docx|pdf|markdown|json)$"),
):
    project = IndustryResearchService().get_project(project_id)
    if project is None:
        raise _error(IndustryResearchError("调研课题不存在"), 404)
    report = project.get("report") if isinstance(project.get("report"), dict) else {}
    if not report:
        raise _error(IndustryResearchError("报告尚未生成完成"), 409)
    topic = str(project.get("topic") or "research-report")[:80]
    extension = {"json": "json", "markdown": "md", "docx": "docx", "pdf": "pdf"}[format]
    filename = quote(f"{topic}-深度研究报告.{extension}")
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    if format == "json":
        payload = {
            "project": {key: value for key, value in project.items() if key not in {"report", "snapshot"}},
            "report": report,
            "snapshot": project.get("snapshot"),
        }
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            media_type="application/json; charset=utf-8",
            headers=headers,
        )
    if format in {"docx", "pdf"}:
        try:
            artifact = IndustryResearchReportExportService().render(project, format)
        except ValueError as exc:
            raise _error(IndustryResearchError(str(exc)), 409)
        return Response(content=artifact.content, media_type=artifact.media_type, headers=headers)
    markdown = str(report.get("long_form_report") or report.get("executive_summary") or report.get("one_sentence") or "")
    if not markdown:
        raise _error(IndustryResearchError("长篇正文仍在后台生成"), 409)
    return Response(content=markdown, media_type="text/markdown; charset=utf-8", headers=headers)
