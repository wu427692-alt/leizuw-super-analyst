# -*- coding: utf-8 -*-
"""One-stop model-assisted data acquisition endpoints."""

from io import BytesIO
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from api.v1.schemas.data_acquisition import (
    DataAcquisitionPlanRequest,
    DataAcquisitionRunRequest,
    ResearchReportExportRequest,
)
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService
from src.services.data_acquisition_task_service import DataAcquisitionTaskService
from src.services.research_report_library_service import (
    ResearchReportLibraryError,
    ResearchReportLibraryService,
)

router = APIRouter()


def _error(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": "data_acquisition_error", "message": str(exc)[:500]})


@router.get("/capabilities", summary="列出一站式取数可用渠道与能力")
def capabilities():
    return DataAcquisitionService().capabilities()


@router.post("/plan", summary="由大模型把自然语言需求拆成可审计取数计划")
def plan(request: DataAcquisitionPlanRequest):
    try:
        return DataAcquisitionService().plan(request.request)
    except DataAcquisitionError as exc:
        raise _error(exc, 502)


@router.post("/run", summary="执行确认后的多渠道取数计划并生成统一数据包")
def run(request: DataAcquisitionRunRequest):
    try:
        return DataAcquisitionService().run(request.request, request.plan)
    except DataAcquisitionError as exc:
        raise _error(exc)


@router.post("/run-async", status_code=202, summary="提交后台取数任务并返回真实进度编号")
def run_async(request: DataAcquisitionRunRequest):
    try:
        return DataAcquisitionTaskService.get_instance().submit(request.request, request.plan)
    except DataAcquisitionError as exc:
        raise _error(exc)


@router.get("/tasks/{task_id}", summary="读取后台取数任务的真实渠道与打包进度")
def task(task_id: str):
    try:
        return DataAcquisitionTaskService.get_instance().get(task_id)
    except DataAcquisitionError as exc:
        raise _error(exc, 404)


@router.get("/jobs", summary="列出最近生成的数据包")
def jobs(limit: int = Query(20, ge=1, le=100)):
    return DataAcquisitionService().list_jobs(limit)


@router.get("/jobs/{job_id}", summary="读取数据包来源清单与执行结果")
def job(job_id: str):
    try:
        return DataAcquisitionService().get_job(job_id)
    except DataAcquisitionError as exc:
        raise _error(exc, 404)


@router.get("/jobs/{job_id}/download", summary="下载包含 JSON、CSV、Excel 和来源清单的 ZIP")
def download(job_id: str):
    try:
        path = DataAcquisitionService().package_path(job_id)
    except DataAcquisitionError as exc:
        raise _error(exc, 404)
    return FileResponse(path, media_type="application/zip", filename=f"财经数据包_{job_id}.zip")


@router.get("/research-reports/status", summary="读取本地研报库覆盖范围与后台同步进度")
def research_report_status():
    return ResearchReportLibraryService.get_instance().ensure_background_sync()


@router.post("/research-reports/sync", status_code=202, summary="后台补齐最近两年研报元数据与PDF链接")
def sync_research_reports(years: int = Query(2, ge=1, le=5), force: bool = Query(False)):
    service = ResearchReportLibraryService.get_instance()
    end = date.today()
    try:
        return service.start_sync(
            start_date=end - timedelta(days=365 * years),
            end_date=end,
            force=force,
        )
    except ResearchReportLibraryError as exc:
        raise _error(exc)


@router.get("/research-reports/facets", summary="读取研报本地库可人工筛选标签")
def research_report_facets():
    return ResearchReportLibraryService.get_instance().facets()


@router.get("/research-reports/search", summary="只从本地SQLite研报库执行人工条件检索")
def search_research_reports(
    title_query: str = Query("", max_length=200),
    content_query: str = Query("", max_length=500),
    broker: str = Query("", max_length=160),
    company: str = Query("", max_length=160),
    ts_code: str = Query("", max_length=20),
    report_type: str = Query("", max_length=80),
    industry: str = Query("", max_length=160),
    author: str = Query("", max_length=160),
    tag: str = Query("", max_length=80),
    start_date: str = Query("", max_length=10),
    end_date: str = Query("", max_length=10),
    has_pdf: bool = Query(True),
    sort: str = Query("latest", pattern="^(latest|oldest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    return ResearchReportLibraryService.get_instance().search(
        title_query=title_query, content_query=content_query, broker=broker,
        company=company, ts_code=ts_code, report_type=report_type,
        industry=industry, author=author, tag=tag, start_date=start_date,
        end_date=end_date, has_pdf=has_pdf, sort=sort, page=page, page_size=page_size,
    )


@router.post("/research-reports/export-selected", summary="导出人工勾选研报及PDF链接Excel")
def export_selected_research_reports(request: ResearchReportExportRequest):
    try:
        content = ResearchReportLibraryService.get_instance().export_selected(request.ids)
    except ResearchReportLibraryError as exc:
        raise _error(exc)
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="selected_research_reports.xlsx"'},
    )
