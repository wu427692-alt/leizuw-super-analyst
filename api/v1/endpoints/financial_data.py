# -*- coding: utf-8 -*-
"""Unified Tushare and MCP-synchronized research-note endpoints."""

from __future__ import annotations

import logging
import json
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Optional
from urllib.parse import quote
import zipfile

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.financial_data import (
    FinancialDataQueryRequest,
    ResearchNoteImportRequest,
    ResearchNoteImportResponse,
    ResearchNoteItem,
    ResearchNoteListResponse,
    ResearchNoteAudioBatchDownloadRequest,
    ResearchNoteAudioAnalysisRequest,
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
from src.services.data_storage_service import DataStorageMaintenanceWorker, DataStorageService
from src.services.research_note_media_task_service import (
    ResearchNoteMediaTaskError,
    ResearchNoteMediaTaskService,
)
from src.services.research_note_audio_analysis_service import (
    ResearchNoteAudioAnalysisError,
    ResearchNoteAudioAnalysisTaskService,
)

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


@router.get(
    "/storage/status",
    summary="查看统一财经事实库、逻辑数据域和新鲜度",
)
def storage_status(include_integrity: bool = Query(False, description="是否执行 SQLite quick_check")):
    result = DataStorageService().status(include_integrity=include_integrity)
    result["maintenance_worker"] = DataStorageMaintenanceWorker.get_instance().status()
    return result


@router.post(
    "/storage/optimize",
    summary="安全优化 SQLite 查询计划并执行非阻塞 WAL 检查点",
)
def optimize_storage():
    return DataStorageMaintenanceWorker.get_instance().run_now()


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


@router.get("/research-notes/audio-files", summary="按录音文件而非帖子检索知识星球附件")
def list_research_note_audio_files(
    days: int = Query(0, ge=0, le=3650),
    query: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    result = ResearchNoteService().list_audio_files(
        days=days, query=query, page=page, page_size=page_size,
    )
    items = result.get("items") or []
    transcript_status = ResearchNoteAudioAnalysisTaskService.get_instance().transcript_availability(
        (str(item.get("topic_id") or ""), str(item.get("file_id") or ""))
        for item in items
    )
    for item in items:
        key = (str(item.get("topic_id") or ""), str(item.get("file_id") or ""))
        item.update(transcript_status.get(key) or {
            "transcribed": False,
            "transcript_task_id": None,
            "transcript_line_count": None,
            "transcribed_at": None,
        })
    return result


@router.post("/research-notes/audio-files/batch-download", summary="将勾选录音源文件临时打包为 ZIP")
def batch_download_research_note_audio(request: ResearchNoteAudioBatchDownloadRequest):
    service = ResearchNoteService()
    selected = list(dict.fromkeys((item.topic_id, item.file_id) for item in request.items))
    handle = tempfile.NamedTemporaryFile(prefix="zsxq-audio-selected-", suffix=".zip", delete=False)
    handle.close()
    archive_path = Path(handle.name)
    manifest = []
    used_names: set[str] = set()
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for topic_id, file_id in selected:
                note = service.get_note(topic_id)
                asset = next(
                    (item for item in note.get("files", []) if str(item.get("file_id") or "") == file_id),
                    None,
                )
                if asset is None or asset.get("asset_kind") != "audio":
                    raise FinancialDataValidationError(f"录音附件不存在：{topic_id}/{file_id}")
                source_url = ZsxqMcpSyncService().resolve_media_url_sync(topic_id, "files", file_id)
                upstream = requests.get(source_url, stream=True, timeout=(10, 300))
                try:
                    upstream.raise_for_status()
                    raw_name = Path(str(asset.get("name") or f"录音-{file_id}")).name
                    safe_name = raw_name.replace("\r", "_").replace("\n", "_") or f"录音-{file_id}"
                    candidate = safe_name
                    suffix = Path(safe_name).suffix
                    stem = Path(safe_name).stem
                    counter = 2
                    while candidate.lower() in used_names:
                        candidate = f"{stem}_{counter}{suffix}"
                        counter += 1
                    used_names.add(candidate.lower())
                    with archive.open(f"录音文件/{candidate}", "w") as target:
                        for chunk in upstream.iter_content(chunk_size=256 * 1024):
                            if chunk:
                                target.write(chunk)
                    manifest.append({
                        "topic_id": topic_id,
                        "file_id": file_id,
                        "filename": candidate,
                        "note_title": note.get("title"),
                        "group_name": note.get("group_name"),
                        "created_at": note.get("created_at"),
                    })
                finally:
                    upstream.close()
            archive.writestr(
                "下载清单.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
    except (FinancialDataValidationError, ResearchNoteNotFoundError) as exc:
        archive_path.unlink(missing_ok=True)
        raise _bad_request(exc)
    except (ZsxqMcpSyncError, requests.RequestException) as exc:
        archive_path.unlink(missing_ok=True)
        raise _upstream_error(exc)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        archive_path,
        filename=f"知识星球录音_{suffix}.zip",
        media_type="application/zip",
        headers={"X-Selected-File-Count": str(len(manifest))},
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@router.post(
    "/research-notes/audio-files/batch-download-tasks",
    status_code=202,
    summary="提交录音源文件后台打包任务",
)
def create_research_note_audio_package_task(request: ResearchNoteAudioBatchDownloadRequest):
    try:
        return ResearchNoteMediaTaskService.get_instance().submit(
            (item.topic_id, item.file_id) for item in request.items
        )
    except ResearchNoteMediaTaskError as exc:
        raise _bad_request(exc)


@router.get(
    "/research-notes/audio-files/batch-download-tasks/{task_id}",
    summary="读取录音源文件后台下载与打包进度",
)
def get_research_note_audio_package_task(task_id: str):
    try:
        return ResearchNoteMediaTaskService.get_instance().get(task_id)
    except ResearchNoteMediaTaskError as exc:
        raise _not_found(exc)


@router.get(
    "/research-notes/audio-files/batch-download-tasks/{task_id}/download",
    summary="下载已完成的录音 ZIP",
)
def download_research_note_audio_package(task_id: str):
    try:
        archive_path, filename = ResearchNoteMediaTaskService.get_instance().download(task_id)
    except ResearchNoteMediaTaskError as exc:
        message = str(exc)
        raise _not_found(exc) if "不存在" in message or "过期" in message else _bad_request(exc)
    return FileResponse(
        archive_path,
        filename=filename,
        media_type="application/zip",
    )


@router.get(
    "/research-notes/audio-analysis/capability",
    summary="查看录音转写与纪要分析能力是否已配置",
)
def research_note_audio_analysis_capability():
    return ResearchNoteAudioAnalysisTaskService.get_instance().capability()


@router.get(
    "/research-notes/audio-analysis/tasks",
    summary="读取当前用户的录音纪要后台任务",
)
def list_research_note_audio_analysis_tasks(limit: int = Query(20, ge=1, le=50)):
    return ResearchNoteAudioAnalysisTaskService.get_instance().list_tasks(limit)


@router.post(
    "/research-notes/audio-analysis/tasks",
    status_code=202,
    summary="提交所选录音的后台转写与 AI 纪要任务",
)
def create_research_note_audio_analysis_task(request: ResearchNoteAudioAnalysisRequest):
    try:
        return ResearchNoteAudioAnalysisTaskService.get_instance().submit(
            ((item.topic_id, item.file_id) for item in request.items),
            title=request.title or "",
            focus=request.focus or "",
            hotwords=request.hotwords,
            speaker_count=request.speaker_count,
            generate_memo=request.generate_memo,
        )
    except ResearchNoteAudioAnalysisError as exc:
        raise _bad_request(exc)


@router.get(
    "/research-notes/audio-analysis/tasks/{task_id}",
    summary="读取录音转写与纪要生成进度",
)
def get_research_note_audio_analysis_task(task_id: str):
    try:
        return ResearchNoteAudioAnalysisTaskService.get_instance().get(task_id)
    except ResearchNoteAudioAnalysisError as exc:
        raise _not_found(exc)


@router.post(
    "/research-notes/audio-analysis/tasks/{task_id}/retry",
    status_code=202,
    summary="重试失败或中断的录音纪要任务",
)
def retry_research_note_audio_analysis_task(task_id: str):
    try:
        return ResearchNoteAudioAnalysisTaskService.get_instance().retry(task_id)
    except ResearchNoteAudioAnalysisError as exc:
        message = str(exc)
        raise _not_found(exc) if "不存在" in message or "无权" in message else _bad_request(exc)


@router.get(
    "/research-notes/audio-analysis/tasks/{task_id}/transcripts/{file_id}",
    summary="读取一个录音的带时间戳逐字稿",
)
def get_research_note_audio_analysis_transcript(task_id: str, file_id: str):
    try:
        return ResearchNoteAudioAnalysisTaskService.get_instance().transcript(task_id, file_id)
    except ResearchNoteAudioAnalysisError as exc:
        raise _not_found(exc)


@router.get(
    "/research-notes/audio-analysis/tasks/{task_id}/download",
    summary="下载录音纪要 Markdown、Word、JSON 或完整 ZIP",
)
def download_research_note_audio_analysis_task(
    task_id: str,
    format: str = Query("zip", pattern="^(zip|md|docx|json)$"),
):
    try:
        path, filename, media_type = ResearchNoteAudioAnalysisTaskService.get_instance().download(task_id, format)
    except ResearchNoteAudioAnalysisError as exc:
        message = str(exc)
        raise _not_found(exc) if "不存在" in message or "无权" in message or "过期" in message else _bad_request(exc)
    return FileResponse(path, filename=filename, media_type=media_type)


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


@router.get(
    "/research-notes/{topic_id}/media/{kind}/{asset_id}/download",
    summary="按需流式下载知识星球源文件（服务器不落盘）",
)
def download_research_note_media(topic_id: str, kind: str, asset_id: str):
    if kind != "files":
        raise _bad_request(ValueError("仅附件支持源文件下载"))
    try:
        note = ResearchNoteService().get_note(topic_id)
        asset = next(
            (item for item in note.get("files", []) if str(item.get("file_id") or "") == asset_id),
            None,
        )
        if asset is None:
            raise ResearchNoteNotFoundError("知识星球附件不存在")
        source_url = ZsxqMcpSyncService().resolve_media_url_sync(topic_id, kind, asset_id)
        upstream = requests.get(source_url, stream=True, timeout=(10, 120))
        upstream.raise_for_status()
        filename = str(asset.get("name") or f"zsxq-{asset_id}").replace("\r", "_").replace("\n", "_")
        content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
        headers = {
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        }
        content_length = upstream.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            headers["Content-Length"] = content_length
        return StreamingResponse(
            upstream.iter_content(chunk_size=128 * 1024),
            media_type=content_type,
            headers=headers,
            background=BackgroundTask(upstream.close),
        )
    except ResearchNoteNotFoundError as exc:
        raise _not_found(exc)
    except (ZsxqMcpSyncError, requests.RequestException) as exc:
        raise _upstream_error(exc)
