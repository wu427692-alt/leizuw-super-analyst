# -*- coding: utf-8 -*-
"""Market command-center endpoint for the Web home page."""

from fastapi import APIRouter, Query

from src.services.home_dashboard_service import HomeDashboardService

router = APIRouter()


@router.get("", summary="聚合A股、海外市场、自选股和投资情报首页大看板")
def dashboard(force: bool = Query(False, description="忽略五分钟缓存并立即刷新")):
    return HomeDashboardService().dashboard(force=force)
