# -*- coding: utf-8 -*-
"""API 全量冒烟的「调用策略」——测试与脚本共用的纯标准库模块。

为什么单独抽一个模块：`tests/test_api_endpoint_smoke.py`（pytest 侧）和
`scripts/list_api_endpoints.py --check-coverage`（脚本侧）必须对「哪些端点会被真正调用」
给出同一个答案，否则覆盖率数字和测试行为会对不上。这里只做 JSON 读取和分类，
不导入 FastAPI，保证脚本在任意 python 下都能跑。

分类结果（对单个端点）：

- ``invoke_read``：GET/HEAD，直接调用。
- ``invoke_body_empty``：写接口且请求体有必填字段 → 发 ``{}``，校验层必然拒绝（422），
  用来验证路由与请求体契约，同时保证 handler 不会被执行（冒烟不能写数据）。
- ``invoke_body_malformed``：写接口但请求体字段全可选 → 发非法 JSON，FastAPI 在解析阶段
  就返回 422，同样不会进 handler。
- ``not_invoked``：不调用。只有三类：策略显式列出的流式/强网络端点，以及
  「写接口且完全没有请求体」的机械规则（这类若调用可能真的改数据/起后台任务）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TESTS_DIR = Path(__file__).resolve().parent
BASELINE_PATH = TESTS_DIR / "fixtures" / "api_endpoints.json"
POLICY_PATH = TESTS_DIR / "fixtures" / "api_endpoint_smoke_policy.json"

READ_METHODS = frozenset({"GET", "HEAD"})

# 分类名
INVOKE_READ = "invoke_read"
INVOKE_BODY_EMPTY = "invoke_body_empty"
INVOKE_BODY_MALFORMED = "invoke_body_malformed"
NOT_INVOKED = "not_invoked"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_baseline(path: Path = BASELINE_PATH) -> Dict[str, Any]:
    """读取端点基线（scripts/list_api_endpoints.py 生成）。"""
    return _load_json(path)


def load_policy(path: Path = POLICY_PATH) -> Dict[str, Any]:
    """读取冒烟策略。"""
    return _load_json(path)


def endpoint_key(endpoint: Dict[str, Any]) -> str:
    """稳定标识：``METHOD /path``。"""
    return f"{endpoint['method']} {endpoint['path']}"


def _policy_maps(policy: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    not_invoked = {entry["endpoint"]: entry for entry in policy.get("not_invoked", [])}
    known_issues = {entry["endpoint"]: entry for entry in policy.get("known_issues", [])}
    return not_invoked, known_issues


def classify(endpoint: Dict[str, Any], policy: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """返回 ``(分类, 原因)``；原因只在 ``not_invoked`` 时有值。"""
    key = endpoint_key(endpoint)
    not_invoked, _known = _policy_maps(policy)
    if key in not_invoked:
        return NOT_INVOKED, str(not_invoked[key].get("reason") or "策略显式跳过")

    if endpoint["method"] in READ_METHODS:
        return INVOKE_READ, None

    if not endpoint.get("has_body"):
        return NOT_INVOKED, (
            "写接口且没有请求体：冒烟不带业务载荷调用会真的改数据或起后台任务，"
            "因此只做鉴权边界与路由注册校验"
        )

    if endpoint.get("body_required_fields"):
        return INVOKE_BODY_EMPTY, None
    return INVOKE_BODY_MALFORMED, None


def known_issue(endpoint: Dict[str, Any], policy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """返回该端点的已知问题条目（没有则 None）。"""
    _not_invoked, known = _policy_maps(policy)
    return known.get(endpoint_key(endpoint))


def summarize(endpoints: Iterable[Dict[str, Any]], policy: Dict[str, Any]) -> Dict[str, Any]:
    """统计各分类数量，供测试与脚本输出一致的口径。"""
    counts: Dict[str, int] = {INVOKE_READ: 0, INVOKE_BODY_EMPTY: 0, INVOKE_BODY_MALFORMED: 0, NOT_INVOKED: 0}
    not_invoked: List[Dict[str, Any]] = []
    invoked: List[Dict[str, Any]] = []
    for endpoint in endpoints:
        kind, reason = classify(endpoint, policy)
        counts[kind] += 1
        if kind == NOT_INVOKED:
            not_invoked.append({"endpoint": endpoint_key(endpoint), "reason": reason})
        else:
            invoked.append(endpoint)
    counts["total"] = len(invoked) + len(not_invoked)
    counts["invoked"] = len(invoked)
    counts["known_issues"] = sum(1 for item in invoked if known_issue(item, policy))
    return {
        "counts": counts,
        "invoked": invoked,
        "not_invoked": not_invoked,
        "known_issue_keys": sorted({endpoint_key(item) for item in invoked if known_issue(item, policy)}),
    }
