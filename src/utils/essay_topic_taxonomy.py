# -*- coding: utf-8 -*-
"""Deterministic topic taxonomy for essay aggregation and retrieval.

The model's raw labels remain the evidence. This module only maps strong
synonyms and explicit sub-themes to one stable display topic; it never changes
stored analysis rows.
"""

from __future__ import annotations

import re
from typing import Iterable, Tuple


TOPIC_TAXONOMY_VERSION = "essay-topic-taxonomy-v1"

# Keep this registry conservative: aliases should be true synonyms or a clear
# investable sub-theme of the canonical topic, not a merely adjacent industry.
TOPIC_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("光通信", (
        "光通信", "光模块", "光器件", "光芯片", "光互联", "硅光", "硅光子",
        "CPO", "NPO", "LPO", "OCS", "共封装光学", "光电共封装", "800G光模块", "1.6T光模块",
    )),
    ("AI算力", ("AI算力", "算力基础设施", "智算中心", "AI服务器", "GPU服务器", "算力租赁")),
    ("PCB", ("PCB", "印制电路板", "高多层板", "HDI", "IC载板", "覆铜板", "CCL")),
    ("先进封装", ("先进封装", "CHIPLET", "COWOS", "HBM封装", "2.5D封装", "3D封装")),
    ("机器人", ("机器人", "人形机器人", "具身智能", "机器人执行器", "谐波减速器", "行星减速器", "滚柱丝杠")),
    ("低空经济", ("低空经济", "飞行汽车", "EVTOL", "通用航空", "无人机物流")),
    ("固态电池", ("固态电池", "全固态电池", "半固态电池", "固态电解质")),
    ("智能驾驶", ("智能驾驶", "自动驾驶", "智驾", "NOA", "ROBOTAXI")),
    ("创新药", ("创新药", "ADC", "双抗", "GLP-1", "小核酸药物")),
)


def _clean(value: str) -> str:
    return re.sub(r"[\s_\-—/（）()·]+", "", str(value or "").strip()).upper()


_ALIAS_LOOKUP = {
    _clean(alias): canonical
    for canonical, aliases in TOPIC_GROUPS
    for alias in aliases
}


def canonicalize_topic(value: str) -> str:
    """Map a raw model label to a stable topic while preserving unknown labels."""
    raw = str(value or "").strip()
    normalized = _clean(raw)
    if not normalized:
        return ""
    exact = _ALIAS_LOOKUP.get(normalized)
    if exact:
        return exact
    upper_raw = raw.upper()
    for canonical, aliases in TOPIC_GROUPS:
        for alias in sorted(aliases, key=len, reverse=True):
            normalized_alias = _clean(alias)
            if not normalized_alias:
                continue
            if normalized_alias.isascii():
                if re.search(rf"(?<![A-Z0-9]){re.escape(normalized_alias)}(?![A-Z0-9])", upper_raw):
                    return canonical
            elif normalized_alias in normalized:
                return canonical
    return raw


def canonicalize_topics(values: Iterable[str]) -> list[tuple[str, str]]:
    """Return unique raw ``(canonical, raw)`` pairs for one record."""
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        raw = str(value or "").strip()
        canonical = canonicalize_topic(raw)
        pair = (canonical, raw)
        if not canonical or pair in seen:
            continue
        seen.add(pair)
        result.append(pair)
    return result


def topic_search_terms(value: str) -> tuple[str, ...]:
    """Expand a canonical topic or alias into equivalent full-text terms."""
    raw = str(value or "").strip()
    canonical = canonicalize_topic(raw)
    for group_name, aliases in TOPIC_GROUPS:
        if group_name == canonical:
            return tuple(dict.fromkeys((group_name, *aliases)))
    return (raw,) if raw else ()
