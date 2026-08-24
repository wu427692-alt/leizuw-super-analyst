# -*- coding: utf-8 -*-
"""Unified financial-data gateway for Tushare and MCP-synchronized research notes."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from data_provider.tushare_fetcher import _TushareHttpClient
from src.config import get_config
from src.research_note_fingerprint import research_note_information_hash
from src.repositories.research_note_repo import ResearchNoteRepository
from src.storage import ResearchNote, to_utc_naive_datetime, utc_naive_now

_API_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STOCK_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?:\.(SH|SS|SZ|BJ))?(?!\d)", re.IGNORECASE)
_MAX_IMPORT_BATCH = 200
_MAX_TOPIC_BYTES = 2 * 1024 * 1024
_SHANGHAI_TZ = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


class FinancialDataValidationError(ValueError):
    """The caller supplied an invalid financial-data request."""


class FinancialDataUpstreamError(RuntimeError):
    """An upstream data source failed without exposing its credentials."""


class ResearchNoteNotFoundError(LookupError):
    """Requested research note does not exist."""


def _parse_datetime(value: Any, *, field_name: str) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_utc_naive_datetime(value)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raw = str(value).strip()
    try:
        date_only = bool(re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", raw))
        if re.fullmatch(r"\d{8}", raw):
            parsed = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=_SHANGHAI_TZ)
            if field_name == "created_to":
                parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return to_utc_naive_datetime(parsed)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI_TZ)
        if date_only and field_name == "created_to":
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return to_utc_naive_datetime(parsed)
    except ValueError as exc:
        raise FinancialDataValidationError(f"invalid {field_name}: {value}") from exc


def _strip_url_signature(value: str) -> str:
    """Keep a stable media locator while dropping temporary signed query parameters."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _sanitize_payload(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitize_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload(item, key) for item in value]
    if isinstance(value, str) and (key == "url" or key.endswith("_url")):
        return _strip_url_signature(value)
    return value


def _normalize_ts_code(code: str, exchange: Optional[str] = None) -> str:
    digits = code.strip()
    suffix = (exchange or "").upper().replace("SS", "SH")
    if not suffix:
        if digits.startswith(("4", "8")):
            suffix = "BJ"
        elif digits.startswith(("6", "9")):
            suffix = "SH"
        else:
            suffix = "SZ"
    return f"{digits}.{suffix}"


def _extract_symbols(text: str) -> List[str]:
    found = {
        _normalize_ts_code(match.group(1), match.group(2))
        for match in _STOCK_CODE_PATTERN.finditer(text or "")
    }
    return sorted(found)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _json_load(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class ResearchNoteService:
    """Normalize, store and query research notes received from the ZSXQ MCP."""

    def __init__(self, repository: Optional[ResearchNoteRepository] = None):
        self.repo = repository or ResearchNoteRepository()

    def import_topics(
        self,
        topics: Iterable[Dict[str, Any]],
        *,
        group_id: Optional[str] = None,
        group_name: Optional[str] = None,
        enqueue_analysis: bool = True,
    ) -> Dict[str, Any]:
        raw_topics = list(topics)
        if len(raw_topics) > _MAX_IMPORT_BATCH:
            raise FinancialDataValidationError(
                f"a single import may contain at most {_MAX_IMPORT_BATCH} topics"
            )
        normalized = [
            self._normalize_topic(topic, group_id=group_id, group_name=group_name)
            for topic in raw_topics
        ]
        stats = self.repo.upsert_notes(normalized)
        # MCP ingestion remains available even when the optional analysis queue is
        # unavailable.  A running essay worker will pick these durable tasks up
        # immediately; changed notes are reset by their content hash.
        changed_topic_ids = list(stats.pop("_changed_topic_ids", []))
        queue_stats = {"created": 0, "reset": 0, "unchanged": 0}
        if enqueue_analysis:
            try:
                from src.services.essay_analysis_service import EssayAnalysisService

                queue_stats = (
                    EssayAnalysisService().enqueue_topic_ids(changed_topic_ids)
                    if changed_topic_ids else queue_stats
                )
            except Exception as exc:  # noqa: BLE001 - ingestion must not depend on AI availability.
                logger.warning("Failed to enqueue imported research notes for analysis: %s", exc)
        return {
            "received": len(raw_topics),
            "saved": stats["created"] + stats["updated"],
            "analysis_queue": queue_stats,
            **stats,
        }

    def import_mcp_page(
        self,
        payload: Dict[str, Any],
        *,
        group_id: Optional[str] = None,
        group_name: Optional[str] = None,
        enqueue_analysis: bool = True,
    ) -> Dict[str, Any]:
        if payload.get("success") is False:
            message = str(payload.get("error") or "ZSXQ MCP returned an unsuccessful response")
            raise FinancialDataUpstreamError(message[:300])
        topics = payload.get("topics_brief")
        if not isinstance(topics, list):
            raise FinancialDataValidationError("MCP payload must contain a topics_brief list")
        result = self.import_topics(
            topics,
            group_id=group_id,
            group_name=group_name,
            enqueue_analysis=enqueue_analysis,
        )
        result.update(
            {
                "has_more": bool(payload.get("has_more")),
                "next_end_time": payload.get("next_end_time"),
            }
        )
        return result

    def list_notes(self, **filters: Any) -> Dict[str, Any]:
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 50), 100))
        symbol = str(filters.get("symbol") or "").strip()
        if symbol:
            match = re.fullmatch(r"(\d{6})(?:\.(SH|SS|SZ|BJ))?", symbol, re.IGNORECASE)
            if not match:
                raise FinancialDataValidationError(f"invalid A-share symbol: {symbol}")
            symbol = _normalize_ts_code(match.group(1), match.group(2))

        rows, total = self.repo.list_notes(
            group_id=str(filters.get("group_id") or "").strip() or None,
            query=str(filters.get("query") or "").strip() or None,
            symbol=symbol or None,
            digested=filters.get("digested"),
            created_from=_parse_datetime(filters.get("created_from"), field_name="created_from"),
            created_to=_parse_datetime(filters.get("created_to"), field_name="created_to"),
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._note_to_dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_note(self, topic_id: str) -> Dict[str, Any]:
        normalized_id = str(topic_id or "").strip()
        if not normalized_id:
            raise FinancialDataValidationError("topic_id is required")
        row = self.repo.get_note(normalized_id)
        if row is None:
            raise ResearchNoteNotFoundError(f"research note not found: {normalized_id}")
        return self._note_to_dict(row, include_raw=True)

    def source_summary(self) -> List[Dict[str, Any]]:
        return self.repo.source_summary()

    def _normalize_topic(
        self,
        topic: Dict[str, Any],
        *,
        group_id: Optional[str],
        group_name: Optional[str],
    ) -> Dict[str, Any]:
        if not isinstance(topic, dict):
            raise FinancialDataValidationError("each topic must be an object")
        topic_size = len(_json_dump(topic).encode("utf-8"))
        if topic_size > _MAX_TOPIC_BYTES:
            raise FinancialDataValidationError(
                f"topic payload exceeds {_MAX_TOPIC_BYTES} bytes"
            )

        topic_id = str(topic.get("topic_id") or "").strip()
        if not topic_id:
            raise FinancialDataValidationError("topic_id is required")
        group = topic.get("group") if isinstance(topic.get("group"), dict) else {}
        normalized_group_id = str(group.get("group_id") or group_id or "").strip()
        normalized_group_name = str(group.get("name") or group_name or "").strip()
        if not normalized_group_id or not normalized_group_name:
            raise FinancialDataValidationError(f"group_id and group_name are required for topic {topic_id}")

        owner = topic.get("owner") if isinstance(topic.get("owner"), dict) else {}
        files = _sanitize_payload(topic.get("files") if isinstance(topic.get("files"), list) else [])
        images = _sanitize_payload(topic.get("images") if isinstance(topic.get("images"), list) else [])
        content = str(topic.get("content") or "").strip()
        title = str(topic.get("title") or "").strip()
        if (not title or title == "「文件」") and files:
            title = str(files[0].get("name") or title).strip()
        if not title:
            title = next((line.strip() for line in content.splitlines() if line.strip()), f"Topic {topic_id}")
        title = title[:500]
        created_at = _parse_datetime(topic.get("create_time"), field_name="create_time")
        if created_at is None:
            raise FinancialDataValidationError(f"create_time is required for topic {topic_id}")
        modified_at = _parse_datetime(topic.get("modify_time"), field_name="modify_time")
        symbols = _extract_symbols(f"{title}\n{content}")
        sanitized_payload = _sanitize_payload({key: value for key, value in topic.items() if key != "counts"})

        content_hash = research_note_information_hash(
            title=title, content=content, files=files, images=images,
        )
        return {
            "topic_id": topic_id,
            "group_id": normalized_group_id,
            "group_name": normalized_group_name[:100],
            "title": title,
            "content": content or None,
            "author_id": str(owner.get("user_id") or "").strip() or None,
            "author_name": str(owner.get("name") or "").strip()[:100] or None,
            "topic_type": str(topic.get("type") or "talk").strip()[:32],
            "text_type": str(topic.get("text_type") or "").strip()[:32] or None,
            "digested": bool(topic.get("digested")),
            "sticky": bool(topic.get("sticky")),
            "symbol_codes": ",".join(symbols),
            "files_json": _json_dump(files),
            "images_json": _json_dump(images),
            "counts_json": "{}",
            "raw_payload": _json_dump(sanitized_payload),
            "content_hash": content_hash,
            "created_at": created_at,
            "modified_at": modified_at,
            "synced_at": utc_naive_now(),
        }

    @staticmethod
    def _note_to_dict(row: ResearchNote, *, include_raw: bool = False) -> Dict[str, Any]:
        files = _json_load(row.files_json, [])
        images = _json_load(row.images_json, [])
        for asset in files:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("file_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{row.topic_id}/media/files/{asset['file_id']}"
                asset["download_status"] = "remote_on_demand"
        for asset in images:
            asset.pop("local_path", None)
            asset.pop("local_url", None)
            if asset.get("image_id"):
                asset["view_url"] = f"/api/v1/financial-data/research-notes/{row.topic_id}/media/images/{asset['image_id']}"
                asset["download_status"] = "remote_on_demand"
        item = {
            "topic_id": row.topic_id,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "title": row.title,
            "content": row.content,
            "author_id": row.author_id,
            "author_name": row.author_name,
            "topic_type": row.topic_type,
            "text_type": row.text_type,
            "digested": bool(row.digested),
            "sticky": bool(row.sticky),
            "symbols": [value for value in (row.symbol_codes or "").split(",") if value],
            "files": files,
            "images": images,
            "counts": _json_load(row.counts_json, {}),
            "created_at": f"{row.created_at.isoformat()}Z" if row.created_at else None,
            "modified_at": f"{row.modified_at.isoformat()}Z" if row.modified_at else None,
            "synced_at": f"{row.synced_at.isoformat()}Z" if row.synced_at else None,
        }
        if include_raw:
            item["raw_payload"] = _json_load(row.raw_payload, {})
        return item


class TushareGatewayService:
    """Validated generic pass-through for every Tushare Pro ``api_name``."""

    _rate_lock = threading.Lock()
    _request_times: deque[float] = deque()

    def __init__(self, client: Optional[_TushareHttpClient] = None):
        config = get_config()
        token = str(config.tushare_token or "").strip()
        if client is None and not token:
            self.client = None
        else:
            timeout = max(1, min(int(os.getenv("TUSHARE_API_TIMEOUT_SEC", "30")), 120))
            self.client = client or _TushareHttpClient(token=token, timeout=timeout)
        self.rate_limit = max(1, min(int(os.getenv("TUSHARE_API_RATE_LIMIT_PER_MINUTE", "480")), 500))

    @property
    def available(self) -> bool:
        return self.client is not None

    def query(
        self,
        api_name: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        fields: Optional[Any] = None,
    ) -> Dict[str, Any]:
        normalized_name = str(api_name or "").strip()
        if not _API_NAME_PATTERN.fullmatch(normalized_name):
            raise FinancialDataValidationError(
                "api_name must contain only lowercase letters, digits and underscores"
            )
        if not self.available:
            raise FinancialDataValidationError("TUSHARE_TOKEN is not configured")
        query_params = params or {}
        if not isinstance(query_params, dict):
            raise FinancialDataValidationError("params must be an object")
        if any(not isinstance(key, str) or not key.strip() for key in query_params):
            raise FinancialDataValidationError("all params keys must be non-empty strings")
        if fields is None:
            field_text = ""
        elif isinstance(fields, str):
            field_text = fields.strip()
        elif isinstance(fields, list) and all(isinstance(item, str) for item in fields):
            field_text = ",".join(item.strip() for item in fields if item.strip())
        else:
            raise FinancialDataValidationError("fields must be a string or a list of strings")

        self._check_rate_limit()
        started = time.perf_counter()
        try:
            frame = self.client.query(normalized_name, fields=field_text, **query_params)
        except Exception as exc:
            raise FinancialDataUpstreamError(
                f"Tushare request failed for api_name={normalized_name}"
            ) from exc
        rows = [
            {column: self._json_value(value) for column, value in zip(frame.columns, values)}
            for values in frame.itertuples(index=False, name=None)
        ]
        return {
            "source": "tushare",
            "resource": normalized_name,
            "fields": [str(column) for column in frame.columns],
            "rows": rows,
            "count": len(rows),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def _check_rate_limit(self) -> None:
        while True:
            now = time.monotonic()
            with self._rate_lock:
                while self._request_times and now - self._request_times[0] >= 60:
                    self._request_times.popleft()
                if len(self._request_times) < self.rate_limit:
                    self._request_times.append(now)
                    return
                wait_seconds = max(0.01, 60 - (now - self._request_times[0]))
            time.sleep(wait_seconds)

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None:
            return None
        try:
            missing = pd.isna(value)
            if not hasattr(missing, "__len__") and bool(missing):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, (datetime, date)):
            if isinstance(value, datetime) and value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return value.item()
            except (TypeError, ValueError):
                pass
        return value


class FinancialDataService:
    """Single entry point shared by the REST query endpoint."""

    def __init__(
        self,
        *,
        tushare: Optional[TushareGatewayService] = None,
        research_notes: Optional[ResearchNoteService] = None,
        monitor: Optional[Any] = None,
    ):
        self.tushare = tushare or TushareGatewayService()
        self.research_notes = research_notes or ResearchNoteService()
        self._monitor = monitor

    def _monitor_service(self):
        if self._monitor is None:
            # Deferred import avoids the monitor -> TushareGatewayService dependency cycle.
            from src.services.investment_monitor_service import InvestmentMonitorService

            self._monitor = InvestmentMonitorService()
        return self._monitor

    @staticmethod
    def _source_freshness(item: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
        current = now or utc_naive_now()
        last_success_raw = str(item.get("last_success_at") or "").strip()
        interval = max(10, int(item.get("poll_interval_seconds") or 300))
        if not last_success_raw:
            return {"status": "never", "age_seconds": None, "overdue_seconds": None}
        try:
            last_success = datetime.fromisoformat(last_success_raw.replace("Z", "+00:00"))
            if last_success.tzinfo is not None:
                last_success = last_success.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return {"status": "unknown", "age_seconds": None, "overdue_seconds": None}
        age = max(0, int((current - last_success).total_seconds()))
        # Allow one full cadence plus a small worker/network grace window.
        stale_after = interval * 2 + 60
        status = "failed" if item.get("last_status") == "failed" else "fresh" if age <= stale_after else "stale"
        return {
            "status": status,
            "age_seconds": age,
            "overdue_seconds": max(0, age - stale_after),
        }

    def list_sources(self) -> Dict[str, Any]:
        monitor_status = self._monitor_service().list_sources()
        monitoring_items = []
        for item in monitor_status.get("items") or []:
            enriched = dict(item)
            enriched["freshness"] = self._source_freshness(item)
            monitoring_items.append(enriched)
        freshness_counts: Dict[str, int] = {}
        for item in monitoring_items:
            status = item["freshness"]["status"]
            freshness_counts[status] = freshness_counts.get(status, 0) + 1
        by_key = {item.get("source_key"): item for item in monitoring_items}

        return {
            "sources": [
                {
                    "source": "tushare",
                    "available": self.tushare.available,
                    "mode": "live",
                    "resources": "all Tushare Pro api_name values allowed by the configured token",
                },
                {
                    "source": "zsxq",
                    "available": True,
                    "mode": "MCP synchronized local index",
                    "resource": "research_notes",
                    "groups": self.research_notes.source_summary(),
                },
                {
                    "source": "monitor",
                    "available": bool(monitor_status.get("enabled")),
                    "mode": "background adapters normalized into SQLite",
                    "resources": ["events", "announcements"],
                    "summary": {
                        "total": monitor_status.get("total", 0),
                        "enabled": monitor_status.get("enabled", 0),
                        "healthy": monitor_status.get("healthy", 0),
                        "freshness": freshness_counts,
                    },
                    "channels": monitoring_items,
                },
                {
                    "source": "cninfo",
                    "available": bool(by_key.get("cninfo.announcements", {}).get("enabled")),
                    "mode": "15-minute watchlist incremental sync plus on-demand query",
                    "resource": "announcements",
                    "status": by_key.get("cninfo.announcements"),
                },
                {
                    "source": "tianyancha",
                    "available": bool(by_key.get("tianyancha.enterprise", {}).get("enabled")),
                    "mode": "licensed enterprise facts synchronized into SQLite",
                    "resource": "enterprise_events",
                    "status": by_key.get("tianyancha.enterprise"),
                },
            ]
        }

    @staticmethod
    def _validate_local_params(params: Dict[str, Any], allowed: set[str], resource: str) -> None:
        unsupported = sorted(set(params) - allowed)
        if unsupported:
            raise FinancialDataValidationError(
                f"unsupported {resource} params: {', '.join(unsupported)}"
            )

    @staticmethod
    def _local_result(source: str, resource: str, result: Dict[str, Any]) -> Dict[str, Any]:
        rows = result.get("items") or []
        return {
            "source": source,
            "resource": resource,
            "rows": rows,
            "count": len(rows),
            "total": int(result.get("total") or 0),
            "page": int(result.get("page") or 1),
            "page_size": int(result.get("page_size") or len(rows) or 1),
        }

    def query(self, *, source: str, resource: str, params: Dict[str, Any], fields: Any = None) -> Dict[str, Any]:
        normalized_source = str(source or "").strip().lower()
        normalized_resource = str(resource or "").strip()
        if normalized_source == "tushare":
            return self.tushare.query(normalized_resource, params=params, fields=fields)
        if normalized_source == "zsxq":
            if normalized_resource != "research_notes":
                raise FinancialDataValidationError(
                    "the zsxq source currently supports resource=research_notes"
                )
            allowed_params = {
                "group_id", "query", "symbol", "digested", "created_from", "created_to", "page", "page_size",
            }
            unsupported_params = sorted(set(params) - allowed_params)
            if unsupported_params:
                raise FinancialDataValidationError(
                    f"unsupported research_notes params: {', '.join(unsupported_params)}"
                )
            result = self.research_notes.list_notes(**params)
            return {
                "source": "zsxq",
                "resource": "research_notes",
                "rows": result["items"],
                "count": len(result["items"]),
                "total": result["total"],
                "page": result["page"],
                "page_size": result["page_size"],
            }
        if fields:
            raise FinancialDataValidationError("fields projection is only supported for source=tushare")
        if normalized_source in {"monitor", "cninfo", "tianyancha"}:
            monitor = self._monitor_service()
            if normalized_source == "cninfo" or (
                normalized_source == "monitor" and normalized_resource == "announcements"
            ):
                if normalized_resource != "announcements":
                    raise FinancialDataValidationError("the cninfo source supports resource=announcements")
                self._validate_local_params(
                    params,
                    {"start_date", "end_date", "days", "symbol", "category", "query", "page", "page_size"},
                    "announcements",
                )
                return self._local_result(normalized_source, normalized_resource, monitor.list_announcements(**params))
            if normalized_source == "tianyancha":
                if normalized_resource != "enterprise_events":
                    raise FinancialDataValidationError("the tianyancha source supports resource=enterprise_events")
                self._validate_local_params(
                    params,
                    {"days", "symbol", "perspective", "event_type", "query", "min_importance", "page", "page_size"},
                    "enterprise_events",
                )
                result = monitor.list_events(**params, source_key="tianyancha.enterprise")
                return self._local_result(normalized_source, normalized_resource, result)
            if normalized_source == "monitor" and normalized_resource == "events":
                self._validate_local_params(
                    params,
                    {"days", "symbol", "perspective", "event_type", "source_key", "channel", "evidence_level", "query", "min_importance", "page", "page_size"},
                    "events",
                )
                return self._local_result(normalized_source, normalized_resource, monitor.list_events(**params))
            raise FinancialDataValidationError(
                "the monitor source supports resource=events or resource=announcements"
            )
        raise FinancialDataValidationError(f"unsupported source: {source}")
