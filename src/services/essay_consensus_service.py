# -*- coding: utf-8 -*-
"""Dedicated AI extraction of expectation claims from a stock's latest essays."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import desc, select

from src.repositories.investment_monitor_repo import InvestmentMonitorRepository
from src.services.essay_analysis_service import DeepSeekEssayAnalyzer, EssayAnalysisError
from src.storage import DatabaseManager, EssayConsensusRecord, ResearchNote, utc_naive_now

logger = logging.getLogger(__name__)

ESSAY_CONSENSUS_PROMPT_VERSION = "essay-consensus-v2-entity-bound-extraction"
_METRICS = {
    "revenue", "net_profit", "eps", "target_price", "market_cap",
    "valuation_multiple", "cash_flow", "margin", "growth_rate", "other",
}

_SYSTEM_PROMPT = """你是中国资本市场预期数据抽取员。输入是某只股票最近最多20篇知识星球小作文。
你的任务不是写泛泛的投资观点，而是寻找原文是否明确或隐含提出收入、净利润、EPS、目标价、目标市值、
估值倍数、现金流、利润率或增速推测。严禁补造数字、单位、预测期或来源；没有数字时可以保留明确的方向性推测，
但 value_text 必须忠实复述原文。每一项必须给 topic_id 和短原文证据。不同作者口径冲突必须分开，不得强行平均。

每项还必须识别“预期主体”。只保留与输入目标股票有明确关系的主体：目标上市公司本身、其合并口径、已明确说明的子公司、
收购标的或业务分部。行业汇总里仅仅同时出现的同行、客户或供应商数字必须丢弃。子公司、收购标的或业务分部的指标不能写成
上市公司自身指标；summary、profit_outlook 和 valuation_outlook 也必须明确区分主体。

严格输出 JSON object：
{
  "summary": "对这批材料预期信息的简洁总结；没有有效预期时明确说明",
  "has_explicit_expectations": true,
  "profit_outlook": "利润/收入/EPS预期总结，无则写信息不足",
  "valuation_outlook": "目标价/市值/估值倍数总结，无则写信息不足",
  "estimates": [
    {
      "topic_id": "原样返回",
      "subject": "该项预期对应的公司、子公司、收购标的或业务名称",
      "subject_relation": "target_stock|consolidated|subsidiary|acquisition_target|business_segment",
      "metric": "revenue|net_profit|eps|target_price|market_cap|valuation_multiple|cash_flow|margin|growth_rate|other",
      "period": "原文预测期；未说明写未注明",
      "value_text": "原文中的数值或方向性推测",
      "value_low": null,
      "value_high": null,
      "unit": "亿元|元|%|倍或原文单位",
      "direction": "up|down|flat|range|unclear",
      "evidence": "不超过160字的原文依据",
      "confidence": 0.0
    }
  ],
  "consensus_points": ["多篇材料一致指向的预期，最多5条"],
  "conflicts": ["作者或口径之间的冲突，最多5条"],
  "caveats": ["来源、口径、时间和可验证性限制，最多6条"]
}
不得输出 Markdown 或 JSON 之外的文本。"""


class EssayConsensusError(RuntimeError):
    """Safe error for the expectation-analysis workflow."""


class EssayConsensusAnalyzer:
    """One bounded DeepSeek request across the latest matched essay bundle."""

    def __init__(self, transport: Optional[DeepSeekEssayAnalyzer] = None):
        self.transport = transport or DeepSeekEssayAnalyzer()
        self.model = self.transport.model

    @property
    def configured(self) -> bool:
        return self.transport.configured

    def analyze(self, *, symbol: str, stock_name: str, notes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.configured:
            raise EssayConsensusError("DEEPSEEK_API_KEY is not configured")
        payload_notes = [{
            "topic_id": str(note["topic_id"]),
            "title": str(note.get("title") or "")[:300],
            "content": str(note.get("content") or "")[:2500],
            "created_at": note.get("created_at"),
            "author_name": str(note.get("author_name") or ""),
            "group_name": str(note.get("group_name") or ""),
        } for note in notes]
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "symbol": symbol, "stock_name": stock_name, "essays": payload_notes,
                }, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0.0,
            "max_tokens": 8000,
            "stream": False,
        }
        try:
            response = self.transport._post_with_retry(request_payload)  # noqa: SLF001 - shared audited transport.
            raw_content = self.transport._extract_content(response)  # noqa: SLF001
            parsed = self.transport._parse_json(raw_content)  # noqa: SLF001
        except EssayAnalysisError as exc:
            raise EssayConsensusError(str(exc)) from exc
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return {
            "result": self._normalize(parsed, allowed_topic_ids={str(note["topic_id"]) for note in notes}),
            "usage": {key: int(usage.get(key) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
        }

    @classmethod
    def _normalize(cls, payload: Dict[str, Any], *, allowed_topic_ids: set[str]) -> Dict[str, Any]:
        estimates = []
        for raw in payload.get("estimates") or []:
            if not isinstance(raw, dict):
                continue
            topic_id = str(raw.get("topic_id") or "").strip()
            evidence = str(raw.get("evidence") or "").strip()[:400]
            value_text = str(raw.get("value_text") or "").strip()[:240]
            if topic_id not in allowed_topic_ids or not evidence or not value_text:
                continue
            metric = str(raw.get("metric") or "other").strip().lower()
            direction = str(raw.get("direction") or "unclear").strip().lower()
            subject_relation = str(raw.get("subject_relation") or "target_stock").strip().lower()
            estimates.append({
                "topic_id": topic_id,
                "subject": str(raw.get("subject") or "目标公司").strip()[:100],
                "subject_relation": subject_relation if subject_relation in {
                    "target_stock", "consolidated", "subsidiary", "acquisition_target", "business_segment",
                } else "target_stock",
                "metric": metric if metric in _METRICS else "other",
                "period": str(raw.get("period") or "未注明").strip()[:80],
                "value_text": value_text,
                "value_low": cls._optional_number(raw.get("value_low")),
                "value_high": cls._optional_number(raw.get("value_high")),
                "unit": str(raw.get("unit") or "").strip()[:30],
                "direction": direction if direction in {"up", "down", "flat", "range", "unclear"} else "unclear",
                "evidence": evidence,
                "confidence": cls._score(raw.get("confidence")),
            })
        return {
            "summary": str(payload.get("summary") or "未提取到有效预期信息。").strip()[:1200],
            "has_explicit_expectations": bool(estimates),
            "profit_outlook": str(payload.get("profit_outlook") or "信息不足").strip()[:1000],
            "valuation_outlook": str(payload.get("valuation_outlook") or "信息不足").strip()[:1000],
            "estimates": estimates[:80],
            "consensus_points": cls._strings(payload.get("consensus_points"), 5),
            "conflicts": cls._strings(payload.get("conflicts"), 5),
            "caveats": cls._strings(payload.get("caveats"), 6),
        }

    @staticmethod
    def _optional_number(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None and str(value).strip() else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _score(value: Any) -> float:
        try:
            return round(max(0.0, min(float(value), 1.0)), 4)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _strings(value: Any, limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:300] for item in value if str(item).strip()][:limit]


class EssayConsensusService:
    """Persist, query and execute bounded stock-level expectation snapshots."""

    def __init__(self, db: Optional[DatabaseManager] = None, analyzer: Optional[EssayConsensusAnalyzer] = None):
        self.db = db or DatabaseManager.get_instance()
        self.monitor_repo = InvestmentMonitorRepository(self.db)
        self.analyzer = analyzer or EssayConsensusAnalyzer()

    def snapshot(self, symbol: str, stock_name: str, *, limit: int = 20) -> Dict[str, Any]:
        selected = self._select_notes(symbol, limit=limit)
        source_hash = self._source_hash(selected)
        with self.db.get_session() as session:
            row = session.execute(select(EssayConsensusRecord).where(EssayConsensusRecord.symbol == symbol)).scalar_one_or_none()
            return self._snapshot_dict(row, selected=selected, source_hash=source_hash)

    def enqueue(self, symbol: str, stock_name: str, *, limit: int = 20, force: bool = True) -> Dict[str, Any]:
        selected = self._select_notes(symbol, limit=limit)
        if not selected:
            raise EssayConsensusError("该股票暂无可供分析的匹配小作文")
        source_hash = self._source_hash(selected)
        topic_ids = [item["topic_id"] for item in selected]
        now = utc_naive_now()
        with self.db.get_session() as session:
            row = session.execute(select(EssayConsensusRecord).where(EssayConsensusRecord.symbol == symbol)).scalar_one_or_none()
            if row is None:
                row = EssayConsensusRecord(
                    symbol=symbol, stock_name=stock_name, status="pending", model=self.analyzer.model,
                    prompt_version=ESSAY_CONSENSUS_PROMPT_VERSION, source_count=len(topic_ids),
                    source_hash=source_hash, source_topic_ids_json=json.dumps(topic_ids, ensure_ascii=False),
                    created_at=now, updated_at=now,
                )
                session.add(row)
            elif not force and row.status == "completed" and row.source_hash == source_hash:
                return self._snapshot_dict(row, selected=selected, source_hash=source_hash)
            else:
                row.stock_name = stock_name
                row.status = "pending"
                row.model = self.analyzer.model
                row.prompt_version = ESSAY_CONSENSUS_PROMPT_VERSION
                row.source_count = len(topic_ids)
                row.source_hash = source_hash
                row.source_topic_ids_json = json.dumps(topic_ids, ensure_ascii=False)
                row.error_message = None
                row.started_at = None
                row.completed_at = None
                row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._snapshot_dict(row, selected=selected, source_hash=source_hash)

    def process_next(self) -> bool:
        now = utc_naive_now()
        with self.db.get_session() as session:
            row = session.execute(
                select(EssayConsensusRecord).where(EssayConsensusRecord.status == "pending")
                .order_by(EssayConsensusRecord.updated_at)
            ).scalars().first()
            if row is None:
                return False
            row.status = "processing"
            row.started_at = now
            row.updated_at = now
            session.commit()
            record_id = row.id
            symbol, stock_name = row.symbol, row.stock_name
            topic_ids = self._loads(row.source_topic_ids_json, [])
        notes = self._notes_by_ids(topic_ids)
        try:
            response = self.analyzer.analyze(symbol=symbol, stock_name=stock_name, notes=notes)
            with self.db.get_session() as session:
                row = session.get(EssayConsensusRecord, record_id)
                if row is None:
                    return True
                row.status = "completed"
                row.result_json = json.dumps(response["result"], ensure_ascii=False)
                row.error_message = None
                row.completed_at = utc_naive_now()
                row.updated_at = row.completed_at
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    setattr(row, key, int(response.get("usage", {}).get(key) or 0))
                session.commit()
        except Exception as exc:  # noqa: BLE001 - persist a safe task failure for the page.
            safe_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.warning("essay consensus analysis failed for %s: %s", symbol, safe_error)
            with self.db.get_session() as session:
                row = session.get(EssayConsensusRecord, record_id)
                if row is not None:
                    row.status = "failed"
                    row.error_message = safe_error
                    row.updated_at = utc_naive_now()
                    session.commit()
        return True

    def _select_notes(self, symbol: str, *, limit: int) -> List[Dict[str, Any]]:
        events = [
            event for event in self.monitor_repo.all_symbol_events(symbol=symbol, days=3650)
            if event.get("event_type") == "essay" and event.get("external_id")
        ][:max(1, min(int(limit), 20))]
        topic_ids = [str(event["external_id"]) for event in events]
        event_by_topic = {str(event["external_id"]): event for event in events}
        notes = self._notes_by_ids(topic_ids)
        note_by_id = {str(note["topic_id"]): note for note in notes}
        selected = []
        for topic_id in topic_ids:
            note = note_by_id.get(topic_id)
            if not note:
                continue
            event = event_by_topic[topic_id]
            selected.append({**note, "event_id": event.get("id"), "event_at": event.get("event_at")})
        return selected

    def _notes_by_ids(self, topic_ids: Sequence[str]) -> List[Dict[str, Any]]:
        normalized = [str(value) for value in topic_ids if str(value)]
        if not normalized:
            return []
        with self.db.get_session() as session:
            rows = session.execute(select(ResearchNote).where(ResearchNote.topic_id.in_(normalized))).scalars().all()
        return [{
            "topic_id": row.topic_id, "title": row.title, "content": row.content or "",
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "author_name": row.author_name or "", "group_name": row.group_name,
        } for row in rows]

    @staticmethod
    def _source_hash(selected: Sequence[Dict[str, Any]]) -> str:
        payload = "|".join(f"{item.get('topic_id')}:{item.get('created_at')}" for item in selected)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _snapshot_dict(self, row: Optional[EssayConsensusRecord], *, selected: Sequence[Dict[str, Any]], source_hash: str) -> Dict[str, Any]:
        selected_by_topic = {str(item["topic_id"]): item for item in selected}
        result = self._loads(row.result_json, {}) if row and row.result_json else {}
        estimates = []
        counts: Dict[str, int] = {}
        for raw in result.get("estimates") or []:
            if not isinstance(raw, dict):
                continue
            topic_id = str(raw.get("topic_id") or "")
            source = selected_by_topic.get(topic_id, {})
            metric = str(raw.get("metric") or "other")
            counts[metric] = counts.get(metric, 0) + 1
            estimates.append({**raw, "event_id": source.get("event_id"), "title": source.get("title"), "event_at": source.get("event_at")})
        stale = bool(row and row.source_hash != source_hash)
        status = "not_started" if row is None else "stale" if stale and row.status == "completed" else row.status
        source_notes = [{
            "topic_id": item["topic_id"], "event_id": item.get("event_id"), "title": item.get("title"),
            "event_at": item.get("event_at") or item.get("created_at"), "author_name": item.get("author_name"),
            "estimate_count": sum(1 for value in estimates if value.get("topic_id") == item["topic_id"]),
        } for item in selected]
        return {
            "status": status,
            "source_count": len(selected),
            "analyzed_count": int(row.source_count or 0) if row and row.status == "completed" and not stale else 0,
            "pending_count": len(selected) if status in {"pending", "processing", "stale", "not_started"} else 0,
            "model": row.model if row else self.analyzer.model,
            "prompt_version": row.prompt_version if row else ESSAY_CONSENSUS_PROMPT_VERSION,
            "summary": result.get("summary") or "",
            "has_explicit_expectations": bool(result.get("has_explicit_expectations")),
            "profit_outlook": result.get("profit_outlook") or "",
            "valuation_outlook": result.get("valuation_outlook") or "",
            "estimates": estimates,
            "metric_counts": counts,
            "consensus_points": result.get("consensus_points") or [],
            "conflicts": result.get("conflicts") or [],
            "caveats": result.get("caveats") or [],
            "source_notes": source_notes,
            "error": row.error_message if row else None,
            "updated_at": row.updated_at.isoformat() + "Z" if row and row.updated_at else None,
            "completed_at": row.completed_at.isoformat() + "Z" if row and row.completed_at else None,
        }

    @staticmethod
    def _loads(value: Optional[str], fallback: Any) -> Any:
        try:
            return json.loads(value) if value else fallback
        except (TypeError, ValueError):
            return fallback


class EssayConsensusWorker:
    """Small durable worker; at most one stock-level DeepSeek request at a time."""

    _instance: Optional["EssayConsensusWorker"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls) -> "EssayConsensusWorker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="essay-consensus-worker", daemon=True)
                self._thread.start()
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {"running": bool(self._thread and self._thread.is_alive())}

    @staticmethod
    def _run() -> None:
        service = EssayConsensusService()
        while service.process_next():
            continue
