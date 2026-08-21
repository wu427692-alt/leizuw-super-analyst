# -*- coding: utf-8 -*-
"""Evidence-first research workspace assembled from the platform's shared stores."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from src.services.investment_monitor_service import InvestmentMonitorService


FUNCTION_CATALOG = [
    {"name": "市场总览", "route": "/app", "purpose": "判断市场环境与自选股当日状态", "data": ["实时/分钟行情", "Tushare 指数与市场广度", "本地行情库"], "output": "市场温度、主要指数、涨跌分布与自选股快照"},
    {"name": "研究决策台", "route": "/research-center", "purpose": "把事实、冲突和缺口压缩成可核验的研究任务", "data": ["全部共享行情", "统一情报事件库", "数据源健康状态"], "output": "研究就绪度、证据链、失效条件与下一步核验"},
    {"name": "问股", "route": "/chat", "purpose": "围绕具体问题调用本地事实与外部接口", "data": ["个股共享上下文", "Tushare", "公告/研报/小作文"], "output": "带来源的回答与后续研究问题"},
    {"name": "小作文雷达", "route": "/essay-radar", "purpose": "发现非结构化语料中的主题、个股和预期变化", "data": ["知识星球 MCP 增量库", "AI 结构化结果", "行情验证"], "output": "主题迁移、提及趋势、原文证据与日报"},
    {"name": "量化回测与数据利用", "route": "/essay-quant", "purpose": "检验信息事件与后续收益的统计关系", "data": ["小作文事件", "行情库", "机构/主题标签"], "output": "样本定义、收益曲线、归因、稳健性与可复现任务"},
    {"name": "投资情报台", "route": "/investment-monitor", "purpose": "按渠道观察新增事实并核验原文", "data": ["公告", "研报", "新闻", "企业事实", "股评", "知识星球"], "output": "全渠道时间线、龙虎榜与数据源 BI"},
    {"name": "自选股超级看板", "route": "/super-watchlist", "purpose": "持续维护每只关注股票的全渠道档案", "data": ["实时行情", "财务估值", "资金筹码", "公告研报", "另类数据"], "output": "单股全景、证据时间线与一致预期"},
    {"name": "数据一站式获取", "route": "/data-acquisition", "purpose": "按自然语言需求分别调用渠道并打包", "data": ["Tushare", "巨潮", "知识星球", "天眼查", "本地库"], "output": "可下载的数据文件、附件与调用清单"},
    {"name": "选股", "route": "/screening", "purpose": "根据可解释条件生成候选池", "data": ["行情", "财务", "因子", "事件"], "output": "筛选结果、命中条件与候选研究清单"},
    {"name": "持仓", "route": "/portfolio", "purpose": "从组合层面观察暴露和证据变化", "data": ["用户持仓", "实时行情", "个股共享上下文"], "output": "组合快照、风险暴露与关注事项"},
    {"name": "AI 建议", "route": "/decision-signals", "purpose": "保存有期限、可反馈的判断记录", "data": ["分析报告", "问股结果", "告警", "市场复盘"], "output": "建议时间线、状态、结果与反馈统计"},
    {"name": "告警", "route": "/alerts", "purpose": "持续监控明确的触发条件", "data": ["行情", "情报事件", "用户规则"], "output": "触发记录、通知尝试与规则状态"},
]


ARCHITECTURE = [
    {"layer": "01 采集", "logic": "各渠道独立轮询；失败隔离；按源保留同步时间、耗时与错误。"},
    {"layer": "02 归一", "logic": "行情进入共享行情库，文本事实进入 monitoring_events；股票代码、时间和来源统一。"},
    {"layer": "03 证据", "logic": "区分事实与待核验观点，保存原文、附件或来源链接，避免摘要替代证据。"},
    {"layer": "04 研究", "logic": "按自选股合并市场、公司、机构与另类数据，计算覆盖、新鲜度和矛盾。"},
    {"layer": "05 验证", "logic": "用行情回测、财报、公告和后续事件验证观点，并记录失效条件。"},
    {"layer": "06 用户", "logic": "账号隔离自选股、问股、持仓和任务；前台只读研究，后台管理密钥与同步。"},
]

REFLECTION_BACKLOG = [
    {"gap": "实体与主题归一仍会误配", "impact": "公司简称、产业链别名和相近主题可能造成漏召回或错关联。", "upgrade": "建立带版本的公司/产品/产业链实体图谱，并让每次自动合并保留人工可回滚记录。"},
    {"gap": "事件与涨跌仍主要是相关性", "impact": "市场同涨、行业因子和幸存者偏差可能被误认为小作文或研报有效。", "upgrade": "加入行业中性异常收益、匹配对照组、样本外检验和多重检验校正。"},
    {"gap": "部分公开数据源稳定性受上游限制", "impact": "网页结构、限流和无交易日会造成安静期，不能把空数据等同于没有事件。", "upgrade": "为关键源增加官方/付费备源、字段级 SLA、断点回补和数据差异对账。"},
    {"gap": "AI 结构化结果仍可能遗漏或过度概括", "impact": "利润区间、预测期限和主体关系一旦抽错，会扭曲一致预期。", "upgrade": "强制字段级原文引用、模式校验、失败自动重跑与双模型分歧复核。"},
    {"gap": "研究就绪不等于适合某个用户", "impact": "相同证据对不同期限、仓位、回撤承受力的用户意义不同。", "upgrade": "把用户投资期限、风险预算、持仓暴露和禁止项加入个性化决策约束。"},
    {"gap": "建议反馈闭环还不够强", "impact": "如果不追踪当时证据、后续结果和失效原因，系统只会不断生成新观点。", "upgrade": "建立逐条建议的版本快照、结果归因、校准曲线和月度错误复盘。"},
]

DECISION_USES = [
    {"name": "证据分诊", "value": "先看新增事实与来源等级，把重复新闻和低可追溯观点放到次级队列。"},
    {"name": "预期差识别", "value": "并排比较券商预测、小作文明确区间和当前估值，记录预测针对的期间。"},
    {"name": "价格确认", "value": "观察事件后价格、成交量和行业相对收益，只把行情当作确认而不是因果证明。"},
    {"name": "反方与失效", "value": "任何研究结论都要列出相反事实、失效条件、验证窗口和需要补齐的数据。"},
    {"name": "组合约束", "value": "把单股证据放回持仓集中度、行业暴露和风险预算中，避免只看故事强度。"},
]


class ResearchDecisionService:
    """Turn existing data breadth into a transparent, non-prescriptive workflow."""

    def __init__(self, monitor: Optional[InvestmentMonitorService] = None):
        self.monitor = monitor or InvestmentMonitorService()

    @staticmethod
    def _score(value: float) -> int:
        return max(0, min(100, round(value)))

    @staticmethod
    def _latest_time(events: Iterable[Dict[str, Any]]) -> Optional[str]:
        values = [str(item.get("event_at") or "") for item in events if item.get("event_at")]
        return max(values) if values else None

    @staticmethod
    def _verification_tasks(stock: Dict[str, Any], coverage: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        tasks: List[Dict[str, str]] = []
        for row in coverage:
            if not row.get("available"):
                tasks.append({"priority": "高", "task": f"补齐{row.get('name') or '缺失渠道'}", "reason": "当前没有可用于交叉验证的数据"})
            elif row.get("freshness_status") == "stale":
                tasks.append({"priority": "中", "task": f"更新{row.get('name') or '陈旧渠道'}", "reason": "已有数据超过该渠道的新鲜度阈值"})
        evidence = stock.get("evidence") or {}
        if float(evidence.get("original_link_coverage") or 0) < 60:
            tasks.append({"priority": "高", "task": "提高原文可追溯覆盖", "reason": "当前可直接回看原文的证据不足 60%"})
        if int(evidence.get("unverified_count") or 0) > int(evidence.get("factual_count") or 0):
            tasks.append({"priority": "高", "task": "核验待证观点", "reason": "待核验观点多于事实证据，不能直接形成判断"})
        if not stock.get("market", {}).get("updated_at"):
            tasks.append({"priority": "高", "task": "补齐最新行情时间", "reason": "没有行情时间戳，无法判断价格确认发生在何时"})
        return tasks[:6] or [{"priority": "常规", "task": "跟踪下一条新增事实", "reason": "当前覆盖完整，等待公告、财报、研报或价格形成新的验证点"}]

    def _decision_packet(self, stock: Dict[str, Any]) -> Dict[str, Any]:
        evidence = stock.get("evidence") or {}
        coverage = list(stock.get("coverage") or [])
        available = [row for row in coverage if row.get("available")]
        fresh = [row for row in available if row.get("freshness_status") == "fresh"]
        timeline = list(stock.get("timeline") or [])
        factual = [item for item in timeline if (item.get("metrics") or {}).get("_evidence", {}).get("evidence_level") != "unverified"]
        bullish = [item for item in factual if item.get("sentiment") == "bullish"]
        bearish = [item for item in factual if item.get("sentiment") == "bearish"]

        coverage_score = self._score(100 * len(available) / max(1, len(coverage)))
        freshness_score = self._score(100 * len(fresh) / max(1, len(available)))
        traceability_score = self._score(float(evidence.get("original_link_coverage") or 0))
        evidence_score = self._score(min(100, int(evidence.get("factual_count") or 0) * 2 + int(evidence.get("source_count") or 0) * 8))
        readiness = self._score(coverage_score * .30 + freshness_score * .25 + traceability_score * .20 + evidence_score * .25)
        state = "可进入研究" if readiness >= 75 else "需要补证" if readiness >= 50 else "数据不足"
        consensus = stock.get("consensus") or {}
        risks = [signal for signal in stock.get("signals") or [] if signal.get("kind") == "risk"][:4]

        return {
            "symbol": stock.get("symbol"), "name": stock.get("name"), "state": state,
            "readiness_score": readiness,
            "score_components": [
                {"name": "渠道覆盖", "score": coverage_score, "weight": 30},
                {"name": "数据新鲜度", "score": freshness_score, "weight": 25},
                {"name": "原文可追溯", "score": traceability_score, "weight": 20},
                {"name": "事实充分度", "score": evidence_score, "weight": 25},
            ],
            "market": stock.get("market") or {},
            "evidence": evidence,
            "latest_evidence_at": self._latest_time(timeline),
            "changes": timeline[:5],
            "agreement": {"bullish_facts": len(bullish), "bearish_facts": len(bearish), "conflict": bool(bullish and bearish)},
            "expectations": {
                "broker_reports": int(consensus.get("broker_report_count") or 0),
                "essay_estimates": int(consensus.get("essay_expectation_count") or 0),
                "as_of": consensus.get("as_of"), "method": consensus.get("method"),
            },
            "invalidation_evidence": risks,
            "verification_tasks": self._verification_tasks(stock, coverage),
            "coverage": coverage,
            "disclaimer": "研究就绪度只衡量数据与证据质量，不预测涨跌，也不构成买卖建议。",
        }

    def overview(self, *, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        source_bi = self.monitor.source_bi(days=30)
        watchlist = self.monitor.super_watchlist(days=365, symbols=symbols)
        packets = [self._decision_packet(stock) for stock in watchlist.get("stocks") or []]
        summary = source_bi.get("summary") or {}
        sources = source_bi.get("sources") or []
        attention_count = sum(
            1 for source in sources
            if source.get("monitoring_status") != "live" or source.get("freshness_status") == "stale"
        )
        return {
            "version": "3.0-evidence-operating-system",
            "generated_at": datetime.now().astimezone().isoformat(),
            "iterations": [
                {"version": "V1", "name": "看清能力", "result": "每个入口明确目的、真实数据和输出，展示数据源存量与状态。"},
                {"version": "V2", "name": "形成决策链", "result": "按自选股把变化、行情确认、一致预期、风险和核验任务串成证据链。"},
                {"version": "V3", "name": "建立信任层", "result": "公开覆盖、新鲜度、原文率、冲突和缺口，数据不足时明确拒绝给结论。"},
            ],
            "system": {
                "source_count": int(summary.get("enabled") or 0),
                "stored_event_count": int(summary.get("stored_event_count") or 0),
                "fresh_source_count": int(summary.get("fresh") or 0),
                "live_monitor_count": int(summary.get("monitoring_live") or 0),
                "attention_source_count": attention_count,
                "watchlist_count": len(packets),
            },
            "decision_packets": packets,
            "functions": FUNCTION_CATALOG,
            "architecture": ARCHITECTURE,
            "data_sources": sources,
            "decision_uses": DECISION_USES,
            "reflection": REFLECTION_BACKLOG,
            "principles": [
                "先事实、后观点；没有时间与来源的内容不进入核心证据。",
                "行情只确认市场是否响应，不能把相关性伪装成因果。",
                "所有判断必须同时写出证据缺口、核验动作和失效条件。",
                "AI 负责压缩与提出问题，原始数据、公告和原文仍是最终依据。",
            ],
        }
