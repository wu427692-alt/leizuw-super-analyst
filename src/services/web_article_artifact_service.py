# -*- coding: utf-8 -*-
"""Bounded, allowlisted HTML retrieval for research evidence.

Search-result snippets are useful for discovery but are not proof that the
underlying page was read.  This service is the narrow bridge from a small set
of allowlisted public HTTPS pages to immutable, cached text.  It deliberately
does not expose a generic URL fetcher.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import unescape as html_unescape
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
from typing import Any, Callable, Dict, Iterable, Optional, Sequence
from urllib.parse import urldefrag, urljoin, urlparse

from lxml import etree, html
import requests


class WebArticleArtifactError(RuntimeError):
    """Safe, bounded failure raised for one web document."""


class WebArticleArtifactService:
    """Fetch and extract a few research pages without opening an SSRF path."""

    # These are auditable source families rather than a blanket Internet
    # permission.  Deployments may add exact organizations through the env
    # variable, for example a listed company's official IR hostname.
    _DEFAULT_ALLOWED_HOSTS = (
        "gov.cn",
        "csrc.gov.cn",
        "sse.com.cn",
        "szse.cn",
        "cninfo.com.cn",
        "pbc.gov.cn",
        "miit.gov.cn",
        "stats.gov.cn",
        "ndrc.gov.cn",
        "mof.gov.cn",
        "samr.gov.cn",
        "sasac.gov.cn",
        "mofcom.gov.cn",
        "chinatax.gov.cn",
        "caict.ac.cn",
        "ieee.org",
        "itu.int",
        "iso.org",
        "nist.gov",
        "sec.gov",
        "ifrs.org",
        "cfainstitute.org",
        "worldbank.org",
        "oecd.org",
        "imf.org",
        "people.com.cn",
        "xinhuanet.com",
        "cs.com.cn",
        "cnstock.com",
        "stcn.com",
        "yicai.com",
        "cls.cn",
        "eastmoney.com",
    )
    _FORBIDDEN_ALLOWLIST_SUFFIXES = frozenset({
        "com", "cn", "net", "org", "com.cn", "net.cn", "org.cn", "co", "io",
    })
    _JAVASCRIPT_MIME_TYPES = frozenset({
        "application/javascript",
        "text/javascript",
        "application/ecmascript",
        "text/ecmascript",
    })

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        cache_dir: Optional[Path] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
        exact_allowed_hosts: Optional[Iterable[str]] = None,
        resolver: Optional[Callable[[str], Sequence[str]]] = None,
    ) -> None:
        self.session = session or requests.Session()
        configured = allowed_hosts if allowed_hosts is not None else self._configured_hosts()
        self.allowed_hosts = frozenset(self._validated_allowlist_host(value) for value in configured)
        self.exact_allowed_hosts = frozenset(
            self._validated_allowlist_host(value) for value in (exact_allowed_hosts or ())
        )
        if not self.allowed_hosts and not self.exact_allowed_hosts:
            raise WebArticleArtifactError("网页正文允许域名不能为空")
        root_value = cache_dir or Path(
            os.getenv("INDUSTRY_RESEARCH_WEB_CACHE_DIR", "./data/research_web_cache")
        ).expanduser()
        self.cache_dir = Path(root_value).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_html_bytes = self._bounded_int(
            "INDUSTRY_RESEARCH_WEB_MAX_MB", default=2, minimum=1, maximum=10,
        ) * 1024 * 1024
        self.max_text_chars = self._bounded_int(
            "INDUSTRY_RESEARCH_WEB_MAX_CHARS", default=60_000, minimum=5_000, maximum=200_000,
        )
        self.min_text_chars = self._bounded_int(
            "INDUSTRY_RESEARCH_WEB_MIN_CHARS", default=300, minimum=100, maximum=2_000,
        )
        self.max_redirects = self._bounded_int(
            "INDUSTRY_RESEARCH_WEB_MAX_REDIRECTS", default=2, minimum=0, maximum=4,
        )
        self.connect_timeout = self._bounded_float(
            "INDUSTRY_RESEARCH_WEB_CONNECT_TIMEOUT_SEC", default=4.0, minimum=1.0, maximum=10.0,
        )
        self.read_timeout = self._bounded_float(
            "INDUSTRY_RESEARCH_WEB_READ_TIMEOUT_SEC", default=15.0, minimum=3.0, maximum=30.0,
        )
        self.cache_ttl_seconds = self._bounded_int(
            "INDUSTRY_RESEARCH_WEB_CACHE_TTL_HOURS", default=24, minimum=1, maximum=168,
        ) * 3600
        self._resolver = resolver or self._resolve_addresses
        self._cache_lock = threading.RLock()

    @classmethod
    def normalize_exact_https_url(cls, value: Any) -> tuple[str, str]:
        """Normalize one first-party HTTPS URL without widening its host.

        Company websites come from an already resolved listed-company profile,
        not from arbitrary user input.  This helper still rejects credentials,
        non-HTTPS schemes, IP/local names and malformed domains.  DNS is checked
        again immediately before the fetch by :meth:`fetch_text`.
        """

        candidate = str(value or "").strip()
        if not candidate:
            raise WebArticleArtifactError("公司官网地址为空")
        if "\\" in candidate or any(character.isspace() for character in candidate):
            raise WebArticleArtifactError("公司官网地址包含无效分隔符或空白")
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        url = urldefrag(candidate)[0]
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise WebArticleArtifactError("公司官网端口无效") from exc
        host = cls._validated_allowlist_host(str(parsed.hostname or ""))
        if (
            parsed.scheme.lower() != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or (parsed.path and not parsed.path.startswith("/"))
        ):
            raise WebArticleArtifactError("公司官网必须是无账号信息的 HTTPS 公网地址")
        return url, host

    def with_exact_allowed_hosts(self, hosts: Iterable[str]) -> "WebArticleArtifactService":
        """Return a task-scoped fetcher that allows only the exact extra hosts.

        The original/global fetcher is intentionally left untouched.  Exact
        hosts do not authorize sibling or child subdomains, and redirects stay
        inside the same exact host through ``_allowlist_scope``.
        """

        exact_hosts = tuple(dict.fromkeys([
            *self.exact_allowed_hosts,
            *(self._validated_allowlist_host(value) for value in hosts),
        ]))
        scoped = WebArticleArtifactService(
            session=self.session,
            cache_dir=self.cache_dir,
            allowed_hosts=self.allowed_hosts,
            exact_allowed_hosts=exact_hosts,
            resolver=self._resolver,
        )
        for attribute in (
            "max_html_bytes", "max_text_chars", "min_text_chars", "max_redirects",
            "connect_timeout", "read_timeout", "cache_ttl_seconds",
        ):
            setattr(scoped, attribute, getattr(self, attribute))
        scoped._cache_lock = self._cache_lock
        return scoped

    def can_fetch(self, url: str) -> bool:
        """Return whether the URL shape and hostname match the static allowlist.

        DNS is intentionally checked only immediately before network access so
        candidate ranking remains deterministic and does not trigger lookups.
        """

        try:
            self._validate_url(url, resolve=False)
            return True
        except WebArticleArtifactError:
            return False

    def fetch_text(
        self,
        url: str,
        *,
        allow_same_origin_module_fallback: bool = False,
    ) -> Dict[str, Any]:
        """Return cached/extracted page text and immutable content metadata."""

        requested_url = self._validate_url(url, resolve=True)
        cache_key = sha256(requested_url.encode("utf-8")).hexdigest()
        target_dir = self.cache_dir / cache_key[:2]
        raw_target = target_dir / f"{cache_key}.html"
        meta_target = target_dir / f"{cache_key}.json"
        with self._cache_lock:
            target_dir.mkdir(parents=True, exist_ok=True)
            cached_payload = self._read_cache(raw_target, meta_target)
            if cached_payload is not None and len(cached_payload[0]) > self.max_html_bytes:
                cached_payload = None
            cached = cached_payload is not None
            if cached_payload is None:
                document, final_url, content_type = self._download(requested_url)
                document_hash = sha256(document).hexdigest()
                metadata = {
                    "requested_url": requested_url,
                    "final_url": final_url,
                    "content_type": content_type,
                    "document_hash": document_hash,
                    "document_bytes": len(document),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                self._atomic_write(raw_target, document)
                self._atomic_write(
                    meta_target,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                )
            else:
                document, metadata = cached_payload
                final_url = str(metadata.get("final_url") or requested_url)
                content_type = str(metadata.get("content_type") or "text/html")
                document_hash = str(metadata.get("document_hash") or sha256(document).hexdigest())

        text, title, extraction_method = self.extract_text(document, content_type=content_type)
        spa_module: Optional[Dict[str, Any]] = None
        effective_document_hash = document_hash
        effective_document_bytes = len(document)
        if len(text) < self.min_text_chars:
            final_host = str(urlparse(final_url).hostname or "").lower().rstrip(".")
            if not allow_same_origin_module_fallback or final_host not in self.exact_allowed_hosts:
                raise WebArticleArtifactError("网页正文过短，不能作为已读全文证据")
            spa_module = self._extract_same_origin_module_text(
                document=document,
                landing_url=final_url,
                html_document_hash=document_hash,
            )
            text = str(spa_module.get("text") or "")
            extraction_method = "same_origin_module_static_strings"
            effective_document_hash = str(spa_module["combined_document_hash"])
            effective_document_bytes += int(spa_module["document_bytes"])
            cached = bool(cached and spa_module.get("cached"))
        text_hash = sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        evidence_digest = sha256(
            f"{final_url}:{effective_document_hash}".encode("utf-8")
        ).hexdigest()[:24]
        evidence_id = f"web_fulltext:{evidence_digest}"
        payload = {
            "requested_url": requested_url,
            "final_url": final_url,
            "cache_key": cache_key,
            "cached": cached,
            "fetched_at": metadata.get("fetched_at"),
            "content_type": content_type,
            "document_hash": effective_document_hash,
            "document_bytes": effective_document_bytes,
            "html_document_hash": document_hash,
            "html_document_bytes": len(document),
            "text": text,
            "text_hash": text_hash,
            "text_chars": len(text),
            "title": title,
            "evidence_id": evidence_id,
            "extraction_method": extraction_method,
        }
        if spa_module is not None:
            module_document = {
                key: spa_module.get(key)
                for key in (
                    "requested_url", "final_url", "content_type", "document_hash",
                    "document_bytes", "fetched_at", "cached",
                )
            }
            payload.update({
                "asset_url": spa_module.get("final_url"),
                "asset_requested_url": spa_module.get("requested_url"),
                "asset_document_hash": spa_module.get("document_hash"),
                "asset_bytes": spa_module.get("document_bytes"),
                "asset_content_type": spa_module.get("content_type"),
                "asset_fetched_at": spa_module.get("fetched_at"),
                "asset_cached": spa_module.get("cached"),
                "module_documents": [module_document],
            })
        return payload

    def _extract_same_origin_module_text(
        self,
        *,
        document: bytes,
        landing_url: str,
        html_document_hash: str,
    ) -> Dict[str, Any]:
        """Read one same-origin module bundle for an exact-host SPA shell.

        This path never executes JavaScript and never follows imports. It only
        extracts bounded static string literals from the first top-level
        ``<script type="module" src>`` asset.
        """

        landing_host = str(urlparse(landing_url).hostname or "").lower().rstrip(".")
        if landing_host not in self.exact_allowed_hosts:
            raise WebArticleArtifactError("SPA module 回退仅允许任务级精确公司官网域名")
        module_url = self._first_same_origin_module_url(
            document=document,
            landing_url=landing_url,
            required_origin_host=landing_host,
        )
        module = self._fetch_module_asset(
            module_url,
            required_origin_host=landing_host,
        )
        text = self._extract_module_human_text(
            module["document"],
            content_type=str(module.get("content_type") or "application/javascript"),
        )
        if len(text) < self.min_text_chars:
            raise WebArticleArtifactError("同源 SPA module 没有足量可审计的人类可读静态文本")
        combined_manifest = "\0".join((
            "web-spa-v1",
            html_document_hash,
            str(module.get("final_url") or module_url),
            str(module.get("document_hash") or ""),
        )).encode("utf-8")
        return {
            **{key: value for key, value in module.items() if key != "document"},
            "text": text[: self.max_text_chars],
            "combined_document_hash": sha256(combined_manifest).hexdigest(),
        }

    def _first_same_origin_module_url(
        self,
        *,
        document: bytes,
        landing_url: str,
        required_origin_host: str,
    ) -> str:
        parser = html.HTMLParser(
            encoding=None,
            recover=True,
            remove_comments=True,
            no_network=True,
            huge_tree=False,
        )
        try:
            tree = html.fromstring(document, parser=parser)
        except (etree.ParserError, ValueError, TypeError) as exc:
            raise WebArticleArtifactError(f"SPA HTML 解析失败：{type(exc).__name__}") from exc
        module_src = ""
        for node in tree.xpath("//script[@src]"):
            if str(node.get("type") or "").strip().casefold() != "module":
                continue
            module_src = str(node.get("src") or "").strip()
            break
        if not module_src:
            raise WebArticleArtifactError("网页正文过短，且未发现外部 type=module 入口")
        if (
            len(module_src) > 2_048
            or "\\" in module_src
            or any(character.isspace() for character in module_src)
        ):
            raise WebArticleArtifactError("SPA module 地址格式无效")
        module_url = urldefrag(urljoin(landing_url, module_src))[0]
        validated = self._validate_url(module_url, resolve=False)
        module_host = str(urlparse(validated).hostname or "").lower().rstrip(".")
        if module_host != required_origin_host or module_host not in self.exact_allowed_hosts:
            raise WebArticleArtifactError("SPA module 必须与公司官网使用同一精确域名")
        return validated

    def _fetch_module_asset(
        self,
        requested_url: str,
        *,
        required_origin_host: str,
    ) -> Dict[str, Any]:
        requested_url = self._validate_url(requested_url, resolve=False)
        cache_key = sha256(requested_url.encode("utf-8")).hexdigest()
        target_dir = self.cache_dir / "module" / cache_key[:2]
        raw_target = target_dir / f"{cache_key}.js"
        meta_target = target_dir / f"{cache_key}.json"
        with self._cache_lock:
            target_dir.mkdir(parents=True, exist_ok=True)
            cached_payload = self._read_cache(raw_target, meta_target)
            cached = False
            if cached_payload is not None and len(cached_payload[0]) > self.max_html_bytes:
                cached_payload = None
            if cached_payload is not None:
                candidate_document, candidate_metadata = cached_payload
                final_url = str(candidate_metadata.get("final_url") or requested_url)
                content_type = str(candidate_metadata.get("content_type") or "")
                mime_type = content_type.split(";", 1)[0].strip().lower()
                try:
                    final_url = self._validate_url(final_url, resolve=False)
                except WebArticleArtifactError:
                    cached_payload = None
                final_host = str(urlparse(final_url).hostname or "").lower().rstrip(".")
                if (
                    cached_payload is not None
                    and final_host == required_origin_host
                    and mime_type in self._JAVASCRIPT_MIME_TYPES
                ):
                    document = candidate_document
                    metadata = candidate_metadata
                    cached = True
                else:
                    cached_payload = None
            if cached_payload is None:
                document, final_url, content_type = self._download_module(
                    requested_url,
                    required_origin_host=required_origin_host,
                )
                metadata = {
                    "requested_url": requested_url,
                    "final_url": final_url,
                    "content_type": content_type,
                    "document_hash": sha256(document).hexdigest(),
                    "document_bytes": len(document),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                self._atomic_write(raw_target, document)
                self._atomic_write(
                    meta_target,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                )
        return {
            "requested_url": requested_url,
            "final_url": str(metadata.get("final_url") or requested_url),
            "content_type": str(metadata.get("content_type") or ""),
            "document_hash": str(metadata.get("document_hash") or sha256(document).hexdigest()),
            "document_bytes": len(document),
            "fetched_at": metadata.get("fetched_at"),
            "cached": cached,
            "document": document,
        }

    def _download_module(
        self,
        requested_url: str,
        *,
        required_origin_host: str,
    ) -> tuple[bytes, str, str]:
        current_url = requested_url
        for redirect_index in range(self.max_redirects + 1):
            current_url = self._validate_url(current_url, resolve=True)
            current_host = str(urlparse(current_url).hostname or "").lower().rstrip(".")
            if current_host != required_origin_host or current_host not in self.exact_allowed_hosts:
                raise WebArticleArtifactError("SPA module 请求或重定向超出公司官网精确域名")
            try:
                with self.session.get(
                    current_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; LeziwuResearch/1.0)",
                        "Accept": "application/javascript,text/javascript;q=0.9",
                    },
                    timeout=(self.connect_timeout, self.read_timeout),
                    stream=True,
                    allow_redirects=False,
                ) as response:
                    status_code = int(getattr(response, "status_code", 200) or 200)
                    headers = getattr(response, "headers", {}) or {}
                    if 300 <= status_code < 400:
                        location = str(headers.get("Location") or "").strip()
                        if not location or redirect_index >= self.max_redirects:
                            raise WebArticleArtifactError("SPA module 重定向缺少地址或超过上限")
                        candidate_url = urldefrag(urljoin(current_url, location))[0]
                        next_host = str(urlparse(candidate_url).hostname or "").lower().rstrip(".")
                        if next_host != required_origin_host:
                            raise WebArticleArtifactError("SPA module 跨域重定向超出公司官网精确域名")
                        next_url = self._validate_url(candidate_url, resolve=True)
                        current_url = next_url
                        continue
                    response.raise_for_status()
                    content_type = str(headers.get("Content-Type") or "")
                    mime_type = content_type.split(";", 1)[0].strip().lower()
                    if mime_type not in self._JAVASCRIPT_MIME_TYPES:
                        raise WebArticleArtifactError("SPA module 响应不是允许的 JavaScript MIME 类型")
                    declared = self._content_length(headers)
                    if declared > self.max_html_bytes:
                        raise WebArticleArtifactError("SPA module 响应超过单文件大小上限")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_html_bytes:
                            raise WebArticleArtifactError("SPA module 响应超过单文件大小上限")
                        chunks.append(bytes(chunk))
                    if size <= 0:
                        raise WebArticleArtifactError("SPA module 响应为空")
                    return b"".join(chunks), current_url, content_type
            except WebArticleArtifactError:
                raise
            except Exception as exc:
                raise WebArticleArtifactError(f"SPA module 下载失败：{type(exc).__name__}") from exc
        raise WebArticleArtifactError("SPA module 重定向超过上限")

    def _extract_module_human_text(self, document: bytes, *, content_type: str) -> str:
        encoding = self._declared_charset(content_type) or "utf-8"
        try:
            source = document.decode(encoding, errors="replace")
        except LookupError:
            source = document.decode("utf-8", errors="replace")
        lines = []
        chinese_lines = []
        seen = set()
        total_chars = 0
        for raw_literal in self._iter_javascript_static_strings(source):
            value = self._decode_javascript_string(raw_literal)
            value = re.sub(r"(?:https?://|www\.)[^\s<>'\"]+", " ", value, flags=re.I)
            value = re.sub(r"</?[a-zA-Z][^>]{0,200}>", " ", value)
            value = self._clean_text(html_unescape(value))
            if not self._is_human_javascript_string(value):
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            remaining = self._bounded_slice(value, max(0, self.max_text_chars - total_chars))
            if not remaining:
                break
            lines.append(remaining)
            if len(re.findall(r"[\u3400-\u9fff]", remaining)) >= 2:
                chinese_lines.append(remaining)
            total_chars += len(remaining) + 1
            if total_chars >= self.max_text_chars:
                break
        chinese_text = "\n".join(chinese_lines)
        if len(chinese_text) >= self.min_text_chars:
            # Many small corporate templates ship generic English marketing
            # placeholders alongside the real Chinese company copy. Prefer
            # the sufficiently complete Chinese set to avoid mixing template
            # claims (employees/revenue/founding year) into research evidence.
            return chinese_text[: self.max_text_chars]
        return "\n".join(lines)

    @staticmethod
    def _iter_javascript_static_strings(source: str) -> Iterable[str]:
        index = 0
        length = len(source)
        quotes = {'"', "'", "`"}
        while index < length:
            if source.startswith("//", index):
                newline = source.find("\n", index + 2)
                index = length if newline < 0 else newline + 1
                continue
            if source.startswith("/*", index):
                end_comment = source.find("*/", index + 2)
                index = length if end_comment < 0 else end_comment + 2
                continue
            quote = source[index]
            if quote not in quotes:
                index += 1
                continue
            index += 1
            buffer = []
            closed = False
            overflow = False
            while index < length:
                character = source[index]
                if character == "\\" and index + 1 < length:
                    if len(buffer) < 8_000:
                        buffer.extend((character, source[index + 1]))
                    else:
                        overflow = True
                    index += 2
                    continue
                if character == quote:
                    closed = True
                    index += 1
                    break
                if quote != "`" and character in "\r\n":
                    break
                if len(buffer) < 8_000:
                    buffer.append(character)
                else:
                    overflow = True
                index += 1
            if closed and not overflow:
                raw = "".join(buffer)
                if quote != "`" or "${" not in raw:
                    yield raw

    @staticmethod
    def _decode_javascript_string(value: str) -> str:
        output = []
        index = 0
        escapes = {
            "n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
            "v": "\v", "0": "", "\\": "\\", "/": "/", "'": "'", '"': '"', "`": "`",
        }
        while index < len(value):
            character = value[index]
            if character != "\\" or index + 1 >= len(value):
                output.append(character)
                index += 1
                continue
            marker = value[index + 1]
            if marker in "\r\n":
                index += 2
                if marker == "\r" and index < len(value) and value[index] == "\n":
                    index += 1
                continue
            if marker == "u":
                if index + 2 < len(value) and value[index + 2] == "{":
                    end = value.find("}", index + 3, index + 11)
                    digits = value[index + 3:end] if end >= 0 else ""
                    consumed = end - index + 1 if end >= 0 else 2
                else:
                    digits = value[index + 2:index + 6]
                    consumed = 6
                try:
                    codepoint = int(digits, 16)
                    output.append(chr(codepoint) if codepoint <= 0x10FFFF else "")
                    index += consumed
                    continue
                except (ValueError, OverflowError):
                    pass
            if marker == "x":
                digits = value[index + 2:index + 4]
                try:
                    output.append(chr(int(digits, 16)))
                    index += 4
                    continue
                except ValueError:
                    pass
            output.append(escapes.get(marker, marker))
            index += 2
        decoded = "".join(output)
        try:
            return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return decoded

    @staticmethod
    def _is_human_javascript_string(value: str) -> bool:
        if len(value) < 8 or len(value) > 8_000:
            return False
        lowered = value.casefold()
        if any(marker in lowered for marker in (
            "sourcemappingurl", "node_modules", "webpack", "data:image", "application/wasm",
            "linear-gradient", "@keyframes", "transform:", "font-family:", "animation:",
        )):
            return False
        if re.fullmatch(r"(?:[./@~_-]?[^\s/]+/)+[^\s]+", value):
            return False
        if re.fullmatch(r"[a-zA-Z0-9_./:@?#=&%+~-]+", value):
            return False
        if re.search(r"\.(?:js|css|map|png|jpe?g|gif|svg|ico|woff2?|ttf)(?:[?#].*)?$", lowered):
            return False
        code_punctuation = sum(value.count(character) for character in "{}[]<>=;`")
        if code_punctuation > max(6, len(value) // 5):
            return False
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", value))
        english_words = re.findall(r"[A-Za-z]{2,}", value)
        visible = len(re.sub(r"\s+", "", value))
        natural = len(re.findall(r"[0-9A-Za-z\u3400-\u9fff]", value))
        if visible <= 0 or natural / visible < 0.35:
            return False
        return cjk_count >= 2 or (len(value) >= 30 and len(english_words) >= 5)

    @staticmethod
    def _bounded_slice(value: str, limit: int) -> str:
        if limit <= 0:
            return ""
        return value[:limit]

    def extract_text(self, document: bytes, *, content_type: str = "text/html") -> tuple[str, str, str]:
        encoding = self._declared_charset(content_type)
        parser = html.HTMLParser(
            encoding=encoding,
            recover=True,
            remove_comments=True,
            no_network=True,
            huge_tree=False,
        )
        try:
            tree = html.fromstring(document, parser=parser)
        except (etree.ParserError, ValueError, TypeError) as exc:
            raise WebArticleArtifactError(f"网页 HTML 解析失败：{type(exc).__name__}") from exc

        title = self._clean_text(" ".join(tree.xpath("//h1[1]//text()")))
        if not title:
            title = self._clean_text(" ".join(tree.xpath("//title[1]//text()")))
        for node in tree.xpath("//script|//style|//noscript|//template|//svg|//canvas|//form|//nav|//footer|//aside"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

        candidates = tree.xpath(
            "//article|//main|//*[@role='main']|"
            "//*[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'article') or "
            "contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'content') or "
            "contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'article') or "
            "contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'content') or "
            "contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'detail')]"
        )
        body = tree.find("body")
        if body is not None:
            candidates.append(body)
        if not candidates:
            candidates = [tree]

        best = max(candidates, key=self._content_score)
        blocks = best.xpath(".//h1|.//h2|.//h3|.//p|.//blockquote|.//li|.//tr")
        lines = []
        seen = set()
        for block in blocks:
            line = self._clean_text(block.text_content())
            key = line.casefold()
            if len(line) < 8 or key in seen:
                continue
            seen.add(key)
            lines.append(line)
        text = "\n".join(lines)
        if len(text) < self.min_text_chars:
            text = self._clean_text(best.text_content())
        return text[: self.max_text_chars], title[:500], "lxml_readable_text"

    def _download(self, requested_url: str) -> tuple[bytes, str, str]:
        current_url = requested_url
        current_scope = self._allowlist_scope(urlparse(current_url).hostname or "")
        for redirect_index in range(self.max_redirects + 1):
            self._validate_url(current_url, resolve=True)
            try:
                with self.session.get(
                    current_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; LeziwuResearch/1.0)",
                        "Accept": "text/html,application/xhtml+xml;q=0.9",
                    },
                    timeout=(self.connect_timeout, self.read_timeout),
                    stream=True,
                    allow_redirects=False,
                ) as response:
                    status_code = int(getattr(response, "status_code", 200) or 200)
                    headers = getattr(response, "headers", {}) or {}
                    if 300 <= status_code < 400:
                        location = str(headers.get("Location") or "").strip()
                        if not location or redirect_index >= self.max_redirects:
                            raise WebArticleArtifactError("网页重定向缺少地址或超过上限")
                        next_url = self._validate_url(urljoin(current_url, location), resolve=True)
                        next_host = str(urlparse(next_url).hostname or "").lower().rstrip(".")
                        # Cross-host redirects are accepted only inside the same
                        # explicit allowlist family (e.g. www.gov.cn -> gov.cn).
                        if self._allowlist_scope(next_host) != current_scope:
                            raise WebArticleArtifactError("网页跨域重定向超出允许来源范围")
                        current_url = next_url
                        continue
                    response.raise_for_status()
                    content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise WebArticleArtifactError("网页响应不是允许的 HTML MIME 类型")
                    declared = self._content_length(headers)
                    if declared > self.max_html_bytes:
                        raise WebArticleArtifactError("网页响应超过单页大小上限")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > self.max_html_bytes:
                            raise WebArticleArtifactError("网页响应超过单页大小上限")
                        chunks.append(bytes(chunk))
                    if size <= 0:
                        raise WebArticleArtifactError("网页响应为空")
                    return b"".join(chunks), current_url, str(headers.get("Content-Type") or content_type)
            except WebArticleArtifactError:
                raise
            except Exception as exc:
                raise WebArticleArtifactError(f"网页正文下载失败：{type(exc).__name__}") from exc
        raise WebArticleArtifactError("网页重定向超过上限")

    def _validate_url(self, value: str, *, resolve: bool) -> str:
        url = urldefrag(str(value or "").strip())[0]
        parsed = urlparse(url)
        host = str(parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise WebArticleArtifactError("网页地址端口无效") from exc
        if (
            parsed.scheme.lower() != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or (parsed.path and not parsed.path.startswith("/"))
            or not self._allowlist_scope(host)
        ):
            raise WebArticleArtifactError("网页地址不在允许的 HTTPS 公网域名")
        self._reject_ip_or_local_name(host)
        if resolve:
            try:
                addresses = list(self._resolver(host))
            except Exception as exc:
                raise WebArticleArtifactError(f"网页域名解析失败：{type(exc).__name__}") from exc
            if not addresses:
                raise WebArticleArtifactError("网页域名没有可用公网地址")
            for raw_address in addresses:
                try:
                    address = ipaddress.ip_address(str(raw_address).split("%", 1)[0])
                except ValueError as exc:
                    raise WebArticleArtifactError("网页域名解析结果无效") from exc
                if not address.is_global:
                    raise WebArticleArtifactError("网页域名解析到内网或保留地址")
        return url

    def _allowlist_scope(self, host: str) -> str:
        normalized = str(host or "").lower().rstrip(".")
        if normalized in self.exact_allowed_hosts:
            return f"exact:{normalized}"
        matches = [value for value in self.allowed_hosts if normalized == value or normalized.endswith(f".{value}")]
        return max(matches, key=len) if matches else ""

    @classmethod
    def _configured_hosts(cls) -> tuple[str, ...]:
        configured = str(os.getenv("INDUSTRY_RESEARCH_WEB_HOSTS", "") or "")
        values = [value.strip() for value in configured.split(",") if value.strip()]
        return tuple(dict.fromkeys([*cls._DEFAULT_ALLOWED_HOSTS, *values]))

    @classmethod
    def _validated_allowlist_host(cls, value: str) -> str:
        host = str(value or "").strip().lower().rstrip(".")
        labels = host.split(".")
        if (
            not host
            or len(host) > 253
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or "." not in host
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                for label in labels
            )
            or host in cls._FORBIDDEN_ALLOWLIST_SUFFIXES
        ):
            raise WebArticleArtifactError("网页正文允许域名格式或范围无效")
        cls._reject_ip_or_local_name(host)
        return host

    @staticmethod
    def _reject_ip_or_local_name(host: str) -> None:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
            raise WebArticleArtifactError("网页正文允许域名必须是公网域名")
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return
        raise WebArticleArtifactError("网页正文地址必须使用域名而不是 IP")

    @staticmethod
    def _resolve_addresses(host: str) -> Sequence[str]:
        return tuple(dict.fromkeys(
            str(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ))

    @staticmethod
    def _content_score(node: Any) -> int:
        text = WebArticleArtifactService._clean_text(node.text_content())
        link_text = " ".join(
            WebArticleArtifactService._clean_text(value)
            for value in node.xpath(".//a//text()")
        )
        paragraphs = len(node.xpath(".//p"))
        return len(text) + paragraphs * 80 - len(link_text) * 2

    @staticmethod
    def _clean_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _declared_charset(content_type: str) -> Optional[str]:
        match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", str(content_type or ""), re.I)
        return match.group(1) if match else None

    def _read_cache(self, raw_path: Path, meta_path: Path) -> Optional[tuple[bytes, Dict[str, Any]]]:
        if not raw_path.is_file() or not meta_path.is_file() or raw_path.stat().st_size <= 0:
            return None
        try:
            document = raw_path.read_bytes()
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(str(metadata.get("fetched_at") or "").replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)).total_seconds()
            if age_seconds < 0 or age_seconds > self.cache_ttl_seconds:
                return None
            expected = str(metadata.get("document_hash") or "")
            if expected and sha256(document).hexdigest() == expected:
                return document, metadata
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return None

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f"{target.stem}.", suffix=".part", dir=target.parent, delete=False,
            ) as output:
                temporary_path = Path(output.name)
                output.write(payload)
            temporary_path.replace(target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

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

    @staticmethod
    def _bounded_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))
