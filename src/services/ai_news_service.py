"""Independent, metadata-only AI news feed. No AIHOT hosted data or LLM calls.

The small cache is separate from the investment/user databases. Page requests
only read a snapshot; one bounded background collector refreshes publisher RSS.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import logging
import math
import os
import re
import requests
import socket
import ssl
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import urllib3

logger = logging.getLogger(__name__)
MAX_FEED_BYTES = 4 * 1024 * 1024
SOURCE_INTERVAL = 300
PARSER_REVISION = 2
RETENTION_SECONDS = 90 * 86400
MAX_ITEMS = 5000

# Public example subscriptions documented by the MIT-licensed AIHOT project.
# We fetch the publishers directly, never AIHOT's licensed hosted API or feeds.
SOURCES = (
    ("openai", "OpenAI", "openai", "official", "https://openai.com/news/rss.xml"),
    ("deepmind", "Google DeepMind", "google", "official", "https://deepmind.google/blog/rss.xml"),
    ("google-research", "Google Research", "google", "official", "https://research.google/blog/rss/"),
    ("huggingface", "Hugging Face", "huggingface", "official", "https://huggingface.co/blog/feed.xml"),
    ("microsoft", "Microsoft Research", "microsoft", "official", "https://www.microsoft.com/en-us/research/feed/"),
    ("nvidia", "NVIDIA", "nvidia", "official", "https://blogs.nvidia.com/feed/"),
    ("aws", "AWS Machine Learning", "amazon", "official", "https://aws.amazon.com/blogs/machine-learning/feed/"),
    ("github", "GitHub AI & ML", "microsoft", "official", "https://github.blog/ai-and-ml/feed/"),
    ("mistral", "Mistral AI", "mistral", "official", "https://mistral.ai/rss.xml"),
    ("bair", "Berkeley AI Research", "berkeley", "research", "https://bair.berkeley.edu/blog/feed.xml"),
    ("techcrunch", "TechCrunch AI", "techcrunch", "media", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("verge", "The Verge AI", "theverge", "media", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("decoder", "The Decoder", "thedecoder", "media", "https://the-decoder.com/feed/"),
    ("simon", "Simon Willison", "simonwillison", "research", "https://simonwillison.net/atom/everything/"),
    ("qbitai", "量子位", "qbitai", "media", "https://www.qbitai.com/feed"),
    ("leiphone", "雷峰网 · 人工智能", "leiphone", "media", "https://www.leiphone.com/feed/categoryRss/name/ai"),
    ("ithome", "IT之家 · AI筛选", "ithome", "media", "https://www.ithome.com/rss/"),
)
TOPICS = {
    "models": "模型与研究", "agents": "智能体与编程", "products": "产品与应用",
    "compute": "算力与基础设施", "business": "产业与政策",
}
TOPIC_PATTERNS = (
    ("compute", r"\b(gpu|chip|cuda|blackwell|infrastructure|datacenter|data center|inference|compute|hardware)\b|算力|芯片"),
    ("agents", r"\b(agent\w*|coding|code|developer|copilot|codex|programming|mcp|cursor)\b|智能体|编程"),
    ("business", r"\b(funding|raises|billion|million|acquir\w*|policy|regulat\w*|safety|copyright|lawsuit|partnership)\b|融资|政策|安全"),
    ("models", r"\b(model\w*|llm\w*|reasoning|research|benchmark\w*|training)\b|gpt[-\d]|gemini|claude|deepseek|qwen|模型|研究"),
)
SEARCH_ALIASES = {"谷歌": "google", "英伟达": "nvidia", "微软": "microsoft", "亚马逊": "aws", "开放人工智能": "openai"}
METHOD = "独立 RSS 标题与公开订阅短摘要索引，不抓新闻正文或 AIHOT 数据。媒体默认每5分钟、官方及研究源每15分钟检查。仅同一原文链接或标准化后完全相同的标题归组；48小时内按独立发布主体计数、24小时半衰期排序，不代表全网阅读量。单一来源不是多源热点。IT之家为综合订阅中的明确AI标题筛选。"
AI_TITLE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:AI|AGI|LLM|GPT(?:[-\d.]+)?|ChatGPT|OpenAI|Claude|Gemini|DeepSeek|Qwen|Kimi|Copilot|Codex|Anthropic|Mistral|Midjourney|Stable Diffusion|Sora|vibecoding)(?![A-Za-z0-9])|人工智能|智能体|大模型|语言模型|多模态|生成式|具身智能|机器学习|深度学习|神经网络|千问|文心一言|豆包|通义|智谱|百川智能|月之暗面", re.I)
_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-news-dns")


def source_interval(source: tuple) -> int:
    """Bounded settings: high-frequency media and less frequent primary sources."""
    key, default = ("AI_NEWS_MEDIA_INTERVAL_SECONDS", 300) if source[3] == "media" else ("AI_NEWS_OFFICIAL_INTERVAL_SECONDS", 900)
    try:
        return max(300, min(3600, int(os.getenv(key, str(default)))))
    except ValueError:
        return default


def next_attempt(source: tuple, state: dict) -> float:
    if state.get("parser_revision") != PARSER_REVISION:
        return 0  # One bounded refresh enriches the pre-upgrade metadata cache.
    interval = source_interval(source) if state.get("status") == "ok" else min(1800, SOURCE_INTERVAL * 2 ** max(0, min(6, state.get("failures", 1)) - 1))
    return (parse_date(state.get("last_attempt") or "") or 0) + interval


def short_summary(value: str, title: str) -> str | None:
    """Use only explicit publisher descriptions, never encoded article bodies."""
    value = html.unescape(value)
    value = re.sub(r"<(script|style|iframe)\b[^>]*>.*?</\1\s*>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]*>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value or value == title:
        return None
    # Word limit applies to space-delimited text; a character cap also bounds CJK.
    words = value.split()
    clipped = " ".join(words[:25])[:160].rstrip()
    return clipped + ("…" if len(clipped) < len(value) else "")


def resolve_public_addresses(host: str) -> list[str]:
    future = _DNS_POOL.submit(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    try:
        addresses = future.result(timeout=5)
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError("dns_deadline") from exc
    ips = list(dict.fromkeys(a[4][0] for a in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global
                      or ipaddress.ip_address(ip).is_multicast
                      or ipaddress.ip_address(ip).is_reserved for ip in ips):
        raise ValueError("non_public_dns")
    return ips


def iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z") if epoch else None


def parse_date(value: str) -> float | None:
    try:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def public_link(value: str) -> str | None:
    """Validate display links without resolving every publisher URL on reads."""
    if len(value) > 2048 or any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")) or "." not in host:
            return None
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global or address.is_multicast or address.is_reserved:
                return None
        except ValueError:
            # Browsers accept legacy short/octal/hex IPv4 spellings. They are
            # not DNS names and must not send readers to their own loopback.
            if all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", part) for part in host.split(".")):
                return None
        query = urlencode([(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}])
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", query, ""))
    except ValueError:
        return None


def classify(title: str) -> str:
    for topic, pattern in TOPIC_PATTERNS:
        if re.search(pattern, title, flags=re.I):
            return topic
    return "products"


def parse_feed(content: bytes, source: tuple, now: float) -> list[dict]:
    # Reject DTD/entity declarations before ElementTree parses untrusted XML.
    # A HTML doctype quoted inside CDATA (e.g. GitHub's article body) is plain
    # text, not an XML declaration. Do not reject these otherwise valid feeds.
    if len(content) > MAX_FEED_BYTES or b"\0" in content:
        raise ValueError("unsafe_or_oversized_xml")
    markup = re.sub(br"<!\[CDATA\[.*?\]\]>|<!--.*?-->", b"", content, flags=re.S)
    if re.search(br"<!\s*(DOCTYPE|ENTITY)", markup, re.I):
        raise ValueError("unsafe_or_oversized_xml")
    root = ET.fromstring(content)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "rss":
        entries = root.findall("./channel/item")
    elif tag == "feed":
        entries = root.findall("./{*}entry")
    elif tag == "rdf":
        entries = root.findall("./{*}item")
    else:
        raise ValueError("not_rss_or_atom")
    result = []
    for entry in entries[:120]:
        def text(name: str) -> str:
            node = entry.find("./{*}" + name)
            return "".join(node.itertext()).strip() if node is not None else ""
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", html.unescape(text("title")))).strip()[:500]
        raw_url = text("link")
        if tag == "feed":
            raw_url = next((n.attrib.get("href", "") for n in entry.findall("./{*}link")
                            if n.attrib.get("rel", "alternate") == "alternate"), "")
        url = public_link(urljoin(source[4], raw_url)) if raw_url else None
        if not title or not url:
            continue
        if source[0] == "ithome" and not AI_TITLE_PATTERN.search(title):
            continue
        published = parse_date(text("pubDate") or text("published") or text("date") or text("updated"))
        if published and (published > now + 300 or published < now - RETENTION_SECONDS):
            continue
        topic = classify(title)
        summary = short_summary(text("description") or text("summary"), title)
        result.append({"id": hashlib.sha256(f"{source[0]}:{url}".encode()).hexdigest()[:24],
                       "title": title, "url": url, "source_id": source[0], "source_name": source[1],
                       "summary": summary, "summary_origin": "publisher_feed" if summary else None,
                       "publisher": source[2], "published_at": iso(published), "discovered_at": iso(now),
                       "time_basis": "published" if published else "discovered",
                       "topic": topic, "topic_label": TOPICS[topic], "_time": published or now})
    return result


def fetch_feed(source: tuple, state: dict) -> tuple[bytes | None, dict]:
    """TLS + pinned public DNS, limited redirects, bytes, and elapsed time.

    We deliberately don't use a process-wide DNS monkeypatch or environment
    proxy. Neither a feed entry nor an API parameter can trigger a URL fetch.
    """
    target = source[4]
    headers = {"User-Agent": "LeziwuAINews/1.0 (+https://app.leziwu.com/ai-news)",
               "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
               "Accept-Encoding": "gzip, deflate, identity"}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("modified"):
        headers["If-Modified-Since"] = state["modified"]
    started = time.monotonic()
    for _ in range(4):
        parsed = urlsplit(target)
        if not public_link(target) or parsed.scheme != "https":
            raise ValueError("unsafe_redirect")
        ips = resolve_public_addresses(parsed.hostname)
        # Prefer IPv4 when available, preserving normal certificate/SNI checks.
        ip = next((ip for ip in ips if ":" not in ip), ips[0])
        context = ssl.create_default_context()
        pool = urllib3.HTTPSConnectionPool(ip, port=443, server_hostname=parsed.hostname,
                                          assert_hostname=parsed.hostname, ssl_context=context,
                                          timeout=urllib3.Timeout(connect=6, read=8, total=15), retries=False)
        response = None
        try:
            response = pool.request("GET", urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
                                    headers={**headers, "Host": parsed.hostname}, redirect=False, preload_content=False)
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("redirect_without_location")
                target = urljoin(target, location)
                if time.monotonic() - started > 20:
                    raise TimeoutError("source_deadline")
                continue
            metadata = {"etag": response.headers.get("ETag", "")[:500],
                        "modified": response.headers.get("Last-Modified", "")[:100]}
            if response.status == 304:
                return None, metadata
            if response.status != 200:
                raise ValueError(f"http_{response.status}")
            encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
            if encoding not in {"", "identity", "gzip", "deflate"}:
                raise ValueError("unexpected_content_encoding")
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding in {"gzip", "deflate"} else None
            chunks, total, wire_total = [], 0, 0
            while True:
                # read1 returns after one underlying read, unlike stream(amt)
                # which can wait indefinitely for a slow source to fill amt.
                chunk = response.read1(16384, decode_content=False)
                if not chunk:
                    break
                wire_total += len(chunk)
                if wire_total > MAX_FEED_BYTES:
                    raise ValueError("feed_too_large")
                if time.monotonic() - started > 25:
                    raise TimeoutError("source_deadline")
                if decoder:
                    chunk = decoder.decompress(chunk, MAX_FEED_BYTES - total + 1)
                    if decoder.unused_data:
                        raise ValueError("invalid_content_encoding")
                total += len(chunk)
                if total > MAX_FEED_BYTES:
                    raise ValueError("feed_too_large")
                chunks.append(chunk)
            if decoder and not decoder.eof:
                raise ValueError("incomplete_content_encoding")
            return b"".join(chunks), metadata
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError("too_many_redirects")


def group_items(items: list[dict], now: float) -> list[dict]:
    """O(n log n) exact normalized titles/URLs; never discard negation or order."""
    groups = []
    by_url, by_title = {}, {}
    for item in sorted(items, key=lambda x: x["_time"], reverse=True):
        title_key = re.sub(r"\W+", " ", item["title"].lower()).strip()
        index = by_url.get(item["url"])
        if index is None and len(title_key.split()) >= 4:
            index = by_title.get(title_key)
        match = groups[index] if index is not None else None
        if match is not None and abs(match["_time"] - item["_time"]) <= 48 * 3600:
            match["related"].append({key: item[key] for key in ("title", "url", "source_name", "published_at")})
            match["_publishers"][item["publisher"]] = max(item["_time"], match["_publishers"].get(item["publisher"], 0))
        else:
            index = len(groups)
            groups.append({**item, "related": [],
                           "_publishers": {item["publisher"]: item["_time"]}})
        by_url[item["url"]] = index
        by_title[title_key] = index
    for group in groups:
        fresh = [t for t in group["_publishers"].values() if now - t <= 48 * 3600]
        group["source_count"] = len(group["_publishers"])
        group["score"] = round(sum(.5 ** (max(0, now - t) / 86400) for t in fresh), 4)
        group["multi_source_hot"] = len(fresh) >= 2
    return groups


class AiNewsService:
    def __init__(self, cache_path: Path | None = None, *, fetcher=fetch_feed, clock=time.time):
        self.path = cache_path or Path(os.getenv("AI_NEWS_CACHE_PATH", "data/ai-news/cache.json"))
        self.fetcher, self.clock = fetcher, clock
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stamp = None
        self._state: dict = {"version": 1, "items": [], "sources": {}, "updated_at": None}
        self._read_cache()

    _translation_cache: dict[str, str] = {}

    @staticmethod
    def _is_english(text: str) -> bool:
        letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
        return letters >= 6 and letters >= len(text) * 0.3

    def _translate(self, text: str) -> str:
        cached = self._translation_cache.get(text)
        if cached:
            return cached
        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
        if not key:
            try:
                from src.config import get_config
                keys = getattr(get_config(), "deepseek_api_keys", []) or []
                key = keys[0] if keys else None
            except Exception:
                key = None
        if not key:
            return text
        base = (os.getenv("DEEPSEEK_API_BASE") or "https://api.deepseek.com").rstrip("/")
        try:
            response = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                    "messages": [
                        {"role": "system", "content": "把英文财经/AI 新闻翻译成简洁、准确的中文，只输出译文，不要解释。"},
                        {"role": "user", "content": text[:2000]},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                },
                timeout=20,
            )
            if response.status_code == 200:
                translated = response.json()["choices"][0]["message"]["content"].strip()
                if translated:
                    self._translation_cache[text] = translated
                    return translated
        except Exception:
            pass
        return text

    def _localize(self, items: list[dict]) -> None:
        for item in items:
            title = str(item.get("title") or "")
            if title and self._is_english(title):
                item["title_zh"] = self._translate(title)
            summary = str(item.get("summary") or "")
            if summary and self._is_english(summary):
                item["summary_zh"] = self._translate(summary)

    def _read_cache(self):
        try:
            with self._lock:
                stamp = self.path.stat().st_mtime_ns
                if stamp == self._stamp:
                    return
                if self.path.stat().st_size > 12 * 1024 * 1024:
                    raise ValueError("cache_size")
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if loaded.get("version") != 1 or not isinstance(loaded.get("items"), list) or not isinstance(loaded.get("sources"), dict):
                    raise ValueError("cache_schema")
                self._state, self._stamp = loaded, stamp
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            logger.warning("AI news cache unreadable; retaining last in-memory snapshot")

    def _write_cache(self):
        # Atomic replace preserves the prior complete file on process failure.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self._state, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
            self._stamp = self.path.stat().st_mtime_ns
        finally:
            if temporary and temporary.exists():
                temporary.unlink()  # Only our task-owned failed temporary file.

    def start(self):
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            def loop():
                while not self._stop.is_set():
                    try:
                        self.refresh_due()
                    except Exception:
                        logger.exception("AI news collector failed; existing snapshot remains available")
                    self._stop.wait(60)
            self._thread = threading.Thread(target=loop, name="ai-news-rss", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def refresh_due(self):
        if not self._refresh_lock.acquire(blocking=False):
            return
        file_lock = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            file_lock = self.path.with_suffix(".lock").open("a")
            try:
                import fcntl
                fcntl.flock(file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            except ImportError:
                pass  # Windows: one configured API process; in-process coalescing remains.
            self._read_cache()
            now = self.clock()
            due = [s for s in SOURCES if next_attempt(s, self._state["sources"].get(s[0], {})) <= now]
            if not due:
                return
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai-news-source") as pool:
                futures = {}
                for source in due:
                    conditional = dict(self._state["sources"].get(source[0], {}))
                    if conditional.get("parser_revision") != PARSER_REVISION:
                        conditional.pop("etag", None)
                        conditional.pop("modified", None)
                    futures[pool.submit(self.fetcher, source, conditional)] = source
                for future in as_completed(futures):
                    source = futures[future]
                    finished = self.clock()
                    previous = self._state["sources"].get(source[0], {})
                    state = {**previous, "last_attempt": iso(finished), "parser_revision": PARSER_REVISION,
                             "new_items": 0}
                    if previous.get("parser_revision") != PARSER_REVISION:
                        state.pop("etag", None)
                        state.pop("modified", None)
                    try:
                        content, metadata = future.result()
                        entries = parse_feed(content, source, finished) if content is not None else None
                        with self._lock:
                            if entries is not None:
                                by_id = {row["id"]: row for row in self._state["items"]}
                                changed = False
                                for row in entries:
                                    old = by_id.get(row["id"])
                                    if old:
                                        row["discovered_at"] = old["discovered_at"]
                                        if not row["published_at"]:
                                            row["_time"] = old["_time"]
                                        changed = changed or any(old.get(k) != row.get(k) for k in ("title", "summary", "published_at", "url"))
                                    else:
                                        state["new_items"] += 1
                                        changed = True
                                    by_id[row["id"]] = row
                                self._state["items"] = sorted(
                                    [r for r in by_id.values() if r["_time"] >= finished - RETENTION_SECONDS],
                                    key=lambda r: r["_time"], reverse=True)[:MAX_ITEMS]
                                if changed:
                                    self._state["content_updated_at"] = iso(finished)
                                    state["content_updated_at"] = iso(finished)
                                if state["new_items"]:
                                    state["last_new_item_at"] = iso(finished)
                            state.update({k: v for k, v in metadata.items() if v})
                            state.update(status="ok", error=None, failures=0, last_success=iso(finished),
                                         next_attempt=finished + source_interval(source))
                            self._state["updated_at"] = iso(finished)
                    except Exception as exc:
                        failures = min(6, previous.get("failures", 0) + 1)
                        # Never expose upstream body, credentials, TLS paths, or tracebacks.
                        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                        safe_error = code if re.fullmatch(r"[A-Za-z0-9_]{1,60}", code) else "upstream_unavailable"
                        state.update(status="error", error=safe_error, failures=failures,
                                     next_attempt=finished + min(1800, SOURCE_INTERVAL * 2 ** (failures - 1)))
                        logger.info("AI news source %s unavailable: %s", source[0], safe_error)
                    with self._lock:
                        self._state["sources"][source[0]] = state
                        self._write_cache()
        finally:
            if file_lock:
                file_lock.close()
            self._refresh_lock.release()

    def feed(self, *, view="hot", q="", topic="", source="", window="7d", page=1, page_size=24) -> dict:
        self._read_cache()
        now = self.clock()
        with self._lock:
            raw = list(self._state["items"])
            statuses = {k: dict(v) for k, v in self._state["sources"].items()}
            updated_at = self._state["updated_at"]
            content_updated_at = self._state.get("content_updated_at")
        cutoff = now - {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}[window]
        recent = [r for r in raw if r["_time"] >= cutoff]
        words = [{word, SEARCH_ALIASES.get(word, word)} for word in q.lower().strip().split()]
        candidates = [r for r in recent if (not topic or r["topic"] == topic)
                      and (not source or r["source_id"] == source)
                      and all(any(word in f'{r["title"]} {r.get("summary") or ""} {r["source_name"]} {r["topic_label"]}'.lower()
                                  for word in alternatives) for alternatives in words)]
        groups = group_items(candidates, now) if view == "hot" else [
            {**r, "related": [], "source_count": 1, "score": 0} for r in candidates]
        if view == "hot":
            groups.sort(key=lambda r: (r["multi_source_hot"], r["score"], r["_time"]), reverse=True)
        else:
            groups.sort(key=lambda r: r["_time"], reverse=True)
        total = len(groups)
        pages = max(1, math.ceil(total / page_size))
        page = min(page, pages)
        items = [{k: v for k, v in r.items() if not k.startswith("_")}
                 for r in groups[(page - 1) * page_size:page * page_size]]
        self._localize(items)
        sources = []
        for identifier, name, publisher, kind, url in SOURCES:
            state = statuses.get(identifier, {})
            own_items = [r for r in raw if r["source_id"] == identifier]
            published = [r["published_at"] for r in own_items if r.get("published_at")]
            interval = source_interval((identifier, name, publisher, kind, url))
            healthy = state.get("status") == "ok" and now - (parse_date(state.get("last_success") or "") or 0) < interval * 3
            sources.append({"id": identifier, "name": name, "publisher": publisher, "url": url, "kind": kind,
                            "last_success": state.get("last_success"), "last_attempt": state.get("last_attempt"),
                            "last_checked_at": state.get("last_attempt"), "latest_published_at": max(published, default=None),
                            "next_check_at": iso(next_attempt((identifier, name, publisher, kind, url), state)),
                            "interval_seconds": interval, "healthy": healthy,
                            "content_updated_at": state.get("content_updated_at"), "last_new_item_at": state.get("last_new_item_at"),
                            "new_items": state.get("new_items", 0),
                            "status": state.get("status", "pending"), "error": state.get("error"),
                            "item_count": sum(r["source_id"] == identifier for r in recent)})
        checked = [s["last_checked_at"] for s in sources if s["last_checked_at"]]
        published = [r["published_at"] for r in raw if r.get("published_at")]
        healthy_count = sum(s["healthy"] for s in sources)
        return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages,
                "updated_at": updated_at, "refreshing": self._refresh_lock.locked(),
                "last_checked_at": max(checked, default=None), "latest_published_at": max(published, default=None),
                "content_updated_at": content_updated_at,
                "stale": healthy_count == 0,
                "sources": sources,
                "topics": [{"id": k, "label": v, "count": sum(r["topic"] == k for r in recent)} for k, v in TOPICS.items()],
                "stats": {"items": len(recent), "sources": len(SOURCES),
                          "items_24h": sum(r["_time"] >= now - 86400 for r in raw),
                          "items_12h": sum(r["_time"] >= now - 43200 for r in raw),
                          "healthy_sources": healthy_count,
                          "multi_source_events": sum(r.get("multi_source_hot", False) for r in groups)},
                "methodology": METHOD}


_instance: AiNewsService | None = None
_instance_lock = threading.Lock()


def get_ai_news_service() -> AiNewsService:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AiNewsService()
        return _instance
