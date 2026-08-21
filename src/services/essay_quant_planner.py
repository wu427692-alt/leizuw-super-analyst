# -*- coding: utf-8 -*-
"""Natural-language planning for the bounded essay quant research engine.

The model may propose a plan, but it never receives a Python interpreter,
filesystem path, SQL connection, credential, or network tool. Executable code
is rendered from a server-owned template and execution calls the allowlisted
EssayQuantService API only.
"""

from __future__ import annotations

import pprint
from typing import Any, Dict, Optional

from src.services.essay_analysis_service import DeepSeekEssayAnalyzer, EssayAnalysisError
from src.services.essay_quant_service import EssayQuantError, EssayQuantService


_SYSTEM_PROMPT = """你是中国A股量化研究任务规划器。把自然语言需求转换为严格 JSON，不得输出 Markdown。
只能使用以下研究引擎能力：知识星球/研报观点事件研究，信号方向，关键词过滤，首次提及，
重要度与置信度阈值，5/10/20/30/60交易日持有期，沪深300/中证500/中证1000/上证指数基准，
交易成本，时间顺序验证。不得声称执行任意代码、联网、下单或直接访问数据库。
JSON 字段：title, strategy_type, hypothesis, source_query, signal_direction, lookback_days,
holding_periods, first_mention_only, first_mention_window_days, min_importance, min_confidence,
benchmark_code, portfolio_size, raw_note_policy, dedupe_window_days, transaction_cost_bps,
validation_method, universe, signal_sources, assumptions, unsupported_requests。
strategy_type 只能为 essay_event；signal_direction 只能 bullish/bearish/all；raw_note_policy 只能 exclude/include；
validation_method 只能 walk_forward/time_split/none。默认排除未AI分析原文，默认成本12bp，默认3日重复信号聚类。
如果需求超出能力，在 unsupported_requests 列出，不要伪装已支持。"""


class EssayQuantNaturalLanguagePlanner:
    def __init__(self, analyzer: Optional[DeepSeekEssayAnalyzer] = None, service: Optional[EssayQuantService] = None):
        self.analyzer = analyzer or DeepSeekEssayAnalyzer()
        self.service = service or EssayQuantService()

    def plan(self, prompt: str) -> Dict[str, Any]:
        normalized_prompt = str(prompt or "").strip()
        if len(normalized_prompt) < 8:
            raise EssayQuantError("请至少用一句完整的话描述研究信号、持有期或评价目标")
        if not self.analyzer.configured:
            raise EssayQuantError("DeepSeek 尚未配置，无法生成自然语言研究方案")
        request_payload = {
            "model": self.analyzer.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": normalized_prompt[:4000]},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0.05,
            "max_tokens": 2500,
            "stream": False,
        }
        try:
            response = self.analyzer._post_with_retry(request_payload)
            parsed = self.analyzer._parse_json(self.analyzer._extract_content(response))
        except EssayAnalysisError as exc:
            raise EssayQuantError(f"研究方案生成失败：{exc}") from exc
        rule = self.service._normalize_rule(parsed)
        plan = {
            "title": str(parsed.get("title") or rule["name"])[:120],
            "hypothesis": str(parsed.get("hypothesis") or normalized_prompt)[:1000],
            "universe": str(parsed.get("universe") or "A股中具备有效事件和日线行情的股票")[:300],
            "signal_sources": [str(item)[:80] for item in (parsed.get("signal_sources") or ["知识星球", "Tushare行情"])][:8],
            "assumptions": [str(item)[:300] for item in (parsed.get("assumptions") or [])][:12],
            "unsupported_requests": [str(item)[:300] for item in (parsed.get("unsupported_requests") or [])][:12],
        }
        rule["name"] = plan["title"]
        return {
            "prompt": normalized_prompt, "plan": plan, "rule": rule,
            "code": self.render_code(rule),
            "safety": {
                "mode": "template_sandbox", "can_execute": True,
                "allowed_operations": ["读取量化服务聚合数据", "调用事件研究引擎", "保存不可变运行快照"],
                "blocked_operations": ["任意 Python exec/eval", "Shell 与文件系统", "凭据读取", "下单", "任意 SQL", "未授权网络访问"],
                "confirmation_required": True,
            },
        }

    def execute(self, rule: Dict[str, Any], *, refresh_prices: bool = True) -> Dict[str, Any]:
        normalized = self.service._normalize_rule(rule)
        return self.service.run(normalized, refresh_prices=refresh_prices, max_symbols=30, persist=True)

    @staticmethod
    def render_code(rule: Dict[str, Any]) -> str:
        literal = pprint.pformat(rule, sort_dicts=True, width=100)
        return (
            "# 由受约束研究方案生成器生成；不执行模型自由代码\n"
            "from src.services.essay_quant_service import EssayQuantService\n\n"
            f"research_rule = {literal}\n\n"
            "result = EssayQuantService().run(\n"
            "    research_rule, refresh_prices=True, max_symbols=30, persist=True\n"
            ")\n"
            "# result 包含收益、超额收益、置信区间、队列稳定性与数据质量\n"
        )
