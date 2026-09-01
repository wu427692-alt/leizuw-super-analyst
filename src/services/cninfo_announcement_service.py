# -*- coding: utf-8 -*-
"""Polite CNInfo announcement client extracted from the legacy PyQt utility."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_PDF_ROOT = "https://static.cninfo.com.cn/"

ANNOUNCEMENT_CATEGORIES: Dict[str, str] = {
    "category_ndbg_szsh": "年报", "category_bndbg_szsh": "半年报",
    "category_yjdbg_szsh": "一季报", "category_sjdbg_szsh": "三季报",
    "category_yjygjxz_szsh": "业绩预告", "category_qyfpxzcs_szsh": "权益分派",
    "category_dshgg_szsh": "董事会", "category_jshgg_szsh": "监事会",
    "category_gddh_szsh": "股东会", "category_rcjy_szsh": "日常经营",
    "category_gszl_szsh": "公司治理", "category_zj_szsh": "中介报告",
    "category_sf_szsh": "首发", "category_zf_szsh": "增发",
    "category_gqjl_szsh": "股权激励", "category_pg_szsh": "配股",
    "category_jj_szsh": "解禁", "category_gszq_szsh": "公司债",
    "category_kzzq_szsh": "可转债", "category_qtrz_szsh": "其他融资",
    "category_gqbd_szsh": "股权变动", "category_bcgz_szsh": "补充更正",
    "category_cqdq_szsh": "澄清致歉", "category_fxts_szsh": "风险提示",
    "category_tbclts_szsh": "特别处理和退市", "category_tszlq_szsh": "整理期",
}


class CninfoAnnouncementError(RuntimeError):
    """Safe upstream/validation failure for announcement APIs."""


class CninfoAnnouncementService:
    """Query CNInfo without inheriting GUI, Excel, or package-install side effects."""

    def __init__(self, session: Optional[requests.Session] = None, *, timeout: float = 20.0,
                 retries: int = 2, request_interval: float = 0.08):
        self.session = session or requests.Session()
        self.timeout = max(3.0, min(float(timeout), 60.0))
        self.retries = max(0, min(int(retries), 5))
        self.request_interval = max(0.0, min(float(request_interval), 2.0))
        self._security_cache: Dict[str, Dict[str, str]] = {}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Referer": "https://www.cninfo.com.cn/new/disclosure",
            "Origin": "https://www.cninfo.com.cn",
            "X-Requested-With": "XMLHttpRequest",
        }

    @staticmethod
    def categories() -> List[Dict[str, str]]:
        return [{"code": code, "name": name} for code, name in ANNOUNCEMENT_CATEGORIES.items()]

    def resolve_security(
        self,
        symbol: str,
        *,
        deadline_monotonic: Optional[float] = None,
        request_budget: Optional[Dict[str, int]] = None,
    ) -> Dict[str, str]:
        code = str(symbol or "").strip().upper().split(".", 1)[0]
        if len(code) != 6 or not code.isdigit():
            raise CninfoAnnouncementError(f"无效 A 股代码：{symbol}")
        cached = self._security_cache.get(code)
        if cached is not None:
            return dict(cached)
        rows = self._post_json(
            CNINFO_SEARCH_URL,
            {"keyWord": code, "maxNum": 10},
            deadline_monotonic=deadline_monotonic,
            request_budget=request_budget,
        )
        if not isinstance(rows, list):
            raise CninfoAnnouncementError("巨潮证券检索返回格式异常")
        row = next((item for item in rows if str(item.get("code")) == code), None)
        if not row or not row.get("orgId"):
            raise CninfoAnnouncementError(f"巨潮未找到证券：{code}")
        resolved = {"code": code, "org_id": str(row["orgId"]), "name": str(row.get("zwjc") or code)}
        self._security_cache[code] = resolved
        return dict(resolved)

    def fetch(
        self, *, start_date: date, end_date: date, symbols: Sequence[str] = (),
        categories: Sequence[str] = (), keyword: str = "", page_size: int = 50,
        max_pages: int = 20, exclude_noise: bool = True,
        deadline_monotonic: Optional[float] = None,
        request_budget: Optional[Dict[str, int]] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if end_date < start_date:
            raise CninfoAnnouncementError("结束日期不能早于开始日期")
        if (end_date - start_date).days > 3660:
            raise CninfoAnnouncementError("单次查询日期范围不能超过 10 年")
        invalid = [value for value in categories if value not in ANNOUNCEMENT_CATEGORIES]
        if invalid:
            raise CninfoAnnouncementError(f"未知公告分类：{invalid[0]}")
        safe_size = max(1, min(int(page_size), 100))
        safe_pages = max(1, min(int(max_pages), 100))
        diagnostic_target = diagnostics if isinstance(diagnostics, dict) else None
        budget_used_before = int((request_budget or {}).get("used") or 0)
        if diagnostic_target is not None:
            diagnostic_target.clear()
            diagnostic_target.update({
                "pages_fetched": 0,
                "total_pages": 0,
                "truncated": False,
                "request_attempts": 0,
            })
        securities: List[Optional[Dict[str, str]]] = [None]
        if symbols:
            securities = [
                self.resolve_security(
                    symbol,
                    deadline_monotonic=deadline_monotonic,
                    request_budget=request_budget,
                )
                for symbol in dict.fromkeys(symbols)
            ]
        collected: Dict[str, Dict[str, Any]] = {}
        for security in securities:
            stock = f"{security['code']},{security['org_id']}" if security else ""
            for page in range(1, safe_pages + 1):
                payload = {
                    "pageNum": page, "pageSize": safe_size, "column": "szse",
                    "tabName": "fulltext", "plate": "", "stock": stock,
                    "searchkey": str(keyword or "").strip()[:100], "secid": "",
                    "category": ";".join(categories), "trade": "",
                    "seDate": f"{start_date.isoformat()}~{end_date.isoformat()}",
                    "sortName": "", "sortType": "", "isHLtitle": "true",
                }
                data = self._post_json(
                    CNINFO_QUERY_URL,
                    payload,
                    deadline_monotonic=deadline_monotonic,
                    request_budget=request_budget,
                )
                if not isinstance(data, dict):
                    raise CninfoAnnouncementError("巨潮公告接口返回格式异常")
                rows = data.get("announcements") or []
                if diagnostic_target is not None:
                    diagnostic_target["pages_fetched"] = int(diagnostic_target["pages_fetched"]) + 1
                for raw in rows:
                    item = self._normalize(raw, categories)
                    if exclude_noise and any(word in item["title"] for word in ("英文", "已取消", "摘要")):
                        continue
                    collected[item["announcement_id"]] = item
                total_pages = int(data.get("totalpages") or 0)
                if diagnostic_target is not None:
                    diagnostic_target["total_pages"] = max(
                        int(diagnostic_target["total_pages"]),
                        total_pages,
                    )
                    if page >= safe_pages and total_pages > page:
                        diagnostic_target["truncated"] = True
                if not rows or page >= total_pages:
                    break
                if self.request_interval:
                    time.sleep(self.request_interval)
        if diagnostic_target is not None:
            diagnostic_target["request_attempts"] = max(
                0,
                int((request_budget or {}).get("used") or 0) - budget_used_before,
            )
        return sorted(collected.values(), key=lambda item: item["announcement_at"], reverse=True)

    def fetch_recent(self, symbols: Sequence[str], *, days: int = 2) -> List[Dict[str, Any]]:
        if not symbols:
            return []
        today = datetime.now(timezone(timedelta(hours=8))).date()
        return self.fetch(start_date=today - timedelta(days=max(1, days) - 1), end_date=today,
                          symbols=symbols, page_size=50, max_pages=5)

    def fetch_recent_market(self, *, days: int = 2, max_pages: int = 30) -> List[Dict[str, Any]]:
        """Fetch recent full-market metadata without downloading announcement files."""
        today = datetime.now(timezone(timedelta(hours=8))).date()
        return self.fetch(
            start_date=today - timedelta(days=max(1, days) - 1), end_date=today,
            symbols=[], page_size=100, max_pages=max_pages,
        )

    def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        *,
        deadline_monotonic: Optional[float] = None,
        request_budget: Optional[Dict[str, int]] = None,
    ) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            remaining_seconds: Optional[float] = None
            if deadline_monotonic is not None:
                remaining_seconds = float(deadline_monotonic) - time.monotonic()
                if remaining_seconds <= 0:
                    raise CninfoAnnouncementError("巨潮请求已达任务总时间预算")
            if request_budget is not None:
                remaining_requests = int(request_budget.get("remaining") or 0)
                if remaining_requests <= 0:
                    raise CninfoAnnouncementError("巨潮请求已达任务总次数预算")
                request_budget["remaining"] = remaining_requests - 1
                request_budget["used"] = int(request_budget.get("used") or 0) + 1
            request_timeout = self.timeout
            if remaining_seconds is not None:
                request_timeout = max(0.25, min(request_timeout, remaining_seconds))
            try:
                response = self.session.post(url, headers=self.headers, data=payload, timeout=request_timeout)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    retry_delay = 0.25 * (2 ** attempt)
                    if deadline_monotonic is not None:
                        retry_delay = min(
                            retry_delay,
                            max(0.0, float(deadline_monotonic) - time.monotonic()),
                        )
                    if retry_delay:
                        time.sleep(retry_delay)
        raise CninfoAnnouncementError(f"巨潮接口请求失败：{type(last_error).__name__}") from last_error

    @staticmethod
    def _normalize(raw: Dict[str, Any], categories: Iterable[str]) -> Dict[str, Any]:
        announcement_id = str(raw.get("announcementId") or "").strip()
        if not announcement_id:
            raise CninfoAnnouncementError("公告缺少 announcementId")
        adjunct = str(raw.get("adjunctUrl") or "").lstrip("/")
        timestamp = raw.get("announcementTime")
        try:
            announcement_at = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError):
            announcement_at = datetime.now(timezone.utc).replace(tzinfo=None)
        category_codes = list(categories)
        return {
            "announcement_id": announcement_id,
            "code": str(raw.get("secCode") or "").strip(),
            "name": str(raw.get("secName") or raw.get("tileSecName") or "").strip(),
            "title": str(raw.get("announcementTitle") or raw.get("shortTitle") or "公告").strip(),
            "announcement_at": announcement_at,
            "pdf_url": CNINFO_PDF_ROOT + adjunct if adjunct else None,
            "category_codes": category_codes,
            "category_names": [ANNOUNCEMENT_CATEGORIES[value] for value in category_codes],
            "size_kb": raw.get("adjunctSize"), "file_type": raw.get("adjunctType"),
            "org_id": raw.get("orgId"), "raw": raw,
        }
