# -*- coding: utf-8 -*-
"""Unified Tushare and MCP-synchronized research-note endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.financial_data import (
    FinancialDataQueryRequest,
    ResearchNoteImportRequest,
    ResearchNoteImportResponse,
    ResearchNoteItem,
    ResearchNoteListResponse,
    TushareQueryRequest,
    ZsxqHistoryBackfillRequest,
)
from src.services.financial_data_service import (
    FinancialDataService,
    FinancialDataUpstreamError,
    FinancialDataValidationError,
    ResearchNoteNotFoundError,
    ResearchNoteService,
    TushareGatewayService,
)
from src.services.zsxq_mcp_sync_service import ZsxqMcpSyncError, ZsxqMcpSyncService, ZsxqMcpSyncWorker

logger = logging.getLogger(__name__)
router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "validation_error", "message": str(exc)},
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": str(exc)},
    )


def _upstream_error(exc: Exception) -> HTTPException:
    logger.warning("Financial-data upstream request failed: %s", exc)
    return HTTPException(
        status_code=502,
        detail={"error": "upstream_error", "message": str(exc)},
    )


@router.get(
    "/sources",
    responses={500: {"model": ErrorResponse}},
    summary="列出统一财经数据源状态",
)
def list_sources():
    return FinancialDataService().list_sources()


@router.post(
    "/query",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="统一查询 Tushare、知识星球、巨潮、天眼查或监控事件",
    description=(
        "source=tushare 时 resource 为任意 Tushare api_name，params/fields 原样映射到 Pro API；"
        "source=zsxq 时 resource 固定为 research_notes；source=monitor 支持 events/announcements；"
        "source=cninfo 支持 announcements；source=tianyancha 支持 enterprise_events。"
    ),
)
def query_financial_data(request: FinancialDataQueryRequest):
    try:
        return FinancialDataService().query(
            source=request.source,
            resource=request.resource,
            params=request.params,
            fields=request.fields,
        )
    except FinancialDataValidationError as exc:
        raise _bad_request(exc)
    except FinancialDataUpstreamError as exc:
        raise _upstream_error(exc)


@router.post(
    "/tushare/{api_name}",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="调用任意 Tushare Pro 接口",
)
def query_tushare(api_name: str, request: TushareQueryRequest):
    try:
        return TushareGatewayService().query(
            api_name,
            params=request.params,
            fields=request.fields,
        )
    except FinancialDataValidationError as exc:
        raise _bad_request(exc)
    except FinancialDataUpstreamError as exc:
        raise _upstream_error(exc)


@router.post(
    "/zsxq/import",
    response_model=ResearchNoteImportResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="导入知识星球 MCP 主题页或标准主题数组",
)
def import_zsxq_topics(request: ResearchNoteImportRequest) -> ResearchNoteImportResponse:
    service = ResearchNoteService()
    try:
        if request.mcp_page is not None:
            result = service.import_mcp_page(
                request.mcp_page,
                group_id=request.group_id,
                group_name=request.group_name,
            )
        else:
            result = service.import_topics(
                request.topics or [],
                group_id=request.group_id,
                group_name=request.group_name,
            )
        return ResearchNoteImportResponse(**result)
    except FinancialDataValidationError as exc:
        raise _bad_request(exc)
    except FinancialDataUpstreamError as exc:
        raise _upstream_error(exc)


@router.get(
    "/research-notes",
    response_model=ResearchNoteListResponse,
    responses={400: {"model": ErrorResponse}},
    summary="检索已通过 MCP 同步的调研纪要",
)
def list_research_notes(
    group_id: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None, description="A 股代码，如 688836 或 688836.SH"),
    digested: Optional[bool] = Query(None),
    created_from: Optional[str] = Query(None, description="ISO 8601 或 YYYYMMDD"),
    created_to: Optional[str] = Query(None, description="ISO 8601 或 YYYYMMDD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> ResearchNoteListResponse:
    try:
        return ResearchNoteListResponse(**ResearchNoteService().list_notes(
            group_id=group_id,
            query=query,
            symbol=symbol,
            digested=digested,
            created_from=created_from,
            created_to=created_to,
            page=page,
            page_size=page_size,
        ))
    except FinancialDataValidationError as exc:
        raise _bad_request(exc)


@router.get(
    "/research-notes/{topic_id}",
    response_model=ResearchNoteItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="读取单篇调研纪要详情",
)
def get_research_note(topic_id: str) -> ResearchNoteItem:
    try:
        return ResearchNoteItem(**ResearchNoteService().get_note(topic_id))
    except FinancialDataValidationError as exc:
        raise _bad_request(exc)
    except ResearchNoteNotFoundError as exc:
        raise _not_found(exc)


@router.get("/zsxq/sync/status", summary="知识星球 MCP 增量同步游标与健康状态")
def zsxq_sync_status():
    return ZsxqMcpSyncWorker.get_instance().status()


@router.post("/zsxq/sync", summary="立即从知识星球 MCP 增量同步到本地 SQLite")
def zsxq_sync_now():
    try:
        return ZsxqMcpSyncWorker.get_instance().sync_now()
    except ZsxqMcpSyncError as exc:
        raise _upstream_error(exc)


@router.post(
    "/zsxq/history/backfill",
    summary="后台同步近 1 年或 2 年知识星球历史纪要，默认不做 AI 分析",
)
def zsxq_history_backfill(request: ZsxqHistoryBackfillRequest):
    if request.years not in {1, 2}:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_history_range", "message": "years 只支持 1 或 2"},
        )
    return ZsxqMcpSyncWorker.get_instance().start_history_backfill(days=request.years * 365)


@router.post("/zsxq/sync/worker/start", summary="启动知识星球 MCP 近实时增量同步")
def start_zsxq_sync_worker():
    return ZsxqMcpSyncWorker.get_instance().start()


@router.post("/zsxq/sync/worker/stop", summary="停止知识星球 MCP 增量同步")
def stop_zsxq_sync_worker():
    return ZsxqMcpSyncWorker.get_instance().stop()


@router.get("/research-notes/{topic_id}/media/{kind}/{asset_id}", summary="按需获取纪要图片/附件远端链接")
def research_note_media(topic_id: str, kind: str, asset_id: str):
    try:
        url = ZsxqMcpSyncService().resolve_media_url_sync(topic_id, kind, asset_id)
        return RedirectResponse(url=url, status_code=307, headers={"Cache-Control": "no-store"})
    except (ZsxqMcpSyncError, ResearchNoteNotFoundError) as exc:
        raise _upstream_error(exc)
