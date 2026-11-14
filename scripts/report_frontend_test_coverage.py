#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端页面测试引用缺口报告（不阻断）。

发布前门禁的「前端使用」层已经有全量渲染冒烟，但页面级测试仍然覆盖不均：
有些页面有专门的测试文件，有些页面除了全量冒烟之外没有任何针对性测试。
这个脚本不判断测试质量，只回答一个客观问题：**哪些页面文件没有任何测试文件引用**。

判定口径（尽量保守，避免假阴性）：
- 页面清单：`apps/dsa-web/src/pages/*.tsx`（不含 `__tests__/` 与 `fixtures/`）
- 测试文件：`apps/dsa-web/src/**/*.test.ts(x)`、`apps/dsa-web/src/**/__tests__/**`、
  `apps/dsa-web/tests/**`、`apps/dsa-web/e2e/**`
- 「被引用」：测试文件里的 `from '<...>/PageName'`、`import('<...>/PageName')` 或
  `vi.mock('<...>/PageName')`，即静态 import / 动态 import / 模块桩三种写法都算。
- 被 App.tsx 挂载的页面，如果只被 `allPagesRender.test.tsx`（自动派生路由）冒烟渲染、
  没有专门的测试文件引用，会单独列为「仅冒烟覆盖」，避免把「有冒烟」误报成「零覆盖」。

用法：
    python scripts/report_frontend_test_coverage.py            # 只列未覆盖页面
    python scripts/report_frontend_test_coverage.py --all      # 列全部页面与覆盖状态
    python scripts/report_frontend_test_coverage.py --json     # 机器可读
    python scripts/report_frontend_test_coverage.py --fail-on-uncovered  # 有缺口时非零退出
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "dsa-web"
PAGES_DIR = WEB / "src" / "pages"
APP_SOURCE = WEB / "src" / "App.tsx"

# 页面导出/引用只有三种写法：静态 import、动态 import、vi.mock 模块桩。
SPECIFIER_PATTERNS = [
    re.compile(r"""\bfrom\s+['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bvi\.mock\s*\(\s*['"]([^'"]+)['"]"""),
]

TEST_FILE_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

# 这些目录下的测试也算：apps/dsa-web/tests 是 node 侧治理测试，e2e 是 Playwright。
EXTRA_TEST_DIRS = [WEB / "tests", WEB / "e2e"]


ALL_PAGES_SMOKE_TEST = "src/pages/__tests__/allPagesRender.test.tsx"


@dataclass
class PageCoverage:
    name: str
    path: Path
    covered_by: List[str]
    routed: bool

    @property
    def dedicated(self) -> bool:
        """有专门引用它的测试文件（不是只有全量渲染冒烟）。"""
        return bool(self.covered_by)

    @property
    def smoke_covered(self) -> bool:
        """被 App.tsx 挂载，因此会被自动派生路由的全量渲染冒烟渲染到。"""
        return self.routed and (WEB / ALL_PAGES_SMOKE_TEST).exists()

    @property
    def refers_anywhere(self) -> bool:
        return self.dedicated or self.smoke_covered


def iter_test_files() -> Iterable[Path]:
    seen: Set[Path] = set()
    for pattern in ("*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx"):
        for path in (WEB / "src").rglob(pattern):
            if path not in seen:
                seen.add(path)
                yield path
    for directory in EXTRA_TEST_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path in seen or not path.is_file():
                continue
            if path.name.endswith(TEST_FILE_SUFFIXES):
                seen.add(path)
                yield path


def referenced_page_names(test_file: Path) -> Set[str]:
    """从测试文件里取出它引用到的页面名（取 import 说明符的最后一段）。"""
    try:
        source = test_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    names: Set[str] = set()
    for pattern in SPECIFIER_PATTERNS:
        for specifier in pattern.findall(source):
            # 去掉 query/hash 与扩展名，再取路径最后一段。
            cleaned = specifier.split("?")[0].split("#")[0]
            stem = Path(cleaned).name
            for suffix in (".tsx", ".ts", ".jsx", ".js"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            if stem:
                names.add(stem)
    return names


def routed_page_names() -> Set[str]:
    """App.tsx 里通过 `lazyRoute(() => import('./pages/X'), ...)` 实际挂载的页面。"""
    if not APP_SOURCE.exists():
        return set()
    source = APP_SOURCE.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"import\('\./pages/([A-Za-z0-9_]+)'\)", source))


def collect() -> List[PageCoverage]:
    pages = [
        path for path in sorted(PAGES_DIR.glob("*.tsx"))
        if path.is_file() and "__tests__" not in path.parts
    ]
    routed = routed_page_names()
    coverage_by_page: Dict[str, List[str]] = {page.stem: [] for page in pages}
    for test_file in iter_test_files():
        for name in referenced_page_names(test_file):
            if name in coverage_by_page:
                try:
                    label = str(test_file.relative_to(WEB))
                except ValueError:
                    label = str(test_file)
                coverage_by_page[name].append(label)
    return [
        PageCoverage(
            name=page.stem,
            path=page,
            covered_by=sorted(set(coverage_by_page[page.stem])),
            routed=page.stem in routed,
        )
        for page in pages
    ]


def render_text(reports: List[PageCoverage], show_all: bool) -> str:
    total = len(reports)
    dedicated = [item for item in reports if item.dedicated]
    smoke_only = [item for item in reports if item.smoke_covered and not item.dedicated]
    uncovered = [item for item in reports if not item.refers_anywhere]
    lines: List[str] = []
    lines.append("前端页面测试引用缺口（apps/dsa-web/src/pages/*.tsx）")
    lines.append(
        f"页面总数 {total}；有专门测试引用 {len(dedicated)}；"
        f"仅全量渲染冒烟覆盖 {len(smoke_only)}；无任何测试引用 {len(uncovered)}"
    )
    if not uncovered:
        lines.append("")
        lines.append("所有页面都至少被专门测试或全量渲染冒烟覆盖（不代表测试充分，只代表不是零覆盖）。")
        return "\n".join(lines)

    lines.append("")
    lines.append("未覆盖页面：")
    for item in uncovered:
        lines.append(f"- {item.path.name}")

    if smoke_only:
        lines.append("")
        lines.append("仅由全量渲染冒烟覆盖（能挂载，但没有针对性测试）：")
        for item in smoke_only:
            lines.append(f"- {item.path.name}")

    if show_all:
        lines.append("")
        lines.append("全部页面：")
        for item in reports:
            if item.dedicated:
                lines.append(f"- [x] {item.path.name}  ← {', '.join(item.covered_by)}")
            elif item.smoke_covered:
                lines.append(f"- [~] {item.path.name}  ← 全量渲染冒烟（无专门测试）")
            else:
                lines.append(f"- [ ] {item.path.name}  [未挂载]")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="前端页面测试引用缺口报告")
    parser.add_argument("--all", action="store_true", help="列出全部页面与覆盖状态")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--fail-on-uncovered", action="store_true", help="有未覆盖页面时非零退出")
    args = parser.parse_args()

    if not PAGES_DIR.exists():
        print(f"找不到页面目录: {PAGES_DIR}", file=sys.stderr)
        return 2

    reports = collect()
    if args.json:
        print(json.dumps(
            {
                "total": len(reports),
                "dedicated": sum(1 for item in reports if item.dedicated),
                "smokeOnly": sum(1 for item in reports if item.smoke_covered and not item.dedicated),
                "uncovered": [
                    {"page": item.name, "file": str(item.path.relative_to(WEB)), "routed": item.routed}
                    for item in reports if not item.refers_anywhere
                ],
                "pages": [
                    {
                        "page": item.name,
                        "file": str(item.path.relative_to(WEB)),
                        "routed": item.routed,
                        "smokeCovered": item.smoke_covered,
                        "tests": item.covered_by,
                    }
                    for item in reports
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(render_text(reports, args.all))

    if args.fail_on_uncovered and any(not item.refers_anywhere for item in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
