# -*- coding: utf-8 -*-
"""Tests for the bounded public Eastmoney Guba listing reader."""

from datetime import datetime, timedelta, timezone

import pytest

from src.services.eastmoney_guba_service import EastmoneyGubaError, EastmoneyGubaService


LISTING_HTML = """
<html><body><ul id="items">
  <li class="type_0">
    <a href="/mguba/article/0/1762131880">
      <span class="name_text"> 股友甲 </span>
      <p class="time">发表于 今天 18:07 <span>1.2万次浏览</span></p>
      <p class="abstract">$华懋科技(SH603306)$ 昨天尾盘抄底，今天清货。</p>
    </a>
    <img data-src="https://example.com/post.jpg" />
    <a class="reply"><em></em>15</a>
    <span class="like_count">3</span>
  </li>
  <li class="type_0 featured">
    <a href="/mguba/article/0/1761804009">
      <span class="name_text">股友乙</span>
      <p class="time">更新于 12-31 09:30 <span>161次浏览</span></p>
      <p class="abstract">110 成本的战友们，还在吗？</p>
    </a>
    <a class="reply"><em></em>评论</a>
    <span class="like_count">赞</span>
  </li>
</ul></body></html>
"""


def test_parse_public_posts_keeps_author_counts_images_and_original_url():
    now = datetime(2026, 1, 2, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    rows = EastmoneyGubaService.parse_listing(LISTING_HTML, code="603306", now=now)

    assert len(rows) == 2
    assert rows[0]["post_id"] == "1762131880"
    assert rows[0]["author"] == "股友甲"
    assert rows[0]["views"] == 12000
    assert rows[0]["reply_count"] == 15
    assert rows[0]["like_count"] == 3
    assert rows[0]["image_urls"] == ["https://example.com/post.jpg"]
    assert rows[0]["url"] == "https://mguba.eastmoney.com/mguba/article/0/1762131880"
    assert rows[0]["published_at"] == datetime(2026, 1, 2, 10, 7)


def test_short_date_rolls_back_year_when_it_would_be_in_the_future():
    now = datetime(2026, 1, 2, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    rows = EastmoneyGubaService.parse_listing(LISTING_HTML, code="603306", now=now)
    assert rows[1]["published_at"] == datetime(2025, 12, 31, 1, 30)


def test_security_verification_page_is_reported_without_bypass():
    with pytest.raises(EastmoneyGubaError, match="安全验证"):
        EastmoneyGubaService.parse_listing("<html><body>访问过于频繁，请完成安全验证</body></html>", code="603306")


def test_invalid_symbol_is_rejected_before_network_access():
    with pytest.raises(EastmoneyGubaError, match="无效 A 股代码"):
        EastmoneyGubaService().fetch_symbol("bad")
