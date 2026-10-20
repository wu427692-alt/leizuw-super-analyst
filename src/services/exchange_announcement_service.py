"""Bounded metadata-only clients for the exchanges' public disclosure pages.

Protocols verified against the exchanges' own page scripts:
https://www.sse.com.cn/assortment/stock/list/info/announcement/
https://res.static.szse.cn/modules/disclosure/js/modules/listedAnnoun.min.js
https://res.static.szse.cn/modules/disclosure/js/concatDisc.min.js
No authentication, PDF download, HTTP downgrade, or automatic retry is performed.
"""

from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import math
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

import requests

from src.services.cninfo_announcement_service import CninfoAnnouncementError, CninfoAnnouncementService


SSE_QUERY_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
SZSE_QUERY_URL = "https://www.szse.cn/api/disc/announcement/annList"
# SSE's disclosure page explicitly defines staticBulletinUrl = "//static.sse.com.cn".
SSE_PDF_ROOT = "https://static.sse.com.cn"
# SZSE's szsePath.js and concatDisc.min.js specify disc_file_path + '/download' + attachPath.
SZSE_PDF_ROOT = "https://disc.static.szse.cn/download"
SHANGHAI_TZ = timezone(timedelta(hours=8))
ATTACHMENT_TYPES = frozenset({"PDF", "DOC", "DOCX", "TXT", "HTML", "HTM", "XLS", "XLSX"})
SOURCES = {"SH": ("sse.announcements", "上海证券交易所公告"),
           "SZ": ("szse.announcements", "深圳证券交易所公告")}


class ExchangeAnnouncementService:
    """Fetch one exact stock/date window, succeeding only on complete pagination."""

    def __init__(self, session: Optional[requests.Session] = None, *, timeout: float = 12.0,
                 request_interval: float = 0.15):
        self.session = session or requests.Session()
        self.timeout = max(0.25, min(float(timeout), 30.0))
        self.request_interval = max(0.0, min(float(request_interval), 2.0))
        self._cooldowns: Dict[str, float] = {}

    @staticmethod
    def _error(source: str, message: str, *, status: Optional[int] = None,
               retry_after: Optional[float] = None, attempts: int = 0) -> CninfoAnnouncementError:
        error = CninfoAnnouncementError(
            f"{SOURCES[source][1]}：{message}", endpoint=SOURCES[source][0],
            status_code=status, retry_after_seconds=retry_after, attempts=attempts,
        )
        error.source_key, error.source_name = SOURCES[source]
        return error

    def fetch(self, *, symbol: str, start_date: date, end_date: date, page_size: int = 50,
              max_pages: int = 20, deadline_monotonic: Optional[float] = None,
              request_budget: Optional[Dict[str, int]] = None,
              diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        value = str(symbol or "").strip().upper()
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", value):
            raise CninfoAnnouncementError("交易所备用源要求完整的单一 SH/SZ 证券代码")
        code, exchange = value.split(".")
        if not ((exchange == "SH" and code.startswith("6"))
                or (exchange == "SZ" and code.startswith(("0", "3")))):
            raise self._error(exchange, "证券代码与 A 股交易所不匹配")
        if (type(start_date) is not date or type(end_date) is not date
                or end_date < start_date or (end_date - start_date).days > 3660):
            raise self._error(exchange, "公告日期范围无效，单次不得超过 10 年")
        if (type(page_size) is not int or not 1 <= page_size <= 100
                or type(max_pages) is not int or not 1 <= max_pages <= 100):
            raise self._error(exchange, "分页参数无效")
        diag = diagnostics if diagnostics is not None else {}
        diag.clear()
        diag.update(source_key=SOURCES[exchange][0], source_name=SOURCES[exchange][1],
                    pages_fetched=0, total_pages=0, total_count=0, truncated=False,
                    request_attempts=0)
        # Even standalone use is bounded; a caller's tighter global budget wins.
        own_deadline = time.monotonic() + 120.0
        deadline = min(own_deadline, deadline_monotonic) if deadline_monotonic is not None else own_deadline
        budget = request_budget if request_budget is not None else {"remaining": max_pages, "used": 0}
        before = int(budget.get("used") or 0)
        collected: Dict[str, Dict[str, Any]] = {}
        initial_total: Optional[int] = None
        try:
            for page in range(1, max_pages + 1):
                data = self._query(exchange, code, start_date, end_date, page_size, page,
                                   deadline, budget)
                rows, total, reported_pages = self._page(exchange, data, page, page_size)
                total_pages = math.ceil(total / page_size)
                diag.update(pages_fetched=page, total_pages=total_pages, total_count=total)
                if initial_total is not None and total != initial_total:
                    raise self._error(exchange, "分页总数在查询期间发生变化，请重新查询")
                initial_total = total
                if total_pages > max_pages or (exchange == "SZ" and total >= 25000):
                    diag["truncated"] = True
                    raise self._error(exchange, "结果超出完整分页预算，需缩小日期窗口；"
                                      "未返回截断结果")
                if reported_pages is not None and reported_pages != total_pages:
                    raise self._error(exchange, "分页页数与总数不一致")
                expected = min(page_size, max(0, total - (page - 1) * page_size))
                if len(rows) != expected:
                    raise self._error(exchange, "公告分页数量不完整，不能确认为同步成功")
                for raw in rows:
                    row = self._normalize(exchange, raw, code, start_date, end_date)
                    key = row["announcement_id"]
                    if key in collected:
                        raise self._error(exchange, "公告分页出现重复记录，不能确认为完整结果")
                    collected[key] = row
                if page >= total_pages:
                    break
                if self.request_interval:
                    if time.monotonic() + self.request_interval >= deadline:
                        raise self._error(exchange, "请求已达任务总时间预算")
                    time.sleep(self.request_interval)
            if len(collected) != initial_total:
                diag["truncated"] = True
                raise self._error(exchange, "公告总数未收齐，未返回截断结果")
            return sorted(collected.values(), key=lambda item: (item["announcement_at"], item["announcement_id"]),
                          reverse=True)
        finally:
            diag["request_attempts"] = max(0, int(budget.get("used") or 0) - before)

    def _query(self, exchange: str, code: str, start: date, end: date, size: int, page: int,
               deadline: float, budget: Dict[str, int]) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._error(exchange, "请求已达任务总时间预算")
        cooldown = self._cooldowns.get(exchange, 0) - time.monotonic()
        if cooldown > 0:
            raise self._error(exchange, "上游要求等待后重试", retry_after=cooldown)
        if int(budget.get("remaining") or 0) <= 0:
            raise self._error(exchange, "请求已达任务总次数预算")
        budget["remaining"] = int(budget["remaining"]) - 1
        budget["used"] = int(budget.get("used") or 0) + 1
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        kwargs = {"headers": headers, "timeout": min(self.timeout, remaining), "allow_redirects": False}
        status, retry_after = None, None
        try:
            if exchange == "SH":
                headers["Referer"] = "https://www.sse.com.cn/assortment/stock/list/info/announcement/"
                response = self.session.get(SSE_QUERY_URL, params={
                    "isPagination": "true", "productId": code, "keyWord": "",
                    "securityType": "0101,120100,020100,020200", "reportType2": "", "reportType": "ALL",
                    "beginDate": start.isoformat(), "endDate": end.isoformat(),
                    "pageHelp.pageSize": size, "pageHelp.pageNo": page, "pageHelp.beginPage": page,
                    "pageHelp.cacheSize": 1, "pageHelp.endPage": page,
                }, **kwargs)
            else:
                headers["Referer"] = "https://www.szse.cn/disclosure/listed/notice/index.html"
                response = self.session.post(SZSE_QUERY_URL, json={
                    "channelCode": ["listedNotice_disc"], "stock": [code],
                    "seDate": [start.isoformat(), end.isoformat()], "pageSize": size, "pageNum": page,
                }, **kwargs)
            status = response.status_code
            retry_after = CninfoAnnouncementService._retry_after_seconds(response.headers.get("Retry-After"))
            if status != 200:
                if retry_after:
                    self._cooldowns[exchange] = time.monotonic() + retry_after
                raise self._error(exchange, f"HTTP {status}", status=status, retry_after=retry_after, attempts=1)
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "application/json" not in content_type:
                raise self._error(exchange, "返回非 JSON 内容", status=status, attempts=1)
            if len(response.content) > 10 * 1024 * 1024:
                raise self._error(exchange, "公告响应体过大", status=status, attempts=1)
            data = response.json()
            if time.monotonic() > deadline:
                raise self._error(exchange, "响应已超过任务总时间预算", attempts=1)
            return data
        except (requests.RequestException, ValueError) as exc:
            # Never expose provider response bodies, cookies or exception strings.
            raise self._error(exchange, type(exc).__name__, status=status,
                              retry_after=retry_after, attempts=1) from exc

    def _page(self, exchange: str, data: Any, page: int, size: int):
        if not isinstance(data, dict):
            raise self._error(exchange, "公告响应格式异常")
        if exchange == "SH":
            payload = data.get("pageHelp")
            if not isinstance(payload, dict):
                raise self._error(exchange, "缺少 pageHelp")
            rows, total, pages = payload.get("data"), payload.get("total"), payload.get("pageCount")
            if payload.get("pageNo") != page or payload.get("pageSize") != size:
                raise self._error(exchange, "返回页码或页大小与请求不一致")
            if type(pages) is not int or pages < 0:
                raise self._error(exchange, "返回分页页数异常")
        else:
            rows, total, pages = data.get("data"), data.get("announceCount"), None
        if type(total) is not int or total < 0 or not isinstance(rows, list):
            raise self._error(exchange, "返回公告列表或总数异常")
        return rows, total, pages

    def _normalize(self, exchange: str, raw: Any, code: str, start: date, end: date) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise self._error(exchange, "公告记录格式异常")
        try:
            if exchange == "SH":
                if raw.get("SECURITY_CODE") != code:
                    raise ValueError("symbol")
                name, title = raw.get("SECURITY_NAME"), raw.get("TITLE")
                disclosure = raw.get("SSEDATE")
                if not isinstance(disclosure, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", disclosure):
                    raise ValueError("date")
                local_at = datetime.combine(date.fromisoformat(disclosure), datetime_time(), SHANGHAI_TZ)
                original_url, file_type = self._attachment_url(exchange, raw.get("URL"))
                announcement_id = "sse-" + hashlib.sha256(original_url.encode()).hexdigest()[:32]
                precision, size_kb = "date", None
            else:
                codes, names = raw.get("secCode"), raw.get("secName")
                if (not isinstance(codes, list) or code not in codes or not isinstance(names, list)
                        or len(names) != len(codes)):
                    raise ValueError("symbol")
                name, title = names[codes.index(code)], raw.get("title")
                stamp = raw.get("publishTime")
                if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stamp):
                    raise ValueError("time")
                local_at = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI_TZ)
                disclosure = local_at.date().isoformat()
                original_url, file_type = self._attachment_url(
                    exchange, raw.get("attachPath"), declared_format=raw.get("attachFormat"),
                )
                ann_id = raw.get("annId")
                if isinstance(ann_id, bool) or not re.fullmatch(r"[1-9]\d{0,24}", str(ann_id)):
                    raise ValueError("id")
                announcement_id = str(ann_id)
                precision, size_kb = "second", raw.get("attachSize")
            if not start <= local_at.date() <= end:
                raise ValueError("date range")
            if not isinstance(name, str) or not name.strip() or not isinstance(title, str) or not title.strip():
                raise ValueError("title/name")
        except (ValueError, TypeError, OverflowError) as exc:
            raise self._error(exchange, "公告证券、披露日期、原文地址或文件类型"
                              "校验失败") from exc
        return {
            "announcement_id": announcement_id, "code": code, "name": name.strip(), "title": title.strip(),
            "announcement_at": local_at.astimezone(timezone.utc).replace(tzinfo=None),
            "disclosure_date": disclosure, "time_precision": precision,
            "original_url": original_url, "pdf_url": original_url if file_type == "PDF" else None,
            "category_codes": [], "category_names": [],
            "size_kb": size_kb, "file_type": file_type, "org_id": None,
            "source_key": SOURCES[exchange][0], "source_name": SOURCES[exchange][1], "raw": dict(raw),
        }

    @classmethod
    def _pdf_url(cls, exchange: str, value: Any) -> str:
        """Keep a strictly PDF-only validator for callers that require a PDF."""
        url, _file_type = cls._attachment_url(exchange, value, declared_format="PDF")
        return url

    @staticmethod
    def _attachment_url(exchange: str, value: Any, *, declared_format: Any = None):
        if not isinstance(value, str) or not value or any(c.isspace() for c in value) or "\\" in value:
            raise ValueError("path")
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("URL decorations")
        if parsed.scheme or parsed.netloc:
            expected_host = "static.sse.com.cn" if exchange == "SH" else "disc.static.szse.cn"
            if parsed.scheme != "https" or parsed.netloc != expected_host:
                raise ValueError("host")
            path = parsed.path
            if exchange == "SZ" and path.startswith("/download/"):
                path = path[len("/download"):]
        else:
            path = parsed.path
        prefix = "/disclosure/listedinfo/announcement/" if exchange == "SH" else "/disc/"
        decoded = unquote(path)
        file_type = path.rsplit(".", 1)[-1].upper()
        if (not path.startswith(prefix) or file_type not in ATTACHMENT_TYPES
                or any(part in (".", "..") for part in decoded.split("/")) or "\\" in decoded
                or any(ord(c) < 32 for c in decoded) or "%" in decoded):
            raise ValueError("path")
        if exchange == "SZ" or declared_format is not None:
            if (not isinstance(declared_format, str)
                    or declared_format.upper() not in ATTACHMENT_TYPES
                    or declared_format.upper() != file_type):
                raise ValueError("declared file type does not match extension")
        return (SSE_PDF_ROOT if exchange == "SH" else SZSE_PDF_ROOT) + path, file_type
