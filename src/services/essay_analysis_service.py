# -*- coding: utf-8 -*-
"""DeepSeek analysis, tagging and dashboard aggregation for research-note essays."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests
from json_repair import repair_json

from src.config import get_config
from src.repositories.essay_daily_report_repo import EssayDailyReportRepository
from src.repositories.essay_analysis_repo import EssayAnalysisRepository
from src.storage import utc_naive_now

logger = logging.getLogger(__name__)

ESSAY_PROMPT_VERSION = "essay-radar-v2-deep"
DEFAULT_ESSAY_MODEL = "deepseek-v4-flash"
DAILY_REPORT_PROMPT_VERSION = "essay-daily-v3-stratified-watchlist"

_CATEGORIES = {
    "company_research",
    "broker_view",
    "industry_chain",
    "macro_policy",
    "market_flow",
    "event_catalyst",
    "earnings",
    "risk_warning",
    "rumor",
    "other",
}
_SENTIMENTS = {"bullish", "bearish", "neutral", "mixed"}
_HORIZONS = {"intraday", "short", "medium", "long", "unclear"}
_STANCES = {"bullish", "bearish", "neutral", "watching"}
_TS_CODE_RE = re.compile(r"^(\d{6})(?:\.(SH|SS|SZ|BJ))?$", re.IGNORECASE)
_DASHBOARD_ROWS_CACHE_TTL_SECONDS = 30.0
_dashboard_rows_cache: Dict[int, tuple[float, List[Dict[str, Any]]]] = {}
_dashboard_rows_cache_lock = threading.Lock()

_SYSTEM_PROMPT = """你是中国资本市场研究资料结构化分析员。请只根据输入文本提取事实、观点和风险，
不要补造公司、代码、数字或来源。每篇文本都必须输出一条结果，并严格输出一个 JSON object。

JSON 格式：
{
  "items": [
    {
      "topic_id": "原样返回",
      "summary": "不超过180字的中文摘要",
      "primary_category": "company_research|broker_view|industry_chain|macro_policy|market_flow|event_catalyst|earnings|risk_warning|rumor|other",
      "sentiment": "bullish|bearish|neutral|mixed",
      "time_horizon": "intraday|short|medium|long|unclear",
      "importance_score": 0,
      "confidence_score": 0.0,
      "tags": ["最多8个稳定中文标签"],
      "industries": ["最多5个申万风格行业名"],
      "themes": ["最多5个主题概念"],
      "stock_mentions": [
        {
          "ts_code": "明确时填写600519.SH；无法确认则为空字符串",
          "name": "公司或标的名称",
          "stance": "bullish|bearish|neutral|watching",
          "confidence": 0.0,
          "rationale": "不超过80字"
        }
      ],
      "key_points": ["最多5条"],
      "catalysts": ["最多4条"],
      "risks": ["最多4条"],
      "evidence": [{"claim":"核心判断", "evidence":"原文依据", "strength":"strong|medium|weak"}],
      "contradictions": ["原文内部矛盾、与常识冲突或尚未证实处，最多4条"],
      "falsification_conditions": ["什么事实出现会证伪该观点，最多4条"],
      "monitoring_points": ["后续应跟踪的数据、公告或时间节点，最多6条"],
      "earnings_impact": "对收入、利润、现金流或成本的可能传导；无依据写信息不足",
      "valuation_impact": "对估值逻辑和风险溢价的可能影响；无依据写信息不足",
      "source_quality": "high|medium|low|unknown",
      "novelty_score": 0,
      "information_type": "fact|management_guidance|institution_view|market_rumor|mixed|unknown"
    }
  ]
}

规则：importance_score 为0-100整数；confidence_score 和股票 confidence 为0-1。
标签优先使用稳定词汇，例如“业绩超预期、涨价、产能扩张、国产替代、政策催化、机构观点、风险提示、订单增长、AI算力、机器人”。
必须把事实、公司指引、机构观点和市场传闻分开；结论要给原文证据、证伪条件与后续验证指标。
novelty_score 为0-100，衡量相对常见市场叙事的信息增量，不得把重复观点打高分。
券商观点不等于事实；传闻、转述、缺少来源或只有图片/文件标题时降低置信度并标记“信息不足”或“传闻”。
不得输出 Markdown、解释或 JSON 之外的文本。"""


class EssayAnalysisError(RuntimeError):
    """Safe error surfaced by the essay analysis pipeline."""


class DeepSeekEssayAnalyzer:
    """Batch analyzer bound directly to the official DeepSeek API."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_content_chars: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ):
        config = get_config()
        keys = list(getattr(config, "deepseek_api_keys", None) or [])
        configured_key = str(api_key or (keys[0] if keys else getattr(config, "deepseek_api_key", "")) or "").strip()
        self.api_key = configured_key
        self.base_url = str(base_url or os.getenv("ESSAY_ANALYSIS_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.model = str(model or os.getenv("ESSAY_ANALYSIS_MODEL") or DEFAULT_ESSAY_MODEL).strip()
        self.timeout_seconds = max(
            10,
            min(int(timeout_seconds or os.getenv("ESSAY_ANALYSIS_TIMEOUT_SEC", "120")), 300),
        )
        self.max_content_chars = max(
            500,
            min(int(max_content_chars or os.getenv("ESSAY_ANALYSIS_MAX_CONTENT_CHARS", "5000")), 20000),
        )
        self.max_retries = max(1, min(int(os.getenv("ESSAY_ANALYSIS_MAX_RETRIES", "3")), 5))
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def analyze_batch(self, notes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.configured:
            raise EssayAnalysisError("DEEPSEEK_API_KEY is not configured")
        if not notes:
            return {"items": [], "usage": {}, "raw_response": ""}

        payload_notes = [self._prompt_note(note) for note in notes]
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "请分析以下小作文数组并输出指定 JSON：\n" + json.dumps(payload_notes, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": 12000,
            "stream": False,
        }
        response_payload = self._post_with_retry(request_payload)
        raw_content = self._extract_content(response_payload)
        parsed = self._parse_json(raw_content)
        normalized_items = self._normalize_batch(parsed, notes)
        usage = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else {}
        return {
            "items": normalized_items,
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
            "raw_response": raw_content,
        }

    def _post_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_error = "DeepSeek request failed"
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 200:
                    result = response.json()
                    if not isinstance(result, dict):
                        raise EssayAnalysisError("DeepSeek returned a non-object response")
                    return result
                retryable = response.status_code == 429 or response.status_code >= 500
                last_error = f"DeepSeek HTTP {response.status_code}"
                if not retryable:
                    raise EssayAnalysisError(last_error)
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 10)
            except EssayAnalysisError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = f"DeepSeek request failed: {type(exc).__name__}"
                delay = min(2 ** attempt, 10)
            if attempt < self.max_retries:
                time.sleep(delay)
        raise EssayAnalysisError(last_error)

    @staticmethod
    def _extract_content(payload: Dict[str, Any]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EssayAnalysisError("DeepSeek response is missing message content") from exc
        normalized = str(content or "").strip()
        if not normalized:
            raise EssayAnalysisError("DeepSeek returned empty JSON content")
        return normalized

    @staticmethod
    def _parse_json(raw_content: str) -> Dict[str, Any]:
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(repair_json(cleaned))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise EssayAnalysisError("DeepSeek returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise EssayAnalysisError("DeepSeek JSON root must be an object")
        return parsed

    def _prompt_note(self, note: Dict[str, Any]) -> Dict[str, Any]:
        content = str(note.get("content") or "")
        return {
            "topic_id": str(note.get("topic_id") or ""),
            "title": str(note.get("title") or "")[:500],
            "content": content[: self.max_content_chars],
            "existing_symbols": list(note.get("existing_symbols") or []),
            "created_at": note.get("created_at"),
        }

    def _normalize_batch(
        self,
        payload: Dict[str, Any],
        notes: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise EssayAnalysisError("DeepSeek JSON must contain an items array")
        note_by_topic = {str(note["topic_id"]): note for note in notes}
        normalized = []
        seen = set()
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            topic_id = str(raw_item.get("topic_id") or "").strip()
            if topic_id not in note_by_topic or topic_id in seen:
                continue
            normalized.append(self._normalize_item(raw_item, note_by_topic[topic_id]))
            seen.add(topic_id)
        return normalized

    def _normalize_item(self, item: Dict[str, Any], note: Dict[str, Any]) -> Dict[str, Any]:
        category = str(item.get("primary_category") or "other").strip().lower()
        sentiment = str(item.get("sentiment") or "neutral").strip().lower()
        horizon = str(item.get("time_horizon") or "unclear").strip().lower()
        stock_mentions = []
        for raw_stock in item.get("stock_mentions") or []:
            if not isinstance(raw_stock, dict):
                continue
            name = str(raw_stock.get("name") or "").strip()[:100]
            ts_code = self._normalize_ts_code(raw_stock.get("ts_code"))
            if not name and not ts_code:
                continue
            stance = str(raw_stock.get("stance") or "neutral").strip().lower()
            stock_mentions.append({
                "ts_code": ts_code,
                "name": name,
                "stance": stance if stance in _STANCES else "neutral",
                "confidence": self._score(raw_stock.get("confidence"), maximum=1.0),
                "rationale": str(raw_stock.get("rationale") or "").strip()[:200],
            })
        existing_codes = {stock["ts_code"] for stock in stock_mentions if stock["ts_code"]}
        for existing_symbol in note.get("existing_symbols") or []:
            ts_code = self._normalize_ts_code(existing_symbol)
            if ts_code and ts_code not in existing_codes:
                stock_mentions.append({
                    "ts_code": ts_code,
                    "name": "",
                    "stance": "neutral",
                    "confidence": 0.5,
                    "rationale": "原文明确出现证券代码",
                })
        return {
            "topic_id": str(note["topic_id"]),
            "summary": str(item.get("summary") or "信息不足，未形成有效摘要").strip()[:500],
            "primary_category": category if category in _CATEGORIES else "other",
            "sentiment": sentiment if sentiment in _SENTIMENTS else "neutral",
            "time_horizon": horizon if horizon in _HORIZONS else "unclear",
            "importance_score": int(round(self._score(item.get("importance_score"), maximum=100.0))),
            "confidence_score": self._score(item.get("confidence_score"), maximum=1.0),
            "tags": self._string_list(item.get("tags"), 8),
            "industries": self._string_list(item.get("industries"), 5),
            "themes": self._string_list(item.get("themes"), 5),
            "stock_mentions": stock_mentions[:10],
            "key_points": self._string_list(item.get("key_points"), 5, max_length=300),
            "catalysts": self._string_list(item.get("catalysts"), 4, max_length=300),
            "risks": self._string_list(item.get("risks"), 4, max_length=300),
            "evidence": self._evidence_list(item.get("evidence"), 6),
            "contradictions": self._string_list(item.get("contradictions"), 4, max_length=300),
            "falsification_conditions": self._string_list(item.get("falsification_conditions"), 4, max_length=300),
            "monitoring_points": self._string_list(item.get("monitoring_points"), 6, max_length=300),
            "earnings_impact": str(item.get("earnings_impact") or "信息不足").strip()[:500],
            "valuation_impact": str(item.get("valuation_impact") or "信息不足").strip()[:500],
            "source_quality": str(item.get("source_quality") or "unknown").strip().lower() if str(item.get("source_quality") or "unknown").strip().lower() in {"high", "medium", "low", "unknown"} else "unknown",
            "novelty_score": int(round(self._score(item.get("novelty_score"), maximum=100.0))),
            "information_type": str(item.get("information_type") or "unknown").strip().lower() if str(item.get("information_type") or "unknown").strip().lower() in {"fact", "management_guidance", "institution_view", "market_rumor", "mixed", "unknown"} else "unknown",
        }

    @staticmethod
    def _evidence_list(value: Any, limit: int) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        result = []
        for raw in value:
            if not isinstance(raw, dict):
                continue
            claim = str(raw.get("claim") or "").strip()[:300]
            evidence = str(raw.get("evidence") or "").strip()[:500]
            strength = str(raw.get("strength") or "weak").strip().lower()
            if claim and evidence:
                result.append({"claim": claim, "evidence": evidence, "strength": strength if strength in {"strong", "medium", "weak"} else "weak"})
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _score(value: Any, *, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return round(max(0.0, min(number, maximum)), 4)

    @staticmethod
    def _string_list(value: Any, limit: int, *, max_length: int = 80) -> List[str]:
        if not isinstance(value, list):
            return []
        result = []
        seen = set()
        for item in value:
            normalized = str(item or "").strip()[:max_length]
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _normalize_ts_code(value: Any) -> str:
        raw = str(value or "").strip().upper()
        match = _TS_CODE_RE.fullmatch(raw)
        if not match:
            return ""
        digits, suffix = match.groups()
        suffix = (suffix or "").replace("SS", "SH")
        if not suffix:
            if digits.startswith(("4", "8")):
                suffix = "BJ"
            elif digits.startswith(("6", "9")):
                suffix = "SH"
            else:
                suffix = "SZ"
        return f"{digits}.{suffix}"


_DAILY_SYSTEM_PROMPT = """你是中国资本市场晨会主笔。仅依据给定的前一日小作文结构化记录形成日报，
区分事实、公司指引、机构观点和传闻，不得补造数据。识别共识、分歧、信息增量、跨来源印证、潜在盈利传导、
估值影响、催化剂、风险和次日验证点。严格输出一个 JSON object，不得输出 Markdown。
格式：{"executive_summary":"300字内", "market_regime":"市场叙事状态", "key_themes":[{"name":"主题","count":1,"direction":"bullish|bearish|mixed|neutral","thesis":"逻辑","evidence_topic_ids":["id"]}], "stock_focus":[{"ts_code":"","name":"","mention_count":1,"stance":"bullish|bearish|mixed|neutral","thesis":"逻辑","catalysts":[],"risks":[],"evidence_topic_ids":[]}], "consensus":[], "divergences":[], "novel_signals":[], "earnings_implications":[], "valuation_implications":[], "risk_watch":[], "next_day_watchlist":[], "data_quality":{"coverage":"","limitations":[]}}"""


class EssayDailyReportService:
    """Generate and persist one previous-day synthesis per configured model."""

    def __init__(self, *, analysis_repo: Optional[EssayAnalysisRepository] = None, report_repo: Optional[EssayDailyReportRepository] = None):
        self.analysis_repo = analysis_repo or EssayAnalysisRepository()
        self.report_repo = report_repo or EssayDailyReportRepository()

    @staticmethod
    def configured_models() -> List[str]:
        raw = os.getenv("ESSAY_DAILY_REPORT_MODELS") or os.getenv("ESSAY_ANALYSIS_MODEL") or DEFAULT_ESSAY_MODEL
        return list(dict.fromkeys(value.strip() for value in raw.split(",") if value.strip()))[:20]

    def generate(self, *, report_date: Optional[str] = None, models: Optional[Sequence[str]] = None, force: bool = False) -> Dict[str, Any]:
        target = self._target_date(report_date)
        start, end = self._utc_bounds(target)
        rows = self.analysis_repo.completed_between(start=start, end=end)
        source_hash = hashlib.sha256(json.dumps(
            [(row["topic_id"], row.get("updated_at"), row.get("prompt_version")) for row in rows],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        selected_models = list(models or self.configured_models())
        results = []
        for model in selected_models:
            existing = self.report_repo.get(target.isoformat(), model)
            if (existing and existing.get("status") == "completed" and
                    existing.get("source_hash") == source_hash and
                    existing.get("prompt_version") == DAILY_REPORT_PROMPT_VERSION and not force):
                results.append({"model": model, "status": "unchanged", "report": existing})
                continue
            self.report_repo.begin(report_date=target.isoformat(), model=model, prompt_version=DAILY_REPORT_PROMPT_VERSION, source_count=len(rows), source_hash=source_hash)
            if not rows:
                empty_report = self._empty_report(target)
                self.report_repo.save_success(report_date=target.isoformat(), model=model, report=empty_report, usage={})
                results.append({"model": model, "status": "completed", "source_count": 0})
                continue
            try:
                report, usage = self._call_model(model, target, rows)
                self.report_repo.save_success(report_date=target.isoformat(), model=model, report=report, usage=usage)
                results.append({"model": model, "status": "completed", "source_count": len(rows)})
            except Exception as exc:
                safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                self.report_repo.save_failure(report_date=target.isoformat(), model=model, error=safe_error)
                results.append({"model": model, "status": "failed", "error": safe_error})
        return {"report_date": target.isoformat(), "source_count": len(rows), "models": results}

    def list(self, *, limit: int = 30, model: Optional[str] = None) -> Dict[str, Any]:
        items = self.report_repo.list(limit=limit, model=model)
        return {"items": items, "models": self.configured_models(), "total": len(items)}

    def _call_model(self, model: str, target: date, rows: Sequence[Dict[str, Any]]) -> tuple[Dict[str, Any], Dict[str, int]]:
        analyzer = DeepSeekEssayAnalyzer(model=model)
        if not analyzer.configured:
            raise EssayAnalysisError("DEEPSEEK_API_KEY is not configured")
        context = self._daily_context(rows)
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _DAILY_SYSTEM_PROMPT}, {"role": "user", "content": f"报告日期：{target.isoformat()}。全量统计覆盖全部 {len(rows)} 篇；代表性证据用于定性归纳。\n" + json.dumps(context, ensure_ascii=False)}],
            "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}, "temperature": 0.1,
            "max_tokens": 12000, "stream": False,
        }
        response = analyzer._post_with_retry(payload)
        report = analyzer._parse_json(analyzer._extract_content(response))
        report["report_date"] = target.isoformat()
        report["source_count"] = len(rows)
        quality = report.get("data_quality") if isinstance(report.get("data_quality"), dict) else {}
        quality.update(context["coverage"])
        report["data_quality"] = quality
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return report, {key: int(usage.get(key) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}

    @classmethod
    def _daily_context(cls, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        sentiment, categories, sources = Counter(), Counter(), Counter()
        information_types, source_quality, tags, themes, stocks = Counter(), Counter(), Counter(), Counter(), Counter()
        evidence_count = low_confidence = rumor_count = 0
        for row in rows:
            sentiment[row.get("sentiment") or "neutral"] += 1
            categories[row.get("primary_category") or "other"] += 1
            sources[(row.get("note") or {}).get("group_name") or "unknown"] += 1
            information_type = row.get("information_type") or "unknown"
            information_types[information_type] += 1
            source_quality[row.get("source_quality") or "unknown"] += 1
            tags.update(row.get("tags") or [])
            themes.update(row.get("themes") or [])
            evidence_count += int(bool(row.get("evidence")))
            low_confidence += int(float(row.get("confidence_score") or 0) < 0.5)
            rumor_count += int(information_type == "market_rumor" or row.get("primary_category") == "rumor")
            for mention in row.get("stock_mentions") or []:
                name = str(mention.get("name") or mention.get("ts_code") or "").strip()
                if name:
                    stocks[name] += 1
        ranked = sorted(rows, key=lambda row: (
            int(row.get("importance_score") or 0) + int(row.get("novelty_score") or 0),
            float(row.get("confidence_score") or 0),
        ), reverse=True)
        representative_rows = cls._representative_rows(ranked, limit=240)
        samples = []
        for row in representative_rows:
            samples.append({
                "topic_id": row["topic_id"], "title": (row.get("note") or {}).get("title"),
                "source": (row.get("note") or {}).get("group_name"), "summary": row.get("summary"),
                "category": row.get("primary_category"), "sentiment": row.get("sentiment"),
                "importance": row.get("importance_score"), "confidence": row.get("confidence_score"),
                "novelty": row.get("novelty_score"), "information_type": row.get("information_type"),
                "source_quality": row.get("source_quality"), "tags": row.get("tags"),
                "themes": row.get("themes"), "stocks": row.get("stock_mentions"),
                "key_points": row.get("key_points"), "catalysts": row.get("catalysts"),
                "risks": row.get("risks"), "evidence": row.get("evidence"),
                "contradictions": row.get("contradictions"), "falsification_conditions": row.get("falsification_conditions"),
                "monitoring_points": row.get("monitoring_points"),
            })
        representative_sources = {
            str((row.get("note") or {}).get("group_name") or "unknown") for row in representative_rows
        }
        representative_categories = {
            str(row.get("primary_category") or "other") for row in representative_rows
        }
        watchlist_records = sum(1 for row in representative_rows if cls._matches_daily_watchlist(row))
        return {
            "coverage": {
                "total_records": len(rows), "representative_records": len(samples),
                "evidence_records": evidence_count,
                "evidence_coverage_percent": round(evidence_count / len(rows) * 100, 1) if rows else 0,
                "low_confidence_records": low_confidence, "rumor_records": rumor_count,
                "representative_watchlist_records": watchlist_records,
                "representative_sources": len(representative_sources),
                "representative_categories": len(representative_categories),
                "selection_strategy": "watchlist_priority_plus_source_category_information_type_strata",
            },
            "full_population_aggregates": {
                "sentiment": dict(sentiment), "categories": dict(categories), "sources": dict(sources),
                "information_types": dict(information_types), "source_quality": dict(source_quality),
                "top_tags": tags.most_common(30), "top_themes": themes.most_common(30),
                "top_stocks": stocks.most_common(50),
            },
            "representative_evidence": samples,
        }

    @classmethod
    def _representative_rows(cls, ranked: Sequence[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
        """Keep watchlist evidence and diverse strata before filling by analytical value."""
        safe_limit = max(1, min(int(limit), 500))
        selected: Dict[str, Dict[str, Any]] = {}

        def add(row: Dict[str, Any]) -> None:
            if len(selected) >= safe_limit:
                return
            topic_id = str(row.get("topic_id") or "").strip()
            if topic_id:
                selected.setdefault(topic_id, row)

        watchlist = cls._daily_watchlist()
        per_stock_limit = max(8, min(60, 120 // max(1, len(watchlist))))
        for symbol, name in watchlist:
            matched = [row for row in ranked if cls._row_mentions(row, symbol, name)]
            for row in matched[:per_stock_limit]:
                add(row)

        strata: Dict[tuple[str, str], int] = defaultdict(int)
        for row in ranked:
            values = (
                ("source", str((row.get("note") or {}).get("group_name") or "unknown")),
                ("category", str(row.get("primary_category") or "other")),
                ("information_type", str(row.get("information_type") or "unknown")),
            )
            for dimension, value in values:
                key = (dimension, value)
                if strata[key] < 4:
                    add(row)
                    strata[key] += 1
        for row in ranked:
            add(row)
        return list(selected.values())

    @staticmethod
    def _daily_watchlist() -> List[tuple[str, str]]:
        raw = os.getenv("ESSAY_WATCHLIST") or "603306.SH:华懋科技,300476.SZ:胜宏科技"
        result = []
        for item in raw.split(","):
            symbol, _, name = item.strip().partition(":")
            if symbol and name:
                result.append((symbol.strip().upper(), name.strip()))
        return result[:20]

    @staticmethod
    def _row_mentions(row: Dict[str, Any], symbol: str, name: str) -> bool:
        return any(
            symbol.upper() == str(item.get("ts_code") or "").upper()
            or name.lower() in str(item.get("name") or "").lower()
            for item in (row.get("stock_mentions") or [])
        )

    @classmethod
    def _matches_daily_watchlist(cls, row: Dict[str, Any]) -> bool:
        return any(cls._row_mentions(row, symbol, name) for symbol, name in cls._daily_watchlist())

    @staticmethod
    def _target_date(value: Optional[str]) -> date:
        if value:
            return date.fromisoformat(value)
        return (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)).date()

    @staticmethod
    def _utc_bounds(target: date) -> tuple[datetime, datetime]:
        shanghai = timezone(timedelta(hours=8))
        local_start = datetime.combine(target, datetime_time.min, tzinfo=shanghai)
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(timezone.utc).replace(tzinfo=None), local_end.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _empty_report(target: date) -> Dict[str, Any]:
        return {"report_date": target.isoformat(), "source_count": 0, "executive_summary": "前一日没有已完成分析的新增小作文。", "market_regime": "无新增样本", "key_themes": [], "stock_focus": [], "consensus": [], "divergences": [], "novel_signals": [], "earnings_implications": [], "valuation_implications": [], "risk_watch": [], "next_day_watchlist": [], "data_quality": {"coverage": "无新增样本", "limitations": ["没有可汇总记录"]}}


class EssayAnalysisService:
    """Application service for queue control, query and aggregate stock views."""

    def __init__(self, repository: Optional[EssayAnalysisRepository] = None):
        self.repo = repository or EssayAnalysisRepository()
        self.report_repo = EssayDailyReportRepository(self.repo.db)
        self.model = str(os.getenv("ESSAY_ANALYSIS_MODEL") or DEFAULT_ESSAY_MODEL).strip()

    def enqueue_recent(self, *, days: int = 30) -> Dict[str, Any]:
        safe_days = max(1, min(int(days), 3650))
        cutoff = utc_naive_now() - timedelta(days=safe_days)
        result = self.repo.enqueue_recent(
            cutoff=cutoff,
            model=self.model,
            prompt_version=ESSAY_PROMPT_VERSION,
        )
        return {"days": safe_days, "cutoff": cutoff.isoformat() + "Z", **result}

    def enqueue_topic_ids(self, topic_ids: Iterable[str]) -> Dict[str, int]:
        return self.repo.enqueue_topic_ids(
            topic_ids,
            model=self.model,
            prompt_version=ESSAY_PROMPT_VERSION,
        )

    def list_analyses(self, **filters: Any) -> Dict[str, Any]:
        days = max(1, min(int(filters.pop("days", 30) or 30), 3650))
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(int(filters.get("page_size") or 20), 100))
        cutoff = utc_naive_now() - timedelta(days=days)
        rows, total = self.repo.list_analyses(cutoff=cutoff, **filters)
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    def get_analysis(self, topic_id: str) -> Dict[str, Any]:
        row = self.repo.get_analysis(str(topic_id or "").strip())
        if row is None:
            raise KeyError(f"essay analysis not found: {topic_id}")
        return row

    def progress(self, *, days: int = 30) -> Dict[str, Any]:
        safe_days = max(1, min(int(days), 3650))
        cutoff = utc_naive_now() - timedelta(days=safe_days)
        analyzer = DeepSeekEssayAnalyzer(model=self.model)
        return {
            "days": safe_days,
            "model": self.model,
            "deepseek_configured": analyzer.configured,
            **self.repo.progress(cutoff=cutoff),
        }

    def _completed_dashboard_rows(self, safe_days: int) -> List[Dict[str, Any]]:
        """Share the expensive decoded row snapshot across dashboard consumers."""
        cutoff = utc_naive_now() - timedelta(days=safe_days)
        # Test doubles must remain isolated and deterministic.
        if type(self.repo) is not EssayAnalysisRepository:
            return self.repo.completed_for_dashboard(cutoff=cutoff)
        now = time.monotonic()
        with _dashboard_rows_cache_lock:
            cached = _dashboard_rows_cache.get(safe_days)
            if cached and now - cached[0] < _DASHBOARD_ROWS_CACHE_TTL_SECONDS:
                return cached[1]
            rows = self.repo.completed_for_dashboard(cutoff=cutoff)
            _dashboard_rows_cache[safe_days] = (now, rows)
            return rows

    def dashboard(self, *, days: int = 30, top_n: int = 12) -> Dict[str, Any]:
        safe_days = max(1, min(int(days), 3650))
        safe_top_n = max(3, min(int(top_n), 30))
        rows = self._completed_dashboard_rows(safe_days)
        sentiment = Counter()
        categories = Counter()
        horizons = Counter()
        tags = Counter()
        industries = Counter()
        themes = Counter()
        tag_by_day: Dict[str, Counter] = defaultdict(Counter)
        stocks: Dict[str, Dict[str, Any]] = {}
        importance_total = 0
        token_total = 0
        for row in rows:
            sentiment[row.get("sentiment") or "neutral"] += 1
            categories[row.get("primary_category") or "other"] += 1
            horizons[row.get("time_horizon") or "unclear"] += 1
            importance = int(row.get("importance_score") or 0)
            importance_total += importance
            token_total += int(row.get("total_tokens") or 0)
            note_date = str((row.get("note") or {}).get("created_at") or "")[:10]
            for tag in row.get("tags") or []:
                tags[tag] += 1
                if note_date:
                    tag_by_day[note_date][tag] += 1
            industries.update(row.get("industries") or [])
            themes.update(row.get("themes") or [])
            for mention in row.get("stock_mentions") or []:
                key = str(mention.get("ts_code") or mention.get("name") or "").strip()
                if not key:
                    continue
                bucket = stocks.setdefault(key, {
                    "key": key,
                    "ts_code": mention.get("ts_code") or "",
                    "name": mention.get("name") or "",
                    "mention_count": 0,
                    "bullish": 0,
                    "bearish": 0,
                    "neutral": 0,
                    "watching": 0,
                    "importance_total": 0,
                    "confidence_total": 0.0,
                    "latest_at": None,
                })
                bucket["mention_count"] += 1
                stance = mention.get("stance") if mention.get("stance") in _STANCES else "neutral"
                bucket[stance] += 1
                bucket["importance_total"] += importance
                bucket["confidence_total"] += float(mention.get("confidence") or 0)
                created_at = (row.get("note") or {}).get("created_at")
                if created_at and (not bucket["latest_at"] or created_at > bucket["latest_at"]):
                    bucket["latest_at"] = created_at

        top_tags = [name for name, _ in tags.most_common(safe_top_n)]
        stock_rows = []
        for bucket in stocks.values():
            count = bucket["mention_count"]
            bucket["average_importance"] = round(bucket.pop("importance_total") / count, 1)
            bucket["average_confidence"] = round(bucket.pop("confidence_total") / count, 3)
            stock_rows.append(bucket)
        stock_rows.sort(
            key=lambda item: (item["mention_count"], item["average_importance"]),
            reverse=True,
        )
        highlights = sorted(
            rows,
            key=lambda item: (item.get("importance_score") or 0, (item.get("note") or {}).get("created_at") or ""),
            reverse=True,
        )[:8]
        return {
            "days": safe_days,
            "generated_at": utc_naive_now().isoformat() + "Z",
            "summary": {
                "analyzed_count": len(rows),
                "average_importance": round(importance_total / len(rows), 1) if rows else 0.0,
                "stock_count": len(stocks),
                "tag_count": len(tags),
                "total_tokens": token_total,
            },
            "sentiment": self._counter_rows(sentiment),
            "categories": self._counter_rows(categories),
            "horizons": self._counter_rows(horizons),
            "top_tags": self._counter_rows(tags, safe_top_n),
            "top_industries": self._counter_rows(industries, safe_top_n),
            "top_themes": self._counter_rows(themes, safe_top_n),
            "top_stocks": stock_rows[:safe_top_n],
            "tag_trend": [
                {"date": day, "tag": tag, "count": count}
                for day in sorted(tag_by_day)
                for tag, count in tag_by_day[day].items()
                if tag in top_tags
            ],
            "highlights": highlights,
        }

    def insights(self, *, days: int = 30, trend_days: int = 14) -> Dict[str, Any]:
        """Evidence, trend, model and watchlist cockpit built from completed analyses."""
        safe_days = max(1, min(int(days), 3650))
        safe_trend_days = max(7, min(int(trend_days), 90))
        rows = self._completed_dashboard_rows(safe_days)
        shanghai = timezone(timedelta(hours=8))
        today = datetime.now(shanghai).date()
        yesterday = today - timedelta(days=1)
        trend_start = today - timedelta(days=safe_trend_days - 1)

        def local_day(row: Dict[str, Any]) -> Optional[date]:
            raw = str((row.get("note") or {}).get("created_at") or "")
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(shanghai).date()
            except ValueError:
                return None

        quality, information_types, sources = Counter(), Counter(), Counter()
        daily: Dict[date, Dict[str, Any]] = {}
        yesterday_rows: List[Dict[str, Any]] = []
        evidence_records = 0
        for row in rows:
            row_day = local_day(row)
            if row_day == yesterday:
                yesterday_rows.append(row)
            quality[row.get("source_quality") or "unknown"] += 1
            information_types[row.get("information_type") or "unknown"] += 1
            sources[(row.get("note") or {}).get("group_name") or "unknown"] += 1
            evidence_records += int(bool(row.get("evidence")))
            if row_day and row_day >= trend_start:
                bucket = daily.setdefault(row_day, {
                    "date": row_day.isoformat(), "total": 0, "bullish": 0, "bearish": 0,
                    "neutral": 0, "mixed": 0, "importance_total": 0, "confidence_total": 0.0,
                })
                bucket["total"] += 1
                sentiment = row.get("sentiment") if row.get("sentiment") in _SENTIMENTS else "neutral"
                bucket[sentiment] += 1
                bucket["importance_total"] += int(row.get("importance_score") or 0)
                bucket["confidence_total"] += float(row.get("confidence_score") or 0)

        trend = []
        for offset in range(safe_trend_days):
            day = trend_start + timedelta(days=offset)
            bucket = daily.get(day, {"date": day.isoformat(), "total": 0, "bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0, "importance_total": 0, "confidence_total": 0.0})
            total = bucket["total"]
            trend.append({
                **{key: bucket[key] for key in ("date", "total", "bullish", "bearish", "neutral", "mixed")},
                "average_importance": round(bucket["importance_total"] / total, 1) if total else 0,
                "average_confidence": round(bucket["confidence_total"] / total, 3) if total else 0,
            })

        latest_reports = self.report_repo.list(limit=60)
        latest_date = max((item["report_date"] for item in latest_reports), default=None)
        report_rows = [item for item in latest_reports if item["report_date"] == latest_date] if latest_date else []
        completed_report_rows = [item for item in report_rows if item.get("status") == "completed"]
        comparison = self._compare_reports(completed_report_rows)

        watchlist = []
        for symbol, name in self._configured_watchlist():
            matched = []
            for row in rows:
                mentions = row.get("stock_mentions") or []
                if any(symbol.upper() == str(item.get("ts_code") or "").upper() or
                       name.lower() in str(item.get("name") or "").lower() for item in mentions):
                    matched.append(row)
            watchlist.append(self._watchlist_summary(symbol, name, matched, local_day, today, safe_trend_days))

        low_confidence = sum(1 for row in yesterday_rows if float(row.get("confidence_score") or 0) < 0.5)
        rumors = sum(1 for row in yesterday_rows if row.get("information_type") == "market_rumor" or row.get("primary_category") == "rumor")
        high_importance = sum(1 for row in yesterday_rows if int(row.get("importance_score") or 0) >= 80)
        high_novelty = sorted(yesterday_rows, key=lambda row: (int(row.get("novelty_score") or 0), int(row.get("importance_score") or 0)), reverse=True)[:8]
        return {
            "generated_at": utc_naive_now().isoformat() + "Z",
            "window_days": safe_days,
            "latest_data_at": max((str((row.get("note") or {}).get("created_at") or "") for row in rows), default=None),
            "yesterday": {
                "date": yesterday.isoformat(), "analyzed_count": len(yesterday_rows),
                "high_importance_count": high_importance, "low_confidence_count": low_confidence,
                "rumor_count": rumors,
                "evidence_coverage_percent": round(sum(bool(row.get("evidence")) for row in yesterday_rows) / len(yesterday_rows) * 100, 1) if yesterday_rows else 0,
            },
            "coverage": {
                "analyzed_count": len(rows), "evidence_records": evidence_records,
                "evidence_coverage_percent": round(evidence_records / len(rows) * 100, 1) if rows else 0,
                "configured_models": EssayDailyReportService.configured_models(),
                "completed_report_models": len(completed_report_rows),
            },
            "trend": trend,
            "source_quality": self._counter_rows(quality),
            "information_types": self._counter_rows(information_types),
            "source_mix": self._counter_rows(sources, 20),
            "model_comparison": {
                "report_date": latest_date, "reports": report_rows,
                "consensus": comparison["consensus"],
                "divergences": comparison["divergences"],
            },
            "watchlist": watchlist,
            "high_novelty_signals": high_novelty,
        }

    @staticmethod
    def _configured_watchlist() -> List[tuple[str, str]]:
        return EssayDailyReportService._daily_watchlist()

    @staticmethod
    def _compare_reports(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Compare structured theme/stock directions across completed model reports."""
        if len(rows) < 2:
            return {"consensus": [], "divergences": []}
        direction_labels = {
            "bullish": "看多", "bearish": "看空", "mixed": "分歧", "neutral": "中性",
            "watching": "观察",
        }
        themes: Dict[str, Dict[str, Any]] = {}
        stocks: Dict[str, Dict[str, Any]] = {}
        exact_consensus, exact_divergences = Counter(), Counter()
        for row in rows:
            model = str(row.get("model") or "unknown")
            report = row.get("report") or {}
            exact_consensus.update(str(value).strip() for value in report.get("consensus") or [] if str(value).strip())
            exact_divergences.update(str(value).strip() for value in report.get("divergences") or [] if str(value).strip())
            for item in report.get("key_themes") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                bucket = themes.setdefault(name.lower(), {"name": name, "models": set(), "directions": Counter()})
                bucket["models"].add(model)
                bucket["directions"][str(item.get("direction") or "neutral").lower()] += 1
            for item in report.get("stock_focus") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("ts_code") or "").strip()
                key = str(item.get("ts_code") or name).strip().upper()
                if not key:
                    continue
                bucket = stocks.setdefault(key, {"name": name, "models": set(), "directions": Counter()})
                bucket["models"].add(model)
                bucket["directions"][str(item.get("stance") or "neutral").lower()] += 1

        consensus: List[Dict[str, Any]] = []
        divergences: List[Dict[str, Any]] = []
        for kind, buckets in (("主题", themes), ("股票", stocks)):
            for bucket in buckets.values():
                model_count = len(bucket["models"])
                if model_count < 2:
                    continue
                directions = bucket["directions"]
                meaningful = {key for key, count in directions.items() if count and key not in {"neutral", "watching"}}
                direction_text = "、".join(
                    f"{direction_labels.get(key, key)} {count}个模型" for key, count in directions.most_common()
                )
                entry = {"text": f"{kind}「{bucket['name']}」：{direction_text}", "model_count": model_count}
                if len(meaningful) > 1 or "mixed" in meaningful:
                    divergences.append(entry)
                else:
                    consensus.append(entry)
        consensus.extend(
            {"text": text, "model_count": count}
            for text, count in exact_consensus.most_common() if count >= 2
        )
        divergences.extend(
            {"text": text, "model_count": count}
            for text, count in exact_divergences.most_common() if count >= 2
        )

        def dedupe(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
            result, seen = [], set()
            for item in sorted(items, key=lambda value: (-int(value["model_count"]), value["text"])):
                if item["text"] in seen:
                    continue
                seen.add(item["text"])
                result.append(item)
            return result[:12]
        return {"consensus": dedupe(consensus), "divergences": dedupe(divergences)}

    @staticmethod
    def _watchlist_summary(symbol: str, name: str, rows: Sequence[Dict[str, Any]], local_day, today: date, trend_days: int) -> Dict[str, Any]:
        sorted_rows = sorted(rows, key=lambda row: str((row.get("note") or {}).get("created_at") or ""), reverse=True)
        stances, timeline = Counter(), defaultdict(Counter)
        for row in rows:
            day = local_day(row)
            if day:
                timeline[day]["total"] += 1
            for mention in row.get("stock_mentions") or []:
                if symbol.upper() == str(mention.get("ts_code") or "").upper() or name.lower() in str(mention.get("name") or "").lower():
                    stance = mention.get("stance") if mention.get("stance") in _STANCES else "neutral"
                    stances[stance] += 1
                    if day:
                        timeline[day][stance] += 1
        def period_count(days: int) -> int:
            start = today - timedelta(days=days - 1)
            return sum(1 for row in rows if (local_day(row) or date.min) >= start)
        latest = sorted_rows[0] if sorted_rows else None
        trend_start = today - timedelta(days=trend_days - 1)
        return {
            "symbol": symbol, "name": name, "mention_count": len(rows),
            "day_mentions": period_count(1), "week_mentions": period_count(7), "month_mentions": period_count(30),
            "stances": dict(stances),
            "average_importance": round(sum(int(row.get("importance_score") or 0) for row in rows) / len(rows), 1) if rows else 0,
            "average_confidence": round(sum(float(row.get("confidence_score") or 0) for row in rows) / len(rows), 3) if rows else 0,
            "latest_at": (latest.get("note") or {}).get("created_at") if latest else None,
            "latest_thesis": latest.get("summary") if latest else None,
            "catalysts": list(dict.fromkeys(value for row in sorted_rows[:10] for value in (row.get("catalysts") or [])))[:6],
            "risks": list(dict.fromkeys(value for row in sorted_rows[:10] for value in (row.get("risks") or [])))[:6],
            "trend": [{"date": (trend_start + timedelta(days=offset)).isoformat(), **dict(timeline[trend_start + timedelta(days=offset)])} for offset in range(trend_days)],
            "latest_items": sorted_rows[:8],
        }

    def word_cloud(
        self,
        *,
        period: str = "day",
        anchor_date: Optional[str] = None,
        kind: str = "stocks",
        stock: Optional[str] = None,
        top_n: int = 80,
    ) -> Dict[str, Any]:
        if period not in {"day", "week", "month"}:
            raise ValueError("period must be day, week or month")
        if kind not in {"stocks", "tags", "themes"}:
            raise ValueError("kind must be stocks, tags or themes")
        anchor = date.fromisoformat(anchor_date) if anchor_date else datetime.now(timezone(timedelta(hours=8))).date()
        if period == "day":
            local_start, local_end = anchor, anchor + timedelta(days=1)
        elif period == "week":
            local_start = anchor - timedelta(days=anchor.weekday())
            local_end = local_start + timedelta(days=7)
        else:
            local_start = anchor.replace(day=1)
            local_end = (local_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        start, _ = EssayDailyReportService._utc_bounds(local_start)
        actual_end, _ = EssayDailyReportService._utc_bounds(local_end)
        window = actual_end - start
        rows = self.repo.completed_between(start=start, end=actual_end)
        previous = self.repo.completed_between(start=start - window, end=start)
        current_counts, metadata = self._cloud_counts(rows, kind=kind, stock=stock)
        previous_counts, _ = self._cloud_counts(previous, kind=kind, stock=stock)
        items = []
        for name, count in current_counts.most_common(max(10, min(int(top_n), 200))):
            prev = int(previous_counts.get(name, 0))
            item = {"name": name, "count": int(count), "previous_count": prev, "change": int(count) - prev}
            item.update(metadata.get(name, {}))
            items.append(item)
        return {
            "period": period,
            "kind": kind,
            "stock": stock,
            "start_date": local_start.isoformat(),
            "end_date": (local_end - timedelta(days=1)).isoformat(),
            "source_count": len(rows),
            "items": items,
        }

    @staticmethod
    def _cloud_counts(rows: Sequence[Dict[str, Any]], *, kind: str, stock: Optional[str]) -> tuple[Counter, Dict[str, Dict[str, Any]]]:
        counts: Counter = Counter()
        metadata: Dict[str, Dict[str, Any]] = {}
        stock_query = str(stock or "").strip().lower()
        for row in rows:
            mentions = row.get("stock_mentions") or []
            if stock_query and not any(stock_query in str(item.get("ts_code") or "").lower() or stock_query in str(item.get("name") or "").lower() for item in mentions):
                continue
            if kind == "stocks":
                for item in mentions:
                    name = str(item.get("name") or item.get("ts_code") or "").strip()
                    if not name or name in {"未知", "不详", "公司", "标的"}:
                        continue
                    counts[name] += 1
                    meta = metadata.setdefault(name, {"ts_code": item.get("ts_code") or "", "bullish": 0, "bearish": 0, "neutral": 0, "watching": 0})
                    stance = item.get("stance") if item.get("stance") in _STANCES else "neutral"
                    meta[stance] += 1
            else:
                counts.update(row.get(kind) or [])
        return counts, metadata

    @staticmethod
    def _counter_rows(counter: Counter, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        rows = counter.most_common(limit)
        return [{"name": str(name), "count": int(count)} for name, count in rows]
