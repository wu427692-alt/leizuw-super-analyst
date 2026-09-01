# -*- coding: utf-8 -*-
"""Deterministic chart contracts for industry/company research reports.

The research writer must never invent a chart.  This module only transforms
the frozen research snapshot into unit-consistent chart specifications and a
portable Markdown data appendix.  The web reader renders the specifications as
SVG, while the Markdown appendix keeps every visual auditable offline.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from typing import Any, Dict, List, Optional, Sequence


class IndustryResearchVisualizationService:
    """Build report figures without model-generated numbers."""

    _REPLACED_FIGURES = {
        "concept_structure",
        "industry_constituent_attribution",
        "financial_trend",
        "valuation_multiples",
        "chip_and_turnover",
    }

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed != parsed or parsed in (float("inf"), float("-inf")):
            return None
        return parsed

    @classmethod
    def _has_values(
        cls,
        rows: Sequence[Dict[str, Any]],
        *keys: str,
        minimum: int = 2,
    ) -> bool:
        return sum(
            1 for row in rows
            if any(cls._number(row.get(key)) is not None for key in keys)
        ) >= minimum

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        raw = str(value or "").strip()
        if not raw:
            return None
        compact = re.sub(r"\D", "", raw[:10])
        try:
            if len(compact) >= 8:
                return datetime.strptime(compact[:8], "%Y%m%d").date()
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            return None

    @classmethod
    def enhance(
        cls,
        snapshot: Dict[str, Any],
        base_figures: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Add analytical context and replace mixed-unit legacy figures."""
        evidence = [item for item in snapshot.get("evidence") or [] if isinstance(item, dict)]
        coverage = [item for item in snapshot.get("coverage") or [] if isinstance(item, dict)]
        financial = [item for item in snapshot.get("financial_series") or [] if isinstance(item, dict)]
        market = [item for item in snapshot.get("market_series") or [] if isinstance(item, dict)]
        valuation = [item for item in snapshot.get("valuation_series") or [] if isinstance(item, dict)]
        concept = snapshot.get("concept_context") if isinstance(snapshot.get("concept_context"), dict) else {}
        concept_items = [item for item in concept.get("items") or [] if isinstance(item, dict)]
        concept_stocks = [item for item in concept.get("constituents") or [] if isinstance(item, dict)]
        peer_matrix = snapshot.get("industry_peer_matrix") if isinstance(snapshot.get("industry_peer_matrix"), dict) else {}

        metadata = cls._base_metadata(snapshot, evidence, coverage, market)
        figures: List[Dict[str, Any]] = []
        for raw in base_figures:
            if raw.get("id") in cls._REPLACED_FIGURES:
                continue
            figure = dict(raw)
            data = [item for item in figure.get("data") or [] if isinstance(item, dict)]
            if figure.get("id") == "source_mix":
                data = [item for item in data if int(item.get("value") or 0) > 0]
            if figure.get("id") in {"market_price_trend", "market_volume_trend", "industry_market_history"} and len(data) < 8:
                continue
            if figure.get("id") == "evidence_timeline" and len(data) < 2:
                continue
            if not data:
                continue
            figure["data"] = data
            figure.update(metadata.get(str(figure.get("id") or ""), {}))
            figures.append(figure)

        figures.append(cls._freshness_figure(snapshot, evidence))
        figures.extend(cls._concept_figures(concept_items, concept_stocks))
        figures.extend(cls._peer_figures(peer_matrix))
        figures.extend(cls._financial_figures(financial))
        figures.extend(cls._valuation_figures(valuation))
        return [figure for figure in figures if figure and figure.get("data")]

    @classmethod
    def _base_metadata(
        cls,
        snapshot: Dict[str, Any],
        evidence: Sequence[Dict[str, Any]],
        coverage: Sequence[Dict[str, Any]],
        market: Sequence[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        coverage_rows = [
            (str(item.get("name") or item.get("key") or "未知来源"), int(item.get("count") or 0))
            for item in coverage if int(item.get("count") or 0) > 0
        ]
        largest_source = max(coverage_rows, key=lambda item: item[1]) if coverage_rows else ("暂无", 0)
        levels = Counter(str(item.get("evidence_level") or "unknown") for item in evidence)
        largest_level = levels.most_common(1)[0] if levels else ("unknown", 0)
        timeline = [item for item in snapshot.get("timeline") or [] if isinstance(item, dict)]
        peak = max(timeline, key=lambda item: int(item.get("count") or 0)) if timeline else {}
        dimensions = ((snapshot.get("data_quality") or {}).get("dimensions") or {}) if isinstance(snapshot.get("data_quality"), dict) else {}
        weakest = min(dimensions.items(), key=lambda item: float(item[1] or 0)) if dimensions else ("未评估", 0)
        start_close = cls._number(market[0].get("close")) if market else None
        end_close = cls._number(market[-1].get("close")) if market else None
        market_change = (
            (end_close / start_close - 1) * 100
            if start_close not in (None, 0) and end_close is not None else None
        )
        return {
            "source_mix": {
                "analytical_question": "本次报告实际由哪些数据源支撑，是否过度依赖单一渠道？",
                "insight": f"当前数量最多的是{largest_source[0]}（{largest_source[1]} 条）；数量只用于识别覆盖偏科，结论仍需按证据等级交叉验证。",
                "unit": "条",
            },
            "evidence_timeline": {
                "analytical_question": "研究材料在时间上是否集中于少数月份，是否存在陈旧样本主导？",
                "insight": f"资料峰值出现在 {peak.get('month') or '日期未知'}（{int(peak.get('count') or 0)} 条）；峰值表示信息供给集中，不代表行业景气拐点。",
                "unit": "条",
            },
            "evidence_quality": {
                "analytical_question": "事实层、观点层和待核验线索的比例是否足以支撑当前结论？",
                "insight": f"当前占比最高的证据层级为 {largest_level[0]}（{largest_level[1]} 条）；待核验线索不能单独升级为公司事实。",
                "unit": "条",
            },
            "research_quality": {
                "analytical_question": "哪一个数据质量维度最限制报告可信度？",
                "insight": f"当前最弱维度为 {weakest[0]}（{weakest[1]} 分）；应优先补数，而不是用更长的 AI 文本掩盖。",
                "unit": "分",
            },
            "industry_market_history": {
                "analytical_question": "市场对该题材的定价趋势和短期波动是否同向？",
                "insight": "累计收益与单日涨跌用于识别趋势和波动时点；价格领先或滞后基本面的关系仍需事件证据验证。",
                "unit": "%",
            },
            "company_evidence": {
                "analytical_question": "哪些公司在当前固定证据中更值得优先核验？",
                "insight": "提及密度高只说明当前资料更集中；龙头结论仍需产品、份额、客户、盈利与竞争证据共同支持。",
                "unit": "条",
            },
            "market_price_trend": {
                "analytical_question": "市场价格在研究窗口内如何变化，是否出现与基本面信息同步的拐点？",
                "insight": (
                    f"窗口首尾收盘价变化约 {market_change:+.1f}%；价格反映预期和交易结构，不能单独证明经营事实。"
                    if market_change is not None else
                    "价格序列用于定位事件前后市场反应，不单独证明经营事实。"
                ),
                "unit": "元",
            },
            "market_volume_trend": {
                "analytical_question": "价格变化是否伴随成交量放大，市场关注度是否显著变化？",
                "insight": "放量只表示交易参与度上升，既可能来自增量买入，也可能来自分歧和抛压；需与价格方向及事件时点联读。",
                "unit": "成交量（源行情单位）",
            },
        }

    @classmethod
    def _freshness_figure(
        cls,
        snapshot: Dict[str, Any],
        evidence: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        cutoff = cls._parse_date(snapshot.get("collected_at")) or date.today()
        freshness = Counter({
            "7日内": 0, "8-30日": 0, "31-90日": 0,
            "91-365日": 0, "1年以上": 0, "日期未知": 0,
        })
        for item in evidence:
            item_date = cls._parse_date(item.get("date"))
            if item_date is None:
                freshness["日期未知"] += 1
                continue
            age = max(0, (cutoff - item_date).days)
            bucket = (
                "7日内" if age <= 7 else "8-30日" if age <= 30 else
                "31-90日" if age <= 90 else "91-365日" if age <= 365 else "1年以上"
            )
            freshness[bucket] += 1
        recent = freshness["7日内"] + freshness["8-30日"]
        return {
            "id": "evidence_freshness", "type": "bar", "title": "证据新鲜度结构",
            "subtitle": f"以研究截止日 {cutoff.isoformat()} 计算资料年龄；不按页面访问时间滚动。",
            "analytical_question": "报告使用的证据有多少来自最近 30 日，旧资料是否需要重新验证？",
            "insight": f"最近 30 日证据 {recent} 条；超过一年或日期未知的材料只适合解释历史，不应直接证明当前状态。",
            "data": [{"name": key, "value": value} for key, value in freshness.items() if value > 0],
            "x_key": "name", "y_keys": ["value"], "unit": "条",
            "source": "本次研究固定证据快照 · 发布日期",
        }

    @classmethod
    def _concept_figures(
        cls,
        items: Sequence[Dict[str, Any]],
        stocks: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        figures: List[Dict[str, Any]] = []
        rows = [{
            "name": item.get("canonical_name") or item.get("name"),
            "heat": cls._number(item.get("heat_score")) or 0,
            "constituents": cls._number(item.get("constituent_count")) or 0,
        } for item in items[:12]]
        if cls._has_values(rows, "heat"):
            top = max(rows, key=lambda item: item["heat"])
            figures.append({
                "id": "concept_heat", "type": "bar", "title": "相关题材市场共识热度",
                "subtitle": "多源题材共识热度单独成图，不与成分股数量共用纵轴；热度不是投资价值。",
                "analytical_question": "研究对象当前与哪些题材形成更强的跨来源市场共识？",
                "insight": f"当前热度最高的相关题材是{top['name']}（{top['heat']:g}）；它反映市场标签集中度，不等于基本面贡献。",
                "data": rows, "x_key": "name", "y_keys": ["heat"], "unit": "热度分",
                "source": "多源概念题材库",
            })
        if cls._has_values(rows, "constituents"):
            figures.append({
                "id": "concept_constituent_scale", "type": "bar", "title": "相关题材成分覆盖",
                "subtitle": "展示各题材纳入的成分股数量，避免与热度分数混为同一量纲。",
                "analytical_question": "相关题材是集中型小赛道，还是覆盖大量公司的宽口径板块？",
                "insight": "成分数量越多，题材口径通常越宽；公司数量不能直接证明单家公司受益程度。",
                "data": rows, "x_key": "name", "y_keys": ["constituents"], "unit": "家公司",
                "source": "多源概念题材库",
            })

        attributed = [{
            "name": item.get("name") or item.get("ts_code"),
            "weight": cls._number(item.get("weight_score")),
            "beta": cls._number(item.get("beta")),
            "alpha": cls._number(item.get("alpha_annualized")),
        } for item in stocks[:16]]
        if cls._has_values(attributed, "weight"):
            figures.append({
                "id": "industry_constituent_weight", "type": "bar", "title": "题材成分共识权重",
                "subtitle": "权重来自多源成分共识；不与 Beta 或 Alpha 共用纵轴。",
                "analytical_question": "哪些公司与该题材的市场共识关联最强？",
                "insight": "权重用于筛选优先研究对象，不代表利润暴露、收入占比或未来收益。",
                "data": attributed, "x_key": "name", "y_keys": ["weight"], "unit": "权重分",
                "source": "多供应商题材成分共识库",
            })
        beta_alpha = [row for row in attributed if row["beta"] is not None and row["alpha"] is not None]
        if len(beta_alpha) >= 4:
            figures.append({
                "id": "industry_beta_alpha", "type": "scatter", "title": "题材 Beta 与个股 Alpha 归因",
                "subtitle": "横轴为历史 Beta，纵轴为历史年化 Alpha；至少四个同口径样本才展示。",
                "analytical_question": "个股历史表现主要来自板块联动，还是存在板块之外的独立收益？",
                "insight": "右上方公司同时具有较高板块敏感度和正 Alpha；该图是历史归因，不代表未来因果或可复制收益。",
                "data": beta_alpha, "x_key": "beta", "y_keys": ["alpha"], "label_key": "name",
                "unit": "Beta / 年化 Alpha(%)", "source": "多供应商题材成分与本地行情归因库",
            })
        return figures

    @classmethod
    def _peer_figures(cls, matrix: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Compare peers only on a common disclosed reporting period."""
        common_period = str(matrix.get("common_period") or "").strip()
        companies = [item for item in matrix.get("companies") or [] if isinstance(item, dict)]
        if not common_period or len(companies) < 3:
            return []
        rows: List[Dict[str, Any]] = []
        for company in companies[:5]:
            fact = company.get("common_period_fact") if isinstance(company.get("common_period_fact"), dict) else None
            if not fact or str(fact.get("period") or "") != common_period:
                continue
            rows.append({
                "name": company.get("name") or company.get("symbol"),
                "symbol": company.get("symbol"),
                "revenue": cls._number(fact.get("revenue")),
                "net_profit": cls._number(fact.get("net_profit")),
                "operating_cashflow": cls._number(fact.get("operating_cashflow")),
                "roe": cls._number(fact.get("roe")),
                "gross_margin": cls._number(fact.get("gross_margin")),
                "revenue_yoy": cls._number(fact.get("revenue_yoy")),
                "net_profit_yoy": cls._number(fact.get("net_profit_yoy")),
            })
        if len(rows) < 3:
            return []
        source = "Tushare 代表企业结构化披露数据 · 同报告期确定性口径"
        figures: List[Dict[str, Any]] = []
        if cls._has_values(rows, "revenue", minimum=3):
            figures.append({
                "id": "industry_peer_revenue", "type": "bar", "title": "代表企业营业收入规模",
                "subtitle": f"仅比较共同报告期 {common_period}；金额保持源接口元口径，未由 AI 估算。",
                "analytical_question": "在统一报告期内，代表企业的收入规模差异有多大？",
                "insight": "收入规模用于识别经营体量，不等于行业份额；份额结论仍需行业总量和分部收入证据。",
                "data": rows, "x_key": "name", "y_keys": ["revenue"], "unit": "元", "source": source,
            })
        if (
            cls._has_values(rows, "net_profit", minimum=3)
            and cls._has_values(rows, "operating_cashflow", minimum=3)
        ):
            figures.append({
                "id": "industry_peer_profit_cash", "type": "bar", "title": "代表企业利润与经营现金流",
                "subtitle": f"共同报告期 {common_period}；利润与经营现金流同为元口径并列。",
                "analytical_question": "同业利润规模是否同步转化为经营现金流？",
                "insight": "利润与经营现金流背离需要进一步核验应收、存货、合同负债及非经常项目，单期差异不自动等于异常。",
                "data": rows, "x_key": "name", "y_keys": ["net_profit", "operating_cashflow"],
                "unit": "元", "source": source,
            })
        if (
            cls._has_values(rows, "roe", minimum=3)
            and cls._has_values(rows, "gross_margin", minimum=3)
        ):
            figures.append({
                "id": "industry_peer_profitability", "type": "bar", "title": "代表企业盈利能力对照",
                "subtitle": f"共同报告期 {common_period}；ROE 与毛利率均按源接口百分比口径。",
                "analytical_question": "代表企业的毛利率和资本回报差异是否支持竞争优势判断？",
                "insight": "高毛利或高 ROE 只描述结果；持续性仍需产品结构、客户、研发、周转和资本投入证据解释。",
                "data": rows, "x_key": "name", "y_keys": ["roe", "gross_margin"],
                "unit": "%", "source": source,
            })
        return figures

    @classmethod
    def _financial_figures(cls, financial: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = list(reversed(financial[:12]))
        figures: List[Dict[str, Any]] = []
        if cls._has_values(rows, "revenue"):
            figures.append({
                "id": "revenue_scale", "type": "bar", "title": "营业收入报告期规模",
                "subtitle": "财报累计口径按报告期并列，未将累计季度值冒充单季值。",
                "analytical_question": "公司收入规模在已披露报告期如何变化？",
                "insight": "该图用于观察已披露累计收入，不直接代表单季增速；单季判断必须结合同比字段或差分口径。",
                "data": rows, "x_key": "period", "y_keys": ["revenue"], "unit": "源报表金额单位",
                "source": "Tushare income · 最新修订口径",
            })
        if cls._has_values(rows, "net_profit", "operating_cashflow"):
            figures.append({
                "id": "profit_cash_bridge", "type": "bar", "title": "归母净利润与经营现金流",
                "subtitle": "同一报告期、同一源报表金额单位并列，用于观察利润现金含量。",
                "analytical_question": "利润增长是否同步转化为经营现金流？",
                "insight": "净利润与经营现金流长期背离需要核验应收、存货、预付款和非经常性项目；单期背离不自动等于财务异常。",
                "data": rows, "x_key": "period", "y_keys": ["net_profit", "operating_cashflow"],
                "unit": "源报表金额单位", "source": "Tushare income + cashflow · 最新修订口径",
            })
        if cls._has_values(rows, "revenue_yoy", "net_profit_yoy"):
            figures.append({
                "id": "financial_growth", "type": "bar", "title": "累计营收与归母净利润同比",
                "subtitle": "使用财务指标接口 or_yoy / netprofit_yoy 的累计同比口径，不与 q_* 单季度同比混用，也不由模型自行差分计算。",
                "analytical_question": "收入增长与利润增长是否同步，经营杠杆向上还是向下？",
                "insight": "利润增速显著偏离收入增速时，应结合毛利率、费用、减值和非经常性损益解释，而不是直接外推。",
                "data": rows, "x_key": "period", "y_keys": ["revenue_yoy", "net_profit_yoy"],
                "unit": "%", "source": "Tushare fina_indicator · 最新修订口径",
            })
        if cls._has_values(rows, "roe", "gross_margin"):
            figures.append({
                "id": "profitability_quality", "type": "bar", "title": "ROE 与毛利率",
                "subtitle": "均为百分比口径；不同报告期可能为累计口径，按原始接口披露展示。",
                "analytical_question": "盈利能力变化来自毛利率还是资本使用效率？",
                "insight": "ROE 与毛利率同向走弱通常需要检查产品结构、价格和资产周转；这里只描述事实，不自动给出因果。",
                "data": rows, "x_key": "period", "y_keys": ["roe", "gross_margin"],
                "unit": "%", "source": "Tushare fina_indicator · 最新修订口径",
            })
        return figures

    @classmethod
    def _valuation_figures(cls, valuation: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(valuation) < 8:
            return []
        rows = list(reversed(valuation[:260]))
        figures: List[Dict[str, Any]] = []
        specs = [
            (
                "valuation_pe", "PE(TTM) 变化", ["pe_ttm"], "倍",
                "PE 单独成图，负值与缺失保持原样；不与数量级更小的 PB/PS 共轴。",
                "市场对公司滚动盈利的定价倍数处于怎样的历史路径？",
                "估值倍数变化可能来自价格和盈利两端，不能把 PE 上升直接解释为基本面改善。",
                "Tushare daily_basic",
            ),
            (
                "valuation_pb_ps", "PB 与 PS(TTM) 变化", ["pb", "ps_ttm"], "倍",
                "同为倍数口径并列展示；仍需结合盈利模式和资产质量解释。",
                "资产和收入维度的估值定价是否出现同向重估？",
                "PB 与 PS 的变化只描述定价，需结合 ROE、毛利率和增长持续性判断重估是否可维持。",
                "Tushare daily_basic",
            ),
            (
                "chip_cost", "筹码加权成本", ["chip_cost"], "元",
                "价格口径单独展示，不与获利比例或换手率共轴。",
                "市场持仓成本中枢如何迁移，当前价格压力区间在哪里？",
                "筹码成本是交易结构统计，不是基本面价值中枢；应与成交量和价格趋势共同观察。",
                "Tushare cyq_perf",
            ),
            (
                "trading_structure", "筹码获利比例与换手率", ["winner_rate", "turnover_rate"], "%",
                "两项均为比例口径；仅按同一交易日并列展示，不替代基本面判断。",
                "获利比例和换手率在同一交易日分别处于什么水平？",
                "获利比例与换手率仅按日期并列展示；在没有预先定义并验证阈值前，不据此定义拥挤、分歧或交易条件。",
                "Tushare cyq_perf + daily_basic",
            ),
        ]
        for figure_id, title, keys, unit, subtitle, question, insight, source in specs:
            if not cls._has_values(rows, *keys, minimum=8):
                continue
            figures.append({
                "id": figure_id, "type": "line", "title": title, "subtitle": subtitle,
                "analytical_question": question, "insight": insight,
                "data": rows, "x_key": "date", "y_keys": keys, "unit": unit, "source": source,
            })
        return figures

    @staticmethod
    def markdown_appendix(figures: Sequence[Dict[str, Any]]) -> str:
        """Serialize visual evidence for the browser-independent download."""
        if not figures:
            return ""

        def markdown(value: Any) -> str:
            if value is None:
                return "—"
            output = f"{value:.6g}" if isinstance(value, float) else str(value)
            return output.replace("|", "\\|").replace("\n", " ").strip() or "—"

        lines = [
            "# 附录：图表数据、口径与阅读说明",
            "",
            "网页报告与打印/PDF版本会渲染真实图表；本附录保存同一固定快照中的图表问题、阅读结论、来源、单位和抽样数据，便于离线复核。图表只描述数据，不自动构成投资结论。",
        ]
        for index, figure in enumerate(figures, 1):
            data = [item for item in figure.get("data") or [] if isinstance(item, dict)]
            x_key = str(figure.get("x_key") or "name")
            y_keys = [str(item) for item in figure.get("y_keys") or [] if str(item)]
            columns = list(dict.fromkeys([x_key, *y_keys]))
            label_key = str(figure.get("label_key") or "")
            if label_key and label_key not in columns:
                columns.append(label_key)
            if len(data) > 30:
                indexes = sorted({round(step * (len(data) - 1) / 29) for step in range(30)})
                rendered_rows = [data[item] for item in indexes]
                sampling_note = f"为控制下载体积，从 {len(data)} 行等距保留 {len(rendered_rows)} 行；完整数据保存在证据 JSON。"
            else:
                rendered_rows = data
                sampling_note = ""
            lines.extend([
                "", f"## 图 {index}：{figure.get('title') or figure.get('id') or '未命名图表'}【{figure.get('id') or 'unknown'}】", "",
                f"- **分析问题**：{markdown(figure.get('analytical_question') or '观察该指标在当前证据快照中的结构或变化。')}",
                f"- **阅读结论**：{markdown(figure.get('insight') or figure.get('subtitle') or '仅作描述性观察。')}",
                f"- **口径/单位**：{markdown(figure.get('subtitle'))}；{markdown(figure.get('unit') or '见字段定义')}",
                f"- **来源**：{markdown(figure.get('source') or '本次研究固定证据快照')}",
            ])
            if sampling_note:
                lines.append(f"- **数据抽样**：{sampling_note}")
            if columns and rendered_rows:
                lines.extend([
                    "", "| " + " | ".join(columns) + " |",
                    "| " + " | ".join("---" for _ in columns) + " |",
                ])
                for row in rendered_rows:
                    lines.append("| " + " | ".join(markdown(row.get(column)) for column in columns) + " |")
        return "\n".join(lines).strip()
