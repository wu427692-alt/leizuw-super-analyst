"""Private paper observations and explicit owner-authorized continuous control."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from urllib.parse import urlsplit

from api.v1.endpoints.jev_backtest import owner
from src.services.jev_live_t_store import LiveTStore
from src.services.jev_live_t_controller import controller, live_t_lifespan

router = APIRouter(lifespan=live_t_lifespan)


class LiveTControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.get("/control")
def get_control(owner_id: str = Depends(owner)):
    return controller().view(owner_id)


@router.post("/control")
def set_control(payload: LiveTControlRequest, request: Request, owner_id: str = Depends(owner)):
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.url.netloc:
            raise HTTPException(403, detail={"error": "origin_rejected", "message": "请从本站页面操作开关"})
    try:
        return controller().set_enabled(owner_id, payload.enabled)
    except PermissionError:
        raise HTTPException(403, detail={"error": "administrator_required", "message": "此纸上账户需要管理员权限"}) from None
    except ValueError as exc:
        message = {"model_budget_exhausted": "模型费用额度已用完，不能重置费用后继续",
                   "wait_until_stopped": "正在结算停止中的请求，请稍后再开启",
                   "server_is_shutting_down": "服务正在重启，请稍后再试"}.get(str(exc), "持续账户配置或状态异常")
        raise HTTPException(409, detail={"error": "continuous_control_conflict", "message": message}) from None


def observed_run(run: dict, now: datetime | None = None) -> dict:
    """An abandoned producer cannot keep displaying a live session indefinitely."""
    if run.get("status") not in {"starting", "running"}:
        return run
    now = now or datetime.now(timezone.utc)
    try:
        updated = datetime.fromisoformat(str(run["updated_at"]).replace("Z", "+00:00"))
        age = (now - updated).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = float("inf")
    if 0 <= age <= 15:
        return run
    projected = deepcopy(run)
    projected["status"] = "paused"
    projected["message"] = "模拟进程心跳暂未更新，等待恢复；当前数据不能视作实时。"
    if isinstance(projected.get("quote"), dict):
        projected["quote"]["stale"] = True
    return projected


@router.get("/status")
def status(owner_id: str = Depends(owner)):
    runs = LiveTStore().list(owner_id, 1)
    return {"available": bool(runs), "message": "当前账号的实时模拟记录" if runs else "当前账号暂无试跑；记录按账号隔离，管理员发起的试跑仅管理员可见。",
            "server_time": datetime.now(timezone.utc).isoformat(), "poll_interval_ms": 2000,
            "current_run": observed_run(runs[0]) if runs else None}


@router.get("/runs")
def runs(owner_id: str = Depends(owner)):
    return {"items": [{key: row.get(key) for key in ("run_id", "status", "config", "created_at", "updated_at")}
                      for row in map(observed_run, LiveTStore().list(owner_id))]}


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, owner_id: str = Depends(owner)):
    run = LiveTStore().get(owner_id, str(run_id))
    if run is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "实时模拟记录不存在"})
    return observed_run(run)
