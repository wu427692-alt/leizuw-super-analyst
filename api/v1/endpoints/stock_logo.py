# -*- coding: utf-8 -*-
"""
===================================
股票公司 Logo 接口
===================================

职责：
1. GET /api/v1/stocks/{stock_code}/logo 返回 A 股公司的公司标识（logo 图片）

数据来源与缓存策略：
- 真实 logo 来源是同花顺 F10 公司资料页（https://basic.10jqka.com.cn/{code}/company.html）
  内嵌的公司 logo 图。该页面与图片都公开可访问、无需密钥，但图片地址是按公司分配的
  UUID，无法直接用股票代码拼出来，所以只能先从页面里解析出图片地址。
- 首次请求某代码时抓取一次并写入本地缓存目录（默认 ``data/stock_logos``），
  之后直接读本地文件，不再访问上游；缓存文件随部署一起持久化。
- 抓取失败 / 没有 logo（非 A 股、退市、页面结构变化）一律返回 404，由前端
  降级为公司名首字色块。本接口绝不返回占位图或破图。
- 失败结果做进程内负缓存（6 小时），避免前端每次挂载都打一次上游。

注意：公司 logo 的版权归各公司所有，这里只做展示用途，不做再分发。
"""

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from fastapi import APIRouter, HTTPException, Response

logger = logging.getLogger(__name__)

router = APIRouter()

_UPSTREAM_PAGE_URL = "https://basic.10jqka.com.cn/{code}/company.html"
# 同花顺公司 logo CDN：https://basic.10jqka.com.cn/ai_data/logo/company/<uuid>.png
_LOGO_URL_PATTERN = re.compile(
    r"https://basic\.10jqka\.com\.cn/ai_data/logo/company/[A-Za-z0-9._~/-]+?\.(?:png|jpe?g|webp|gif)",
    re.IGNORECASE,
)
_A_SHARE_CODE_PATTERN = re.compile(r"^(?:sh|sz|bj)?(\d{6})$", re.IGNORECASE)
_MARKET_SUFFIX_PATTERN = re.compile(r"\.(?:sh|sz|bj)$", re.IGNORECASE)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REQUEST_TIMEOUT: Tuple[float, float] = (3.05, 8)
_MAX_IMAGE_BYTES = 1024 * 1024
_MISS_TTL_SECONDS = 6 * 60 * 60
_HIT_CACHE_CONTROL = "public, max-age=604800"  # 7 天，公司 logo 极少变化
_UPSTREAM_CONCURRENCY = 4
_CACHE_DIR = Path("data") / "stock_logos"

_miss_lock = threading.Lock()
_misses: Dict[str, float] = {}
_code_locks_lock = threading.Lock()
_code_locks: Dict[str, threading.Lock] = {}
_upstream_slots = threading.BoundedSemaphore(_UPSTREAM_CONCURRENCY)


def normalize_a_share_code(symbol: str) -> Optional[str]:
    """把 300750 / sz300750 / 600519.SH 统一成 6 位 A 股代码；非 A 股返回 None。"""
    text = (symbol or "").strip()
    if not text:
        return None
    text = _MARKET_SUFFIX_PATTERN.sub("", text)
    match = _A_SHARE_CODE_PATTERN.match(text)
    return match.group(1) if match else None


def _cache_file(code: str) -> Path:
    return _CACHE_DIR / f"{code}.bin"


def _sniff_image_media_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_cached_image(code: str) -> Optional[Tuple[bytes, str]]:
    path = _cache_file(code)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    media_type = _sniff_image_media_type(data)
    if media_type is None:
        # 缓存文件损坏时丢弃，避免把脏数据当图片返回。
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data, media_type


def _write_cached_image(code: str, data: bytes) -> None:
    try:
        path = _cache_file(code)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f".{code}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)
    except OSError as exc:
        # 缓存写不进去不影响本次响应，只是下次还要再抓一次。
        logger.warning("写入 stock logo 缓存失败: code=%s error=%s", code, exc)


def _download_image(url: str) -> Optional[bytes]:
    response = requests.get(url, headers={"User-Agent": _UA}, timeout=_REQUEST_TIMEOUT, stream=True)
    try:
        if response.status_code != 200:
            logger.info("stock logo 图片请求失败: url=%s status=%s", url, response.status_code)
            return None
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > _MAX_IMAGE_BYTES:
            logger.info("stock logo 图片超过大小上限: url=%s length=%s", url, declared)
            return None
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_IMAGE_BYTES:
                logger.info("stock logo 图片超过大小上限: url=%s", url)
                return None
            chunks.append(chunk)
        return b"".join(chunks) or None
    finally:
        response.close()


def _fetch_remote_image(code: str) -> Optional[Tuple[bytes, str]]:
    """抓取公司资料页并解析 logo 图片；任何一步失败都返回 None。"""
    with _upstream_slots:
        page = requests.get(
            _UPSTREAM_PAGE_URL.format(code=code),
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=_REQUEST_TIMEOUT,
        )
        if page.status_code != 200:
            logger.info("stock logo 公司页请求失败: code=%s status=%s", code, page.status_code)
            return None

        html = page.text
        match = _LOGO_URL_PATTERN.search(html)
        if match is None:
            # 页面是 GBK 编码；logo 地址本身是 ASCII，两个解码方式都试一次更稳。
            html = page.content.decode("gbk", errors="replace")
            match = _LOGO_URL_PATTERN.search(html)
        if match is None:
            logger.info("stock logo 公司页里没有找到 logo: code=%s", code)
            return None

        data = _download_image(match.group(0))
        if data is None:
            return None
        media_type = _sniff_image_media_type(data)
        if media_type is None:
            logger.info("stock logo 返回内容不是图片: code=%s", code)
            return None
        return data, media_type


def _code_lock(code: str) -> threading.Lock:
    with _code_locks_lock:
        lock = _code_locks.get(code)
        if lock is None:
            lock = threading.Lock()
            _code_locks[code] = lock
        return lock


def _is_recent_miss(code: str) -> bool:
    with _miss_lock:
        missed_at = _misses.get(code)
    return missed_at is not None and (time.monotonic() - missed_at) < _MISS_TTL_SECONDS


def _remember_miss(code: str) -> None:
    with _miss_lock:
        _misses[code] = time.monotonic()


def _logo_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "logo_not_found", "message": "暂无该公司 logo"},
        headers={"Cache-Control": "no-store"},
    )


def _image_response(data: bytes, media_type: str) -> Response:
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": _HIT_CACHE_CONTROL,
            "X-Content-Type-Options": "nosniff",
        },
    )


def _load_logo(code: str) -> Optional[Tuple[bytes, str]]:
    cached = _read_cached_image(code)
    if cached is not None:
        return cached
    if _is_recent_miss(code):
        return None

    with _code_lock(code):
        # 等锁期间别的请求可能已经把图抓回来了。
        cached = _read_cached_image(code)
        if cached is not None:
            return cached
        if _is_recent_miss(code):
            return None
        try:
            fetched = _fetch_remote_image(code)
        except requests.RequestException as exc:
            logger.info("抓取 stock logo 失败: code=%s error=%s", code, exc)
            fetched = None
        if fetched is None:
            _remember_miss(code)
            return None
        _write_cached_image(code, fetched[0])
        return fetched


@router.get(
    "/{stock_code}/logo",
    summary="获取股票公司 logo",
    description=(
        "返回 A 股公司的公司标识图片（本地缓存优先，首次回源同花顺公司资料页）。"
        "非 A 股代码或上游没有 logo 时返回 404，由前端降级为首字色块。"
    ),
    responses={
        200: {"description": "公司 logo 图片", "content": {"image/png": {}}},
        404: {"description": "没有可用的公司 logo"},
    },
)
def get_stock_logo(stock_code: str) -> Response:
    code = normalize_a_share_code(stock_code)
    if code is None:
        raise _logo_not_found()
    logo = _load_logo(code)
    if logo is None:
        raise _logo_not_found()
    return _image_response(*logo)
