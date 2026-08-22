from fastapi import APIRouter, HTTPException

from api.v1.schemas.essay_quant import (
    EssayQuantExecutePlanRequest,
    EssayQuantNaturalLanguageRequest,
    EssayQuantRuleRequest,
    EssayQuantRunRequest,
    EssayQuantTaskListResponse,
    EssayQuantTaskResponse,
)
from src.services.essay_quant_planner import EssayQuantNaturalLanguagePlanner
from src.services.essay_quant_service import EssayQuantError, EssayQuantService
from src.services.essay_quant_task_service import EssayQuantTaskManager
from src.services.essay_quant_worker import EssayQuantWorker

router = APIRouter()


def _error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": "essay_quant_error", "message": str(exc)[:500]})


@router.get("/dashboard", summary="读取最近一次小作文量化回测结果")
def dashboard():
    return EssayQuantService().latest_dashboard()


@router.get("/research-catalog", summary="读取量化研究方法与真实本地数据资产")
def research_catalog():
    return EssayQuantService().research_catalog()


@router.get("/runs", summary="读取量化研究运行历史")
def runs(limit: int = 30):
    return EssayQuantService().list_runs(limit)


@router.get("/runs/{run_id}", summary="读取本人一次已完成的量化运行结果")
def run_result(run_id: int):
    try:
        return EssayQuantService().get_run(run_id)
    except EssayQuantError as exc:
        raise _error(exc, 404)


@router.post(
    "/tasks",
    status_code=202,
    response_model=EssayQuantTaskResponse,
    summary="提交本人量化后台任务",
)
def create_task(request: EssayQuantRunRequest):
    return EssayQuantTaskManager.get_instance().submit(request.model_dump())


@router.get(
    "/tasks",
    response_model=EssayQuantTaskListResponse,
    summary="读取本人量化后台任务",
)
def list_tasks(limit: int = 50):
    return EssayQuantTaskManager.get_instance().list_tasks(limit)


@router.get(
    "/tasks/{task_id}",
    response_model=EssayQuantTaskResponse,
    summary="读取本人量化后台任务状态",
)
def task_status(task_id: str):
    task = EssayQuantTaskManager.get_instance().get_task(task_id)
    if task is None:
        raise _error(EssayQuantError("量化后台任务不存在"), 404)
    return task


@router.post("/natural-language/plan", summary="用大模型生成受约束量化研究方案与模板代码")
def natural_language_plan(request: EssayQuantNaturalLanguageRequest):
    try:
        return EssayQuantNaturalLanguagePlanner().plan(request.prompt)
    except EssayQuantError as exc:
        raise _error(exc, 422)


@router.post("/natural-language/execute", summary="确认并执行已校验的自然语言研究方案")
def natural_language_execute(request: EssayQuantExecutePlanRequest):
    try:
        return EssayQuantNaturalLanguagePlanner().execute(
            request.rule.model_dump(), refresh_prices=request.refresh_prices,
        )
    except EssayQuantError as exc:
        raise _error(exc, 422)


@router.get("/institution-dashboard", summary="读取后台全机构小作文量化基线")
def institution_dashboard():
    return EssayQuantService().latest_institution_dashboard()


@router.get("/precompute/status", summary="读取机构排名后台预计算状态")
def precompute_status():
    return EssayQuantWorker.get_instance().status()


@router.post("/precompute/run", summary="立即后台预计算全量机构排名")
def precompute_run():
    worker = EssayQuantWorker.get_instance()
    worker.start()
    return worker.request_refresh("manual", force=True)


@router.get("/rules", summary="读取自定义小作文量化规则")
def list_rules():
    return EssayQuantService().list_rules()


@router.post("/rules", summary="保存自定义小作文量化规则")
def create_rule(request: EssayQuantRuleRequest):
    return EssayQuantService().save_rule(request.model_dump())


@router.put("/rules/{rule_id}", summary="更新自定义小作文量化规则")
def update_rule(rule_id: int, request: EssayQuantRuleRequest):
    try:
        return EssayQuantService().save_rule(request.model_dump(), rule_id)
    except EssayQuantError as exc:
        raise _error(exc, 404)


@router.post("/run", summary="运行并保存小作文事件研究与组合回测")
def run(request: EssayQuantRunRequest):
    payload = request.model_dump()
    return EssayQuantService().run(
        payload,
        refresh_prices=request.refresh_prices,
        max_symbols=request.max_symbols,
        persist=True,
        rule_id=request.rule_id,
    )
