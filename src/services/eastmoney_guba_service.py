# -*- coding: utf-8 -*-
"""Polite reader for publicly visible Eastmoney Guba stock-post listings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

from lxml import html
import requests


GUBA_ROOT = "https://mguba.eastmoney.com"
GUBA_LIST_URL = GUBA_ROOT + "/mguba/list/{code},1,f_1"
_CHINA_TZ = timezone(timedelta(hours=8))


class EastmoneyGubaError(RuntimeError):
    """Safe upstream or public-page parsing failure."""


class EastmoneyGubaService:
    """Read the newest public posts for a bounded set of A-share symbols.

    The adapter only reads the public list page. It does not log in, solve
    CAPTCHAs, enumerate replies, or crawl older pages.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        *,
        timeout: float = 15.0,
        retries: int = 1,
        request_interval: float = 0.15,
    ):
        self.session = session or requests.Session()
        self.timeout = max(3.0, min(float(timeout), 30.0))
        self.retries = max(0, min(int(retries), 2))
        self.request_interval = max(0.0, min(float(request_interval), 2.0))
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://guba.eastmoney.com/",
        }

    def fetch_latest(self, symbols: Iterable[str], *, limit_per_symbol: int = 20) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit_per_symbol), 30))
        rows: List[Dict[str, Any]] = []
        seen_codes = set()
        for raw_symbol in symbols:
            symbol = str(raw_symbol or "").strip().upper().replace("SS", "SH")
            code = symbol.split(".", 1)[0]
            if not re.fullmatch(r"\d{6}", code) or code in seen_codes:
                continue
            seen_codes.add(code)
            rows.extend(self.fetch_symbol(code, limit=limit))
            if self.request_interval:
                time.sleep(self.request_interval)
        return rows

    def fetch_symbol(self, code: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        normalized = str(code or "").strip().split(".", 1)[0]
        if not re.fullmatch(r"\d{6}", normalized):
            raise EastmoneyGubaError(f"无效 A 股代码：{code}")
        url = GUBA_LIST_URL.format(code=normalized)
        response = self._get(url)
        return self.parse_listing(response.text, code=normalized, now=datetime.now(_CHINA_TZ))[:max(1, min(limit, 30))]

    def _get(self, url: str) -> requests.Response:
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, headers=self.headers, timeout=self.timeout)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding or "utf-8"
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.35 * (attempt + 1))
        raise EastmoneyGubaError(f"东方财富股吧公开页面请求失败：{type(last_error).__name__}") from last_error

    @classmethod
    def parse_listing(cls, markup: str, *, code: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        current = now or datetime.now(_CHINA_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=_CHINA_TZ)
        try:
            document = html.fromstring(markup or "")
        except (TypeError, ValueError) as exc:
            raise EastmoneyGubaError("东方财富股吧公开页面无法解析") from exc
        nodes = document.xpath('//ul[@id="items"]/li[contains(concat(" ", normalize-space(@class), " "), " type_0 ")]')
        if not nodes:
            visible = cls._clean_text(" ".join(document.xpath("//body//text()")))
            if any(word in visible for word in ("验证码", "访问过于频繁", "安全验证")):
                raise EastmoneyGubaError("东方财富股吧要求安全验证，已停止本轮同步")
            raise EastmoneyGubaError("东方财富股吧公开列表未返回帖子")

        result: List[Dict[str, Any]] = []
        seen = set()
        for node in nodes:
            links = node.xpath('.//a[contains(@href, "/mguba/article/")]/@href')
            href = str(links[0]).strip() if links else ""
            match = re.search(r"/mguba/article/\d+/(\d+)", href)
            if not match or match.group(1) in seen:
                continue
            post_id = match.group(1)
            seen.add(post_id)
            content = cls._clean_text(" ".join(node.xpath('.//p[contains(concat(" ", normalize-space(@class), " "), " abstract ")]//text()')))
            if not content:
                continue
            author = cls._clean_text(" ".join(node.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " name_text ")]//text()')))
            time_text = cls._clean_text(" ".join(node.xpath('.//p[contains(concat(" ", normalize-space(@class), " "), " time ")]//text()')))
            reply_text = cls._clean_text(" ".join(node.xpath('.//a[contains(concat(" ", normalize-space(@class), " "), " reply ")]//text()')))
            like_text = cls._clean_text(" ".join(node.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " like_count ")]//text()')))
            image_urls = [str(value).strip() for value in node.xpath('.//img[@data-src]/@data-src') if str(value).strip()]
            result.append({
                "post_id": post_id,
                "code": code,
                "author": author or "东方财富股吧用户",
                "content": content,
                "published_at": cls._parse_public_time(time_text, current),
                "time_text": time_text,
                "views": cls._count_from_text(time_text, suffix="次浏览"),
                "reply_count": cls._count_from_text(reply_text),
                "like_count": cls._count_from_text(like_text),
                "image_urls": list(dict.fromkeys(image_urls))[:9],
                "url": urljoin(GUBA_ROOT, href),
            })
        if not result:
            raise EastmoneyGubaError("东方财富股吧公开列表没有可入库的帖子")
        return result

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _count_from_text(value: str, *, suffix: str = "") -> int:
        text = str(value or "")
        if suffix:
            match = re.search(rf"(\d+(?:\.\d+)?)\s*(万)?\s*{re.escape(suffix)}", text)
        else:
            match = re.search(r"(\d+(?:\.\d+)?)\s*(万)?", text)
        if not match:
            return 0
        count = float(match.group(1)) * (10000 if match.group(2) else 1)
        return int(count)

    @staticmethod
    def _parse_public_time(value: str, now: datetime) -> datetime:
        text = str(value or "")
        today_match = re.search(r"今天\s*(\d{1,2}):(\d{2})", text)
        if today_match:
            local = now.replace(hour=int(today_match.group(1)), minute=int(today_match.group(2)), second=0, microsecond=0)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        full_match = re.search(r"(20\d{2})[-/]([01]?\d)[-/]([0-3]?\d)\s+(\d{1,2}):(\d{2})", text)
        if full_match:
            local = datetime(*(int(value) for value in full_match.groups()), tzinfo=_CHINA_TZ)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        short_match = re.search(r"([01]?\d)-([0-3]?\d)\s+(\d{1,2}):(\d{2})", text)
        if short_match:
            month, day, hour, minute = (int(value) for value in short_match.groups())
            local = datetime(now.year, month, day, hour, minute, tzinfo=_CHINA_TZ)
            if local > now + timedelta(days=2):
                local = local.replace(year=local.year - 1)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        return now.astimezone(timezone.utc).replace(tzinfo=None)
