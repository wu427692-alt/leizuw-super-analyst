# -*- coding: utf-8 -*-
"""Bounded, allowlisted broker-report PDF download and text extraction.

The research-report library deliberately stores metadata and original links.
This service is the narrow bridge that may turn a small number of explicitly
allowlisted HTTPS PDFs into cached, auditable text for one research task.  It
does not crawl arbitrary URLs and it never treats a link as extracted text.
"""

from __future__ import annotations

from hashlib import sha256
import ipaddress
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

import requests


class BrokerReportArtifactError(RuntimeError):
    """Safe, bounded failure raised for one broker-report artifact."""


class BrokerReportArtifactService:
    """Download and extract a broker report without opening a generic SSRF path."""

    _DEFAULT_ALLOWED_HOSTS = ("pdf.dfcfw.com",)

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        cache_dir: Optional[Path] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
    ) -> None:
        self.session = session or requests.Session()
        configured_hosts = allowed_hosts if allowed_hosts is not None else self._configured_hosts()
        self.allowed_hosts = frozenset(self._validated_host(value) for value in configured_hosts)
        if not self.allowed_hosts:
            raise BrokerReportArtifactError("券商研报 PDF 允许域名不能为空")
        root_value = cache_dir or Path(
            os.getenv("INDUSTRY_RESEARCH_BROKER_PDF_CACHE_DIR", "./data/research_report_pdf_cache")
        ).expanduser()
        self.cache_dir = Path(root_value).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_pdf_bytes = self._bounded_int(
            "INDUSTRY_RESEARCH_BROKER_PDF_MAX_MB", default=25, minimum=1, maximum=80,
        ) * 1024 * 1024
        self.max_pages = self._bounded_int(
            "INDUSTRY_RESEARCH_BROKER_PDF_MAX_PAGES", default=160, minimum=1, maximum=500,
        )
        self.max_text_chars = self._bounded_int(
            "INDUSTRY_RESEARCH_BROKER_PDF_MAX_CHARS", default=120_000, minimum=10_000, maximum=500_000,
        )
        self._cache_lock = threading.RLock()

    def fetch_text(self, url: str) -> Dict[str, Any]:
        """Return cached/extracted PDF text and immutable content metadata."""

        normalized_url = self._validate_url(url)
        cache_key = sha256(normalized_url.encode("utf-8")).hexdigest()
        target_dir = self.cache_dir / cache_key[:2]
        target = target_dir / f"{cache_key}.pdf"
        with self._cache_lock:
            target_dir.mkdir(parents=True, exist_ok=True)
            cached = self._is_pdf(target)
            if not cached:
                self._download(normalized_url, target)
            document_hash = self._file_hash(target)
        text, page_count, pages_read = self.extract_text(target)
        if not text.strip():
            raise BrokerReportArtifactError("券商研报 PDF 未提取到可用文字，可能是扫描版")
        return {
            "url": normalized_url,
            "cache_key": cache_key,
            "cached": cached,
            "document_hash": document_hash,
            "document_bytes": target.stat().st_size,
            "text": text,
            "text_hash": sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
            "text_chars": len(text),
            "page_count": page_count,
            "pages_read": pages_read,
            "extraction_method": "pypdf_text",
        }

    def extract_text(self, path: Path) -> tuple[str, int, int]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise BrokerReportArtifactError("缺少 pypdf，无法提取券商研报正文") from exc
        try:
            reader = PdfReader(str(path))
            page_count = len(reader.pages)
            pages_read = min(page_count, self.max_pages)
            chunks = []
            current_chars = 0
            for page in reader.pages[:pages_read]:
                value = (page.extract_text() or "").strip()
                if not value:
                    continue
                remaining = self.max_text_chars - current_chars
                if remaining <= 0:
                    break
                chunks.append(value[:remaining])
                current_chars += min(len(value), remaining)
            text = "\n\n".join(chunks).strip()[: self.max_text_chars]
            return text, page_count, pages_read
        except BrokerReportArtifactError:
            raise
        except Exception as exc:
            raise BrokerReportArtifactError(f"券商研报 PDF 文本提取失败：{type(exc).__name__}") from exc

    def _download(self, url: str, target: Path) -> None:
        temporary_path: Optional[Path] = None
        try:
            with self.session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf"},
                timeout=(10, 60),
                stream=True,
                allow_redirects=False,
            ) as response:
                status_code = int(getattr(response, "status_code", 200) or 200)
                if 300 <= status_code < 400:
                    raise BrokerReportArtifactError("券商研报 PDF 返回重定向，已拒绝跨域跟随")
                response.raise_for_status()
                declared = self._content_length(getattr(response, "headers", {}))
                if declared > self.max_pdf_bytes:
                    raise BrokerReportArtifactError("券商研报 PDF 超过单文件大小上限")
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=f"{target.stem}.", suffix=".part", dir=target.parent, delete=False,
                ) as output:
                    temporary_path = Path(output.name)
                    size = 0
                    header = b""
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        if len(header) < 4:
                            header = (header + bytes(chunk))[:4]
                            if len(header) == 4 and header != b"%PDF":
                                raise BrokerReportArtifactError("下载内容不是有效 PDF")
                        size += len(chunk)
                        if size > self.max_pdf_bytes:
                            raise BrokerReportArtifactError("券商研报 PDF 超过单文件大小上限")
                        output.write(chunk)
                    if size <= 4 or header != b"%PDF":
                        raise BrokerReportArtifactError("下载内容不是有效 PDF")
            if temporary_path is None:
                raise BrokerReportArtifactError("券商研报 PDF 下载未生成文件")
            temporary_path.replace(target)
            temporary_path = None
        except BrokerReportArtifactError:
            raise
        except Exception as exc:
            raise BrokerReportArtifactError(f"券商研报 PDF 下载失败：{type(exc).__name__}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _validate_url(self, value: str) -> str:
        url = str(value or "").strip()
        parsed = urlparse(url)
        host = str(parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise BrokerReportArtifactError("券商研报 PDF 地址端口无效") from exc
        if (
            parsed.scheme.lower() != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or host not in self.allowed_hosts
            or not parsed.path.lower().endswith(".pdf")
        ):
            raise BrokerReportArtifactError("券商研报 PDF 地址不在允许的 HTTPS 公网域名")
        return url

    @classmethod
    def _configured_hosts(cls) -> tuple[str, ...]:
        configured = str(os.getenv("INDUSTRY_RESEARCH_BROKER_PDF_HOSTS", "") or "")
        values = [value.strip() for value in configured.split(",") if value.strip()]
        return tuple(dict.fromkeys([*cls._DEFAULT_ALLOWED_HOSTS, *values]))

    @staticmethod
    def _validated_host(value: str) -> str:
        host = str(value or "").strip().lower().rstrip(".")
        if not host or not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
            raise BrokerReportArtifactError("券商研报 PDF 允许域名格式无效")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
            raise BrokerReportArtifactError("券商研报 PDF 允许域名必须是公网域名")
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return host
        if not address.is_global:
            raise BrokerReportArtifactError("券商研报 PDF 允许域名不能是内网地址")
        # Raw IP allowlisting is intentionally rejected even for public IPs;
        # an explicit, auditable DNS host is required.
        raise BrokerReportArtifactError("券商研报 PDF 允许域名必须使用域名而不是 IP")

    @staticmethod
    def _is_pdf(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size <= 4:
            return False
        try:
            with path.open("rb") as handle:
                return handle.read(4) == b"%PDF"
        except OSError:
            return False

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(128 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _content_length(headers: Any) -> int:
        try:
            return max(0, int((headers or {}).get("Content-Length") or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _bounded_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))
