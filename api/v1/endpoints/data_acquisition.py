# -*- coding: utf-8 -*-
"""One-stop model-assisted data acquisition endpoints."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from api.v1.schemas.data_acquisition import DataAcquisitionPlanRequest, DataAcquisitionRunRequest
from src.services.data_acquisition_service import DataAcquisitionError, DataAcquisitionService
from src.services.data_acquisition_task_service import DataAcquisitionTaskService

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
