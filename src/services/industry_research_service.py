# -*- coding: utf-8 -*-
"""Evidence-first, owner-scoped industry/company research projects."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import logging
from queue import Empty, Queue
import re
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, desc, func, or_, select

from src.data.stock_index_loader import get_index_stock_name
from src.request_identity import current_owner_id
from src.services.essay_analysis_service import DeepSeekEssayAnalyzer, EssayAnalysisError
from src.services.research_report_library_service import ResearchReportLibraryService
from src.services.run_diagnostics import sanitize_diagnostic_text
from src.storage import (
    DatabaseManager,
    IndustryResearchProjectRecord,
    MonitoringEventRecord,
    ResearchNote,
    ResearchReportRecord,
    utc_naive_now,
)

logger = logging.getLogger(__name__)

INDUSTRY_RESEARCH_PROMPT_VERSION = "industry-research-v1-evidence-ledger"
_ACTIVE_STATUSES = ("queued", "collecting", "analyzing")
_OWNER_UNSET = object()
_BLUEPRINT_CACHE_TTL_SECONDS = 300.0
_BLUEPRINT_CACHE: Dict[tuple[str, str, int], tuple[float, Dict[str, Any]]] = {}
_BLUEPRINT_CACHE_LOCK = threading.RLock()
_TERM_LIBRARY = {
    "光模块": ["光模块", "光通信", "CPO", "LPO", "硅光", "800G", "1.6T", "光芯片", "光器件"],
    "低空经济": ["低空经济", "eVTOL", "飞行汽车", "无人机", "通航", "低空空域"],
    "人工智能": ["人工智能", "AI", "大模型", "算力", "推理", "训练"],
}

_METHODOLOGY = [
    {
        "stage": "boundary", "hours": "立即", "title": "定义边界",
        "goal": "提交课题后立即识别行业口径、相邻领域、关键词与必答问题。",
        "deliverables": ["行业定义与口径", "核心术语/同义词", "研究问题清单"],
    },
    {
        "stage": "chain", "hours": "数秒至数分钟", "title": "建立产业链",
        "goal": "并行召回六类数据，沿原料、设备、部件、系统、客户和应用场景组织证据。",
        "deliverables": ["产业链地图", "成本与价值量线索", "关键参与者候选"],
    },
    {
        "stage": "validation", "hours": "后台并行", "title": "验证龙头与趋势",
        "goal": "证据底稿生成后立即交叉验证研报、公告、财务、行情和非结构化语料。",
        "deliverables": ["公司对比表", "趋势/拐点证据", "分歧与反证"],
    },
    {
        "stage": "synthesis", "hours": "就绪即输出", "title": "形成结论",
        "goal": "数据和模型一旦就绪立即输出首版报告，后续新证据持续补强。",
        "deliverables": ["研究报告", "访谈问题", "证伪条件与监控表"],
    },
]

_SYSTEM_PROMPT = """你是严谨的中国资本市场行业研究负责人。只能使用输入证据，不得补造公司、数字、来源、市场份额或结论。
输入中的 evidence_id 是唯一允许引用的依据；每项判断必须列 evidence_ids。证据不足就明确写“待验证”。
严格输出 JSON object，不输出 Markdown。结构：
{
  "one_sentence":"一句话结论，证据不足时说明当前只能形成研究假设",
  "industry_boundary":{"included":[],"excluded":[],"definition":""},
  "chain_nodes":[{"stage":"","role":"","economics":"","participants":[],"evidence_ids":[]}],
  "trends":[{"claim":"","horizon":"短期|中期|长期","drivers":[],"confidence":"high|medium|low","evidence_ids":[]}],
  "leaders":[{"name":"","symbol":"","rationale":"","open_questions":[],"evidence_ids":[]}],
  "bottlenecks":[{"issue":"","why_it_matters":"","validation":"","evidence_ids":[]}],
  "applications":[{"scenario":"","demand_logic":"","evidence_ids":[]}],
  "disagreements":[{"question":"","sides":[],"evidence_ids":[]}],
  "falsification_conditions":[],
  "monitoring_indicators":[{"indicator":"","frequency":"","source":""}],
  "interview_questions":[],
  "open_questions":[],
  "executive_summary":"500-1000字，分事实、推断、未知三层总结",
  "caveats":[]
}
participant 和 leader 只能来自证据中明确出现的公司或股票。"""


class IndustryResearchError(RuntimeError):
    """Safe error surfaced by the industry research API."""


def _loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
        return parsed
    except (TypeError, ValueError):
        return fallback


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class IndustryResearchService:
    """Build a bounded evidence snapshot and synthesize a research deliverable."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or DatabaseManager.get_instance()

    @staticmethod
    def methodology() -> Dict[str, Any]:
        return {
            "name": "极速行业调研法",
            "principles": [
                "先定义边界，再搜材料，避免把相邻行业混成一个结论。",
                "事实、机构观点、市场传闻分层，所有重要判断保留原文入口。",
                "用反证和待验证问题结束研究，而不是用资料数量冒充确定性。",
                "先输出可用底稿，再由后台持续补强，不设置人为等待时间。",
            ],
            "stages": _METHODOLOGY,
            "required_questions": [
                "行业如何定义，哪些相邻领域应排除？", "产业链如何分层，钱和议价权流向哪里？",
                "需求来自哪些应用场景，真实驱动指标是什么？", "技术、标准和供需正在发生什么变化？",
                "哪些公司可能是龙头，领先依据能否交叉验证？", "最大瓶颈和痛点是什么？",
                "什么事实会证伪当前判断，后续监控什么？",
            ],
            "evidence_rule": "官方/公告/标准 > 经授权或可追溯研报与新闻 > 机构段子与公开评论；低等级证据可生成线索，不能单独形成事实结论。",
        }

    def blueprint(self, topic: str, *, lookback_days: int = 730) -> Dict[str, Any]:
        normalized = self._normalize_topic(topic)
        days = max(30, min(int(lookback_days), 3650))
        cache_key = (str(getattr(self.db, "_db_url", "default")), normalized.casefold(), days)
        now = time.monotonic()
        with _BLUEPRINT_CACHE_LOCK:
            cached = _BLUEPRINT_CACHE.get(cache_key)
            if cached is not None and now - cached[0] < _BLUEPRINT_CACHE_TTL_SECONDS:
                return cached[1]

        terms = self._expand_terms(normalized)
        # Hold a per-process lock during the cold scan so several visitors do
        # not launch the same expensive SQLite LIKE queries simultaneously.
        with _BLUEPRINT_CACHE_LOCK:
            cached = _BLUEPRINT_CACHE.get(cache_key)
            if cached is not None and time.monotonic() - cached[0] < _BLUEPRINT_CACHE_TTL_SECONDS:
                return cached[1]
            snapshot = self.collect_evidence(normalized, terms=terms, lookback_days=days)
            result = {
                "topic": normalized,
                "research_type": "industry",
                "lookback_days": snapshot["lookback_days"],
                "query_terms": terms,
                "methodology": self.methodology(),
                "snapshot": snapshot,
                "generated_at": _iso(utc_naive_now()),
                "cache_ttl_seconds": int(_BLUEPRINT_CACHE_TTL_SECONDS),
            }
            _BLUEPRINT_CACHE[cache_key] = (time.monotonic(), result)
            if len(_BLUEPRINT_CACHE) > 24:
                oldest_key = min(_BLUEPRINT_CACHE, key=lambda key: _BLUEPRINT_CACHE[key][0])
                _BLUEPRINT_CACHE.pop(oldest_key, None)
            return result

    def create_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        topic = self._normalize_topic(payload.get("topic"))
        research_type = str(payload.get("research_type") or "industry").strip().lower()
        if research_type not in {"industry", "company"}:
            raise IndustryResearchError("研究类型仅支持行业或公司")
        lookback_days = max(30, min(int(payload.get("lookback_days") or 730), 3650))
        objective = str(payload.get("objective") or f"尽快理解{topic}的产业脉络、趋势、龙头、痛点和应用场景").strip()[:2000]
        terms = self._expand_terms(topic, payload.get("query_terms"))
        project_id = uuid.uuid4().hex
        with self.db.get_session() as session:
            session.add(IndustryResearchProjectRecord(
                project_id=project_id,
                owner_id=current_owner_id(),
                topic=topic,
                research_type=research_type,
                objective=objective,
                lookback_days=lookback_days,
                query_json=json.dumps({"terms": terms}, ensure_ascii=False),
            ))
            session.commit()
        IndustryResearchTaskManager.get_instance().start()
        IndustryResearchTaskManager.get_instance().enqueue(project_id)
        project = self.get_project(project_id)
        if project is None:
            raise IndustryResearchError("课题创建后无法读取")
        return project

    def list_projects(self, limit: int = 30) -> Dict[str, Any]:
        owner_id = current_owner_id()
        with self.db.get_session() as session:
            rows = session.execute(
                select(IndustryResearchProjectRecord)
                .where(self._owner_clause(owner_id))
                .order_by(desc(IndustryResearchProjectRecord.id))
                .limit(max(1, min(int(limit), 100)))
            ).scalars().all()
        return {"items": [self._serialize_project(row, include_snapshot=False) for row in rows], "total": len(rows)}

    def get_project(self, project_id: str, *, owner_id: object = _OWNER_UNSET) -> Optional[Dict[str, Any]]:
        effective_owner = current_owner_id() if owner_id is _OWNER_UNSET else owner_id
        with self.db.get_session() as session:
            row = session.execute(select(IndustryResearchProjectRecord).where(
                IndustryResearchProjectRecord.project_id == str(project_id),
                self._owner_clause(effective_owner),
            )).scalar_one_or_none()
            return self._serialize_project(row, include_snapshot=True) if row is not None else None

    def collect_evidence(
        self, topic: str, *, terms: Sequence[str], lookback_days: int,
    ) -> Dict[str, Any]:
        days = max(30, min(int(lookback_days), 3650))
        cutoff_dt = utc_naive_now() - timedelta(days=days)
        cutoff_date = cutoff_dt.date()
        report_where = and_(ResearchReportRecord.trade_date >= cutoff_date, self._term_clause(
            terms, ResearchReportRecord.title, ResearchReportRecord.abstract, ResearchReportRecord.industry,
        ))
        # Long note bodies dominate the local corpus. Search expanded aliases in
        # the compact title, but scan the full body only for the user's primary
        # topic. This keeps broad industry aliases useful without multiplying
        # full-text scans by every synonym.
        note_where = and_(ResearchNote.created_at >= cutoff_dt, or_(
            self._term_clause(terms, ResearchNote.title),
            self._term_clause([topic], ResearchNote.content),
        ))
        event_where = and_(
            MonitoringEventRecord.event_at >= cutoff_dt,
            MonitoringEventRecord.event_type != "realtime_quote",
            MonitoringEventRecord.source_key != "zsxq.essays",
            self._term_clause(terms, MonitoringEventRecord.title, MonitoringEventRecord.summary, MonitoringEventRecord.tags_json),
        )
        with self.db.get_session() as session:
            report_rows = session.execute(select(
                ResearchReportRecord, func.count(ResearchReportRecord.id).over().label("match_count"),
            ).where(report_where).order_by(
                desc(ResearchReportRecord.trade_date), desc(ResearchReportRecord.id),
            ).limit(40)).all()
            note_rows = session.execute(select(
                ResearchNote, func.count(ResearchNote.id).over().label("match_count"),
            ).where(note_where).order_by(
                desc(ResearchNote.created_at), desc(ResearchNote.id),
            ).limit(60)).all()
            media_payloads = session.execute(select(ResearchNote.files_json).where(
                note_where,
                ResearchNote.files_json.is_not(None),
                ResearchNote.files_json != "[]",
            ).limit(5000)).scalars().all()
            event_rows = session.execute(select(
                MonitoringEventRecord, func.count(MonitoringEventRecord.id).over().label("match_count"),
            ).where(event_where).order_by(
                desc(MonitoringEventRecord.importance_score), desc(MonitoringEventRecord.event_at),
            ).limit(120)).all()
            event_groups = session.execute(select(
                MonitoringEventRecord.source_key,
                MonitoringEventRecord.source_type,
                MonitoringEventRecord.event_type,
                func.count(MonitoringEventRecord.id),
            ).where(event_where).group_by(
                MonitoringEventRecord.source_key,
                MonitoringEventRecord.source_type,
                MonitoringEventRecord.event_type,
            )).all()

        reports = [row for row, _ in report_rows]
        notes = [row for row, _ in note_rows]
        events = [row for row, _ in event_rows]
        report_count = int(report_rows[0][1] or 0) if report_rows else 0
        note_count = int(note_rows[0][1] or 0) if note_rows else 0
        event_count = int(event_rows[0][1] or 0) if event_rows else 0

        evidence: List[Dict[str, Any]] = []
        source_counts: Counter[str] = Counter()
        monthly: Counter[str] = Counter()
        company_counts: Counter[tuple[str, str]] = Counter()

        for source_key, source_type, event_type, count in event_groups:
            source_counts[self._event_bucket_values(source_key, source_type, event_type)] += int(count or 0)

        for row in reports:
            evidence.append({
                "evidence_id": f"report:{row.id}", "kind": "broker_report", "source": row.broker or row.source,
                "title": row.title, "summary": (row.abstract or "")[:500], "date": _iso(row.trade_date),
                "url": row.pdf_url, "symbol": row.ts_code, "company": row.company_name,
                "evidence_level": "reported", "original_available": bool(row.pdf_url),
            })
            source_counts["broker_reports"] += 1
            monthly[row.trade_date.strftime("%Y-%m")] += 1
            if row.company_name or row.ts_code:
                company_counts[(row.ts_code or "", row.company_name or get_index_stock_name(row.ts_code or "") or "")] += 1

        media_files = sum(
            len(files) for files in (_loads(value, []) for value in media_payloads) if isinstance(files, list)
        )
        for row in notes:
            files = _loads(row.files_json, [])
            evidence.append({
                "evidence_id": f"note:{row.topic_id}", "kind": "institution_note", "source": row.group_name,
                "title": row.title, "summary": (row.content or "")[:500], "date": _iso(row.created_at),
                "url": None, "symbol": row.symbol_codes, "company": None,
                "evidence_level": "unverified", "original_available": True, "file_count": len(files) if isinstance(files, list) else 0,
            })
            source_counts["institution_notes"] += 1
            monthly[row.created_at.strftime("%Y-%m")] += 1
            for code in self._symbol_codes(row.symbol_codes):
                name = get_index_stock_name(code)
                if name:
                    company_counts[(code, name)] += 1

        for row in events:
            bucket = self._event_bucket(row)
            evidence.append({
                "evidence_id": f"event:{row.id}", "kind": bucket, "source": row.source_name,
                "title": row.title, "summary": (row.summary or "")[:500], "date": _iso(row.event_at),
                "url": row.url, "symbol": row.symbol_codes, "company": None,
                "evidence_level": self._event_evidence_level(row), "original_available": bool(row.url or row.raw_payload),
                "importance": int(row.importance_score or 0), "confidence": float(row.confidence_score or 0),
            })
            monthly[row.event_at.strftime("%Y-%m")] += 1
            for code in self._symbol_codes(row.symbol_codes):
                name = get_index_stock_name(code)
                if name:
                    company_counts[(code, name)] += 1

        evidence.sort(key=lambda item: (item.get("importance", 50), item.get("date") or ""), reverse=True)
        source_rows = [
            self._coverage("broker_reports", "券商研报与 PDF", report_count, "reported"),
            self._coverage("institution_notes", "机构段子、录音与文件", note_count, "unverified", media_files=media_files),
            self._coverage("announcements", "公司公告与治理事实", source_counts["announcements"], "factual"),
            self._coverage("market_financial", "行情、财务与资金", source_counts["market_financial"], "factual"),
            self._coverage("enterprise", "企业与工商事实", source_counts["enterprise"], "factual"),
            self._coverage("news_comments", "新闻与公开评论", source_counts["news_comments"], "mixed"),
        ]
        companies = [{"symbol": code, "name": name or code or "待识别", "evidence_count": count} for (code, name), count in company_counts.most_common(20) if code or name]
        timeline = [{"month": month, "count": count} for month, count in sorted(monthly.items())[-18:]]
        source_hash = sha256(json.dumps(
            [(item["evidence_id"], item.get("date"), item.get("title")) for item in evidence],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        try:
            report_library = ResearchReportLibraryService.get_instance().status()
        except Exception:  # noqa: BLE001 - a missing optional report library must not break research.
            report_library = {"status": "unavailable", "total": 0, "pdf_count": 0}
        return {
            "topic": topic, "lookback_days": days, "query_terms": list(terms),
            "totals": {"evidence": report_count + note_count + event_count, "reports": report_count, "notes": note_count, "events": event_count, "media_files": media_files},
            "coverage": source_rows, "companies": companies, "timeline": timeline,
            "evidence": evidence[:180], "source_hash": source_hash, "report_library": report_library,
            "cutoff": _iso(cutoff_dt), "collected_at": _iso(utc_naive_now()),
        }

    def analyze_snapshot(self, topic: str, objective: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        analyzer = DeepSeekEssayAnalyzer()
        if not analyzer.configured:
            return self._evidence_only_report(topic, snapshot, "AI 服务未配置，已保留完整证据工作台，可稍后重新分析。")
        compact_evidence = []
        for item in snapshot.get("evidence", [])[:120]:
            compact_evidence.append({
                "evidence_id": item.get("evidence_id"), "kind": item.get("kind"), "source": item.get("source"),
                "date": item.get("date"), "title": item.get("title"), "summary": str(item.get("summary") or "")[:700],
                "symbol": item.get("symbol"), "company": item.get("company"), "evidence_level": item.get("evidence_level"),
            })
        request = {
            "model": analyzer.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "topic": topic, "objective": objective, "query_terms": snapshot.get("query_terms"),
                    "coverage": snapshot.get("coverage"), "company_candidates": snapshot.get("companies"),
                    "evidence": compact_evidence,
                }, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
            "temperature": 0.1, "max_tokens": 12000, "stream": False,
        }
        response = analyzer._post_with_retry(request)
        report = analyzer._parse_json(analyzer._extract_content(response))
        report = self._sanitize_report(report, {item["evidence_id"] for item in compact_evidence})
        report["prompt_version"] = INDUSTRY_RESEARCH_PROMPT_VERSION
        report["generated_at"] = _iso(utc_naive_now())
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        report["usage"] = {key: int(usage.get(key) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        return report

    @staticmethod
    def _sanitize_report(report: Dict[str, Any], valid_ids: set[str]) -> Dict[str, Any]:
        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                result = {key: sanitize(item) for key, item in value.items()}
                if "evidence_ids" in result:
                    raw_ids = result.get("evidence_ids") if isinstance(result.get("evidence_ids"), list) else []
                    result["evidence_ids"] = [str(value) for value in raw_ids if str(value) in valid_ids]
                return result
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            return value
        return sanitize(report) if isinstance(report, dict) else {}

    @staticmethod
    def _evidence_only_report(topic: str, snapshot: Dict[str, Any], caveat: str) -> Dict[str, Any]:
        return {
            "one_sentence": f"已为“{topic}”建立证据底稿；当前结论待 AI 综合或人工核验。",
            "industry_boundary": {"included": snapshot.get("query_terms", []), "excluded": [], "definition": "待验证"},
            "chain_nodes": [], "trends": [], "leaders": [], "bottlenecks": [], "applications": [], "disagreements": [],
            "falsification_conditions": ["核心需求指标连续走弱", "公司公告或财务数据否定现有机构叙事"],
            "monitoring_indicators": [], "interview_questions": ["客户真正为哪一项性能或成本改善付费？", "行业最大产能或技术瓶颈在哪里？"],
            "open_questions": ["产业链价值量与议价权如何分布？", "龙头领先来自技术、客户、产能还是成本？"],
            "executive_summary": f"本课题已召回 {snapshot.get('totals', {}).get('evidence', 0)} 条相关证据。{caveat}",
            "caveats": [caveat], "prompt_version": INDUSTRY_RESEARCH_PROMPT_VERSION, "generated_at": _iso(utc_naive_now()),
        }

    @staticmethod
    def _normalize_topic(value: Any) -> str:
        topic = re.sub(r"\s+", " ", str(value or "").strip())[:200]
        if len(topic) < 2:
            raise IndustryResearchError("请输入至少两个字的行业或公司名称")
        return topic

    @staticmethod
    def _expand_terms(topic: str, extra: Any = None) -> List[str]:
        values = [topic]
        for key, terms in _TERM_LIBRARY.items():
            if key in topic or topic in key:
                values.extend(terms)
        if isinstance(extra, (list, tuple)):
            values.extend(str(item).strip() for item in extra)
        return list(dict.fromkeys(value[:80] for value in values if value))[:15]

    @staticmethod
    def _term_clause(terms: Sequence[str], *columns: Any):
        clauses = []
        for term in terms:
            escaped = str(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.extend(column.like(pattern, escape="\\") for column in columns)
        return or_(*clauses)

    @staticmethod
    def _symbol_codes(value: Any) -> List[str]:
        return list(dict.fromkeys(re.findall(r"\b\d{6}(?:\.(?:SH|SZ|BJ))?\b", str(value or "").upper())))[:20]

    @staticmethod
    def _event_bucket(row: MonitoringEventRecord) -> str:
        return IndustryResearchService._event_bucket_values(row.source_key, row.source_type, row.event_type)

    @staticmethod
    def _event_bucket_values(source_key: Any, source_type: Any, event_type: Any) -> str:
        key = f"{source_key} {source_type} {event_type}".lower()
        if "cninfo" in key or "announcement" in key or "governance" in key:
            return "announcements"
        if any(term in key for term in ("finance", "financial", "market", "moneyflow", "technical", "quote", "tushare.daily")):
            return "market_financial"
        if any(term in key for term in ("tianyan", "enterprise", "company_fact", "company_profile", "equity")):
            return "enterprise"
        if any(term in key for term in ("news", "comment", "guba", "eastmoney", "cls", "sina")):
            return "news_comments"
        if "zsxq" in key or "essay" in key:
            return "institution_notes"
        return "other_events"

    @staticmethod
    def _event_evidence_level(row: MonitoringEventRecord) -> str:
        metrics = _loads(row.metrics_json, {})
        level = str(metrics.get("evidence_level") or "").strip().lower() if isinstance(metrics, dict) else ""
        if level:
            return level
        return "unverified" if row.source_key == "zsxq.essays" else "reported"

    @staticmethod
    def _coverage(key: str, name: str, count: int, evidence_level: str, **extra: Any) -> Dict[str, Any]:
        return {"key": key, "name": name, "count": int(count), "status": "covered" if count else "missing", "evidence_level": evidence_level, **extra}

    @staticmethod
    def _owner_clause(owner_id: object):
        return IndustryResearchProjectRecord.owner_id == owner_id if owner_id else IndustryResearchProjectRecord.owner_id.is_(None)

    @staticmethod
    def _serialize_project(row: IndustryResearchProjectRecord, *, include_snapshot: bool) -> Dict[str, Any]:
        payload = {
            "project_id": row.project_id, "topic": row.topic, "research_type": row.research_type,
            "objective": row.objective, "lookback_days": int(row.lookback_days or 0), "status": row.status,
            "progress": int(row.progress or 0), "stage": row.stage, "message": row.message,
            "query": _loads(row.query_json, {}), "report": _loads(row.report_json, None),
            "source_hash": row.source_hash, "error": row.error_message,
            "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
            "started_at": _iso(row.started_at), "completed_at": _iso(row.completed_at),
        }
        payload["snapshot"] = _loads(row.evidence_snapshot_json, {}) if include_snapshot else None
        return payload


class IndustryResearchTaskManager:
    """Durable two-worker queue; projects survive route changes and restarts."""

    _instance: Optional["IndustryResearchTaskManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db: Optional[DatabaseManager] = None, worker_count: int = 2) -> None:
        self.db = db or DatabaseManager.get_instance()
        self.worker_count = max(1, min(int(worker_count), 3))
        self._queue: Queue[str] = Queue()
        self._workers: List[threading.Thread] = []
        self._scheduled: set[str] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._started = False

    @classmethod
    def get_instance(cls) -> "IndustryResearchTaskManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    def start(self) -> None:
        with self._lock:
            if self._started and any(worker.is_alive() for worker in self._workers):
                return
            self._stop.clear()
            self._started = True
            self._workers = [threading.Thread(target=self._worker_loop, name=f"industry-research-{index + 1}", daemon=True) for index in range(self.worker_count)]
            for worker in self._workers:
                worker.start()
        with self.db.get_session() as session:
            session.query(IndustryResearchProjectRecord).filter(
                IndustryResearchProjectRecord.status.in_(("collecting", "analyzing")),
            ).update({
                IndustryResearchProjectRecord.status: "queued", IndustryResearchProjectRecord.progress: 0,
                IndustryResearchProjectRecord.stage: "boundary", IndustryResearchProjectRecord.message: "服务重启后已恢复到后台队列",
                IndustryResearchProjectRecord.updated_at: utc_naive_now(),
            }, synchronize_session=False)
            pending = session.execute(select(IndustryResearchProjectRecord.project_id).where(
                IndustryResearchProjectRecord.status == "queued",
            ).order_by(IndustryResearchProjectRecord.id)).scalars().all()
            session.commit()
        for project_id in pending:
            self.enqueue(project_id)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for worker in list(self._workers):
            if worker.is_alive():
                worker.join(timeout=max(0.0, timeout / max(1, len(self._workers))))
        with self._lock:
            self._workers = []
            self._started = False

    def enqueue(self, project_id: str) -> None:
        with self._lock:
            if project_id in self._scheduled:
                return
            self._scheduled.add(project_id)
            self._queue.put(project_id)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                project_id = self._queue.get(timeout=0.25)
            except Empty:
                continue
            try:
                self._execute(project_id)
            finally:
                with self._lock:
                    self._scheduled.discard(project_id)
                self._queue.task_done()

    def _execute(self, project_id: str) -> None:
        with self.db.get_session() as session:
            row = session.execute(select(IndustryResearchProjectRecord).where(
                IndustryResearchProjectRecord.project_id == project_id,
            )).scalar_one_or_none()
            if row is None or row.status != "queued":
                return
            row.status = "collecting"; row.progress = 12; row.stage = "boundary"
            row.message = "正在定义边界并从六类本地数据中召回证据"
            row.started_at = utc_naive_now(); row.updated_at = utc_naive_now()
            topic = row.topic; objective = row.objective; days = row.lookback_days
            terms = _loads(row.query_json, {}).get("terms") or [topic]
            session.commit()
        service = IndustryResearchService(self.db)
        try:
            default_terms = service._expand_terms(topic)
            if list(terms) == default_terms:
                snapshot = service.blueprint(topic, lookback_days=days)["snapshot"]
            else:
                snapshot = service.collect_evidence(topic, terms=terms, lookback_days=days)
            with self.db.get_session() as session:
                row = session.execute(select(IndustryResearchProjectRecord).where(IndustryResearchProjectRecord.project_id == project_id)).scalar_one()
                row.status = "analyzing"; row.progress = 62; row.stage = "validation"
                row.message = "证据底稿已完成，正在交叉验证趋势、龙头、痛点与应用"
                row.evidence_snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
                row.source_hash = snapshot.get("source_hash")
                row.updated_at = utc_naive_now(); session.commit()
            report = service.analyze_snapshot(topic, objective, snapshot)
            with self.db.get_session() as session:
                row = session.execute(select(IndustryResearchProjectRecord).where(IndustryResearchProjectRecord.project_id == project_id)).scalar_one()
                row.status = "completed"; row.progress = 100; row.stage = "synthesis"
                row.message = "首版研究底稿、证据矩阵与结论报告已生成，后续可随新证据继续更新"
                row.report_json = json.dumps(report, ensure_ascii=False, default=str)
                row.error_message = None; row.completed_at = utc_naive_now(); row.updated_at = utc_naive_now(); session.commit()
        except Exception as exc:  # noqa: BLE001 - failures are persisted and retryable.
            safe = sanitize_diagnostic_text(exc, max_length=440) or "行业研究任务失败"
            logger.warning("[industry-research] project %s failed: %s", project_id, safe)
            with self.db.get_session() as session:
                row = session.execute(select(IndustryResearchProjectRecord).where(IndustryResearchProjectRecord.project_id == project_id)).scalar_one_or_none()
                if row is not None:
                    row.status = "failed"; row.progress = 100; row.message = "课题处理失败，可重新创建后重试"
                    row.error_message = f"{type(exc).__name__}: {safe}"; row.completed_at = utc_naive_now(); row.updated_at = utc_naive_now(); session.commit()
