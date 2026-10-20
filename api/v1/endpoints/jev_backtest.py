"""Private, explicit paid submissions and side-effect-free replay reads."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.v1.endpoints.user_auth import _resolve_request_user
from api.v1.schemas.jev_backtest import JevBacktestRequest
from src.services.jev_backtest_service import JevBacktestService, capabilities

router = APIRouter()


def owner(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Vary"] = "Cookie, Authorization"
    user, method = _resolve_request_user(request)
    if method == "admin":
        return "admin"
    if user is not None and user.status == "approved":
        return f"user:{user.id}"
    raise HTTPException(401, detail={"error": "authentication_required", "message": "请登录后使用 Jev 回测"})


@router.get("/capabilities")
def get_capabilities(owner_id: str = Depends(owner)):
    return capabilities()


@router.post("/tasks", status_code=202)
def create_task(payload: JevBacktestRequest, owner_id: str = Depends(owner)):
    try:
        return JevBacktestService.instance().submit(owner_id, payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(409, detail={"error": "replay_conflict", "message": str(exc)}) from None


@router.get("/tasks")
def list_tasks(limit: int = Query(20, ge=1, le=100), owner_id: str = Depends(owner)):
    items = JevBacktestService.instance().repo.list(owner_id, limit)
    return {"items": items}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, owner_id: str = Depends(owner)):
    result = JevBacktestService.instance().repo.get(owner_id, task_id)
    if result is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "回测任务不存在"})
    return result


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, owner_id: str = Depends(owner)):
    result = JevBacktestService.instance().cancel(owner_id, task_id)
    if result is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "回测任务不存在"})
    return result
