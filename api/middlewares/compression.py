"""按路径选择是否压缩。

starlette 的 GZipMiddleware 不区分 content-type，会把 zip/pdf/xlsx 这类已经压缩的
下载再 gzip 一遍：Content-Length 被删成 chunked，Range 语义被破坏（断点续传拿到的
是 gzip 流的字节偏移），同时把压缩 CPU 烧在单 worker 的事件循环上。下载与导出路径
因此跳过压缩。
"""

from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# 命中这些片段的请求不做压缩：都是文件下载 / 导出产物
UNCOMPRESSED_PATH_MARKERS: tuple[str, ...] = (
    "/download",
    "/export",
    "/package",
    "/batch-download",
    "/media",
)


class PathAwareGZipMiddleware:
    """文本与 JSON 走 gzip，下载/导出原样透传。"""

    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 5) -> None:
        self.app = app
        self._compressing = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if path and any(marker in path for marker in UNCOMPRESSED_PATH_MARKERS):
            await self.app(scope, receive, send)
            return
        await self._compressing(scope, receive, send)
