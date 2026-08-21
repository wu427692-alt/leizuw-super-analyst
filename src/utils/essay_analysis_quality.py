# -*- coding: utf-8 -*-
"""Shared quality rules for durable essay-analysis results."""

from __future__ import annotations

import re
from typing import Any


LOW_QUALITY_SUMMARY_MARKERS = (
    "信息不足，未形成有效摘要",
    "信息不足未形成有效摘要",
    "未形成有效摘要",
    "无法形成有效摘要",
    "无法生成有效摘要",
)


def is_low_quality_summary(value: Any) -> bool:
    """Return true when a model response is a placeholder rather than a summary."""
    normalized = re.sub(r"\s+", "", str(value or "").strip())
    if not normalized:
        return True
    return any(marker.replace("，", "") in normalized.replace("，", "") for marker in LOW_QUALITY_SUMMARY_MARKERS)
