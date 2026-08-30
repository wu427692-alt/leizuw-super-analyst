# -*- coding: utf-8 -*-
"""Concept/theme graph, membership, exposure, beta, and alpha endpoints."""

from fastapi import APIRouter, HTTPException, Query

from src.services.concept_theme_service import ConceptThemeError, ConceptThemeService
from src.services.concept_theme_worker import ConceptThemeWorker

router = APIRouter()


def _http_error(exc: Exception, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": "concept_theme_error", "message": str(exc)[:500]})


@router.get("/overview", summary="读取分层概念题材总览")
def overview(
    query: str = Query(default="", max_length=100),
    theme_type: str = Query(default="", max_length=24),
    source: str = Query(default="", max_length=24),
    family: str = Query(default="", max_length=80),
    cluster: str = Query(default="", max_length=80),
    sort_by: str = Query(default="heat", pattern="^(heat|name|size|change)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=80, ge=12, le=200),
):
    return ConceptThemeService().overview(
        query=query, theme_type=theme_type, source=source, family=family, cluster=cluster,
        sort_by=sort_by, page=page, page_size=page_size,
    )


@router.get("/themes/{theme_id}", summary="读取题材成分、权重及Beta/Alpha")
def theme_detail(
    theme_id: int,
    refresh_if_empty: bool = Query(default=True),
    horizon_days: int = Query(default=60, ge=20, le=120),
):
    try:
        return ConceptThemeService().theme_detail(
            theme_id, refresh_if_empty=refresh_if_empty, horizon_days=horizon_days,
        )
    except ConceptThemeError as exc:
        raise _http_error(exc, 404 if "不存在" in str(exc) else 422)


@router.get("/stocks/{ts_code}", summary="读取个股题材暴露与独特Alpha证据")
def stock_lens(
    ts_code: str,
    refresh_if_empty: bool = Query(default=True),
    horizon_days: int = Query(default=60, ge=20, le=120),
):
    try:
        return ConceptThemeService().stock_lens(
            ts_code, refresh_if_empty=refresh_if_empty, horizon_days=horizon_days,
        )
    except ConceptThemeError as exc:
        raise _http_error(exc)


@router.get("/status", summary="读取题材库同步状态")
def status():
    return {**ConceptThemeService().sync_status(), "worker": ConceptThemeWorker.get_instance().status()}


@router.get("/rotation", summary="读取多源题材轮动强弱")
def rotation(
    days: int = Query(default=20, ge=5, le=60),
    limit: int = Query(default=24, ge=8, le=60),
):
    return ConceptThemeService().rotation(days=days, limit=limit)


@router.post("/sync/catalog", status_code=202, summary="唤醒题材库后台更新")
def wake_catalog_sync():
    return ConceptThemeWorker.get_instance().trigger()


@router.post("/themes/{theme_id}/refresh", status_code=202, summary="直接更新单一题材及归因")
def refresh_theme(theme_id: int):
    try:
        return ConceptThemeService().refresh_theme(theme_id, calculate=True)
    except ConceptThemeError as exc:
        raise _http_error(exc)


@router.post("/stocks/{ts_code}/refresh", status_code=202, summary="直接更新单股题材与归因")
def refresh_stock(ts_code: str):
    try:
        return ConceptThemeService().refresh_stock(ts_code, calculate=True)
    except ConceptThemeError as exc:
        raise _http_error(exc)
