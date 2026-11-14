# -*- coding: utf-8 -*-
"""Package research-report PDFs into a one-click ZIP.

Protected sources such as pdf.dfcfw.com challenge the container's egress IP.
The service first tries a direct request, then falls back to the macOS host
fetcher (host.docker.internal:8765) which uses the clean host network.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from contextvars import copy_context

from src.config import runtime_data_directory
from src.services.workspace import workspace_id, workspace_directory, workspace_owner
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

# 默认可写目录来自运行时数据目录（DATABASE_PATH 的同级目录）。Docker 显式设置
# DATABASE_PATH=/app/data/stock_analysis.db，默认值因此仍是 /app/data/...，行为不变；
# 非容器部署落在仓库 ./data 下，不再因 mkdir('/app') 抛 OSError。
DB_PATH = os.environ.get("DATABASE_PATH", str(runtime_data_directory() / "stock_analysis.db"))
DOWNLOAD_DIR = Path(os.environ.get("REPORT_DOWNLOAD_DIR") or runtime_data_directory() / "downloads")


def _workspace_name(workspace: Optional[str] = None) -> str:
    return workspace_id(workspace)


def _output_dir(workspace: Optional[str] = None) -> Path:
    return workspace_directory(DOWNLOAD_DIR, workspace, create=True)


def _download_url(filename: str, workspace: Optional[str] = None) -> str:
    ws = _workspace_name(workspace)
    prefix = f"{quote(ws)}/" if ws else ""
    return f"/api/v1/financial-data/downloads/{prefix}{quote(filename)}"
CACHE_DIR = Path(os.environ.get("REPORT_CACHE_DIR") or runtime_data_directory() / "report_cache")
FETCH_URL = os.environ.get("REPORT_FETCH_URL", "http://host.docker.internal:8765/fetch")
FETCH_TOKEN = os.environ.get("REPORT_FETCH_TOKEN", "")

_TASKS: Dict[str, Dict[str, Any]] = {}
_TASK_LOCK = threading.Lock()


def _safe_name(value: Any, limit: int = 80) -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text or "report")[:limit]


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


class ReportPackService:
    def __init__(self) -> None:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def list_reports(
        self,
        symbol: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 10,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["pdf_url IS NOT NULL", "pdf_url <> ''"]
        params: List[Any] = []
        code = (symbol or "").strip().upper().split(".", 1)[0]
        word = (keyword or "").strip()
        if code and word:
            clauses.append("(ts_code LIKE ? OR title LIKE ? OR company_name LIKE ?)")
            params.extend([code + "%", f"%{word}%", f"%{word}%"])
        elif code:
            clauses.append("(ts_code LIKE ? OR title LIKE ? OR company_name LIKE ?)")
            params.extend([code + "%", f"%{code}%", f"%{code}%"])
        elif word:
            clauses.append("(title LIKE ? OR company_name LIKE ?)")
            params.extend([f"%{word}%", f"%{word}%"])
        else:
            return []
        if begin_date:
            clauses.append("trade_date >= ?")
            params.append(str(begin_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(str(end_date))
        sql = (
            "SELECT ts_code, title, broker, trade_date, pdf_url, company_name "
            "FROM research_report_records WHERE " + " AND ".join(clauses) +
            " ORDER BY trade_date DESC LIMIT ?"
        )
        params.append(max(1, min(int(limit), 20)))
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
        reports = []
        for row in rows:
            reports.append(
                {
                    "ts_code": row[0] or "",
                    "title": row[1] or "",
                    "broker": row[2] or "",
                    "date": row[3] or "",
                    "pdf_url": row[4] or "",
                    "company_name": row[5] or "",
                }
            )
        return reports

    def _from_host(self, url: str) -> Optional[bytes]:
        try:
            response = requests.get(
                FETCH_URL,
                params={"url": url},
                headers={"X-Fetch-Token": FETCH_TOKEN} if FETCH_TOKEN else {},
                timeout=90,
            )
            if response.status_code != 200:
                return None
            return response.content
        except Exception:
            return None

    def _download(self, report: Dict[str, Any]) -> Dict[str, Any]:
        url = report["pdf_url"]
        cache_path = CACHE_DIR / f"{_sha1(url)}.pdf"
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            with open(cache_path, "rb") as handle:
                if handle.read(5) == b"%PDF-":
                    return {**report, "ok": True, "bytes": cache_path.stat().st_size, "cached": True}

        session = requests.Session()
        session.trust_env = False
        data: Optional[bytes] = None
        try:
            response = session.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                    "Referer": "https://data.eastmoney.com/",
                    "Accept": "application/pdf,*/*",
                },
                timeout=30,
            )
            if response.status_code == 200 and response.content[:5] == b"%PDF-":
                data = response.content
        except Exception:
            data = None

        if data is None:
            data = self._from_host(url)

        if not data or data[:5] != b"%PDF-":
            return {**report, "ok": False, "error": "PDF 下载失败或不是有效 PDF"}

        cache_path.write_bytes(data)
        return {**report, "ok": True, "bytes": len(data), "cached": False}

    def package(
        self,
        symbol: Optional[str] = None,
        keyword: Optional[str] = None,
        urls: Optional[List[str]] = None,
        limit: int = 10,
        progress_cb=None,
        workspace: Optional[str] = None,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        reports = self.list_reports(symbol=symbol, keyword=keyword, limit=limit, begin_date=begin_date, end_date=end_date)
        seen = {report["pdf_url"] for report in reports}
        for index, url in enumerate(urls or [], 1):
            url = str(url or "").strip()
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            reports.append(
                {
                    "ts_code": "",
                    "title": f"外部研报 {index}",
                    "broker": "",
                    "date": "",
                    "pdf_url": url,
                    "company_name": "",
                }
            )
        if not reports:
            return {"ok": False, "error": "没有找到可下载的研报"}
        if progress_cb:
            progress_cb(5, f"找到 {len(reports)} 份研报")

        downloaded: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        done = 0
        total = max(1, len(reports))
        with ThreadPoolExecutor(max_workers=min(6, len(reports))) as pool:
            futures = {pool.submit(self._download, report): report for report in reports}
            for future in as_completed(futures):
                result = future.result()
                done += 1
                if result.get("ok"):
                    downloaded.append(result)
                else:
                    failed.append(result)
                if progress_cb:
                    progress_cb(5 + int(85 * done / total), f"下载 {done}/{total}")

        if not downloaded:
            return {
                "ok": False,
                "error": "所有研报 PDF 下载失败",
                "failed": [{"title": item.get("title"), "error": item.get("error")} for item in failed],
            }

        if progress_cb:
            progress_cb(92, "正在打包 ZIP")
        label = (symbol or keyword or "reports").strip()
        subject = next(
            (str(item.get("company_name") or "").strip() for item in downloaded if item.get("company_name")),
            "",
        ) or (symbol or keyword or "研报")
        subject = _safe_name(subject, 24)
        kind = "研报"
        date_text = datetime.now().strftime("%Y-%m-%d")
        key = _sha1("|".join(sorted(item["pdf_url"] for item in downloaded)) + "|" + label)[:10]
        filename = f"{subject}_{kind}_{date_text}_{len(downloaded)}份_{key}.zip"
        zip_path = _output_dir(workspace) / filename
        if not zip_path.is_file():
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
                manifest = ["研报打包清单", f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
                for item in downloaded:
                    base = "_".join(
                        part for part in [
                            _safe_name(item.get("date"), 12),
                            _safe_name(item.get("broker"), 20),
                            _safe_name(item.get("title"), 60),
                        ] if part
                    ) or _safe_name(item.get("title"))
                    name = base if base.lower().endswith(".pdf") else base + ".pdf"
                    archive.write(CACHE_DIR / f"{_sha1(item['pdf_url'])}.pdf", arcname=name)
                    manifest.append(f"- {item.get('title')} | {item.get('broker')} | {item.get('date')} | {item['pdf_url']}")
                archive.writestr("清单.txt", "\n".join(manifest).encode("utf-8-sig"))

        size = zip_path.stat().st_size
        if progress_cb:
            progress_cb(100, "打包完成")
        return {
            "ok": True,
            "title": f"{subject} {kind}（{len(downloaded)}/{len(reports)} 份）",
            "filename": filename,
            "size": size,
            "download_url": _download_url(filename, workspace),
            "report_count": len(downloaded),
            "requested_count": len(reports),
            "failed_count": len(failed),
            "partial": bool(failed),
            "reports": [
                {
                    "title": item.get("title"),
                    "broker": item.get("broker"),
                    "date": item.get("date"),
                    "bytes": item.get("bytes"),
                }
                for item in downloaded
            ],
            "failed": [
                {"title": item.get("title"), "error": item.get("error")}
                for item in failed
            ],
        }

    def package_documents(
        self,
        items: List[Dict[str, Any]],
        label: str = "文件包",
        retries: int = 1,
        missing: Optional[List[Dict[str, Any]]] = None,
        kind: Optional[str] = None,
        subject: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized: List[Dict[str, Any]] = []
        seen = set()
        for item in items or []:
            url = str(item.get("url") or item.get("pdf_url") or "").strip()
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            normalized.append(
                {
                    "title": str(item.get("title") or "文件"),
                    "url": url,
                    "pdf_url": url,
                    "date": str(item.get("date") or ""),
                    "broker": str(item.get("broker") or ""),
                }
            )
        missing = list(missing or [])
        if not normalized and not missing:
            return {"ok": False, "error": "没有可打包的文件"}

        def fetch(item: Dict[str, Any]) -> Dict[str, Any]:
            last: Dict[str, Any] = {"ok": False, "error": "下载失败"}
            for _ in range(max(0, retries) + 1):
                last = self._download(item)
                if last.get("ok"):
                    return last
            return last

        downloaded: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        if normalized:
            with ThreadPoolExecutor(max_workers=min(6, len(normalized))) as pool:
                futures = {pool.submit(fetch, item): item for item in normalized}
                for future in as_completed(futures):
                    result = future.result()
                    if result.get("ok"):
                        downloaded.append(result)
                    else:
                        failed.append(result)

        if not downloaded:
            return {
                "ok": False,
                "error": "所有文件下载失败",
                "requested_count": len(normalized) + len(missing),
                "failed_count": len(normalized) + len(missing),
                "failed": [
                    {"title": item.get("title"), "error": item.get("error")}
                    for item in (normalized + missing)
                ],
            }

        kind_text = _safe_name(kind or "文件", 8)
        subject_text = _safe_name(subject or "", 24)
        label_text = str(label or f"{subject_text} {kind_text}").strip()[:40]
        date_text = datetime.now().strftime("%Y-%m-%d")
        key = _sha1("|".join(sorted(item["url"] for item in normalized)) + "|" + label_text)[:10]
        filename = f"{subject_text + '_' if subject_text else ''}{kind_text}_{date_text}_{len(downloaded)}份_{key}.zip"
        zip_path = _output_dir(workspace) / filename
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
            manifest = [
                f"{label_text} 打包清单",
                f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"请求 {len(normalized) + len(missing)} 份｜成功 {len(downloaded)} 份｜失败 {len(failed) + len(missing)} 份",
                "",
            ]
            for item in downloaded:
                base = "_".join(
                    part for part in [
                        _safe_name(item.get("date"), 12),
                        _safe_name(item.get("broker"), 20),
                        _safe_name(item.get("title"), 60),
                    ] if part
                ) or _safe_name(item.get("title"))
                name = base if base.lower().endswith(".pdf") else base + ".pdf"
                archive.write(CACHE_DIR / f"{_sha1(item['pdf_url'])}.pdf", arcname=name)
                manifest.append(f"[成功] {item.get('title')} | {item.get('date')} | {item['pdf_url']}")
            for item in failed:
                manifest.append(f"[失败] {item.get('title')} | {item.get('error')} | {item.get('pdf_url')}")
            for item in missing:
                manifest.append(f"[缺失] {item.get('title')} | {item.get('error')}")
            archive.writestr("清单.txt", "\n".join(manifest).encode("utf-8-sig"))

        size = zip_path.stat().st_size
        total = len(normalized) + len(missing)
        failed_count = len(failed) + len(missing)
        return {
            "ok": True,
            "title": f"{label_text}（{len(downloaded)}/{total} 份）",
            "filename": filename,
            "size": size,
            "download_url": _download_url(filename, workspace),
            "requested_count": total,
            "report_count": len(downloaded),
            "failed_count": failed_count,
            "partial": failed_count > 0,
            "files": [
                {"title": item.get("title"), "date": item.get("date"), "url": item.get("pdf_url")}
                for item in downloaded
            ],
            "failed": [
                {"title": item.get("title"), "error": item.get("error")}
                for item in (failed + missing)
            ],
        }

    def package_essays(
        self, items: List[Dict[str, Any]], subject: str = "小作文",
        workspace: Optional[str] = None, retrieval: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cleaned = [dict(item) for item in (items or []) if isinstance(item, dict)]
        if not cleaned:
            return {"ok": False, "error": "没有可打包的小作文"}
        subject_text = _safe_name(subject or "小作文", 24)
        date_text = datetime.now().strftime("%Y-%m-%d")
        # Every export is immutable: equal titles can have different original
        # text, scopes or partial results. Never overwrite an existing download.
        key = uuid.uuid4().hex[:12]
        coverage = "_部分" if retrieval is not None and not retrieval.get("complete") else ""
        filename = f"{subject_text}_小作文{coverage}_{date_text}_{len(cleaned)}条_{key}.zip"
        zip_path = _output_dir(workspace) / filename

        scalar_fields: List[str] = ["topic_id", "title", "published_at", "source", "author", "content", "summary"]
        for item in cleaned:
            for field, value in item.items():
                if field not in scalar_fields and not isinstance(value, (dict, list)):
                    scalar_fields.append(field)

        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=scalar_fields, extrasaction="ignore")
        writer.writeheader()
        for item in cleaned:
            writer.writerow({field: item.get(field) for field in scalar_fields})

        lines = [
            "# 小作文清单",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"共 {len(cleaned)} 条",
            "",
        ]
        if retrieval is not None:
            lines.extend([
                "## 检索与完整性",
                f"状态：{'完整获取' if retrieval.get('complete') else '部分获取，不是全部结果'}",
                f"筛选条件：{json.dumps(retrieval.get('filters') or {}, ensure_ascii=False, default=str)}",
                f"检索命中：{retrieval.get('expected_total')} 条；实际导出：{len(cleaned)} 条；去重：{retrieval.get('duplicates', 0)} 条",
                *[f"注意：{error}" for error in retrieval.get("errors") or []],
                "",
            ])
        for index, item in enumerate(cleaned, 1):
            lines.append(f"## {index}. {item.get('title') or '未命名'}")
            meta = [
                str(item.get(key) or "")
                for key in ("published_at", "created_at", "date", "source", "stock", "sentiment", "importance")
                if item.get(key)
            ]
            if meta:
                lines.append(" · ".join(meta))
            content = item.get("content")
            if content:
                lines.extend(["### 原文全文", str(content)])
            summary = item.get("summary") or item.get("snippet")
            if summary and summary != content:
                lines.extend(["### 现有分析摘要", str(summary)])
            url = item.get("url") or item.get("source_url")
            if url:
                lines.append(str(url))
            lines.append("")

        temporary_path = zip_path.with_suffix(f".zip.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("小作文.json", json.dumps(cleaned, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
                archive.writestr("小作文.csv", csv_buffer.getvalue().encode("utf-8-sig"))
                archive.writestr("清单.md", "\n".join(lines).encode("utf-8-sig"))
                if retrieval is not None:
                    archive.writestr("检索与完整性.json", json.dumps(retrieval, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
            os.replace(temporary_path, zip_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        size = zip_path.stat().st_size
        return {
            "ok": True,
            "title": f"{subject_text} 小作文（{len(cleaned)} 条）",
            "filename": filename,
            "size": size,
            "download_url": _download_url(filename, workspace),
            "count": len(cleaned),
        }

    def _prewarm(self, filename: str, workspace: Optional[str] = None) -> None:
        """Private files are not CDN objects; do not re-download or mint a session."""
        return None

    # ---------------- background tasks ----------------

    def start_package(
        self,
        symbol: Optional[str] = None,
        keyword: Optional[str] = None,
        urls: Optional[List[str]] = None,
        limit: int = 10,
        workspace: Optional[str] = None,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        owner = workspace_owner()
        workspace = _workspace_name(workspace)
        task_id = uuid.uuid4().hex
        with _TASK_LOCK:
            _TASKS[task_id] = {
                "task_id": task_id,
                "owner": owner,
                "status": "pending",
                "progress": 0,
                "message": "排队中",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "created_ts": time.time(),
                "result": None,
                "error": None,
            }
        thread = threading.Thread(
            target=copy_context().run,
            args=(self._run_task, task_id, symbol, keyword, urls, limit, workspace, begin_date, end_date),
            daemon=True,
        )
        thread.start()
        return {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "poll_path": f"/api/v1/financial-data/reports/package/tasks/{task_id}",
        }

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        owner = workspace_owner()
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            return {k: v for k, v in task.items() if k != "owner"} if task and task.get("owner") == owner else None

    def _update_task(self, task_id: str, **fields: Any) -> None:
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if task is not None:
                task.update(fields)

    def _run_task(
        self,
        task_id: str,
        symbol: Optional[str],
        keyword: Optional[str],
        urls: Optional[List[str]],
        limit: int,
        workspace: Optional[str] = None,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> None:
        self._update_task(task_id, status="processing", message="正在整理研报")
        try:
            result = self.package(
                symbol=symbol,
                keyword=keyword,
                urls=urls,
                limit=limit,
                workspace=workspace,
                begin_date=begin_date,
                end_date=end_date,
                progress_cb=lambda progress, message: self._update_task(
                    task_id, status="processing", progress=progress, message=message
                ),
            )
            if result.get("ok"):
                self._update_task(
                    task_id,
                    status="completed",
                    progress=100,
                    message="打包完成",
                    result=result,
                )
            else:
                self._update_task(
                    task_id,
                    status="failed",
                    error=result.get("error") or "打包失败",
                    result=result,
                )
        except Exception as exc:  # noqa: BLE001
            self._update_task(task_id, status="failed", error=str(exc)[:300])
