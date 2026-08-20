from fastapi import APIRouter, HTTPException

from api.v1.schemas.essay_quant import EssayQuantRuleRequest, EssayQuantRunRequest
from src.services.essay_quant_service import EssayQuantError, EssayQuantService

router = APIRouter()


def _error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": "essay_quant_error", "message": str(exc)[:500]})


@router.get("/dashboard", summary="读取最近一次小作文量化回测结果")
def dashboard():
    return EssayQuantService().latest_dashboard()


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
