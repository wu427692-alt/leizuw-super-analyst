"""请求耗时观测。

后端的访问日志是关闭的，慢接口无法从日志定位（这正是这次排障主要靠只读探测的原因）。
这里加一个轻量中间件：只记录超过阈值的请求，避免刷爆日志。
"""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

SLOW_REQUEST_MS = 200.0


class RequestTimingMiddleware:
    """记录慢请求的 path / status / 耗时；正常请求不落日志。"""

    def __init__(self, app: ASGIApp, slow_ms: float = SLOW_REQUEST_MS) -> None:
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms >= self.slow_ms:
                logger.warning(
                    "slow request: %s %s -> %s in %.0fms",
                    scope.get("method", "?"),
                    scope.get("path", "?"),
                    status_holder["status"],
                    elapsed_ms,
                )
