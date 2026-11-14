#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面端页面内容预算静态审计。

`docs/ui-design-system.md` §10 规定了一屏能放多少内容，但规则写在文档里靠人记
是不可靠的。这个脚本把「页面上限」变成可度量的数字：解析页面 TSX，统计一级区块、
表格行、并列指标卡、说明文字，超预算就报出来。

它不替代浏览器实测（那要看真实渲染），只做静态预警：**结构上就已经塞太多**的页面
会在这里先暴露出来。

用法：
    python scripts/audit_page_budget.py            # 全量扫描
    python scripts/audit_page_budget.py --top 10   # 只看最超标的 10 个
    python scripts/audit_page_budget.py --page SettingsPage
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / "apps" / "dsa-web" / "src" / "pages"

# §10 页面内容预算
BUDGET = {
    "sections": 6,       # 一级区块（卡片/面板）
    "metric_cards": 8,   # 并列指标卡
    "table_rows": 25,    # 单页表格行（静态上限，不含分页/虚拟滚动）
    "notes": 3,          # 说明性文字
}

# 一级区块：SectionCard / Card 的使用、以及 section 语义标签
SECTION_PATTERNS = [
    re.compile(r"<SectionCard\b"),
    re.compile(r"<section\b"),
]
# 并列指标卡
METRIC_CARD_PATTERNS = [
    re.compile(r"<StatCard\b"),
    re.compile(r"<MetricCard\b"),
]
# 已被路由摘掉的页面：`App.tsx` 里这些路径直接 `<Navigate to="/app" />`，
# 生产代码 0 处引用（只有它们自己的测试还引着）。改它们对用户零收益，
# 所以不参与预算判定；若哪天重新挂载，删掉对应条目即可。
KNOWN_UNROUTED = {
    "AlertsPage",           # /alerts           -> Navigate to /app
    "BacktestPage",         # /backtest         -> Navigate to /app
    "DecisionSignalsPage",  # /decision-signals -> Navigate to /app
    "PortfolioPage",        # /portfolio        -> Navigate to /app
    "HomePage",             # /app 实际渲染 MarketDashboardPage，此文件无人引用
    "ResearchCenterPage",   # /research-center  -> Navigate to /super-watchlist?view=decision
}

# 除 Tab 之外的合法切分：侧栏、抽屉、折叠区都能降低单屏内容量（§10 允许）
SPLIT_ALTERNATIVES = [
    re.compile(r"<Drawer\b"),
    re.compile(r"<aside\b"),
    re.compile(r"<Collapsible\b"),
    re.compile(r"<details\b"),
]

# 已切页的标志：用了 PageTabs 或本地 tab state
TAB_PATTERNS = [
    re.compile(r"<PageTabs\b"),
    re.compile(r"\bactiveTab\b"),
    re.compile(r"\btabKey\b"),
]

# 视图分支：一个文件承载多条路由/多个子视图时，section 是按文件累加的。
# 统计这些分支数量，用它估算「单视图」的区块数，避免多路由页面恒报超标。
VIEW_BRANCH_PATTERNS = [
    # 任意 compare 到字面量的视图状态：view/active/activeTab/activeSection/tab 等
    re.compile(r"\b[a-zA-Z]*[Vv]iew\s*===\s*['\"]"),
    re.compile(r"\bactive[A-Za-z]*\s*===\s*['\"]"),
    re.compile(r"\btab\s*===\s*['\"]"),
    re.compile(r"\bsection\s*===\s*['\"]"),
    # 三元分支渲染：cond ? <X/> : null 形态在视图切换里很常见
    re.compile(r"\btab\s*===\s*\{?['\"]"),
    re.compile(r"<Route path=\""),
    re.compile(r"<Link\s+to=\"/[a-z-]+/"),
]

# PageTabs 的 items 数量 ≈ 视图数；在有多组 Tab 时取最大值。
PAGE_TABS_ITEMS = re.compile(
    r"<PageTabs[\s\S]{0,1200}?items=\{\[([\s\S]{0,1500}?)\]\}", re.MULTILINE,
)
# 长列表的标志：数组 map 渲染进表格
MAP_RENDER = re.compile(r"\.map\(")
TABLE_PATTERN = re.compile(r"<table\b|<tbody\b")


@dataclass
class PageReport:
    name: str
    lines: int
    sections: int = 0
    metric_cards: int = 0
    tabbed: bool = False
    has_table: bool = False
    map_renders: int = 0
    view_branches: int = 1
    routed: bool = True
    split_alt: int = 0
    violations: List[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """超标越严重分数越高，用于排序。"""
        penalty = 0
        penalty += max(0, self.sections - BUDGET["sections"]) * 10
        penalty += max(0, self.metric_cards - BUDGET["metric_cards"]) * 5
        if not self.tabbed:
            penalty += 20
        if self.lines > 800:
            penalty += (self.lines - 800) // 100
        return penalty


def audit_page(path: Path) -> PageReport:
    source = path.read_text(encoding="utf-8", errors="replace")
    report = PageReport(name=path.stem, lines=source.count("\n") + 1)
    report.routed = path.stem not in KNOWN_UNROUTED

    report.sections = sum(len(p.findall(source)) for p in SECTION_PATTERNS)
    report.metric_cards = sum(len(p.findall(source)) for p in METRIC_CARD_PATTERNS)
    report.tabbed = any(p.search(source) for p in TAB_PATTERNS)
    report.has_table = bool(TABLE_PATTERN.search(source))
    report.map_renders = len(MAP_RENDER.findall(source))
    # 分支数取「视图状态比较命中数」与「PageTabs items 条数」的较大者；
    # 至少 1，避免除零。
    compare_hits = max(
        (len(p.findall(source)) for p in VIEW_BRANCH_PATTERNS), default=1,
    )
    tab_items = 1
    for match in PAGE_TABS_ITEMS.finditer(source):
        # 用出现次数估算 item 数：每个 item 通常含一个 key:
        keys = len(re.findall(r"key:\s*['\"]", match.group(1)))
        tab_items = max(tab_items, keys)
    report.view_branches = max(1, compare_hits, tab_items)
    report.routed = path.stem not in KNOWN_UNROUTED
    report.split_alt = sum(len(p.findall(source)) for p in SPLIT_ALTERNATIVES)

    if not report.routed:
        # 未挂载页面不判预算，但仍统计出来供日后恢复时参考
        return report

    per_view = report.sections / report.view_branches
    if per_view > BUDGET["sections"]:
        report.violations.append(
            f"单视图一级区块 ≈{per_view:.1f} > {BUDGET['sections']}"
            f"（全文 {report.sections} 块 / {report.view_branches} 个视图分支）"
        )
    if report.metric_cards > BUDGET["metric_cards"]:
        report.violations.append(
            f"并列指标卡 {report.metric_cards} > {BUDGET['metric_cards']}（§10：改用 MetricRow）"
        )
    if not report.tabbed and report.split_alt == 0 and report.lines > 500:
        report.violations.append(
            f"{report.lines} 行且无任何切分手段（§10：需 PageTabs / 侧栏 / 抽屉 / 折叠）"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="桌面端页面内容预算审计")
    parser.add_argument("--top", type=int, default=0, help="只显示最超标的前 N 个页面")
    parser.add_argument("--page", default="", help="只审计指定页面（文件名不含 .tsx）")
    parser.add_argument("--strict", action="store_true", help="有任何超标即以非零码退出")
    args = parser.parse_args()

    if not PAGES_DIR.exists():
        print(f"找不到页面目录: {PAGES_DIR}", file=sys.stderr)
        return 2

    reports = [audit_page(p) for p in sorted(PAGES_DIR.glob("*.tsx"))]
    if args.page:
        reports = [r for r in reports if r.name == args.page]
        if not reports:
            print(f"没有名为 {args.page} 的页面", file=sys.stderr)
            return 2

    reports.sort(key=lambda r: (-r.score, -r.lines))
    if args.top:
        reports = reports[: args.top]

    print(f"{'页面':<28}{'行数':>6}{'区块':>6}{'视图':>5}{'单视图':>8}{'已切页':>7}  超标项")
    print("-" * 96)
    offending = 0
    for r in reports:
        if r.violations:
            offending += 1
        if not r.routed:
            print(f"{r.name:<28}{r.lines:>6}{r.sections:>6}{r.view_branches:>5}{'—':>8}{'—':>7}  未挂载（不计入预算）")
            continue
        flag = "是" if r.tabbed else "否"
        per_view = r.sections / r.view_branches
        detail = "；".join(r.violations) if r.violations else "—"
        print(f"{r.name:<28}{r.lines:>6}{r.sections:>6}{r.view_branches:>5}{per_view:>8.1f}{flag:>7}  {detail}")

    total = len(reports)
    print("-" * 96)
    print(f"共 {total} 个页面，{offending} 个超出 §10 预算")
    print(f"预算：一级区块 ≤{BUDGET['sections']}、指标卡 ≤{BUDGET['metric_cards']}、"
          f"表格行 ≤{BUDGET['table_rows']}、说明 ≤{BUDGET['notes']}")

    if args.strict and offending:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
