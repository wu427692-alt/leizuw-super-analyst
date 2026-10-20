"""Authenticated event-time research. Reads never invoke the paid model."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.v1.endpoints.jev_backtest import owner
from api.v1.schemas.jev_convertible_t import JevEventRequest
from src.services.jev_event_service import JevEventService, event_capabilities, research_report, research_round

router = APIRouter()


def event_owner(owner_id: str = Depends(owner)):
    # Separate task/cache namespace keeps legacy minute clients from reading
    # event results under an incompatible schema; identity remains mandatory.
    return "event:" + owner_id


@router.get("/capabilities")
def capabilities(owner_id: str = Depends(event_owner)):
    return event_capabilities()


@router.get("/availability")
def availability(symbol: str = Query("113677", pattern=r"^(?:[A-Z]{1,8}|\d{6}(?:\.(?:SH|SZ))?)$"),
                 start_date: date | None = None, end_date: date | None = None,
                 event_granularity: Literal["tick", "1s"] = "1s",
                 dataset_id: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{1,100}$"),
                 owner_id: str = Depends(event_owner)):
    from src.services.jev_cb_data import availability as inspect
    config = {"symbol": symbol, "event_granularity": event_granularity, "dataset_id": dataset_id}
    if start_date:
        config["start_date"] = start_date.isoformat()
    if end_date:
        config["end_date"] = end_date.isoformat()
    return inspect(config)


@router.get("/research")
def research(owner_id: str = Depends(event_owner)):
    return research_report()


@router.get("/research/rounds/{task_id}")
def get_research_round(task_id: UUID, owner_id: str = Depends(event_owner)):
    sample = research_round(task_id)
    if sample is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "公开研究样本不存在或尚未发布"})
    return sample


@router.post("/tasks", status_code=202)
def create_task(payload: JevEventRequest, owner_id: str = Depends(event_owner)):
    try:
        return JevEventService.instance().submit(owner_id, payload.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(409, detail={"error": "replay_conflict", "message": str(exc)}) from None


@router.get("/tasks")
def list_tasks(limit: int = Query(20, ge=1, le=100), owner_id: str = Depends(event_owner)):
    return {"items": JevEventService.instance().repo.list(owner_id, limit)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, owner_id: str = Depends(event_owner)):
    task = JevEventService.instance().repo.get(owner_id, task_id)
    if task is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "研究任务不存在"})
    return task


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, owner_id: str = Depends(event_owner)):
    task = JevEventService.instance().cancel(owner_id, task_id)
    if task is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "研究任务不存在"})
    return task
