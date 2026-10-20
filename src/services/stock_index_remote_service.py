# -*- coding: utf-8 -*-
"""Best-effort remote cache for the generated stock autocomplete index."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STOCK_INDEX_REMOTE_URL = (
    "https://raw.githubusercontent.com/ZhuLinsen/daily_stock_analysis/"
    "main/apps/dsa-web/public/stocks.index.json"
)
DEFAULT_STOCK_INDEX_CACHE_PATH = REPO_ROOT / "data" / "cache" / "stocks.index.json"
DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS = 48
DEFAULT_STOCK_INDEX_REMOTE_TIMEOUT_SECONDS = 10
DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES = 3
STOCK_DIRECTORY_TTL_SECONDS = 3600
STOCK_DIRECTORY_RETRY_SECONDS = 300
SUPPORTED_STOCK_INDEX_MARKETS = {"CN", "HK", "US", "BSE", "JP", "KR"}

_REMOTE_REFRESH_LOCK = Lock()
_REMOTE_FAILURE_LOCK = Lock()
_REMOTE_CONSECUTIVE_FAILURES = 0
_REMOTE_SUPPRESS_UNTIL = 0.0
_DIRECTORY_SCHEDULE_LOCK = Lock()
_DIRECTORY_REFRESH_THREAD: Optional[Thread] = None


@dataclass(frozen=True)
class RemoteStockIndexSettings:
    """Runtime settings for remote stock-index refresh."""

    enabled: bool = True
    url: str = DEFAULT_STOCK_INDEX_REMOTE_URL
    ttl_hours: int = DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS
    timeout_seconds: int = DEFAULT_STOCK_INDEX_REMOTE_TIMEOUT_SECONDS
    cache_path: Path = DEFAULT_STOCK_INDEX_CACHE_PATH
    live_directory_enabled: bool = False


@dataclass(frozen=True)
class RemoteStockIndexResult:
    """Outcome of a best-effort refresh attempt."""

    cache_path: Optional[Path]
    refreshed: bool = False
    skipped: bool = False
    error: Optional[str] = None


def settings_from_config(config: Any) -> RemoteStockIndexSettings:
    """Build remote stock-index settings from the application config object."""
    return RemoteStockIndexSettings(
        enabled=bool(getattr(config, "stock_index_remote_update_enabled", True)),
        url=DEFAULT_STOCK_INDEX_REMOTE_URL,
        ttl_hours=DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS,
        timeout_seconds=DEFAULT_STOCK_INDEX_REMOTE_TIMEOUT_SECONDS,
        live_directory_enabled=bool(getattr(config, "tushare_token", None)),
    )


def get_remote_stock_index_cache_path() -> Path:
    """Return the canonical on-disk cache path for remote stock index data."""
    return DEFAULT_STOCK_INDEX_CACHE_PATH


def is_remote_stock_index_cache_fresh(
    cache_path: Path = DEFAULT_STOCK_INDEX_CACHE_PATH,
    *,
    ttl_hours: int = DEFAULT_STOCK_INDEX_REMOTE_TTL_HOURS,
    now: Optional[float] = None,
) -> bool:
    """Return whether the remote cache exists and is still inside its TTL."""
    if ttl_hours <= 0 or not cache_path.is_file():
        return False

    current_time = time.time() if now is None else now
    try:
        age_seconds = current_time - cache_path.stat().st_mtime
    except OSError:
        return False
    return age_seconds < ttl_hours * 3600


def validate_stock_index_payload(
    payload: Any,
    *,
    min_items: int = 100,
) -> list[list[Any]]:
    """Validate the compressed ``stocks.index.json`` wire format."""
    if not isinstance(payload, list):
        raise ValueError("stock index payload must be a list")
    if len(payload) < min_items:
        raise ValueError(f"stock index payload is unexpectedly small: {len(payload)}")

    for index, item in enumerate(payload):
        if not isinstance(item, list) or len(item) < 10:
            raise ValueError(f"stock index item {index} is not a compressed tuple")

        (
            canonical_code,
            display_code,
            name,
            _pinyin,
            _abbr,
            aliases,
            market,
            asset_type,
            active,
            popularity,
        ) = item[:10]
        if not all(isinstance(value, str) and value.strip() for value in (canonical_code, display_code, name)):
            raise ValueError(f"stock index item {index} is missing code or name")
        if not isinstance(aliases, list):
            raise ValueError(f"stock index item {index} aliases must be a list")
        if market not in SUPPORTED_STOCK_INDEX_MARKETS:
            raise ValueError(f"stock index item {index} has unsupported market: {market!r}")
        if asset_type not in {"stock", "index", "etf"}:
            raise ValueError(f"stock index item {index} has unsupported asset type: {asset_type!r}")
        if not isinstance(active, bool):
            raise ValueError(f"stock index item {index} active flag must be boolean")
        if (
            isinstance(popularity, bool)
            or not isinstance(popularity, (int, float))
            or not math.isfinite(float(popularity))
        ):
            raise ValueError(f"stock index item {index} popularity must be a finite number")

    return payload


def is_valid_remote_stock_index_file(cache_path: Path = DEFAULT_STOCK_INDEX_CACHE_PATH) -> bool:
    """Return whether a cached remote stock-index file is still usable."""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        validate_stock_index_payload(payload)
        return True
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("[stock-index] cached remote index is invalid: %s", exc)
        return False


def _download_remote_stock_index(settings: RemoteStockIndexSettings) -> bytes:
    response = requests.get(settings.url, timeout=settings.timeout_seconds)
    response.raise_for_status()

    content = response.content
    payload = json.loads(content.decode("utf-8"))
    validate_stock_index_payload(payload)
    return content


def _atomic_write(cache_path: Path, content: bytes) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(content)
        os.replace(temp_path, cache_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _clear_backend_stock_index_cache() -> None:
    try:
        from src.data.stock_index_loader import clear_stock_index_cache

        clear_stock_index_cache()
    except Exception as exc:  # noqa: BLE001 - cache clearing must not break refresh.
        logger.warning("[stock-index] remote index refreshed but backend cache clear failed: %s", exc)


def _reset_remote_failure_state() -> None:
    global _REMOTE_CONSECUTIVE_FAILURES, _REMOTE_SUPPRESS_UNTIL
    with _REMOTE_FAILURE_LOCK:
        _REMOTE_CONSECUTIVE_FAILURES = 0
        _REMOTE_SUPPRESS_UNTIL = 0.0


def _remote_refresh_suppressed(now: float) -> bool:
    global _REMOTE_CONSECUTIVE_FAILURES, _REMOTE_SUPPRESS_UNTIL
    with _REMOTE_FAILURE_LOCK:
        if _REMOTE_CONSECUTIVE_FAILURES < DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES:
            return False
        if now < _REMOTE_SUPPRESS_UNTIL:
            return True
        _REMOTE_CONSECUTIVE_FAILURES = 0
        _REMOTE_SUPPRESS_UNTIL = 0.0
        return False


def _record_remote_failure(now: float, ttl_hours: int) -> int:
    global _REMOTE_CONSECUTIVE_FAILURES, _REMOTE_SUPPRESS_UNTIL
    with _REMOTE_FAILURE_LOCK:
        _REMOTE_CONSECUTIVE_FAILURES += 1
        if _REMOTE_CONSECUTIVE_FAILURES >= DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES:
            _REMOTE_SUPPRESS_UNTIL = now + max(ttl_hours, 1) * 3600
        return _REMOTE_CONSECUTIVE_FAILURES


def refresh_remote_stock_index_cache(settings: RemoteStockIndexSettings) -> RemoteStockIndexResult:
    """Refresh the remote stock index cache without breaking callers on failure."""
    if settings.live_directory_enabled:
        return refresh_live_stock_directory(settings)
    cache_path = settings.cache_path
    if not settings.enabled:
        return RemoteStockIndexResult(cache_path=cache_path if cache_path.is_file() else None, skipped=True)
    if _read_directory_metadata(cache_path).get("source") == "tushare.stock_basic":
        # Losing a token must not replace a verified/newer official directory
        # with an older static GitHub seed on a later fallback refresh.
        return RemoteStockIndexResult(cache_path=cache_path if cache_path.is_file() else None,
                                      skipped=True, error="official directory refresh is not configured")

    current_time = time.time()
    if is_remote_stock_index_cache_fresh(cache_path, ttl_hours=settings.ttl_hours, now=current_time):
        if is_valid_remote_stock_index_file(cache_path):
            _reset_remote_failure_state()
            return RemoteStockIndexResult(cache_path=cache_path, skipped=True)

    if _remote_refresh_suppressed(current_time):
        return RemoteStockIndexResult(
            cache_path=cache_path if is_valid_remote_stock_index_file(cache_path) else None,
            skipped=True,
            error="remote update temporarily suppressed after repeated failures",
        )

    if not _REMOTE_REFRESH_LOCK.acquire(blocking=False):
        return RemoteStockIndexResult(
            cache_path=cache_path if is_valid_remote_stock_index_file(cache_path) else None,
            skipped=True,
        )

    try:
        if is_remote_stock_index_cache_fresh(cache_path, ttl_hours=settings.ttl_hours):
            if is_valid_remote_stock_index_file(cache_path):
                _reset_remote_failure_state()
                return RemoteStockIndexResult(cache_path=cache_path, skipped=True)

        content = _download_remote_stock_index(settings)
        _atomic_write(cache_path, content)
        _clear_backend_stock_index_cache()
        _reset_remote_failure_state()
        _write_directory_metadata(cache_path, {"source": "github_seed", "status": "seed_downloaded",
                                              "last_download_at": _iso_time(current_time),
                                              "last_success_at": None, "as_of_date": None})
        logger.info("[stock-index] remote index refreshed: %s", cache_path)
        return RemoteStockIndexResult(cache_path=cache_path, refreshed=True)
    except Exception as exc:  # noqa: BLE001 - remote refresh is best-effort by design.
        message = str(exc)
        failures = _record_remote_failure(current_time, settings.ttl_hours)
        logger.warning(
            "[stock-index] remote update failed (%d/%d), using local fallback: %s",
            failures,
            DEFAULT_STOCK_INDEX_REMOTE_MAX_FAILURES,
            message,
        )
        if is_valid_remote_stock_index_file(cache_path):
            return RemoteStockIndexResult(cache_path=cache_path, error=message)
        return RemoteStockIndexResult(cache_path=None, error=message)
    finally:
        _REMOTE_REFRESH_LOCK.release()


def _iso_time(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _metadata_path(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.stem}.meta.json")


def _read_directory_metadata(cache_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_metadata_path(cache_path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _next_attempt_epoch(metadata: dict[str, Any]) -> float:
    try:
        return float(metadata.get("next_attempt_epoch") or 0)
    except (TypeError, ValueError):
        return 0.0


def _write_directory_metadata(cache_path: Path, metadata: dict[str, Any]) -> None:
    _atomic_write(_metadata_path(cache_path), json.dumps(metadata, ensure_ascii=False).encode("utf-8"))


def stock_directory_status(cache_path: Path | None = None, *, now: float | None = None) -> dict[str, Any]:
    """Read synchronization metadata only; never fetch, scan rows or block a request.

    A GitHub seed's download time is deliberately not an official directory
    verification timestamp. ``as_of_date`` remains unknown for seed downloads.
    """
    path = cache_path or get_remote_stock_index_cache_path()
    metadata = _read_directory_metadata(path)
    current = time.time() if now is None else now
    verified = metadata.get("source") == "tushare.stock_basic" and bool(metadata.get("last_success_at"))
    try:
        success_epoch = float(metadata.get("last_success_epoch") or 0)
    except (TypeError, ValueError):
        success_epoch = 0.0
    return {**metadata, "status": str(metadata.get("status") or "not_verified"),
            "source": metadata.get("source") or "bundled_seed",
            "last_success_at": metadata.get("last_success_at"),
            "actual_success_at": metadata.get("last_success_at"),
            "as_of_date": metadata.get("as_of_date"),
            "stale": not verified or current - success_epoch >= STOCK_DIRECTORY_TTL_SECONDS,
            "cache_available": path.is_file()}


def _lock_directory_file(handle: Any) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        return False


def _unlock_directory_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fetch_tushare_directory() -> dict[str, list[dict[str, Any]]]:
    from src.services.financial_data_service import TushareGatewayService

    gateway = TushareGatewayService()
    return {status: gateway.query("stock_basic", params={"exchange": "", "list_status": status},
                                  fields="ts_code,symbol,name,fullname,list_status,list_date,delist_date")["rows"]
            for status in ("L", "P", "D")}


def _merge_tushare_directory(
    existing: list[list[Any]], groups: dict[str, list[dict[str, Any]]], *, min_listed_items: int = 1000,
) -> list[list[Any]]:
    """Merge an all-or-nothing official directory into the existing wire format."""
    if set(groups) != {"L", "P", "D"} or len(groups["L"]) < min_listed_items:
        raise ValueError("stock_basic directory is incomplete")
    previous = {str(row[0]): row for row in existing if isinstance(row, list) and len(row) >= 10}
    previous_active = sum(row[6] in {"CN", "BSE"} and row[7] == "stock" and row[8] is True
                          for row in previous.values())
    if previous_active >= 1000 and len(groups["L"]) < previous_active * .8:
        raise ValueError("stock_basic active directory unexpectedly shrank")
    merged = {code: list(row) for code, row in previous.items()}
    seen: set[str] = set()
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        Style = lazy_pinyin = None
    for status, records in groups.items():
        if not isinstance(records, list):
            raise ValueError("stock_basic group is not a list")
        for item in records:
            code = str(item.get("ts_code") or "").strip().upper()
            name = str(item.get("name") or "").strip()
            if status == "D" and re.fullmatch(r"T\d{6}\.(SH|SZ|BJ)", code):
                # Tushare preserves a few pre-relisting historical identities
                # with a T prefix. They are not six-digit tradable securities;
                # never strip T and overwrite a current listing of that code.
                # Count separately in sync metadata, preserve any old row.
                continue
            if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) or not name or code in seen:
                raise ValueError("stock_basic directory contains invalid or duplicate security")
            if item.get("list_status") and str(item["list_status"]) != status:
                raise ValueError("stock_basic group has mismatching listing status")
            seen.add(code)
            old = previous.get(code)
            aliases = [*(old[5] if old and isinstance(old[5], list) else []), str(item.get("fullname") or "").strip()]
            if old and str(old[2]) != name:
                aliases.append(str(old[2]))
            aliases = list(dict.fromkeys(alias for alias in aliases if alias and alias != name))
            if lazy_pinyin:
                pinyin = "".join(lazy_pinyin(name))
                abbr = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))
            else:
                pinyin, abbr = (old[3], old[4]) if old and old[2] == name else ("", "")
            merged[code] = [code, code.split(".")[0], name, pinyin, abbr, aliases,
                            "BSE" if code.endswith(".BJ") else "CN", "stock", status == "L",
                            old[9] if old else 100]
    # Do not delete old CN rows on absence: an omission is not proof of delisting.
    # Explicit D/P rows above are inactive, while HK/US/indices/ETF stay untouched.
    return list(merged.values())


def refresh_live_stock_directory(
    settings: RemoteStockIndexSettings, *, fetcher: Any = None, min_listed_items: int = 1000,
) -> RemoteStockIndexResult:
    """Refresh verified A-share identities; old data survives any upstream error."""
    path = settings.cache_path
    if not settings.enabled:
        return RemoteStockIndexResult(cache_path=path if path.is_file() else None, skipped=True)
    current = time.time()
    meta = _read_directory_metadata(path)
    if _next_attempt_epoch(meta) > current:
        return RemoteStockIndexResult(cache_path=path if path.is_file() else None, skipped=True)
    if not _REMOTE_REFRESH_LOCK.acquire(blocking=False):
        return RemoteStockIndexResult(cache_path=path if path.is_file() else None, skipped=True)
    handle = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.with_name(f".{path.name}.refresh.lock").open("a+b")
        locked = _lock_directory_file(handle)
        if not locked:
            return RemoteStockIndexResult(cache_path=path if path.is_file() else None, skipped=True)
        meta = _read_directory_metadata(path)
        if _next_attempt_epoch(meta) > current:
            return RemoteStockIndexResult(cache_path=path if path.is_file() else None, skipped=True)
        meta = {**meta, "status": "refreshing", "last_attempt_at": _iso_time(current),
                "next_attempt_epoch": current + STOCK_DIRECTORY_RETRY_SECONDS}
        _write_directory_metadata(path, meta)
        if is_valid_remote_stock_index_file(path):
            existing = json.loads(path.read_text(encoding="utf-8"))
        else:
            from src.data.stock_index_loader import get_stock_entity_index_records

            existing = get_stock_entity_index_records()
        groups = (fetcher or _fetch_tushare_directory)()
        merged = _merge_tushare_directory(existing, groups, min_listed_items=min_listed_items)
        validate_stock_index_payload(merged)
        _atomic_write(path, json.dumps(merged, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        completed = time.time()
        success_metadata = {"source": "tushare.stock_basic", "status": "ok",
                                          "last_attempt_at": _iso_time(current), "last_success_at": _iso_time(completed),
                                          "last_success_epoch": completed,
                                          "as_of_date": None,
                                          "next_attempt_epoch": completed + STOCK_DIRECTORY_TTL_SECONDS,
                                          "next_refresh_at": _iso_time(completed + STOCK_DIRECTORY_TTL_SECONDS),
                                          "listed_count": len(groups["L"]), "pending_count": len(groups["P"]),
                                          "delisted_count": len(groups["D"]), "row_count": len(merged),
                                          "historical_nonstandard_count": sum(bool(re.fullmatch(r"T\d{6}\.(SH|SZ|BJ)", str(item.get("ts_code") or ""))) for item in groups["D"])}
        try:
            _write_directory_metadata(path, success_metadata)
        except OSError:
            logger.warning("[stock-index] directory updated; refresh metadata could not be persisted")
        _clear_backend_stock_index_cache()
        return RemoteStockIndexResult(cache_path=path, refreshed=True)
    except Exception as exc:  # noqa: BLE001 - preserve a last-known-good directory.
        error_code = type(exc).__name__
        try:
            _write_directory_metadata(path, {**meta, "status": "error", "error_code": error_code,
                                              "next_attempt_epoch": current + STOCK_DIRECTORY_RETRY_SECONDS})
        except OSError:
            logger.warning("[stock-index] refresh metadata could not be persisted")
        logger.warning("[stock-index] official directory refresh failed (%s); old directory retained", error_code)
        return RemoteStockIndexResult(cache_path=path if path.is_file() else None, error=error_code)
    finally:
        if handle is not None:
            if locked:
                _unlock_directory_file(handle)
            handle.close()
        _REMOTE_REFRESH_LOCK.release()


def ensure_stock_directory_refresh(settings: RemoteStockIndexSettings | None = None) -> bool:
    """Non-blocking bounded maintenance wakeup for the existing collector loop."""
    global _DIRECTORY_REFRESH_THREAD
    if settings is None:
        from src.config import get_config

        settings = settings_from_config(get_config())
    if not settings.enabled:
        return False
    if settings.live_directory_enabled:
        meta = _read_directory_metadata(settings.cache_path)
        if _next_attempt_epoch(meta) > time.time():
            return False
    elif is_remote_stock_index_cache_fresh(settings.cache_path, ttl_hours=settings.ttl_hours):
        return False
    with _DIRECTORY_SCHEDULE_LOCK:
        if _DIRECTORY_REFRESH_THREAD is not None and _DIRECTORY_REFRESH_THREAD.is_alive():
            return False
        _DIRECTORY_REFRESH_THREAD = Thread(target=refresh_remote_stock_index_cache, args=(settings,),
                                           name="stock-directory-refresh", daemon=True)
        _DIRECTORY_REFRESH_THREAD.start()
        return True


def _reset_remote_stock_index_state_for_tests() -> None:
    _reset_remote_failure_state()
