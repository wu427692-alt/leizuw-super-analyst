# -*- coding: utf-8 -*-
"""按需抽取单条快讯正文：给「有链接但没有正文」的事件补上可读内容。

背景：Tushare 类快讯入库时 ``monitoring_events.summary`` 自带正文，RSS/NewsNow
媒体流只存标题和 ``url``（生产库中约 55% 没有正文）。手机端用户点开快讯必须
看到具体内容，因此在「打开单条快讯」时再抓原文，抽正文并落库缓存。

这个模块是监控 API 里唯一会去抓「任意发布方 URL」的地方，所以边界必须很窄：

* 只允许 http/https、不允许携带账号密码、只允许 80/443 端口；
* 每一跳重定向都重新校验，域名只解析一次并把连接钉在解析出的 IP 上
  （没有「校验完再解析」的 DNS rebinding 窗口）；
* 解析结果必须是公网地址：私网/回环/链路本地/保留/组播一律拒绝；
* 连接 5s、读取 8s、总时限 10s；线上 2MB、解压后 2MB 双上限；最多 3 次重定向；
* 只接受 text/html / application/xhtml+xml，其它类型直接失败（fail closed）。

抽取全部复用项目已有依赖，不新增重量级包，按「便宜且准」的顺序级联：

1. 站点自带的 JSON-LD ``articleBody``（有就是最干净的原文）；
2. lxml 正文密度打分（本项目已有 lxml），中文门户基本一次命中，实测 0.01s；
3. ``newspaper3k``（requirements.txt 已有）作为最后兜底——它的中文路径要加载
   jieba 词典，首次调用实测约 9s，放进 8-10s 的抓取预算里不合适，所以只在
   前两档都抽不出正文时才跑。

返回的是纯文本，已去掉 script/style/导航/APP 导流行，前端按纯文本渲染即可，
不需要 ``dangerouslySetInnerHTML``。
"""

from __future__ import annotations

from datetime import datetime, timedelta
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import urljoin, urlsplit

import urllib3

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.storage import utc_naive_now

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 抓取边界：这些数值决定了「一条快讯最多花多少时间和带宽」
# ---------------------------------------------------------------------------
ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443)
MAX_REDIRECTS = 3
MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_DECODED_BYTES = 2 * 1024 * 1024
MAX_CONTENT_CHARS = 10_000
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 8.0
TOTAL_DEADLINE_SECONDS = 10.0
DNS_TIMEOUT_SECONDS = 5.0
# 抽出来的正文短于这个长度就当作「没抽到」：短于此的多是导航残片或版权行。
MIN_CONTENT_CHARS = 30
# 同一事件在这个窗口内不重复抓取（成功的结果本来就走缓存，这里主要挡失败重试）。
RETRY_COOLDOWN_SECONDS = 300
SAME_EVENT_LOCK_LIMIT = 256

_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_ACCEPTED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
# RFC 2544 保留段，被 Clash/Surge 这类 fake-IP 代理当成「任意域名」的合成地址。
# 这类环境下真实目的地由代理决定，本机 DNS 结果没有安全含义；只有显式开启
# NEWS_CONTENT_ALLOW_PROXY_DNS 才放行这一段，其余内网判定完全不变。
_FORWARD_PROXY_DNS_RANGE = ipaddress.ip_network("198.18.0.0/15")
_PROXY_DNS_ENV = "NEWS_CONTENT_ALLOW_PROXY_DNS"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_LOCAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
)
_CHARSET_IN_META = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9._:-]+)""",
    re.I,
)
_CHARSET_IN_CONTENT_TYPE = re.compile(r"charset\s*=\s*[\"']?([a-zA-Z0-9._:-]+)", re.I)
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_BOILERPLATE_LINE = re.compile(
    r"^[\s　]*(?:\(?\s*)?(?:责任编辑|责编|免责声明|版权声明|风险提示)\s*[：:].{0,80}$"
)
# 门户页面上的 APP 导流/解锁提示会混进正文，例如「打开APP」「芝麻AI为您解读全文APP内免费解锁」。
_PROMO_LINE = re.compile(
    r"^[\s　]*(?:"
    r"打开\s*APP|下载\s*APP|APP\s*内[^。]{0,24}|"
    r"[^。]{0,24}(?:免费解锁|扫码下载|扫码关注|点击下载|前往\s*APP|安装\s*APP)[^。]{0,24}|"
    r"[^。]{0,24}(?:APP\s*搜索|登录[^。]{0,12}APP)[^。]{0,24}|"
    r"来源\s*[：:][^。]{0,40}|原标题\s*[：:][^。]{0,60}"
    r")[\s　]*$",
    re.I,
)

# 结构化的失败原因：前端只需要认这几个字符串，中文提示直接展示。
_REASON_MESSAGES = {
    "invalid_url": "原文链接格式不合法",
    "unsupported_scheme": "原文链接必须是 http 或 https",
    "ssrf_blocked": "原文地址指向内网或保留地址，已拒绝抓取",
    "dns_failed": "原文域名解析失败",
    "redirect_limit": "原文重定向次数过多",
    "not_html": "原文不是 HTML 页面",
    "too_large": "原文体积超过抓取上限",
    "timeout": "原文抓取超时",
    "tls_error": "原文 HTTPS 证书校验失败",
    "network": "原文网络请求失败",
    "no_text": "原文页面没有可读正文",
    "no_url": "该快讯没有原文链接",
    "throttled": "原文刚刚抓取失败，稍后会自动重试",
    "fetch_failed": "原文抓取失败",
}

_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="news-content-dns")


class NewsContentError(ValueError):
    """事件本身不可读（不存在 / ID 非法），由接口层转成 404。"""


class FetchFailure(RuntimeError):
    """单条原文抓取失败，带机器可读原因；不应对调用方抛成 500。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or _REASON_MESSAGES.get(reason, "原文抓取失败"))
        self.reason = reason
        self.message = message or _REASON_MESSAGES.get(reason, "原文抓取失败")


# ---------------------------------------------------------------------------
# SSRF 防护
# ---------------------------------------------------------------------------
def allows_synthetic_proxy_dns() -> bool:
    """部署在 fake-IP 代理后面时，是否接受 198.18.0.0/15 这种合成 DNS 结果。"""
    return str(os.getenv(_PROXY_DNS_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def is_blocked_ip(address: Any) -> bool:
    """公网以外的一切地址一律视为不可抓取目标。

    唯一的例外是显式开启 ``NEWS_CONTENT_ALLOW_PROXY_DNS`` 后的 RFC 2544 段：
    它不是任何真实内网网段，只是 fake-IP 代理用来占位的合成地址。10/8、172.16/12、
    192.168/16、127/8、169.254/16、fc00::/7 等真实内网地址即使开启选项仍然拒绝。
    """
    if (
        allows_synthetic_proxy_dns()
        and getattr(address, "version", 0) == 4
        and address in _FORWARD_PROXY_DNS_RANGE
    ):
        return False
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _normalize_host(host: Optional[str]) -> str:
    value = str(host or "").strip().lower().rstrip(".")
    if not value:
        raise FetchFailure("invalid_url", "原文链接缺少域名")
    if value in _LOCAL_HOSTNAMES or value.endswith(_LOCAL_HOST_SUFFIXES):
        raise FetchFailure("ssrf_blocked", "原文地址指向本机或内网域名")
    try:
        ipaddress.ip_address(value.strip("[]"))
        return value.strip("[]")
    except ValueError:
        pass
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise FetchFailure("invalid_url", "原文域名不合法") from exc


def parse_target(url: str) -> Tuple[str, str, int, str]:
    """校验一条待抓取 URL，返回 (scheme, host, port, path?query)。

    这里只做「字面量」校验，不做 DNS；解析与 IP 校验在 resolve_public_addresses。
    """
    raw = str(url or "").strip()
    if not raw or len(raw) > 2000 or any(ord(char) < 32 for char in raw) or "\\" in raw:
        raise FetchFailure("invalid_url")
    parsed = urlsplit(raw)
    scheme = str(parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchFailure("unsupported_scheme", "原文链接必须是 http 或 https")
    if parsed.username or parsed.password:
        raise FetchFailure("invalid_url", "原文链接不能携带账号密码")
    try:
        port = parsed.port
    except ValueError as exc:
        raise FetchFailure("invalid_url", "原文链接端口不合法") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    if port not in ALLOWED_PORTS:
        raise FetchFailure("invalid_url", "原文链接只允许 80/443 端口")
    host = _normalize_host(parsed.hostname)
    path = parsed.path or "/"
    if not path.startswith("/"):
        raise FetchFailure("invalid_url", "原文链接路径不合法")
    target = f"{path}?{parsed.query}" if parsed.query else path
    return scheme, host, port, target


def _default_resolver(host: str, port: int) -> Sequence[str]:
    """带超时的 DNS 解析；失败映射成结构化原因而不是抛裸异常。"""
    future = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    try:
        addresses = future.result(timeout=DNS_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        future.cancel()
        raise FetchFailure("timeout", "原文域名解析超时") from exc
    except OSError as exc:
        raise FetchFailure("dns_failed", "原文域名解析失败") from exc
    return tuple(item[4][0] for item in addresses if item[4])


def resolve_public_addresses(
    host: str,
    port: int,
    *,
    resolver: Optional[Callable[[str, int], Sequence[str]]] = None,
) -> List[str]:
    """解析域名并要求每一个结果都是公网地址。

    返回去重后的 IP 列表；调用方必须把连接钉在这些 IP 上，否则
    「校验用一次解析、连接再解析一次」会留下 DNS rebinding 窗口。
    """
    resolve = resolver or _default_resolver
    try:
        raw_addresses = list(resolve(host, port))
    except FetchFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都降级成结构化失败
        raise FetchFailure("dns_failed", "原文域名解析失败") from exc
    if not raw_addresses:
        raise FetchFailure("dns_failed", "原文域名没有可用地址")
    public: List[str] = []
    for raw in raw_addresses:
        try:
            address = ipaddress.ip_address(str(raw).split("%", 1)[0])
        except ValueError as exc:
            raise FetchFailure("dns_failed", "原文域名解析结果不合法") from exc
        if is_blocked_ip(address):
            raise FetchFailure("ssrf_blocked", "原文域名解析到内网或保留地址")
        public.append(str(address))
    return list(dict.fromkeys(public))


_SSL_CONTEXT: Optional[ssl.SSLContext] = None
_SSL_CONTEXT_LOCK = threading.Lock()


def _ssl_context() -> ssl.SSLContext:
    """构造带可信根的 TLS 上下文。

    部分 Python 发行版（含 macOS python.org 构建）默认 CA 路径为空，
    ``create_default_context()`` 会得到 0 张根证书，导致所有 HTTPS 抓取都
    报证书错误。requests 靠 certifi 解决同一问题，这里与之对齐。
    """
    global _SSL_CONTEXT
    with _SSL_CONTEXT_LOCK:
        if _SSL_CONTEXT is not None:
            return _SSL_CONTEXT
        context = ssl.create_default_context()
        if not context.cert_store_stats().get("x509"):
            try:
                import certifi

                context.load_verify_locations(cafile=certifi.where())
            except Exception as exc:  # noqa: BLE001 - 没有根证书也没办法更好
                logger.warning("[news-content] failed to load CA bundle from certifi: %s", exc)
        _SSL_CONTEXT = context
        return context


def _build_pool(
    scheme: str, ip: str, port: int, host: str, timeout: urllib3.Timeout,
) -> Any:
    """把连接钉在已校验的 IP 上；HTTPS 仍然用真实域名做 SNI 与证书校验。"""
    if scheme == "https":
        return urllib3.HTTPSConnectionPool(
            ip,
            port=port,
            server_hostname=host,
            assert_hostname=host,
            ssl_context=_ssl_context(),
            timeout=timeout,
            retries=False,
        )
    return urllib3.HTTPConnectionPool(ip, port=port, timeout=timeout, retries=False)


def _classify_fetch_exception(exc: Exception) -> FetchFailure:
    name = type(exc).__name__.lower()
    detail = str(exc).lower()
    if "timeout" in name or "timedout" in name or "timeout" in detail:
        return FetchFailure("timeout", "原文抓取超时")
    if "ssl" in name or "certificate" in name or "certificate" in detail:
        return FetchFailure("tls_error", "原文 HTTPS 证书校验失败")
    return FetchFailure("network", "原文网络请求失败")


def _declared_charset(content_type: str, raw: bytes) -> Optional[str]:
    match = _CHARSET_IN_CONTENT_TYPE.search(str(content_type or ""))
    if match:
        return match.group(1)
    match = _CHARSET_IN_META.search(raw[:4096])
    if match:
        try:
            return match.group(1).decode("ascii", errors="ignore") or None
        except (UnicodeDecodeError, LookupError):
            return None
    return None


def decode_html(raw: bytes, content_type: str = "") -> str:
    """按声明/元信息/UTF-8/GB18030 的顺序解码中文页面。

    中文财经站点大量是 GBK 且不带 ``Content-Type: charset``，直接按
    ``requests`` 的猜测会得到乱码，所以这里自己判断。
    """
    candidates = [_declared_charset(content_type, raw), "utf-8", "utf-8-sig", "gb18030"]
    for charset in dict.fromkeys(item for item in candidates if item):
        try:
            return raw.decode(str(charset))
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_public_html(
    url: str,
    *,
    resolver: Optional[Callable[[str, int], Sequence[str]]] = None,
    pool_factory: Optional[Callable[..., Any]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> Tuple[str, str]:
    """抓取一个已校验的公网 HTML 页面，返回 (最终 URL, HTML 文本)。

    任何失败都抛 :class:`FetchFailure`；调用方负责把它降级成 ``status: failed``。
    """
    now = clock or time.monotonic
    build_pool = pool_factory or _build_pool
    started = now()
    target = str(url or "").strip()
    timeout = urllib3.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=READ_TIMEOUT_SECONDS,
        total=TOTAL_DEADLINE_SECONDS,
    )
    for _hop in range(MAX_REDIRECTS + 1):
        if now() - started > TOTAL_DEADLINE_SECONDS:
            raise FetchFailure("timeout", "原文抓取超时")
        scheme, host, port, request_target = parse_target(target)
        # 每一跳都重新解析并校验，重定向链上的内网地址在这里被拒绝。
        addresses = resolve_public_addresses(host, port, resolver=resolver)
        # 优先 IPv4：财经站点几乎都有 A 记录，IPv6 兜底也不影响校验结论。
        ip = next((item for item in addresses if ":" not in item), addresses[0])
        pool = build_pool(scheme, ip, port, host, timeout)
        response = None
        try:
            response = pool.request(
                "GET",
                request_target,
                headers={
                    "Host": host,
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
                redirect=False,
                preload_content=False,
            )
            status = int(getattr(response, "status", 0) or 0)
            if status in _REDIRECT_STATUSES:
                location = str(response.headers.get("Location") or "").strip()
                if not location:
                    raise FetchFailure("network", "原文重定向缺少 Location")
                target = urljoin(target, location)
                continue
            if status != 200:
                raise FetchFailure("network", f"原文返回 HTTP {status}")
            raw = _read_bounded_body(response)
            content_type = str(response.headers.get("Content-Type") or "")
            _ensure_html_content_type(content_type, raw)
            return target, decode_html(raw, content_type)
        except FetchFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一降级
            raise _classify_fetch_exception(exc) from exc
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响结果
                    pass
            try:
                pool.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响结果
                pass
    raise FetchFailure("redirect_limit", "原文重定向次数过多")


def _read_bounded_body(response: Any) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = response.read1(16384)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_WIRE_BYTES:
            raise FetchFailure("too_large", "原文体积超过抓取上限")
        chunks.append(bytes(chunk))
    body = b"".join(chunks)
    if len(body) > MAX_DECODED_BYTES:
        raise FetchFailure("too_large", "原文体积超过抓取上限")
    if not body:
        raise FetchFailure("not_html", "原文内容为空")
    return body


def _ensure_html_content_type(content_type: str, raw: bytes) -> None:
    media_type = str(content_type or "").split(";")[0].strip().lower()
    if media_type in _ACCEPTED_CONTENT_TYPES:
        return
    if not media_type:
        # 少数站点不给 Content-Type；只接受确实长得像 HTML 的正文，其余 fail closed。
        head = raw[:512].lstrip().lower()
        if head.startswith((b"<!doctype html", b"<html", b"<head", b"<meta")):
            return
    raise FetchFailure("not_html", f"原文不是 HTML 页面（{media_type or '未声明类型'}）")


# ---------------------------------------------------------------------------
# 正文抽取：JSON-LD → newspaper3k → lxml 正文密度
# ---------------------------------------------------------------------------
def _clean_text(value: Any) -> str:
    text = _ZERO_WIDTH.sub("", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or _BOILERPLATE_LINE.match(line) or _PROMO_LINE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _iter_json_objects(payload: Any) -> Sequence[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    stack: List[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found.append(node)
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _extract_json_ld(html_text: str, _url: str) -> str:
    from lxml import html as lxml_html

    document = lxml_html.document_fromstring(html_text)
    bodies: List[str] = []
    for node in document.xpath("//script[translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='application/ld+json']"):
        raw = node.text_content() or node.text or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for item in _iter_json_objects(payload):
            body = item.get("articleBody")
            if isinstance(body, str) and body.strip():
                bodies.append(body)
    return "\n".join(bodies)


def _detect_language(html_text: str) -> str:
    match = re.search(r"<html[^>]+lang\s*=\s*[\"']?([a-zA-Z-]+)", html_text[:4096], re.I)
    if match and match.group(1).lower().startswith("en"):
        return "en"
    return "zh"


def _extract_newspaper(html_text: str, url: str) -> str:
    from newspaper import Article

    article = Article(url or "https://example.invalid/article", language=_detect_language(html_text))
    article.set_html(html_text)
    article.parse()
    return article.text or ""


_UNREADABLE_TAGS = (
    "script", "style", "noscript", "iframe", "svg", "form", "nav", "aside",
    "header", "footer", "button", "select", "template",
)


def _extract_readability(html_text: str, _url: str) -> str:
    """lxml 正文密度打分：兜住 newspaper 抽不出的中文门户页面。"""
    from lxml import html as lxml_html

    try:
        document = lxml_html.document_fromstring(html_text)
    except Exception:  # noqa: BLE001 - 解析失败即放弃这一档
        return ""
    for tag in _UNREADABLE_TAGS:
        for node in document.xpath(f"//{tag}"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
    best_node = None
    best_score = 0
    for node in document.xpath("//article | //main | //section | //div"):
        text = _clean_text(node.text_content())
        if len(text) < MIN_CONTENT_CHARS:
            continue
        paragraphs = len(node.xpath(".//p"))
        link_text = " ".join(
            _clean_text(value) for value in node.xpath(".//a//text()")
        )
        # 正文越长、段落越多、链接占比越低越像正文。
        score = len(text) + paragraphs * 80 - len(link_text) * 2
        if score > best_score:
            best_node, best_score = node, score
    if best_node is None:
        return ""
    blocks = [
        _clean_text(node.text_content())
        for node in best_node.xpath(".//h1 | .//h2 | .//h3 | .//p | .//li")
    ]
    blocks = [block for block in blocks if len(block) >= 8]
    return "\n".join(blocks) if blocks else _clean_text(best_node.text_content())


def _title_key(value: str) -> str:
    return re.sub(r"[\s　|丨|\-—:：,，。.、；;！!？?【】\[\]（）()\"'“”]", "", str(value or ""))


def strip_leading_title(text: str, title: str) -> str:
    """去掉正文开头与事件标题重复的那一行（标题由接口单独返回）。"""
    lines = [line for line in str(text or "").split("\n") if line.strip()]
    if not lines:
        return ""
    key = _title_key(title)
    if key and _title_key(lines[0]) == key:
        return "\n".join(lines[1:])
    return "\n".join(lines)


def extract_article_text(html_text: str, *, url: str = "") -> str:
    """按成本从低到高依次尝试抽正文，返回清洗后的纯文本（可能为空串）。"""
    if not html_text or not html_text.strip():
        return ""
    # 顺序即成本顺序：newspaper 的中文分词首次加载约 9s，只在前两档失败时才用。
    for extractor in (_extract_json_ld, _extract_readability, _extract_newspaper):
        try:
            text = _clean_text(extractor(html_text, url))
        except Exception as exc:  # noqa: BLE001 - 单档抽取失败不能影响其它档
            logger.debug("[news-content] extractor %s failed: %s", extractor.__name__, exc)
            continue
        if len(text) >= MIN_CONTENT_CHARS:
            return text[:MAX_CONTENT_CHARS]
    return ""


# ---------------------------------------------------------------------------
# 服务编排：读库 → 判定 → 抓取 → 落库缓存
# ---------------------------------------------------------------------------
_STATUS_OK = "ok"
_STATUS_EMPTY = "empty"
_STATUS_FAILED = "failed"


def _iso_utc(value: Any) -> Optional[str]:
    """与监控事件一致的 UTC 字符串格式（库里存的是 naive UTC）。"""
    if not isinstance(value, datetime):
        return None
    return value.isoformat() + "Z"


class NewsContentService:
    """为单条情报事件解析正文，带数据库缓存与失败冷却。"""

    def __init__(
        self,
        repository: Optional[InvestmentMonitorRepository] = None,
        *,
        fetcher: Optional[Callable[[str], Tuple[str, str]]] = None,
        clock: Optional[Callable[[], datetime]] = None,
        cooldown_seconds: int = RETRY_COOLDOWN_SECONDS,
    ):
        self.repo = repository or InvestmentMonitorRepository()
        self._fetch = fetcher or fetch_public_html
        self._clock = clock or utc_naive_now
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self._locks: Dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- 对外入口 ----------------------------------------------------------
    def get_event_content(self, event_id: Any) -> Dict[str, Any]:
        identifier = self._coerce_id(event_id)
        event = self.repo.get_event(identifier)
        if event is None:
            raise NewsContentError("情报事件不存在")
        title = str(event.get("title") or "")
        # 原文链接不总在 url 列：long_news 只把它写在 raw_payload/metrics 里。
        url = self._resolve_source_url(event, identifier)
        summary = str(event.get("summary") or "").strip()

        # 数据源已经给了正文：直接回库里的内容，绝不发网络请求。
        if summary:
            return self._payload(
                identifier, title=title, status=_STATUS_OK, content=summary,
                source="db", url=url, fetched_at=None,
            )

        cached = self.repo.get_event_content_cache(identifier)
        now = self._clock()
        cached_payload = self._from_cache(identifier, title, url, cached, now)
        if cached_payload is not None:
            return cached_payload

        if not url:
            return self._payload(
                identifier, title=title, status=_STATUS_EMPTY, source=None, url=None,
                reason="no_url",
            )

        with self._event_lock(identifier):
            # 双保险：并发进入的第二个请求在锁内再读一次缓存，避免重复抓取。
            cached = self.repo.get_event_content_cache(identifier)
            cached_payload = self._from_cache(identifier, title, url, cached, self._clock())
            if cached_payload is not None:
                return cached_payload
            return self._fetch_and_store(identifier, title=title, url=url)

    # -- 内部实现 ----------------------------------------------------------
    @staticmethod
    def _usable_url(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    def _resolve_source_url(self, event: Dict[str, Any], event_id: int) -> Optional[str]:
        direct = self._usable_url(event.get("url"))
        if direct:
            return direct
        metrics = event.get("metrics")
        if isinstance(metrics, dict):
            nested = self._usable_url(metrics.get("url"))
            if nested:
                return nested
        try:
            return self._usable_url(self.repo.get_event_raw_url(event_id))
        except Exception as exc:  # noqa: BLE001 - 取链接失败只影响能否抓取
            logger.debug("[news-content] event %s raw url lookup failed: %s", event_id, exc)
            return None

    @staticmethod
    def _coerce_id(event_id: Any) -> int:
        try:
            identifier = int(event_id)
        except (TypeError, ValueError) as exc:
            raise NewsContentError("情报事件 ID 不合法") from exc
        if identifier <= 0:
            raise NewsContentError("情报事件 ID 不合法")
        return identifier

    def _event_lock(self, event_id: int) -> threading.Lock:
        with self._locks_guard:
            if len(self._locks) > SAME_EVENT_LOCK_LIMIT:
                self._locks = {key: value for key, value in self._locks.items() if value.locked()}
            return self._locks.setdefault(event_id, threading.Lock())

    def _from_cache(
        self, event_id: int, title: str, url: Optional[str],
        cached: Optional[Dict[str, Any]], now: datetime,
    ) -> Optional[Dict[str, Any]]:
        if not cached:
            return None
        if cached.get("status") == _STATUS_OK and cached.get("content"):
            return self._payload(
                event_id, title=title, status=_STATUS_OK, content=cached["content"],
                source="cache", url=url, fetched_at=cached.get("fetched_at"),
            )
        attempted_at = cached.get("attempted_at")
        remaining = self._cooldown_remaining(attempted_at, now)
        if remaining <= 0:
            return None
        # 冷却期内重复点开：返回上次的结构化结果，不再打网络。
        status = _STATUS_EMPTY if cached.get("status") == _STATUS_EMPTY else _STATUS_FAILED
        return self._payload(
            event_id, title=title, status=status, source=None, url=url,
            reason=str(cached.get("error") or _STATUS_FAILED),
            message=_REASON_MESSAGES.get("throttled"),
            retry_after=remaining,
        )

    def _cooldown_remaining(self, attempted_at: Any, now: datetime) -> int:
        if not isinstance(attempted_at, datetime) or self.cooldown_seconds <= 0:
            return 0
        elapsed = (now - attempted_at).total_seconds()
        return max(0, int(round(self.cooldown_seconds - elapsed)))

    def _fetch_and_store(self, event_id: int, *, title: str, url: str) -> Dict[str, Any]:
        final_url = url
        try:
            final_url, html_text = self._fetch(url)
            text = extract_article_text(str(html_text or ""), url=str(final_url or url))
            text = strip_leading_title(text, title)[:MAX_CONTENT_CHARS]
        except FetchFailure as exc:
            logger.info("[news-content] event %s fetch failed: %s", event_id, exc.reason)
            stored = self.repo.save_event_content_cache(
                event_id, status=_STATUS_FAILED, content=None,
                error=exc.reason, source_url=url, attempted_at=self._clock(),
            )
            return self._payload(
                event_id, title=title, status=_STATUS_FAILED, source=None, url=url,
                reason=exc.reason, message=exc.message,
                retry_after=self._cooldown_from(stored),
            )
        except Exception as exc:  # noqa: BLE001 - 未预期异常也不能变成 500
            logger.warning("[news-content] event %s fetch crashed: %s", event_id, type(exc).__name__)
            stored = self.repo.save_event_content_cache(
                event_id, status=_STATUS_FAILED, content=None,
                error="fetch_failed", source_url=url, attempted_at=self._clock(),
            )
            return self._payload(
                event_id, title=title, status=_STATUS_FAILED, source=None, url=url,
                reason="fetch_failed", message=_REASON_MESSAGES["fetch_failed"],
                retry_after=self._cooldown_from(stored),
            )

        if not text:
            stored = self.repo.save_event_content_cache(
                event_id, status=_STATUS_EMPTY, content=None,
                error="no_text", source_url=str(final_url or url),
                attempted_at=self._clock(),
            )
            return self._payload(
                event_id, title=title, status=_STATUS_EMPTY, source="fetched", url=url,
                reason="no_text", retry_after=self._cooldown_from(stored),
            )

        stored = self.repo.save_event_content_cache(
            event_id, status=_STATUS_OK, content=text,
            error=None, source_url=str(final_url or url),
            fetched_at=self._clock(), attempted_at=self._clock(),
        )
        return self._payload(
            event_id, title=title, status=_STATUS_OK, content=text, source="fetched",
            url=url, fetched_at=(stored or {}).get("fetched_at") or self._clock(),
        )

    def _cooldown_from(self, stored: Optional[Dict[str, Any]]) -> int:
        if not stored:
            return 0
        return self._cooldown_remaining(stored.get("attempted_at"), self._clock())

    @staticmethod
    def _payload(
        event_id: int, *, title: str, status: str, content: Optional[str] = None,
        source: Optional[str] = None, url: Optional[str] = None,
        fetched_at: Any = None, reason: Optional[str] = None,
        message: Optional[str] = None, retry_after: int = 0,
    ) -> Dict[str, Any]:
        text = content if status == _STATUS_OK else None
        return {
            "eventId": int(event_id),
            "title": title,
            "status": status,
            "content": text,
            "contentChars": len(text or ""),
            "source": source,
            "url": url,
            "fetchedAt": _iso_utc(fetched_at),
            "reason": None if status == _STATUS_OK else (reason or status),
            "message": message or (
                None if status == _STATUS_OK else _REASON_MESSAGES.get(reason or "", _REASON_MESSAGES["fetch_failed"])
            ),
            "retryAfterSeconds": max(0, int(retry_after or 0)),
        }
