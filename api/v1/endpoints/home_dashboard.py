# -*- coding: utf-8 -*-
"""Market command-center endpoint for the Web home page."""

from fastapi import APIRouter, Query, Request

from src.services.home_dashboard_service import HomeDashboardService

router = APIRouter()


@router.get("", summary="聚合A股、海外市场、自选股和投资情报首页大看板")
def dashboard(
    request: Request,
    force: bool = Query(False, description="忽略缓存并同步刷新"),
    refresh: bool = Query(False, description="返回已有数据并在后台静默刷新"),
):
    payload = HomeDashboardService().dashboard(force=force, refresh=refresh)
    user_id = int(getattr(request.state, "user_id", 0) or 0)
    if user_id <= 0:
        return payload
    from src.services.user_account_service import UserAccountService
    wanted = {
        str(symbol or "").upper().split(".", 1)[0].removeprefix("SH").removeprefix("SZ").removeprefix("BJ")
        for symbol in UserAccountService().list_watchlist(user_id)
    }
    result = dict(payload)
    result["watchlist"] = [
        item for item in payload.get("watchlist", [])
        if str(item.get("symbol") or "").upper().split(".", 1)[0].removeprefix("SH").removeprefix("SZ").removeprefix("BJ") in wanted
    ]
    return result
