# -*- coding: utf-8 -*-
"""Excel/PDF/TXT artifacts backed by persisted CNInfo monitoring events."""

from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse
import zipfile

from openpyxl import Workbook
import requests


class AnnouncementArtifactError(RuntimeError):
    """Safe artifact generation or upstream file error."""


class AnnouncementArtifactService:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.root = Path(os.getenv("ANNOUNCEMENT_FILE_DIR", "./data/announcements")).expanduser().resolve()
        self.max_pdf_bytes = max(1, min(int(os.getenv("ANNOUNCEMENT_PDF_MAX_MB", "40")), 200)) * 1024 * 1024
        self.root.mkdir(parents=True, exist_ok=True)

    def excel_bytes(self, events: Iterable[Dict[str, Any]]) -> bytes:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("上市公司公告")
        sheet.append(["公告ID", "证券代码", "公司名称", "公告标题", "公告日期", "公告分类", "PDF原文", "文件大小KB"])
        for event in events:
            metrics = event.get("metrics") or {}
            sheet.append([
                event.get("external_id"), (event.get("symbols") or [metrics.get("code") or ""])[0],
                (event.get("actors") or [metrics.get("name") or ""])[0], event.get("title"),
                str(event.get("event_at") or "")[:10], " / ".join(event.get("tags") or []),
                event.get("url"), metrics.get("size_kb"),
            ])
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def package(self, events: List[Dict[str, Any]], *, include_text: bool = True) -> Dict[str, Any]:
        if not events:
            raise AnnouncementArtifactError("没有可打包的已入库公告")
        if len(events) > 20:
            raise AnnouncementArtifactError("单次最多打包 20 份公告，请缩小日期或股票范围")
        handle = tempfile.NamedTemporaryFile(prefix="cninfo_announcements_", suffix=".zip", delete=False)
        archive_path = Path(handle.name)
        handle.close()
        results: List[Dict[str, Any]] = []
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("上市公司公告索引.xlsx", self.excel_bytes(events))
                for event in events:
                    result = self._archive_event(archive, event, include_text=include_text)
                    results.append(result)
                archive.writestr("manifest.json", json.dumps(results, ensure_ascii=False, indent=2))
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        return {
            "path": archive_path, "filename": "上市公司公告_PDF_TXT.zip",
            "requested": len(events), "downloaded": sum(1 for item in results if item["status"] == "success"),
            "failed": sum(1 for item in results if item["status"] == "failed"), "items": results,
        }

    def _archive_event(self, archive: zipfile.ZipFile, event: Dict[str, Any], *, include_text: bool) -> Dict[str, Any]:
        metrics = event.get("metrics") or {}
        announcement_id = str(event.get("external_id") or metrics.get("announcement_id") or "announcement")
        symbol = str((event.get("symbols") or [metrics.get("code") or "unknown"])[0]).split(".", 1)[0]
        title = self._safe_name(str(event.get("title") or announcement_id), 100)
        prefix = f"{symbol}_{announcement_id}_{title}"
        try:
            pdf_path, cached = self.download_pdf(event)
            archive.write(pdf_path, f"PDF/{prefix}.pdf")
            text_written = False
            if include_text:
                text_path = pdf_path.with_suffix(".txt")
                if not text_path.exists():
                    text_path.write_text(self.extract_text(pdf_path), encoding="utf-8")
                archive.write(text_path, f"TXT/{prefix}.txt")
                text_written = True
            return {"id": event.get("id"), "announcement_id": announcement_id, "title": event.get("title"),
                    "status": "success", "cached": cached, "text": text_written}
        except Exception as exc:  # One malformed PDF must not discard the remaining archive.
            return {"id": event.get("id"), "announcement_id": announcement_id, "title": event.get("title"),
                    "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}

    def download_pdf(self, event: Dict[str, Any]) -> Tuple[Path, bool]:
        url = str(event.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "static.cninfo.com.cn" or not parsed.path.lower().endswith(".pdf"):
            raise AnnouncementArtifactError("公告 PDF 地址不在允许的巨潮 HTTPS 域名")
        metrics = event.get("metrics") or {}
        announcement_id = self._safe_name(str(event.get("external_id") or metrics.get("announcement_id") or "announcement"), 80)
        symbol = self._safe_name(str((event.get("symbols") or [metrics.get("code") or "unknown"])[0]), 20)
        directory = self.root / symbol
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{announcement_id}.pdf"
        if target.exists() and target.stat().st_size > 4:
            with target.open("rb") as existing:
                if existing.read(4) == b"%PDF":
                    return target, True
        temporary = target.with_suffix(".pdf.part")
        try:
            with self.session.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"},
                                  timeout=(10, 60), stream=True) as response:
                response.raise_for_status()
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > self.max_pdf_bytes:
                    raise AnnouncementArtifactError("公告 PDF 超过单文件大小上限")
                size = 0
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_pdf_bytes:
                            raise AnnouncementArtifactError("公告 PDF 超过单文件大小上限")
                        output.write(chunk)
            with temporary.open("rb") as downloaded:
                if downloaded.read(4) != b"%PDF":
                    raise AnnouncementArtifactError("下载内容不是有效 PDF")
            temporary.replace(target)
            return target, False
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def extract_text(pdf_path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise AnnouncementArtifactError("缺少 pypdf，无法提取公告文本") from exc
        try:
            reader = PdfReader(str(pdf_path))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            return "\n\n".join(text for text in pages if text)
        except Exception as exc:
            raise AnnouncementArtifactError(f"PDF 文本提取失败：{type(exc).__name__}") from exc

    @staticmethod
    def _safe_name(value: str, limit: int) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")[:limit] or "announcement"
