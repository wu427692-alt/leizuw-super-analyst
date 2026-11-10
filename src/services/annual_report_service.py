# -*- coding: utf-8 -*-
"""Annual-report lookup and download via the CNINFO announcement service."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.services.cninfo_announcement_service import CninfoAnnouncementService

DOWNLOAD_DIR = Path(os.getenv("ANNUAL_REPORT_DIR", "/app/data/annual_reports"))

REPORT_TYPES = {
    "annual": ("category_ndbg_szsh", "年度报告"),
    "semi": ("category_bndbg_szsh", "半年度报告"),
    "q1": ("category_yjdbg_szsh", "第一季度报告"),
    "q3": ("category_sjdbg_szsh", "第三季度报告"),
}


def _title_targets(year: int, label: str) -> List[str]:
    short = (
        label.replace("年度报告", "年报")
        .replace("半年度报告", "半年报")
        .replace("第一季度报告", "一季报")
        .replace("第三季度报告", "三季报")
    )
    extra = (
        label.replace("第一季度报告", "一季度报告")
        .replace("第三季度报告", "三季度报告")
    )
    return [
        f"{year}年{label}",
        f"{year}{label}",
        f"{year}年{short}",
        f"{year}{short}",
        f"{year}年{extra}",
        f"{year}{extra}",
    ]


def _clean_code(symbol: str) -> str:
    code = str(symbol or "").strip().upper().split(".", 1)[0]
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效 A 股代码：{symbol}")
    return code


class AnnualReportService:
    def __init__(self, timeout: float = 60.0):
        self.cninfo = CninfoAnnouncementService(timeout=timeout)
        self.download_dir = DOWNLOAD_DIR
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def find(self, symbol: str, year: Optional[int] = None, report_type: str = "annual") -> Optional[Dict[str, Any]]:
        code = _clean_code(symbol)
        category, label = REPORT_TYPES.get(report_type, REPORT_TYPES["annual"])
        today = date.today()
        if year is not None:
            start = date(int(year) - 1, 1, 1)
            end = date(int(year) + 1, 12, 31)
        else:
            start = date(today.year - 4, 1, 1)
            end = today
        items = self.cninfo.fetch(
            start_date=start,
            end_date=end if end <= today else today,
            symbols=[code],
            categories=[category],
            page_size=50,
            max_pages=2,
            exclude_noise=True,
        )
        if year is not None:
            targets = _title_targets(int(year), label)
            items = [
                item for item in items
                if any(target in str(item.get("title") or "") for target in targets)
            ]
        if not items:
            return None
        item = items[0]
        announcement_at = item.get("announcement_at")
        return {
            "code": code,
            "name": item.get("name") or "",
            "title": item.get("title") or "",
            "date": announcement_at.strftime("%Y-%m-%d") if hasattr(announcement_at, "strftime") else str(announcement_at or ""),
            "pdf_url": item.get("pdf_url") or "",
            "announcement_id": item.get("announcement_id") or "",
            "size_kb": item.get("size_kb"),
            "report_type": report_type,
            "report_label": label,
            "category": category,
        }

    def download(self, symbol: str, year: Optional[int] = None, report_type: str = "annual") -> Dict[str, Any]:
        item = self.find(symbol, year, report_type)
        if not item:
            return {"ok": False, "error": f"未找到 {symbol} 的{REPORT_TYPES.get(report_type, REPORT_TYPES['annual'])[1]}"}
        pdf_url = item.get("pdf_url")
        if not pdf_url:
            return {"ok": False, "error": "报告没有可下载的 PDF 地址", **item}

        code = item["code"]
        report_year = year
        if report_year is None:
            matched = re.search(r"(\d{4})年", item["title"])
            report_year = matched.group(1) if matched else "latest"
        type_slug = report_type if report_type in REPORT_TYPES else "annual"
        filename = f"{code}-{type_slug}-{report_year}-{item['announcement_id']}.pdf"
        path = self.download_dir / filename
        if not path.is_file() or path.stat().st_size <= 0:
            response = requests.get(pdf_url, timeout=60, stream=True)
            response.raise_for_status()
            temp = path.with_suffix(".part")
            with open(temp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
            temp.replace(path)

        size = path.stat().st_size
        with open(path, "rb") as handle:
            magic = handle.read(5)
        if magic != b"%PDF-":
            return {"ok": False, "error": "下载的文件不是有效 PDF", **item}

        return {
            "ok": True,
            **item,
            "filename": filename,
            "size": size,
            "local_path": str(path),
            "download_url": f"/api/v1/financial-data/annual-report/file/{filename}",
        }

    def list_announcements(
        self,
        symbol: str,
        days: int = 30,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        code = _clean_code(symbol)
        end = date.today()
        wanted = max(1, min(int(limit), 30))

        def fetch_for(days_value: int):
            start = end - timedelta(days=max(1, min(int(days_value), 365)))
            return self.cninfo.fetch(
                start_date=start,
                end_date=end,
                symbols=[code],
                categories=[category] if category else [],
                page_size=50,
                max_pages=3,
                exclude_noise=True,
            )

        items = fetch_for(days)
        if len(items) < wanted and int(days) < 365:
            extended = fetch_for(365)
            seen = {str(item.get("announcement_id") or "") for item in items}
            for item in extended:
                key = str(item.get("announcement_id") or "")
                if key and key not in seen:
                    seen.add(key)
                    items.append(item)
        result = []
        for item in items[:wanted]:
            announcement_at = item.get("announcement_at")
            result.append(
                {
                    "announcement_id": item.get("announcement_id"),
                    "title": item.get("title"),
                    "date": announcement_at.strftime("%Y-%m-%d") if hasattr(announcement_at, "strftime") else str(announcement_at or ""),
                    "pdf_url": item.get("pdf_url"),
                    "category_names": item.get("category_names") or [],
                }
            )
        return result

    def download_document(self, url: str, title: str = "公告") -> Dict[str, Any]:
        """Download an arbitrary CNINFO/PDF document, with host-fetcher fallback."""
        if not str(url or "").startswith("http"):
            return {"ok": False, "error": "无效的 PDF 地址", "title": title, "pdf_url": url}
        filename = f"doc-{hashlib.sha1(str(url).encode('utf-8')).hexdigest()[:12]}.pdf"
        path = self.download_dir / filename
        data = None
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                    "Referer": "https://www.cninfo.com.cn/",
                },
                timeout=60,
            )
            if response.status_code == 200 and response.content[:5] == b"%PDF-":
                data = response.content
        except Exception:
            data = None
        if data is None:
            token = os.environ.get("REPORT_FETCH_TOKEN", "")
            fetch_url = os.environ.get("REPORT_FETCH_URL", "http://host.docker.internal:8765/fetch")
            try:
                response = requests.get(
                    fetch_url,
                    params={"url": url},
                    headers={"X-Fetch-Token": token} if token else {},
                    timeout=90,
                )
                if response.status_code == 200 and response.content[:5] == b"%PDF-":
                    data = response.content
            except Exception:
                data = None
        if not data:
            return {"ok": False, "error": "下载失败或不是有效 PDF", "title": title, "pdf_url": url}
        path.write_bytes(data)
        return {
            "ok": True,
            "title": title,
            "filename": filename,
            "size": len(data),
            "pdf_url": url,
            "download_url": f"/api/v1/financial-data/annual-report/file/{filename}",
        }

    def _fetch_type_items(self, code: str, report_type: str, years: int) -> List[Dict[str, Any]]:
        category, _label = REPORT_TYPES.get(report_type, REPORT_TYPES["annual"])
        end = date.today()
        span = max(2, min(int(years) + 1, 9))
        start = date(end.year - span, 1, 1)
        return self.cninfo.fetch(
            start_date=start,
            end_date=end,
            symbols=[code],
            categories=[category],
            page_size=100,
            max_pages=5,
            exclude_noise=True,
        )

    def package_periodic(self, symbol: str, types: List[str], years: int = 3) -> Dict[str, Any]:
        code = _clean_code(symbol)
        types = [item for item in (types or []) if item in REPORT_TYPES] or ["annual"]
        years = max(1, min(int(years), 9))
        current = date.today().year
        found: List[Dict[str, Any]] = []
        missing: List[Dict[str, Any]] = []
        for report_type in types:
            label = REPORT_TYPES[report_type][1]
            items = self._fetch_type_items(code, report_type, years)
            end_year = current - 1 if report_type == "annual" else current
            for year in range(end_year, end_year - years, -1):
                targets = _title_targets(year, label)
                matches = [
                    item for item in items
                    if any(target in str(item.get("title") or "") for target in targets)
                ]
                if matches:
                    item = matches[0]
                    found.append(
                        {
                            "title": item.get("title"),
                            "url": item.get("pdf_url"),
                            "date": item.get("announcement_at").strftime("%Y-%m-%d") if hasattr(item.get("announcement_at"), "strftime") else str(item.get("announcement_at") or ""),
                            "broker": label,
                        }
                    )
                else:
                    missing.append({"title": f"{year}年{label}", "error": "未找到"})
        from src.services.report_pack_service import ReportPackService

        result = ReportPackService().package_documents(
            found,
            label=f"{code} 财报",
            retries=1,
            missing=missing,
            kind="财报",
            subject=code,
        )
        result["missing"] = missing
        return result

    def package_announcements(
        self,
        symbol: str,
        days: int = 30,
        limit: int = 10,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        code = _clean_code(symbol)
        items = self.list_announcements(code, days, category, limit)
        found: List[Dict[str, Any]] = []
        missing: List[Dict[str, Any]] = []
        for item in items:
            if item.get("pdf_url"):
                found.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("pdf_url"),
                        "date": item.get("date"),
                        "broker": "公告",
                    }
                )
            else:
                missing.append({"title": item.get("title"), "error": "没有 PDF"})
        if not found and not missing:
            return {"ok": False, "error": "没有找到公告"}
        from src.services.report_pack_service import ReportPackService

        return ReportPackService().package_documents(
            found,
            label=f"{code} 公告",
            retries=1,
            missing=missing,
            kind="公告",
            subject=code,
        )
