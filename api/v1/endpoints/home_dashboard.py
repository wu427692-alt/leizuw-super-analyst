# -*- coding: utf-8 -*-
"""Market command-center endpoint for the Web home page."""

from fastapi import APIRouter, Query, Request, Response

from src.services.home_dashboard_service import HomeDashboardService

router = APIRouter()


@router.get("", summary="聚合A股、海外市场、自选股和投资情报首页大看板")
def dashboard(
    request: Request,
    response: Response,
    force: bool = Query(False, description="忽略缓存并同步刷新"),
    refresh: bool = Query(False, description="返回已有数据并在后台静默刷新"),
):
    # Even the legacy/public response must not survive a later account switch
    # in the browser or a shared HTTP cache. Server-side market caching remains.
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie, Authorization"
    service = HomeDashboardService()
    payload = service.dashboard(force=force, refresh=refresh)
    user_id = int(getattr(request.state, "user_id", 0) or 0)
    if user_id <= 0:
        return payload
    from src.services.user_account_service import UserAccountService
    result = dict(payload)
    result["watchlist"] = service.watchlist_for_symbols(
        UserAccountService().list_watchlist(user_id), cached_cards=payload.get("watchlist"),
    )
    return result
