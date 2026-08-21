# -*- coding: utf-8 -*-
"""DeepSeek research-note tagging, queue and dashboard endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.essay_radar import (
    EssayBackfillRequest,
    EssayCountBackfillRequest,
    EssayDailyReportRunRequest,
    EssayRetryRequest,
)
from src.services.essay_analysis_service import EssayAnalysisError, EssayAnalysisService, EssayDailyReportService
from src.services.essay_daily_report_worker import EssayDailyReportWorker
from src.services.essay_analysis_worker import EssayAnalysisWorker
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncWorker

router = APIRouter()


def _service_error(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": "essay_analysis_error", "message": str(exc)[:500]},
    )


@router.get("/status", summary="读取近 30 天分析进度与后台任务状态")
def status(days: int = Query(30, ge=1, le=3650)):
    return {
        "progress": EssayAnalysisService().progress(days=days),
        "worker": EssayAnalysisWorker.get_instance().status(),
        "mcp_sync": ZsxqMcpSyncWorker.get_instance().status(),
        "daily_report_worker": EssayDailyReportWorker.get_instance().status(),
    }


@router.post("/backfill", summary="将指定天数内纪要加入分析队列")
def backfill(request: EssayBackfillRequest):
    service = EssayAnalysisService()
    result = service.enqueue_recent(days=request.days)
    try:
        worker = EssayAnalysisWorker.get_instance().start()
    except EssayAnalysisError as exc:
        raise _service_error(exc, 503)
    return {"queue": result, "worker": worker}


@router.get("/historical-backlog", summary="读取全部已入库小作文的 AI 分析与未入队数量")
def historical_backlog():
    return EssayAnalysisService().historical_backlog()


@router.post("/backfill-count", summary="按篇数选择尚未入队的历史小作文进行 AI 分析")
def backfill_count(request: EssayCountBackfillRequest):
    service = EssayAnalysisService()
    result = service.enqueue_unqueued(count=request.count, order=request.order)
    try:
        # This entry point must honor the requested count exactly. New MCP
        # records are already queued at ingest, so no 30-day bootstrap is needed.
        worker = EssayAnalysisWorker.get_instance().start(bootstrap_recent=False)
    except EssayAnalysisError as exc:
        raise _service_error(exc, 503)
    return {"queue": result, "backlog": service.historical_backlog(), "worker": worker}


@router.post("/worker/start", summary="启动实时 DeepSeek 分析任务")
def start_worker():
    try:
        return EssayAnalysisWorker.get_instance().start()
    except EssayAnalysisError as exc:
        raise _service_error(exc, 503)


@router.post("/worker/stop", summary="停止实时 DeepSeek 分析任务")
def stop_worker():
    return EssayAnalysisWorker.get_instance().stop()


@router.post("/retry-failed", summary="重置失败任务并可立即启动后台任务")
def retry_failed(request: EssayRetryRequest):
    retried = EssayAnalysisService().repo.retry_failed()
    try:
        worker = EssayAnalysisWorker.get_instance().start() if request.start_worker else None
    except EssayAnalysisError as exc:
        raise _service_error(exc, 503)
    return {"retried": retried, "worker": worker}


@router.get("/dashboard", summary="获取小作文标签和股票热度看板")
def dashboard(
    days: int = Query(30, ge=1, le=3650),
    top_n: int = Query(12, ge=3, le=30),
):
    return EssayAnalysisService().dashboard(days=days, top_n=top_n)


@router.get("/insights", summary="获取小作文趋势、证据质量、模型共识与关注股洞察")
def insights(
    days: int = Query(30, ge=1, le=3650),
    trend_days: int = Query(14, ge=7, le=90),
):
    return EssayAnalysisService().insights(days=days, trend_days=trend_days)


@router.get("/deep-insights", summary="获取小作文来源、主题、标的、催化风险多层洞察")
def deep_insights(
    days: int = Query(30, ge=7, le=3650),
    trend_days: int = Query(14, ge=7, le=90),
):
    return EssayAnalysisService().deep_insights(days=days, trend_days=trend_days)


@router.get("/word-cloud", summary="获取日、周、月股票/标签/主题词云")
def word_cloud(
    period: str = Query("day", pattern="^(day|week|month)$"),
    kind: str = Query("stocks", pattern="^(stocks|tags|themes)$"),
    anchor_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    stock: Optional[str] = None,
    top_n: int = Query(80, ge=10, le=200),
):
    try:
        return EssayAnalysisService().word_cloud(period=period, kind=kind, anchor_date=anchor_date, stock=stock, top_n=top_n)
    except ValueError as exc:
        raise _service_error(exc, 400)


@router.get("/daily-reports", summary="读取各模型的前一日小作文日报")
def daily_reports(limit: int = Query(30, ge=1, le=366), model: Optional[str] = None):
    return EssayDailyReportService().list(limit=limit, model=model)


@router.post("/daily-reports/run", summary="立即生成指定日期的多模型小作文日报")
def run_daily_reports(request: EssayDailyReportRunRequest):
    return EssayDailyReportWorker.get_instance().run_now(request.report_date, request.force)


@router.post("/daily-reports/worker/start", summary="启动每日多模型报告任务")
def start_daily_report_worker():
    return EssayDailyReportWorker.get_instance().start()


@router.post("/daily-reports/worker/stop", summary="停止每日多模型报告任务")
def stop_daily_report_worker():
    return EssayDailyReportWorker.get_instance().stop()


@router.get("/analyses", summary="筛选和分页读取小作文分析结果")
def list_analyses(
    days: int = Query(30, ge=1, le=3650),
    query: Optional[str] = None,
    status: Optional[str] = None,
    sentiment: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    stock: Optional[str] = None,
    min_importance: Optional[int] = Query(None, ge=0, le=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return EssayAnalysisService().list_analyses(
        days=days,
        query=query,
        status=status,
        sentiment=sentiment,
        category=category,
        tag=tag,
        stock=stock,
        min_importance=min_importance,
        page=page,
        page_size=page_size,
    )


@router.get("/feed", summary="检索全部已入库小作文，可选关联 AI 分析结果")
def list_feed(
    days: int = Query(0, ge=0, le=3650),
    query: Optional[str] = Query(None, max_length=200),
    analysis_status: Optional[str] = Query(
        None,
        pattern="^(completed|uncompleted|not_queued|pending|processing|failed)$",
    ),
    sentiment: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    stock: Optional[str] = None,
    min_importance: Optional[int] = Query(None, ge=0, le=100),
    known_total: Optional[int] = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return EssayAnalysisService().list_feed(
        days=days,
        query=query,
        analysis_status=analysis_status,
        sentiment=sentiment,
        category=category,
        tag=tag,
        stock=stock,
        min_importance=min_importance,
        known_total=known_total,
        page=page,
        page_size=page_size,
    )


@router.get("/analyses/{topic_id}", summary="读取单篇小作文分析详情")
def get_analysis(topic_id: str):
    try:
        return EssayAnalysisService().get_analysis(topic_id)
    except KeyError as exc:
        raise _service_error(exc, 404)
