"""Retired routes fail closed after the normal authentication boundary."""

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.feature_availability import unavailable_feature_for_path


def unavailable_feature_response(path: str, feature: str):
    """One response for both route paths and resolved static-file identities."""
    headers = {"Cache-Control": "no-store, private"}
    if path.startswith("/api/"):
        return JSONResponse(
            status_code=410,
            content={"error": "feature_unavailable", "feature": feature,
                     "message": "Jev 功能暂时下线，历史任务和记录已保留。"},
            headers=headers,
        )
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>功能暂时下线 · 乐子乌超级价值</title>"
        "<body><main><h1>Jev 功能暂时下线</h1>"
        "<p>历史任务和记录已保留，不再接受新任务。</p>"
        "<a href='/app'>返回市场首页</a></main></body></html>",
        status_code=410,
        headers=headers,
    )


class FeatureAvailabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        feature = unavailable_feature_for_path(request.url.path)
        if feature is None:
            return await call_next(request)
        return unavailable_feature_response(request.url.path, feature)
