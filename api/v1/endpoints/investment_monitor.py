# -*- coding: utf-8 -*-
"""Unified investor/company/institution monitoring endpoints."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import BackgroundTasks
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from api.v1.schemas.investment_monitor import AnnouncementPackageRequest, AnnouncementSyncRequest, DragonTigerSyncRequest, ExternalEventBatch, MonitorSyncRequest, MonitoringSourceCreate
from src.services.investment_monitor_service import InvestmentMonitorError, InvestmentMonitorService
from src.services.investment_monitor_worker import InvestmentMonitorWorker
from src.services.sync_watchdog_worker import SyncWatchdogWorker
from src.services.watchlist_backfill_worker import WatchlistBackfillWorker
from src.services.icloud_knowledge_service import ICloudKnowledgeError, ICloudKnowledgeService, ICloudKnowledgeWorker

router = APIRouter()


def _error(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": "investment_monitor_error", "message": str(exc)[:500]})


@router.get("/status", summary="监控任务与全部消息源健康状态")
def status():
    return {
        "watchdog": SyncWatchdogWorker.get_instance().status(),
        "worker": InvestmentMonitorWorker.get_instance().status(),
        "sources": InvestmentMonitorService().list_sources(),
    }


@router.post("/watchdog/audit", summary="立即检查同步心跳并自动修复")
def audit_sync_watchdog():
    return SyncWatchdogWorker.get_instance().audit_once()


@router.get("/sources", summary="列出内置和外部消息源")
def sources():
    return InvestmentMonitorService().list_sources()


@router.post("/sources", summary="注册可扩展 API、Webhook 或 MCP 消息源")
def create_source(request: MonitoringSourceCreate):
    try:
        return InvestmentMonitorService().create_external_source(request.model_dump())
    except InvestmentMonitorError as exc:
        raise _error(exc, 409 if "already exists" in str(exc) else 400)


@router.post("/sources/{source_key}/events", summary="批量注入外部消息并幂等去重")
def ingest_events(source_key: str, request: ExternalEventBatch):
    try:
        return InvestmentMonitorService().ingest_external_events(
            source_key, [item.model_dump(exclude_none=True) for item in request.events]
        )
    except InvestmentMonitorError as exc:
        raise _error(exc, 404 if "not found" in str(exc) else 400)


@router.post("/sync", summary="立即同步全部或指定类别消息源")
def sync(request: MonitorSyncRequest):
    return InvestmentMonitorService().sync_all(categories=request.categories)


@router.post("/sources/{source_key}/sync", summary="立即同步单个消息源")
def sync_source(source_key: str):
    try:
        return InvestmentMonitorService().sync_source(source_key)
    except InvestmentMonitorError as exc:
        raise _error(exc, 404)


@router.post("/worker/start", summary="启动实时监控轮询")
def start_worker():
    return InvestmentMonitorWorker.get_instance().start()


@router.post("/worker/stop", summary="停止实时监控轮询")
def stop_worker():
    return InvestmentMonitorWorker.get_instance().stop()


@router.get("/dashboard", summary="自选股三视角投资情报总览")
def dashboard(days: int = Query(7, ge=1, le=90)):
    return InvestmentMonitorService().dashboard(days=days)


@router.get("/intelligence-dashboard", summary="投资情报台决策看板聚合")
def intelligence_dashboard(days: int = Query(14, ge=7, le=90)):
    return InvestmentMonitorService().intelligence_dashboard(days=days)


@router.get("/source-bi", summary="全部数据源存量、增量、时效与可调用能力 BI")
def source_bi(days: int = Query(30, ge=7, le=90)):
    return InvestmentMonitorService().source_bi(days=days)


@router.get("/dragon-tiger/daily", summary="龙虎榜单日全市场明细与营业部席位")
def dragon_tiger_daily(
    trade_date: Optional[str] = None,
    refresh: bool = Query(False, description="直接调用 Tushare top_list/top_inst 刷新该交易日"),
):
    try:
        return InvestmentMonitorService().dragon_tiger_daily(trade_date=trade_date, refresh=refresh)
    except InvestmentMonitorError as exc:
        raise _error(exc, 502 if "获取失败" in str(exc) else 400)


@router.get("/dragon-tiger/history", summary="查询本地龙虎榜历史库")
def dragon_tiger_history(
    start_date: str, end_date: str, symbol: Optional[str] = None,
    query: Optional[str] = None, page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    try:
        return InvestmentMonitorService().dragon_tiger_history(
            start_date=start_date, end_date=end_date, symbol=symbol,
            query=query, page=page, page_size=page_size,
        )
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)


@router.post("/dragon-tiger/sync", summary="按交易日历补齐龙虎榜历史摘要")
def sync_dragon_tiger(request: DragonTigerSyncRequest):
    try:
        return InvestmentMonitorService().sync_dragon_tiger_range(
            request.start_date, request.end_date,
        )
    except InvestmentMonitorError as exc:
        raise _error(exc, 502 if "获取失败" in str(exc) else 400)


@router.get("/events", summary="筛选统一消息事件流")
def events(
    days: int = Query(7, ge=1, le=3650), symbol: Optional[str] = None,
    perspective: Optional[str] = None, event_type: Optional[str] = None,
    source_key: Optional[str] = None, query: Optional[str] = None,
    channel: Optional[str] = None, evidence_level: Optional[str] = None,
    min_importance: Optional[int] = Query(None, ge=0, le=100),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    return InvestmentMonitorService().list_events(
        days=days, symbol=symbol, perspective=perspective, event_type=event_type,
        source_key=source_key, query=query, min_importance=min_importance,
        channel=channel, evidence_level=evidence_level,
        page=page, page_size=page_size,
    )


@router.get("/events/{event_id}", summary="读取单条情报事件原文与来源信息")
def event_detail(event_id: int):
    try:
        return InvestmentMonitorService().event_detail(event_id)
    except InvestmentMonitorError as exc:
        raise _error(exc, 404)


@router.get("/symbols/{symbol}", summary="单只自选股的三视角证据和评分")
def symbol_detail(symbol: str, days: int = Query(30, ge=1, le=3650)):
    return InvestmentMonitorService().symbol_detail(symbol, days=days)


@router.get("/super-watchlist", summary="动态自选股全渠道证据工作台")
def super_watchlist(days: int = Query(365, ge=30, le=3650)):
    return InvestmentMonitorService().super_watchlist(days=days)


@router.get("/stock-workspace/{symbol}", summary="供问股、持仓、信号、回测和告警共享的个股数据上下文")
def stock_workspace(
    symbol: str,
    days: int = Query(365, ge=30, le=3650),
    refresh: bool = Query(False, description="绕过短时本地缓存重新组装，不直接请求外部数据源"),
):
    try:
        return InvestmentMonitorService().stock_workspace(symbol, days=days, refresh=refresh)
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)


@router.get("/super-watchlist/{symbol}/essay-consensus", summary="读取最近20篇小作文的独立 AI 一致预期快照")
def essay_consensus(symbol: str):
    try:
        return InvestmentMonitorService().essay_consensus(symbol)
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)


@router.post("/super-watchlist/{symbol}/essay-consensus/analyze", summary="重新分析该股票最近20篇匹配小作文")
def analyze_essay_consensus(symbol: str):
    try:
        return InvestmentMonitorService().request_essay_consensus(symbol)
    except InvestmentMonitorError as exc:
        raise _error(exc, 503 if "DEEPSEEK" in str(exc).upper() else 400)


@router.post("/super-watchlist/refresh", summary="刷新共享行情并唤醒到期情报源")
def refresh_super_watchlist():
    from src.services.market_data_worker import MarketDataWorker

    monitor = InvestmentMonitorService()
    return {
        "market": MarketDataWorker.get_instance().run_now(),
        "keyword_index": monitor.reindex_watchlist_keywords(),
        "intelligence": InvestmentMonitorWorker.get_instance().trigger(),
        "mode": "shared_workers",
    }


@router.post("/super-watchlist/{symbol}/backfill", summary="重跑单只自选股最近半年全渠道回填")
def backfill_watchlist_symbol(symbol: str):
    try:
        return WatchlistBackfillWorker.get_instance().enqueue(symbol, days=183)
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)


@router.get("/announcements/categories", summary="巨潮资讯公告分类")
def announcement_categories():
    return InvestmentMonitorService().announcement_categories()


@router.post("/announcements/sync", summary="按日期、个股、分类或关键词抓取巨潮公告")
def sync_announcements(request: AnnouncementSyncRequest):
    try:
        return InvestmentMonitorService().sync_announcements(request.model_dump())
    except (InvestmentMonitorError, ValueError) as exc:
        raise _error(exc, 400)


@router.get("/announcements", summary="查询已入库上市公司公告")
def announcements(
    days: int = Query(30, ge=1, le=3650), start_date: Optional[str] = None,
    end_date: Optional[str] = None, symbol: Optional[str] = None,
    category: Optional[str] = None, query: Optional[str] = None, page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    try:
        return InvestmentMonitorService().list_announcements(
            days=days, start_date=start_date, end_date=end_date, symbol=symbol,
            category=category, query=query, page=page, page_size=page_size,
        )
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)


@router.get("/announcements/export", summary="从已入库公告导出 Excel 索引")
def export_announcements(
    days: int = Query(30, ge=1, le=3650), start_date: Optional[str] = None,
    end_date: Optional[str] = None, symbol: Optional[str] = None,
    category: Optional[str] = None, query: Optional[str] = None,
):
    try:
        content = InvestmentMonitorService().export_announcements(
            days=days, start_date=start_date, end_date=end_date, symbol=symbol,
            category=category, query=query,
        )
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)
    filename = quote("上市公司公告.xlsx")
    return StreamingResponse(
        BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/announcements/package", summary="从已入库公告下载并打包 PDF 与 TXT")
def package_announcements(request: AnnouncementPackageRequest, background_tasks: BackgroundTasks):
    try:
        result = InvestmentMonitorService().package_announcements(
            request.event_ids, include_text=request.include_text,
        )
    except InvestmentMonitorError as exc:
        raise _error(exc, 400)
    path = Path(result["path"])
    background_tasks.add_task(path.unlink, missing_ok=True)
    return FileResponse(
        path, media_type="application/zip", filename=result["filename"],
        background=background_tasks,
        headers={"X-Announcement-Downloaded": str(result["downloaded"]),
                 "X-Announcement-Failed": str(result["failed"])},
    )


@router.get("/cloud/status", summary="iCloud 云端知识库状态与版本清单")
def cloud_status():
    service = ICloudKnowledgeService()
    snapshots = service.list_snapshots()
    return {"storage": service.status(snapshots), "worker": ICloudKnowledgeWorker.get_instance().status(), "snapshots": snapshots}


@router.post("/cloud/snapshot", summary="立即生成一致性 iCloud 知识库快照")
def cloud_snapshot():
    try:
        return ICloudKnowledgeService().create_snapshot()
    except ICloudKnowledgeError as exc:
        raise _error(exc, 503)


@router.post("/cloud/worker/start", summary="启动 iCloud 知识库定时同步")
def start_cloud_worker():
    return ICloudKnowledgeWorker.get_instance().start()


@router.post("/cloud/worker/stop", summary="停止 iCloud 知识库定时同步")
def stop_cloud_worker():
    return ICloudKnowledgeWorker.get_instance().stop()


@router.get("/cloud/snapshots/{filename}/verify", summary="校验云端知识库完整性")
def verify_cloud_snapshot(filename: str):
    try:
        return ICloudKnowledgeService().verify(filename)
    except ICloudKnowledgeError as exc:
        raise _error(exc, 404)
