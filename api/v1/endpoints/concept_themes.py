# -*- coding: utf-8 -*-
"""Concept/theme graph, membership, exposure, beta, and alpha endpoints."""

import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from src.services.concept_theme_service import ConceptThemeError, ConceptThemeService
from src.services.concept_theme_worker import ConceptThemeWorker

router = APIRouter()


def _http_error(exc: Exception, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": "concept_theme_error", "message": str(exc)[:500]})


def _watchlist_codes(request: Request) -> set[str]:
    user_id = int(getattr(request.state, "user_id", 0) or 0)
    if user_id <= 0:
        return set()
    from src.services.user_account_service import UserAccountService
    return {
        str(symbol or "").upper().split(".", 1)[0].removeprefix("SH").removeprefix("SZ").removeprefix("BJ")
        for symbol in UserAccountService().list_watchlist(user_id)
    }


@router.get("/overview", summary="读取分层概念题材总览")
def overview(
    query: str = Query(default="", max_length=100),
    theme_type: str = Query(default="", max_length=24),
    source: str = Query(default="", max_length=24),
    family: str = Query(default="", max_length=80),
    cluster: str = Query(default="", max_length=80),
    min_sources: int = Query(default=1, ge=1, le=6),
    view: str = Query(default="source", pattern="^(canonical|source)$"),
    sort_by: str = Query(default="heat", pattern="^(heat|name|size|change)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=80, ge=12, le=200),
):
    return ConceptThemeService().overview(
        query=query, theme_type=theme_type, source=source, family=family, cluster=cluster, min_sources=min_sources, view=view,
        sort_by=sort_by, page=page, page_size=page_size,
    )


@router.get("/themes/{theme_id}", summary="读取题材成分、权重及Beta/Alpha")
def theme_detail(
    request: Request,
    theme_id: int,
    refresh_if_empty: bool = Query(default=True),
    horizon_days: int = Query(default=60, ge=20, le=120),
):
    try:
        result = ConceptThemeService().theme_detail(
            theme_id, refresh_if_empty=refresh_if_empty, horizon_days=horizon_days,
        )
        watched = _watchlist_codes(request)
        watchlist_stocks = []
        for stock in result.get("stocks", []):
            compact = str(stock.get("ts_code") or "").upper().split(".", 1)[0]
            stock["in_watchlist"] = compact in watched
            if stock["in_watchlist"]:
                watchlist_stocks.append({"ts_code": stock.get("ts_code"), "name": stock.get("name")})
        result["watchlist_stocks"] = watchlist_stocks
        return result
    except ConceptThemeError as exc:
        raise _http_error(exc, 404 if "不存在" in str(exc) else 422)


@router.get("/themes/{theme_id}/export.csv", summary="导出题材成分、权重与归因结果")
def export_theme_csv(
    theme_id: int,
    horizon_days: int = Query(default=60, ge=20, le=120),
):
    try:
        detail = ConceptThemeService().theme_detail(
            theme_id, refresh_if_empty=False, horizon_days=horizon_days,
        )
    except ConceptThemeError as exc:
        raise _http_error(exc, 404 if "不存在" in str(exc) else 422)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "股票代码", "股票名称", "题材权重", "题材Beta", "市场Beta", f"{detail['horizon_days']}日Alpha",
        "R²", "有效样本", "置信等级", "独立来源数", "来源", "入选理由",
    ])
    for item in detail["stocks"]:
        writer.writerow([
            item.get("ts_code"), item.get("name"), item.get("weight_score"), item.get("beta"),
            item.get("market_beta"), item.get("residual_return"), item.get("r_squared"),
            item.get("observations"), item.get("confidence"), item.get("source_count"),
            "、".join(item.get("sources") or []), "；".join(item.get("reasons") or []),
        ])
    safe_name = str(detail["theme"].get("canonical_name") or "题材").replace("/", "-").replace("\\", "-")
    filename = f"{safe_name}_{detail['horizon_days']}日归因.csv"
    return Response(
        content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


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


@router.get("/leaders", summary="读取跨题材市场共识股票雷达")
def leaders(
    request: Request,
    horizon_days: int = Query(default=60, ge=20, le=120),
    limit: int = Query(default=24, ge=8, le=60),
    mode: str = Query(default="consensus", pattern="^(consensus|alpha|beta|specificity)$"),
):
    result = ConceptThemeService().market_consensus_leaders(
        horizon_days=horizon_days, limit=limit, mode=mode,
    )
    watched = _watchlist_codes(request)
    for item in result["items"]:
        compact = str(item.get("ts_code") or "").split(".", 1)[0]
        item["in_watchlist"] = compact in watched
    return result


@router.get("/cluster-detail", summary="读取二级题材簇的聚合成分")
def cluster_detail(
    request: Request,
    family: str = Query(min_length=1, max_length=80),
    cluster: str = Query(min_length=1, max_length=80),
    horizon_days: int = Query(default=60, ge=20, le=120),
    limit: int = Query(default=80, ge=12, le=200),
):
    try:
        result = ConceptThemeService().cluster_detail(
            family, cluster, horizon_days=horizon_days, limit=limit,
        )
    except ConceptThemeError as exc:
        raise _http_error(exc, 404 if "不存在" in str(exc) else 422)
    watched = _watchlist_codes(request)
    for item in result["items"]:
        compact = str(item.get("ts_code") or "").split(".", 1)[0]
        item["in_watchlist"] = compact in watched
    return result


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
