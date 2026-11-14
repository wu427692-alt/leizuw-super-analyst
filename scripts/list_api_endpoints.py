#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出并校验后端 API 端点清单（全量冒烟 / 契约测试的基线）。

这个脚本只做三件事，都是为了让「全站端点」变成可重复比对的数字：

1. 默认行为：从 ``api.app.app.openapi()`` 导出全部端点，写成两份稳定清单
   - ``tests/fixtures/api_endpoints.json``：机器可读基线，测试直接读它
   - ``docs/api-endpoints.md``：人可读清单，按 tag 分组
2. ``--check``：确认磁盘上的基线没有过期（OpenAPI 变了但没人重新生成）。
3. ``--check-coverage``：把基线里的端点和 ``tests/`` 里真实出现过的请求路径做比对，
   输出「没有任何测试引用的端点」清单。默认只提示不阻断，``--strict-coverage``
   才会以非零码退出。

为什么基线要落盘而不是测试里现场调 openapi：
- 基线进 git 后，任何端点的增删改都会在 diff 里显形，评审时看得见；
- 测试失败时能直接指出「哪个 method+path」，而不是只给一个总数；
- ``--check-coverage`` 不需要导入 FastAPI 也能跑（可离线、可用任意 python）。

用法：
    .venv/bin/python scripts/list_api_endpoints.py                     # 重新生成基线
    .venv/bin/python scripts/list_api_endpoints.py --check             # 校验基线是否过期
    .venv/bin/python scripts/list_api_endpoints.py --check-coverage    # 输出未覆盖清单
    .venv/bin/python scripts/list_api_endpoints.py --check-coverage --strict-coverage

退出码：
    0 = 正常；1 = 基线过期 / 严格模式下存在未覆盖端点 / 生成失败
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON = ROOT / "tests" / "fixtures" / "api_endpoints.json"
DEFAULT_MD = ROOT / "docs" / "api-endpoints.md"
TESTS_DIR = ROOT / "tests"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
# method 排序：读接口在前、写接口在后，清单 diff 更稳定。
_METHOD_ORDER = {name: index for index, name in enumerate(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"))}

_PLACEHOLDER = re.compile(r"\{[^}/]*\}")


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


def _resolve_ref(schema: Dict[str, Any], node: Any) -> Any:
    """把 JSON Schema 里的 ``$ref`` 就地展开（只处理本地 components/$defs）。"""
    seen = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            return {}
        seen.add(ref)
        if not ref.startswith("#/"):
            return {}
        cursor: Any = schema
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(cursor, dict) or part not in cursor:
                return {}
            cursor = cursor[part]
        node = cursor
    return node if isinstance(node, dict) else {}


def _body_shape(schema: Dict[str, Any], operation: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """返回 (是否有请求体, 请求体的必填字段)。"""
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return False, []
    contents = request_body.get("content") or {}
    for media_type in ("application/json", "application/x-www-form-urlencoded", "multipart/form-data"):
        if media_type not in contents:
            continue
        spec = _resolve_ref(schema, contents[media_type].get("schema") or {})
        required = spec.get("required") or []
        return True, sorted(str(name) for name in required)
    return bool(contents), []


def _operation_params(operation: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = []
    for raw in operation.get("parameters") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not name:
            continue
        schema = raw.get("schema") or {}
        params.append(
            {
                "name": str(name),
                "in": raw.get("in") or "query",
                "required": bool(raw.get("required")),
                "type": schema.get("type") or "",
            }
        )
    return params


def collect_endpoints() -> Dict[str, Any]:
    """从运行中的 FastAPI app 采集全部端点（慢：要 import 整个后端）。"""
    sys.path.insert(0, str(ROOT))
    from api.app import app  # noqa: WPS433  (延迟导入，--check-coverage 不需要它)
    from api.middlewares import auth as auth_middleware

    schema = app.openapi()
    endpoints: List[Dict[str, Any]] = []
    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method not in HTTP_METHODS:
                continue
            operation = operation if isinstance(operation, dict) else {}
            params = _operation_params(operation)
            has_body, required_body_fields = _body_shape(schema, operation)
            if auth_middleware._path_exempt(path):
                auth_scope = "public"
            elif auth_middleware._requires_admin(method.upper(), path):
                auth_scope = "admin"
            else:
                auth_scope = "user"
            endpoints.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "tag": (operation.get("tags") or ["(untagged)"])[0],
                    "operation_id": operation.get("operationId") or "",
                    "summary": operation.get("summary") or "",
                    "auth": auth_scope,
                    "deprecated": bool(operation.get("deprecated")),
                    "has_body": has_body,
                    "body_required_fields": required_body_fields,
                    "path_params": [p for p in params if p["in"] == "path"],
                    "query_required": [p["name"] for p in params if p["in"] == "query" and p["required"]],
                    "path_param_specs": [
                        {"name": p["name"], "type": p["type"]} for p in params if p["in"] == "path"
                    ],
                }
            )

    endpoints.sort(key=lambda item: (item["path"], _METHOD_ORDER.get(item["method"], 99)))
    tags = sorted({item["tag"] for item in endpoints})
    auth_counts: Dict[str, int] = {}
    for item in endpoints:
        auth_counts[item["auth"]] = auth_counts.get(item["auth"], 0) + 1

    return {
        "generated_by": "scripts/list_api_endpoints.py",
        "source": "api.app.app.openapi()",
        "totals": {
            "paths": len(schema.get("paths", {})),
            "endpoints": len(endpoints),
            "tags": len(tags),
        },
        "auth_counts": dict(sorted(auth_counts.items())),
        "tags": tags,
        "endpoints": endpoints,
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_markdown(payload: Dict[str, Any]) -> str:
    totals = payload["totals"]
    auth_counts = payload["auth_counts"]
    lines: List[str] = []
    lines.append("# 后端 API 端点清单")
    lines.append("")
    lines.append(
        "> 本文件由 `scripts/list_api_endpoints.py` 自动生成，**不要手工编辑**；"
        "端点有增删改就重新跑一次脚本并提交 diff。"
    )
    lines.append("")
    lines.append(
        f"- 来源：`{payload['source']}`\n"
        f"- 路径数：**{totals['paths']}**\n"
        f"- 端点数（method + path）：**{totals['endpoints']}**\n"
        f"- tag 数：**{totals['tags']}**\n"
        f"- 鉴权分布：公开 {auth_counts.get('public', 0)} / 前台用户 {auth_counts.get('user', 0)} / 管理员 "
        f"{auth_counts.get('admin', 0)}"
    )
    lines.append("")
    lines.append("鉴权口径：`public` = 未登录也可访问；`user` = `USER_ACCESS_ENABLED=true` 时需要有效用户会话；"
                 "`admin` = `ADMIN_AUTH_ENABLED=true` 时需要管理员会话（见 `api/middlewares/auth.py`）。")
    lines.append("")
    lines.append("## 按 tag 汇总")
    lines.append("")
    lines.append("| tag | 端点数 |")
    lines.append("| --- | ---: |")
    by_tag: Dict[str, List[Dict[str, Any]]] = {}
    for item in payload["endpoints"]:
        by_tag.setdefault(item["tag"], []).append(item)
    for tag in sorted(by_tag, key=lambda name: (-len(by_tag[name]), name)):
        lines.append(f"| {tag} | {len(by_tag[tag])} |")
    lines.append("")
    for tag in sorted(by_tag):
        lines.append(f"## {tag}（{len(by_tag[tag])}）")
        lines.append("")
        lines.append("| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for item in by_tag[tag]:
            body = (
                ", ".join(item["body_required_fields"])
                if item["body_required_fields"]
                else ("有（无必填字段）" if item["has_body"] else "-")
            )
            query = ", ".join(item["query_required"]) or "-"
            path_params = ", ".join(f"{p['name']}:{p['type'] or 'any'}" for p in item["path_params"]) or "-"
            summary = (item["summary"] or "").replace("|", "\\|")
            lines.append(
                f"| {item['method']} | `{item['path']}` | {item['auth']} | {body} | {query} | {path_params} | {summary} |"
            )
        lines.append("")
    lines.append("## 使用方式")
    lines.append("")
    lines.append("```bash")
    lines.append("# 重新生成清单（改了路由后必须跑）")
    lines.append(".venv/bin/python scripts/list_api_endpoints.py")
    lines.append("")
    lines.append("# 校验清单是否过期（可进 CI，不阻断以外先做提示）")
    lines.append(".venv/bin/python scripts/list_api_endpoints.py --check")
    lines.append("")
    lines.append("# 输出「没有任何测试引用」的端点清单（提示项，默认不阻断）")
    lines.append(".venv/bin/python scripts/list_api_endpoints.py --check-coverage")
    lines.append("")
    lines.append("# 发布门禁里想看覆盖率缺口就加 --strict-coverage（存在两层都未覆盖的端点时 exit 1）")
    lines.append(".venv/bin/python scripts/list_api_endpoints.py --check-coverage --strict-coverage")
    lines.append("```")
    lines.append("")
    lines.append("## 对应测试")
    lines.append("")
    lines.append("| 文件 | 作用 |")
    lines.append("| --- | --- |")
    lines.append(
        "| `tests/test_api_endpoint_smoke.py` | 全量冒烟：端点注册 / 未鉴权 401/403 / 合法调用不 5xx / 响应可序列化 |"
    )
    lines.append(
        "| `tests/test_api_contract.py` | 关键业务端点结构化契约：agent/chat、investment-monitor、"
        "stocks（含 logo）、user-auth、home-dashboard、system |"
    )
    lines.append(
        "| `tests/fixtures/api_endpoint_smoke_policy.json` | 冒烟跳过原因与已知问题（xfail）白名单 |"
    )
    lines.append("")
    return "\n".join(lines)


def write_baseline(payload: Dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")


def load_baseline(json_path: Path) -> Dict[str, Any]:
    if not json_path.is_file():
        raise SystemExit(
            f"[error] 找不到端点基线 {json_path}；先跑 .venv/bin/python scripts/list_api_endpoints.py 生成它。"
        )
    return json.loads(json_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 基线漂移检查
# ---------------------------------------------------------------------------


def _endpoint_keys(payload: Dict[str, Any]) -> List[str]:
    return [f"{item['method']} {item['path']}" for item in payload["endpoints"]]


def check_drift(json_path: Path) -> int:
    stored = load_baseline(json_path)
    current = collect_endpoints()
    stored_keys, current_keys = _endpoint_keys(stored), _endpoint_keys(current)
    added = [key for key in current_keys if key not in set(stored_keys)]
    removed = [key for key in stored_keys if key not in set(current_keys)]
    changed = [
        key
        for key in current_keys
        if key in set(stored_keys)
        and _endpoint_detail(current, key) != _endpoint_detail(stored, key)
    ]

    if not (added or removed or changed):
        print(
            f"[ok] 端点基线未过期：{current['totals']['endpoints']} 个端点 / "
            f"{current['totals']['paths']} 条路径与 openapi() 一致"
        )
        return 0

    print("[drift] 端点基线已过期，请重新生成：`.venv/bin/python scripts/list_api_endpoints.py`")
    for title, keys in (("新增端点", added), ("删除端点", removed), ("元信息变化", changed)):
        if keys:
            print(f"\n{title}（{len(keys)}）：")
            for key in keys:
                print(f"  - {key}")
    return 1


def _endpoint_detail(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    for item in payload["endpoints"]:
        if f"{item['method']} {item['path']}" == key:
            return {k: v for k, v in item.items() if k not in {"summary"}}
    return {}


# ---------------------------------------------------------------------------
# 覆盖率检查（测试里是否真的出现过这个端点）
# ---------------------------------------------------------------------------


_WILDCARD = "\x00"


def _iter_test_files() -> Iterable[Path]:
    if not TESTS_DIR.is_dir():
        return ()
    return sorted(TESTS_DIR.rglob("test_*.py"))


def _path_segments(template: str) -> Tuple[str, ...]:
    """把路径模板拆成段；``{param}``（以及已知的动态段）统一成通配符 ``*``。"""
    text = template.strip()
    # 去掉 scheme://host、query、fragment；只保留 path 部分。
    for scheme in ("http://", "https://"):
        if text.startswith(scheme):
            rest = text.split("/", 3)
            text = "/" + (rest[3] if len(rest) > 3 else "")
            break
    for separator in ("?", "#"):
        text = text.split(separator, 1)[0]
    text = _PLACEHOLDER.sub(_WILDCARD, text)
    segments = [segment if segment != _WILDCARD else "*" for segment in text.split("/") if segment]
    return tuple(segments)


def _fstring_template(node: ast.JoinedStr) -> str:
    """把 f-string 还原成带 ``{*}`` 占位符的模板字符串。"""
    parts: List[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append(_WILDCARD)
    return "".join(parts)


def _reference_paths() -> List[Tuple[str, Tuple[str, ...]]]:
    """从 tests/ 的 AST 里收集路径字面量（含 f-string），转成段列表。"""
    references: List[Tuple[str, Tuple[str, ...]]] = []
    seen = set()
    for path in _iter_test_files():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literal = node.value.strip()
            elif isinstance(node, ast.JoinedStr):
                literal = _fstring_template(node).strip()
            else:
                continue
            if "/api/" in literal:
                literal = literal[literal.index("/api/"):]
            elif literal.startswith(("/health", "/landing/")):
                pass
            else:
                continue
            if not literal or literal in seen:
                continue
            seen.add(literal)
            references.append((literal, _path_segments(literal)))
    return references


def _segments_match(endpoint: Tuple[str, ...], reference: Tuple[str, ...]) -> bool:
    if len(endpoint) != len(reference):
        return False
    return all(left == right or left == "*" or right == "*" for left, right in zip(endpoint, reference))


def _load_smoke_summary(endpoints: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """读取冒烟策略，得到「冒烟层实际会调用哪些端点」的统一口径。"""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tests.api_smoke_policy import load_policy, summarize  # noqa: WPS433

        return summarize(endpoints, load_policy())
    except Exception as exc:  # pragma: no cover - 策略文件缺失时降级
        print(f"[coverage][warn] 读取冒烟策略失败，跳过冒烟覆盖率：{exc}")
        return None


def check_coverage(json_path: Path, strict: bool = False, show: int = 40) -> int:
    payload = load_baseline(json_path)
    endpoints = payload["endpoints"]
    references = _reference_paths()

    uncovered: List[Dict[str, Any]] = []
    covered: List[Dict[str, Any]] = []
    for endpoint in endpoints:
        endpoint_segments = _path_segments(endpoint["path"])
        hit = any(_segments_match(endpoint_segments, reference_segments) for _literal, reference_segments in references)
        (covered if hit else uncovered).append(endpoint)

    total = len(endpoints)
    ratio = (len(covered) / total * 100) if total else 0.0
    smoke = _load_smoke_summary(endpoints)
    smoke_keys = {f"{item['method']} {item['path']}" for item in (smoke["invoked"] if smoke else [])}
    gap = [endpoint for endpoint in uncovered
           if f"{endpoint['method']} {endpoint['path']}" not in smoke_keys]

    print(f"[coverage] 端点 {total} 个。")
    if smoke:
        counts = smoke["counts"]
        smoke_ratio = counts["invoked"] / total * 100 if total else 0.0
        print(f"[coverage] 冒烟层实际调用 {counts['invoked']} 个（{smoke_ratio:.1f}%）；"
              f"不调用 {counts['not_invoked']} 个（流式/强网络/无请求体写接口，原因见策略文件）；"
              f"其中已知问题 {counts['known_issues']} 个（xfail 记录）。")
    print(f"[coverage] 专项测试引用到 {len(covered)} 个（{ratio:.1f}%），"
          f"测试文件里没有路径字面量引用 {len(uncovered)} 个。")
    print(f"[coverage] 扫描了 {len(_iter_test_files())} 个 test_*.py，"
          f"提取到 {len(references)} 个 API 路径字面量。")
    print(f"[coverage] 两层都没有覆盖的「真空端点」{len(gap)} 个。")
    print("[coverage] 口径：专项覆盖 = 测试文件里出现过与该端点路径等价的字符串；"
          "冒烟覆盖 = tests/test_api_endpoint_smoke.py 会真正发出请求的端点。")

    if uncovered:
        by_tag: Dict[str, int] = {}
        for endpoint in uncovered:
            by_tag[endpoint["tag"]] = by_tag.get(endpoint["tag"], 0) + 1
        print("\n无专项测试引用的端点按 tag 分布（前 15）：")
        for tag, count in sorted(by_tag.items(), key=lambda item: (-item[1], item[0]))[:15]:
            print(f"  - {tag:20s} {count}")
        print(f"\n无专项测试引用的端点明细（最多显示 {show} 条）：")
        for endpoint in uncovered[:show]:
            key = f"{endpoint['method']} {endpoint['path']}"
            smoke_note = "冒烟覆盖" if key in smoke_keys else "冒烟也不调用"
            print(f"  - {endpoint['method']:6s} {endpoint['path']}  [{endpoint['auth']}] ({smoke_note})")
        if len(uncovered) > show:
            print(f"  ... 其余 {len(uncovered) - show} 条省略（用 --show 调整）")

    if smoke and smoke["not_invoked"]:
        mechanical = "写接口且没有请求体"
        mechanical_items = [item for item in smoke["not_invoked"] if item["reason"].startswith(mechanical)]
        explicit_items = [item for item in smoke["not_invoked"] if not item["reason"].startswith(mechanical)]
        print(f"\n冒烟不调用的端点（{len(smoke['not_invoked'])}）：")
        if explicit_items:
            print("  策略显式跳过：")
            for item in explicit_items:
                print(f"    - {item['endpoint']}：{item['reason'][:90]}")
        if mechanical_items:
            print(f"  写接口但无请求体（机械规则，避免改数据/起任务）：{len(mechanical_items)} 个")
            for item in mechanical_items[:8]:
                print(f"    - {item['endpoint']}")
            if len(mechanical_items) > 8:
                print(f"    ... 其余 {len(mechanical_items) - 8} 个省略")

    if strict and gap:
        print(f"\n[coverage] --strict-coverage：存在 {len(gap)} 个两层都未覆盖的端点，按失败处理。")
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="导出 / 校验后端 API 端点清单，并检查测试覆盖率缺口。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  .venv/bin/python scripts/list_api_endpoints.py\n"
            "  .venv/bin/python scripts/list_api_endpoints.py --check\n"
            "  .venv/bin/python scripts/list_api_endpoints.py --check-coverage --show 80\n"
        ),
    )
    parser.add_argument("--json", dest="json_path", default=str(DEFAULT_JSON), help="JSON 基线输出路径")
    parser.add_argument("--md", dest="md_path", default=str(DEFAULT_MD), help="Markdown 清单输出路径")
    parser.add_argument("--check", action="store_true", help="只校验基线是否过期，不写文件")
    parser.add_argument("--check-coverage", action="store_true", help="输出未被测试引用的端点清单")
    parser.add_argument("--strict-coverage", action="store_true", help="覆盖率检查发现未覆盖时以非零码退出")
    parser.add_argument("--show", type=int, default=40, help="覆盖率检查时最多展示多少条未覆盖端点")
    parser.add_argument("--quiet", action="store_true", help="生成模式不打印明细")
    args = parser.parse_args(argv)

    json_path = Path(args.json_path).resolve()
    md_path = Path(args.md_path).resolve()

    if args.check_coverage:
        return check_coverage(json_path, strict=args.strict_coverage, show=args.show)

    if args.check:
        return check_drift(json_path)

    try:
        payload = collect_endpoints()
    except ModuleNotFoundError as exc:  # pragma: no cover - 环境问题提示
        print(
            f"[error] 生成端点清单需要后端依赖（{exc.name}）。请用项目 venv，"
            "例如 `.venv/bin/python scripts/list_api_endpoints.py`。",
            file=sys.stderr,
        )
        return 1

    write_baseline(payload, json_path, md_path)
    totals = payload["totals"]
    if not args.quiet:
        print(
            f"[ok] 已生成端点清单：{totals['endpoints']} 个端点 / {totals['paths']} 条路径 / "
            f"{totals['tags']} 个 tag"
        )
        print(f"     JSON: {json_path}")
        print(f"     Markdown: {md_path}")
        print(f"     鉴权分布: {payload['auth_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
