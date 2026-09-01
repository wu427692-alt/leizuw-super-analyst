#!/usr/bin/env python3
"""Replay the final release gate for one frozen industry-research project.

This is an intentionally narrow production-repair tool.  It reads an existing
terminal project, replays only deterministic storage sanitation, the final
independent editorial review, reconciliation, quality assurance and report
assembly, then optionally replaces the stored report with a compare-and-swap
update.

It never calls the source collector, ZSXQ synchronization, report-library
synchronization, or the audio transcription pipeline.  ``--dry-run`` is the
safe default.  ``--commit`` first writes an exact, compressed backup of the
original row payload and only then performs one atomic CAS update.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterator, List, Optional, Sequence


# Support both normal repository execution and ``docker cp`` to /tmp followed
# by execution with /app as the container working directory.
for _candidate in (Path.cwd(), Path(__file__).resolve().parents[1]):
    if (_candidate / "src").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import src.services.essay_analysis_service as essay_analysis_module  # noqa: E402
import src.services.industry_research_service as research_module  # noqa: E402
from src.services.industry_research_service import IndustryResearchService  # noqa: E402
from src.services.industry_research_visualization_service import (  # noqa: E402
    IndustryResearchVisualizationService,
)
from src.storage import (  # noqa: E402
    DatabaseManager,
    IndustryResearchProjectRecord,
    utc_naive_now,
)


PROJECT_ID_ENV = "INDUSTRY_RESEARCH_REPAIR_PROJECT_ID"
BACKUP_DIR_ENV = "INDUSTRY_RESEARCH_REPAIR_BACKUP_DIR"
TERMINAL_STATUSES = {"completed", "limited", "failed"}
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


class SnapshotRepairError(RuntimeError):
    """Raised before any target project mutation when a safety gate fails."""


@dataclass(frozen=True)
class FrozenProject:
    row_id: int
    project_id: str
    owner_id: Optional[str]
    topic: str
    research_type: str
    objective: str
    lookback_days: int
    status: str
    progress: int
    stage: str
    message: str
    error_message: Optional[str]
    query_json: str
    evidence_snapshot_json: str
    report_json: str
    source_hash: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    snapshot: Dict[str, Any]
    report: Dict[str, Any]
    report_sha256: str
    snapshot_sha256: str


class ReadOnlyProjectDatabase:
    """Minimal read-only session provider for pre-commit validation.

    ``DatabaseManager`` performs idempotent schema maintenance during
    construction.  That is appropriate for the running application, but a
    dry-run/failing one-off repair must not acquire a write path merely to
    inspect one frozen row.  SQLite URI ``mode=ro`` enforces that boundary at
    the database driver rather than relying only on application discipline.
    """

    def __init__(self, database_path: Path) -> None:
        resolved = database_path.expanduser().resolve()
        if not resolved.is_file():
            raise SnapshotRepairError(f"数据库文件不存在：{resolved}")
        uri = f"sqlite:///file:{resolved}?mode=ro&uri=true"
        self._engine = create_engine(
            uri,
            echo=False,
            pool_pre_ping=True,
            connect_args={"timeout": 5.0},
        )
        self._sessions = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
        )

    @contextmanager
    def get_session(self) -> Iterator[Any]:
        session = self._sessions()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    def close(self) -> None:
        self._engine.dispose()


def _database_path() -> Path:
    value = str(os.getenv("DATABASE_PATH") or "./data/stock_analysis.db").strip()
    if not value or value == ":memory:":
        raise SnapshotRepairError("一次性生产修复只支持现有 SQLite 文件数据库")
    return Path(value)


def _json_object(raw: Any, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError) as exc:
        raise SnapshotRepairError(f"{label} 不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise SnapshotRepairError(f"{label} 必须是 JSON object")
    return value


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _stable_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _expected_chapter_specs(research_type: str) -> Sequence[Dict[str, Any]]:
    return (
        research_module._COMPANY_LONG_FORM_CHAPTERS
        if str(research_type or "").strip().lower() == "company"
        else research_module._LONG_FORM_CHAPTERS
    )


def _validate_chapters(
    chapters: Any,
    *,
    research_type: str,
) -> List[Dict[str, Any]]:
    if not isinstance(chapters, list) or len(chapters) != 8:
        raise SnapshotRepairError("原报告必须恰好包含 8 个章节")
    rows = [item for item in chapters if isinstance(item, dict)]
    if len(rows) != 8:
        raise SnapshotRepairError("原报告章节必须全部为 JSON object")
    by_id: Dict[str, Dict[str, Any]] = {}
    for chapter in rows:
        chapter_id = str(chapter.get("chapter_id") or "").strip()
        if not chapter_id:
            raise SnapshotRepairError("原报告存在缺少 chapter_id 的章节")
        if chapter_id in by_id:
            raise SnapshotRepairError(f"原报告存在重复章节：{chapter_id}")
        if not str(chapter.get("body_markdown") or "").strip():
            raise SnapshotRepairError(f"原报告章节正文为空：{chapter_id}")
        by_id[chapter_id] = chapter

    expected_ids = [
        str(spec.get("chapter_id") or "")
        for spec in _expected_chapter_specs(research_type)
    ]
    if len(expected_ids) != 8 or set(by_id) != set(expected_ids):
        raise SnapshotRepairError(
            "原报告章节集合与当前八章契约不一致："
            f"expected={expected_ids}, actual={list(by_id)}"
        )
    return [deepcopy(by_id[chapter_id]) for chapter_id in expected_ids]


def load_frozen_project(
    db: DatabaseManager,
    service: IndustryResearchService,
    project_id: str,
) -> FrozenProject:
    """Load and strictly validate one terminal row without mutating it."""

    with db.get_session() as session:
        row = session.execute(
            select(IndustryResearchProjectRecord).where(
                IndustryResearchProjectRecord.project_id == project_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise SnapshotRepairError(f"项目不存在：{project_id}")
        values = {
            "row_id": int(row.id),
            "project_id": str(row.project_id),
            "owner_id": row.owner_id,
            "topic": str(row.topic or ""),
            "research_type": str(row.research_type or "industry"),
            "objective": str(row.objective or ""),
            "lookback_days": int(row.lookback_days or 0),
            "status": str(row.status or ""),
            "progress": int(row.progress or 0),
            "stage": str(row.stage or ""),
            "message": str(row.message or ""),
            "error_message": row.error_message,
            "query_json": str(row.query_json or "{}"),
            "evidence_snapshot_json": str(row.evidence_snapshot_json or "{}"),
            "report_json": str(row.report_json or "{}"),
            "source_hash": str(row.source_hash or ""),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    if values["status"] not in TERMINAL_STATUSES:
        raise SnapshotRepairError(
            f"项目不是终态，禁止修复：status={values['status']}"
        )
    if not values["topic"]:
        raise SnapshotRepairError("项目 topic 为空")
    if values["updated_at"] is None:
        raise SnapshotRepairError("项目 updated_at 为空，无法执行 CAS")

    snapshot = _json_object(values["evidence_snapshot_json"], "evidence_snapshot_json")
    report = _json_object(values["report_json"], "report_json")
    _validate_chapters(report.get("chapters"), research_type=values["research_type"])

    row_hash = values["source_hash"]
    snapshot_hash = str(snapshot.get("source_hash") or "")
    report_hash = str(report.get("evidence_snapshot_hash") or "")
    if not row_hash or not re.fullmatch(r"[0-9a-f]{64}", row_hash, re.IGNORECASE):
        raise SnapshotRepairError("项目 source_hash 缺失或格式错误")
    if not (row_hash == snapshot_hash == report_hash):
        raise SnapshotRepairError(
            "冻结快照哈希不一致："
            f"row={row_hash}, snapshot={snapshot_hash}, report={report_hash}"
        )
    recomputed_hash = service._snapshot_hash(snapshot)
    if recomputed_hash != row_hash:
        raise SnapshotRepairError(
            "当前代码重算的快照哈希与存储值不一致，禁止在未知规则漂移下修复："
            f"stored={row_hash}, recomputed={recomputed_hash}"
        )
    if not isinstance(snapshot.get("evidence"), list) or not snapshot["evidence"]:
        raise SnapshotRepairError("冻结快照没有 evidence，禁止生成空洞报告")
    if str(snapshot.get("topic") or values["topic"]).strip() != values["topic"].strip():
        raise SnapshotRepairError("冻结快照 topic 与项目行不一致")
    if not isinstance(report.get("visualizations"), list):
        raise SnapshotRepairError("原报告 visualizations 不是数组，无法保留图表资产")

    return FrozenProject(
        **values,
        snapshot=snapshot,
        report=report,
        report_sha256=_text_sha256(values["report_json"]),
        snapshot_sha256=_text_sha256(values["evidence_snapshot_json"]),
    )


def _compact_editor_evidence(
    service: IndustryResearchService,
    snapshot: Dict[str, Any],
) -> List[Dict[str, Any]]:
    evidence = [
        item for item in snapshot.get("evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    by_id = {str(item["evidence_id"]): item for item in evidence}
    governing_ids = list(dict.fromkeys(
        str(evidence_id)
        for fact in snapshot.get("governing_statutory_facts") or []
        if isinstance(fact, dict)
        for evidence_id in (
            fact.get("supporting_evidence_ids")
            or fact.get("evidence_ids")
            or []
        )
        if str(evidence_id) in by_id
    ))
    governing_set = set(governing_ids)
    prioritized = [
        *[by_id[evidence_id] for evidence_id in governing_ids],
        *[
            item for item in evidence
            if str(item.get("evidence_id") or "") not in governing_set
        ],
    ]
    compact = service._compact_model_evidence(
        prioritized,
        limit=research_module.INDUSTRY_RESEARCH_SYNTHESIS_EVIDENCE_LIMIT,
    )
    if not compact:
        raise SnapshotRepairError("冻结快照无法形成最终总编证据包")
    return compact


@contextmanager
def _suppress_llm_usage_writes() -> Iterator[None]:
    """Keep dry-run/failure paths free of incidental usage-table writes.

    The final report still accumulates provider-reported usage returned by the
    editorial call.  This process-local patch is safe because the repair tool
    runs in its own Python process and is restored immediately afterward.
    """

    original = essay_analysis_module.persist_llm_usage
    essay_analysis_module.persist_llm_usage = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        essay_analysis_module.persist_llm_usage = original


def _chapter_failure_ids(report: Dict[str, Any]) -> List[str]:
    chapters = report.get("chapters") if isinstance(report.get("chapters"), list) else []
    failures: List[str] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        chapter_id = str(chapter.get("chapter_id") or "")
        validation = (
            chapter.get("citation_validation")
            if isinstance(chapter.get("citation_validation"), dict)
            else {}
        )
        if (
            chapter.get("validation_failures")
            or validation.get("storage_validation_acceptable") is False
            or (
                validation.get("revision_attempted")
                and not validation.get("revision_accepted")
            )
        ):
            failures.append(chapter_id)
    qa = report.get("quality_assurance") if isinstance(report.get("quality_assurance"), dict) else {}
    metrics = qa.get("metrics") if isinstance(qa.get("metrics"), dict) else {}
    failures.extend(str(item) for item in metrics.get("chapter_validation_failures") or [])
    return list(dict.fromkeys(item for item in failures if item))


def _review_counts(review: Any) -> Dict[str, int]:
    value = review if isinstance(review, dict) else {}
    unresolved_numeric = [
        item for item in value.get("numeric_conflicts") or []
        if not IndustryResearchService._review_issue_resolved(item)
    ]
    unresolved_contradictions = [
        item for item in value.get("contradictions") or []
        if not IndustryResearchService._review_issue_resolved(item)
    ]
    return {
        "unsupported_claims": len(value.get("unsupported_claims") or []),
        "numeric_conflicts": len(unresolved_numeric),
        "contradictions": len(unresolved_contradictions),
    }


def _report_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    quality = report.get("quality_assurance") if isinstance(report.get("quality_assurance"), dict) else {}
    review = report.get("editorial_review") if isinstance(report.get("editorial_review"), dict) else {}
    generation = report.get("generation") if isinstance(report.get("generation"), dict) else {}
    return {
        "generation_status": generation.get("status"),
        "narrative_chars": int(
            report.get("narrative_char_count")
            or generation.get("narrative_chars")
            or 0
        ),
        "long_form_chars": int(report.get("long_form_char_count") or 0),
        "qa_status": quality.get("status"),
        "qa_score": int(quality.get("score") or 0),
        "critical_failures": list(quality.get("critical_failures") or []),
        "chapter_failures": _chapter_failure_ids(report),
        "editor_status": review.get("status"),
        "editor_release": review.get("release_recommendation"),
        **_review_counts(review),
    }


def _merge_usage(total: Dict[str, int], addition: Any) -> None:
    values = addition if isinstance(addition, dict) else {}
    for key in USAGE_KEYS:
        total[key] = int(total.get(key) or 0) + int(values.get(key) or 0)


def _chapter_ids_with_current_failures(
    chapters: Sequence[Dict[str, Any]],
) -> List[str]:
    return [
        str(chapter.get("chapter_id") or "")
        for chapter in chapters
        if isinstance(chapter, dict)
        and str(chapter.get("chapter_id") or "")
        and bool(chapter.get("validation_failures"))
    ]


def _repair_failed_chapters_only(
    frozen: FrozenProject,
    service: IndustryResearchService,
    snapshot: Dict[str, Any],
    chapters: Sequence[Dict[str, Any]],
    compact_evidence: Sequence[Dict[str, Any]],
    previous_review: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, Any]]:
    """Boundedly repair only chapters still failing after storage replay."""

    ordered = [deepcopy(item) for item in chapters]
    initially_failed = _chapter_ids_with_current_failures(ordered)
    usage_total = {key: 0 for key in USAGE_KEYS}
    if not initially_failed:
        return ordered, usage_total, {
            "attempted": False,
            "method": "no_failed_chapters",
            "affected_chapters": [],
            "accepted_chapters": [],
            "failed_chapters": [],
        }

    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in snapshot.get("evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    reconciled_previous_review = service._reconcile_final_editorial_state(
        deepcopy(previous_review),
        ordered,
        evidence_by_id,
        expected_subject=snapshot.get("subject"),
        governing_facts=snapshot.get("governing_statutory_facts") or [],
    )
    reconciled_previous_review = service._sanitize_editorial_narrative_fields(
        reconciled_previous_review,
        evidence_by_id,
    )

    by_id = {str(item.get("chapter_id") or ""): item for item in ordered}
    failed_subset = [by_id[chapter_id] for chapter_id in initially_failed]
    with _suppress_llm_usage_writes():
        editorial_repaired, editorial_usage, editorial_metadata = (
            service._repair_chapters_from_editorial_review(
                frozen.topic,
                frozen.objective,
                snapshot,
                failed_subset,
                compact_evidence,
                reconciled_previous_review,
            )
        )
    _merge_usage(usage_total, editorial_usage)
    for chapter in editorial_repaired:
        chapter_id = str(chapter.get("chapter_id") or "")
        if chapter_id in by_id:
            by_id[chapter_id] = chapter
    ordered = [by_id[str(item.get("chapter_id") or "")] for item in ordered]
    ordered = [
        service._sanitize_chapter_for_storage(
            item,
            governing_facts=snapshot.get("governing_statutory_facts") or [],
        )
        for item in ordered
    ]
    ordered = service._deduplicate_canonical_safety_across_chapters(ordered)

    remaining = _chapter_ids_with_current_failures(ordered)
    if remaining:
        spec_by_id = {
            str(spec.get("chapter_id") or ""): spec
            for spec in _expected_chapter_specs(frozen.research_type)
        }
        full_evidence_by_id = {
            str(item.get("evidence_id") or ""): item
            for item in [*(snapshot.get("evidence") or []), *compact_evidence]
            if isinstance(item, dict) and item.get("evidence_id")
        }
        evidence_pool = list(full_evidence_by_id.values())
        by_id = {str(item.get("chapter_id") or ""): item for item in ordered}
        for chapter_id in remaining:
            spec = spec_by_id.get(chapter_id)
            if spec is None:
                raise SnapshotRepairError(f"无法定位失败章节契约：{chapter_id}")
            evidence_limit = (
                research_module.INDUSTRY_RESEARCH_STRUCTURED_CHAPTER_EVIDENCE_LIMIT
                if spec.get("required_structured_blocks")
                else research_module.INDUSTRY_RESEARCH_CHAPTER_EVIDENCE_LIMIT
            )
            selected = service._select_chapter_evidence(
                evidence_pool,
                spec,
                limit=evidence_limit,
                subject_symbol=str((snapshot.get("subject") or {}).get("symbol") or ""),
            )
            payload = service._chapter_model_payload(
                frozen.topic,
                frozen.objective,
                snapshot,
                spec,
                selected,
            )
            working = deepcopy(by_id[chapter_id])
            working["allowed_evidence_ids"] = list(
                payload.get("allowed_evidence_ids") or []
            )
            working["allowed_figure_ids"] = [
                str(item.get("figure_id") or "")
                for item in payload.get("visualization_plan") or []
                if isinstance(item, dict) and item.get("figure_id")
            ]
            with _suppress_llm_usage_writes():
                repaired, repair_usage = service._repair_chapter_citations_once(
                    frozen.topic,
                    frozen.objective,
                    snapshot,
                    spec,
                    selected,
                    working,
                )
            _merge_usage(usage_total, repair_usage)
            by_id[chapter_id] = service._sanitize_chapter_for_storage(
                repaired,
                governing_facts=snapshot.get("governing_statutory_facts") or [],
            )
        ordered = [by_id[str(item.get("chapter_id") or "")] for item in ordered]
        ordered = service._deduplicate_canonical_safety_across_chapters(ordered)

    finally_failed = _chapter_ids_with_current_failures(ordered)
    accepted = [
        chapter_id for chapter_id in initially_failed if chapter_id not in finally_failed
    ]
    metadata = {
        "attempted": True,
        "method": "editorial_then_citation_repair_from_frozen_snapshot",
        "affected_chapters": initially_failed,
        "accepted_chapters": accepted,
        "failed_chapters": finally_failed,
        "editorial_attempt": editorial_metadata,
    }
    if finally_failed:
        raise SnapshotRepairError(
            "失败章节有界修订后仍未通过，已停止且不会写库："
            + ", ".join(finally_failed)
        )
    return ordered, usage_total, metadata


def replay_final_gate(
    frozen: FrozenProject,
    service: IndustryResearchService,
    *,
    repair_failed_chapters: bool = False,
    reuse_reviewed_editorial: bool = False,
) -> Dict[str, Any]:
    """Build a replacement report entirely in memory from the frozen snapshot."""

    frozen_snapshot_digest_before = _stable_json_sha256(frozen.snapshot)
    snapshot = deepcopy(frozen.snapshot)
    stored_governing_facts = deepcopy(
        snapshot.get("governing_statutory_facts") or []
    )
    # Governing facts are deterministic derived state, not a new source fetch.
    # Rebuild them from the frozen evidence so stale facts stored by an older
    # prompt/service version cannot govern the replay.  The original snapshot
    # JSON and source_hash remain the CAS authority and are never rewritten.
    snapshot["governing_statutory_facts"] = (
        service._build_governing_statutory_facts(snapshot)
    )
    working_snapshot_digest = _stable_json_sha256(snapshot)
    report = deepcopy(frozen.report)
    visualizations_digest_before = _stable_json_sha256(report.get("visualizations") or [])
    chapters = _validate_chapters(
        report.get("chapters"),
        research_type=frozen.research_type,
    )
    governing_facts = snapshot.get("governing_statutory_facts") or []

    chapters = [
        service._sanitize_chapter_for_storage(
            chapter,
            governing_facts=governing_facts,
        )
        for chapter in chapters
    ]
    chapters = service._deduplicate_canonical_safety_across_chapters(chapters)
    _validate_chapters(chapters, research_type=frozen.research_type)

    compact_evidence = _compact_editor_evidence(service, snapshot)
    previous_review = (
        report.get("editorial_review")
        if isinstance(report.get("editorial_review"), dict)
        else {}
    )
    repair_usage = {key: 0 for key in USAGE_KEYS}
    repair_metadata: Optional[Dict[str, Any]] = None
    if repair_failed_chapters:
        chapters, repair_usage, repair_metadata = _repair_failed_chapters_only(
            frozen,
            service,
            snapshot,
            chapters,
            compact_evidence,
            previous_review,
        )
    previous_revision_cycle = (
        previous_review.get("revision_cycle")
        if isinstance(previous_review.get("revision_cycle"), dict)
        else None
    )
    if reuse_reviewed_editorial:
        if repair_failed_chapters:
            raise SnapshotRepairError(
                "--reuse-reviewed-editorial 不得与需调用模型的"
                " --repair-failed-chapters 同时使用"
            )
        editorial_review = deepcopy(previous_review)
        editorial_usage = {key: 0 for key in USAGE_KEYS}
        if editorial_review.get("status") != "completed":
            raise SnapshotRepairError(
                "原任务没有已完成的独立总编结果，禁止离线复用"
            )
    else:
        with _suppress_llm_usage_writes():
            editorial_review, editorial_usage = service._run_editorial_review(
                frozen.topic,
                snapshot,
                chapters,
                compact_evidence,
            )
        if editorial_review.get("status") != "completed":
            raise SnapshotRepairError(
                "最终总编未完成，已停止且不会写库："
                f"{editorial_review.get('editor_note') or 'unknown error'}"
            )
    if previous_revision_cycle is not None or repair_metadata is not None:
        revision_cycle = deepcopy(previous_revision_cycle or {})
        cycles = list(revision_cycle.get("cycles") or [])
        if repair_metadata is not None and repair_metadata.get("attempted"):
            repair_cycle = deepcopy(repair_metadata)
            repair_cycle["cycle"] = len(cycles) + 1
            cycles.append(repair_cycle)
        accepted_chapters = (
            list(repair_metadata.get("accepted_chapters") or [])
            if repair_metadata is not None
            else list(revision_cycle.get("accepted_chapters") or [])
        )
        failed_chapters = (
            list(repair_metadata.get("failed_chapters") or [])
            if repair_metadata is not None
            else list(revision_cycle.get("failed_chapters") or [])
        )
        revision_cycle.update({
            "attempted": bool(
                revision_cycle.get("attempted")
                or (repair_metadata or {}).get("attempted")
            ),
            "cycles": cycles,
            "affected_chapters": list(dict.fromkeys([
                *list(revision_cycle.get("affected_chapters") or []),
                *list((repair_metadata or {}).get("affected_chapters") or []),
            ])),
            "accepted_chapters": accepted_chapters,
            "failed_chapters": failed_chapters,
        })
        editorial_review["revision_cycle"] = revision_cycle
    editorial_review["final_text_review"] = {
        "performed": True,
        "chapter_count": len(chapters),
        "prompt_version": research_module.INDUSTRY_RESEARCH_PROMPT_VERSION,
        "post_review_chapter_mutation": False,
        "replayed_from_frozen_snapshot": True,
        "review_mode": (
            "existing_completed_editorial_plus_deterministic_reconciliation"
            if reuse_reviewed_editorial else "fresh_independent_editorial"
        ),
    }

    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in snapshot.get("evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    editorial_review = service._reconcile_final_editorial_state(
        editorial_review,
        chapters,
        evidence_by_id,
        expected_subject=snapshot.get("subject"),
        governing_facts=governing_facts,
    )
    editorial_review = service._sanitize_editorial_narrative_fields(
        editorial_review,
        evidence_by_id,
    )

    subject = snapshot.get("subject") if isinstance(snapshot.get("subject"), dict) else {}
    contract = (
        snapshot.get("research_contract")
        if isinstance(snapshot.get("research_contract"), dict)
        else {}
    )
    subject_name = str(subject.get("name") or frozen.topic)
    cutoff = str(
        contract.get("cutoff")
        or snapshot.get("collected_at")
        or "当前证据截止时点"
    )[:10]
    report["prompt_version"] = research_module.INDUSTRY_RESEARCH_PROMPT_VERSION
    report["one_sentence"] = (
        f"本报告围绕{subject_name}，基于截至 {cutoff} 的固定证据快照形成八章研究结论；"
        "公司事实、机构观点、录音线索与待核验传闻分层呈现。"
    )
    report["executive_summary"] = service._validated_executive_summary(chapters)

    final_safety_signatures: set[str] = set()
    for chapter in chapters:
        service._deduplicate_canonical_safety_text(
            chapter.get("body_markdown"),
            final_safety_signatures,
        )
        service._deduplicate_canonical_safety_text(
            chapter.get("summary"),
            final_safety_signatures,
        )
        for question in chapter.get("open_questions") or []:
            service._deduplicate_canonical_safety_text(
                question,
                final_safety_signatures,
            )
    report["executive_summary"] = service._deduplicate_canonical_safety_text(
        report["executive_summary"],
        final_safety_signatures,
    )
    report["executive_summary"] = re.sub(
        r"(?m)^(\*\*[^*]+\*\*：)\s*(?=\n|$)",
        r"\1本章具体结论与口径限制详见已审查正文。",
        report["executive_summary"],
    )

    for key in (
        "chain_nodes",
        "trends",
        "leaders",
        "bottlenecks",
        "applications",
        "disagreements",
        "falsification_conditions",
        "monitoring_indicators",
        "interview_questions",
        "open_questions",
    ):
        report[key] = []
    report["company_analysis"] = {}
    report["industry_boundary"] = {
        "included": [value for value in (subject_name, subject.get("symbol")) if value],
        "excluded": [],
        "definition": (
            f"研究对象以任务解析主体“{subject_name}”及固定证据截止日 {cutoff} 为准。"
        ),
    }
    quality = snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    compact_ids = {
        str(item.get("evidence_id") or "")
        for item in compact_evidence
        if item.get("evidence_id")
    }
    report["caveats"] = [
        service._strip_unsupported_citation_markers(item, compact_ids)[:320]
        for item in [
            *(quality.get("critical_gaps") or []),
            *(quality.get("warnings") or []),
        ]
        if str(item).strip()
    ][:16]
    report["verified_cards_status"] = "reviewed_chapters_only"

    narrative_markdown = service._assemble_long_form_report(
        frozen.topic,
        report,
        chapters,
        research_type=str(snapshot.get("research_type") or frozen.research_type),
    )
    narrative_chars = service._count_report_chars(narrative_markdown)
    visualization_appendix = service._deduplicate_canonical_safety_text(
        IndustryResearchVisualizationService.markdown_appendix(
            report.get("visualizations") or [],
        ),
        final_safety_signatures,
    )
    evidence_appendix = service._deduplicate_canonical_safety_text(
        service._build_evidence_appendix(snapshot),
        final_safety_signatures,
    )
    report_parts = [narrative_markdown, visualization_appendix, evidence_appendix]
    full_markdown = "\n\n".join(value for value in report_parts if value)
    governance_appendix = service._build_research_governance_appendix(
        snapshot,
        editorial_review,
    )
    if governance_appendix:
        governance_appendix = service._deduplicate_canonical_safety_text(
            governance_appendix,
            final_safety_signatures,
        )
        full_markdown = f"{full_markdown}\n\n{governance_appendix}"

    report["chapters"] = chapters
    report["long_form_report"] = full_markdown
    report["long_form_char_count"] = service._count_report_chars(full_markdown)
    report["narrative_char_count"] = narrative_chars
    report["editorial_review"] = editorial_review
    report["quality_assurance"] = service._verify_report_quality(
        snapshot,
        chapters,
        narrative_markdown,
        editorial_review=editorial_review,
    )
    release_ready = report["quality_assurance"].get("status") == "ready"

    previous_generation = (
        frozen.report.get("generation")
        if isinstance(frozen.report.get("generation"), dict)
        else {}
    )
    report["generation"] = {
        "target_chars": research_module.INDUSTRY_RESEARCH_TARGET_CHARS,
        "actual_chars": report["long_form_char_count"],
        "narrative_chars": narrative_chars,
        "chapter_count": len(chapters),
        "model": previous_generation.get("model"),
        "provider": previous_generation.get("provider"),
        "channel": previous_generation.get("channel"),
        "status": "completed" if release_ready else "limited",
        "completed_at": research_module._iso(utc_naive_now()),
        "replayed_from_frozen_snapshot": True,
    }
    previous_usage = (
        frozen.report.get("usage")
        if isinstance(frozen.report.get("usage"), dict)
        else {}
    )
    report["usage"] = {
        key: (
            int(previous_usage.get(key) or 0)
            + int(repair_usage.get(key) or 0)
            + int(editorial_usage.get(key) or 0)
        )
        for key in USAGE_KEYS
    }
    report["evidence_snapshot_hash"] = frozen.source_hash
    report["snapshot_repair"] = {
        "mode": (
            "failed_chapters_then_final_gate"
            if repair_failed_chapters
            else "final_gate_only"
        ),
        "source_project_id": frozen.project_id,
        "replayed_at": research_module._iso(utc_naive_now()),
        "prompt_version": research_module.INDUSTRY_RESEARCH_PROMPT_VERSION,
        "source_hash": frozen.source_hash,
        "original_report_sha256": frozen.report_sha256,
        "chapter_failures_before": _chapter_failure_ids(frozen.report),
        "chapter_failures_after": _chapter_failure_ids(report),
        "chapter_repair": repair_metadata,
        "governing_facts_rebuilt": {
            "from_frozen_evidence": True,
            "stored_count": len(stored_governing_facts),
            "rebuilt_count": len(snapshot.get("governing_statutory_facts") or []),
            "stored_sha256": _stable_json_sha256(stored_governing_facts),
            "rebuilt_sha256": _stable_json_sha256(
                snapshot.get("governing_statutory_facts") or []
            ),
        },
        "collector_called": False,
        "audio_pipeline_called": False,
        "external_llm_called": not reuse_reviewed_editorial,
    }

    if _stable_json_sha256(frozen.snapshot) != frozen_snapshot_digest_before:
        raise SnapshotRepairError("最终门禁意外修改了原始冻结快照，已停止且不会写库")
    if _stable_json_sha256(snapshot) != working_snapshot_digest:
        raise SnapshotRepairError("最终门禁意外修改了内存派生快照，已停止且不会写库")
    if _stable_json_sha256(report.get("visualizations") or []) != visualizations_digest_before:
        raise SnapshotRepairError("最终门禁意外修改了原报告图表资产，已停止且不会写库")
    _validate_chapters(report.get("chapters"), research_type=frozen.research_type)
    if report.get("evidence_snapshot_hash") != frozen.source_hash:
        raise SnapshotRepairError("重组报告的 evidence_snapshot_hash 发生漂移")
    if not str(report.get("long_form_report") or "").strip():
        raise SnapshotRepairError("重组后的 long_form_report 为空")
    if report["editorial_review"].get("status") != "completed":
        raise SnapshotRepairError("重组后的最终总编状态不是 completed")
    if frozen.status == "completed" and not release_ready:
        raise SnapshotRepairError("原项目已 completed，但重放结果降级为 limited，禁止覆盖")
    return report


def _default_backup_dir() -> Path:
    configured = str(os.getenv(BACKUP_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path("reports") / "repair-backups"


def persist_backup(
    frozen: FrozenProject,
    *,
    backup_dir: Path,
) -> tuple[Path, str]:
    """Atomically persist an exact compressed copy of the original row payload."""

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = utc_naive_now().strftime("%Y%m%dT%H%M%S%fZ")
    path = backup_dir / f"{frozen.project_id}-{timestamp}-{frozen.report_sha256[:12]}.json.gz"
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "schema_version": 1,
        "backup_reason": "frozen_snapshot_final_gate_replay",
        "backed_up_at": research_module._iso(utc_naive_now()),
        "row": {
            "id": frozen.row_id,
            "project_id": frozen.project_id,
            "owner_id": frozen.owner_id,
            "topic": frozen.topic,
            "research_type": frozen.research_type,
            "objective": frozen.objective,
            "lookback_days": frozen.lookback_days,
            "status": frozen.status,
            "progress": frozen.progress,
            "stage": frozen.stage,
            "message": frozen.message,
            "error_message": frozen.error_message,
            "query_json": frozen.query_json,
            "source_hash": frozen.source_hash,
            "created_at": frozen.created_at,
            "updated_at": frozen.updated_at,
            "started_at": frozen.started_at,
            "completed_at": frozen.completed_at,
            "report_sha256": frozen.report_sha256,
            "snapshot_sha256": frozen.snapshot_sha256,
            "report_json": frozen.report_json,
            "evidence_snapshot_json": frozen.evidence_snapshot_json,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
    try:
        with temporary.open("xb") as handle:
            handle.write(compressed)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(backup_dir), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    stored_bytes = path.read_bytes()
    backup_sha256 = sha256(stored_bytes).hexdigest()
    if not path.is_file() or path.stat().st_size <= 0:
        raise SnapshotRepairError("备份文件未成功持久化")
    try:
        restored = gzip.decompress(stored_bytes)
    except (OSError, EOFError) as exc:
        raise SnapshotRepairError("备份文件压缩校验失败") from exc
    if restored != encoded:
        raise SnapshotRepairError("备份文件内容校验失败")
    return path, backup_sha256


def commit_report_cas(
    db: DatabaseManager,
    frozen: FrozenProject,
    report: Dict[str, Any],
    *,
    backup_path: Path,
    backup_sha256: str,
) -> str:
    """Replace only the unchanged target row, in one database transaction."""

    if not backup_path.is_file() or backup_path.stat().st_size <= 0:
        raise SnapshotRepairError("CAS 前未找到已持久化备份")
    if sha256(backup_path.read_bytes()).hexdigest() != backup_sha256:
        raise SnapshotRepairError("CAS 前备份校验和不一致")

    report_json = json.dumps(
        report,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    report_sha256 = _text_sha256(report_json)
    quality = report.get("quality_assurance") if isinstance(report.get("quality_assurance"), dict) else {}
    release_ready = quality.get("status") == "ready"
    target_status = "completed" if release_ready else "limited"
    score = int(quality.get("score") or 0)
    char_count = int(report.get("long_form_char_count") or 0)
    if release_ready:
        message = (
            f"冻结快照终门重放完成 · 完整报告已通过质量门 · {char_count:,} 字 · 8 章"
        )
    else:
        message = (
            f"冻结快照终门重放完成 · 受限报告 · 质量 {score} 分 · 缺口仍保留"
        )
    now = utc_naive_now()

    with db.get_session() as session:
        try:
            result = session.execute(
                update(IndustryResearchProjectRecord)
                .where(
                    IndustryResearchProjectRecord.id == frozen.row_id,
                    IndustryResearchProjectRecord.project_id == frozen.project_id,
                    IndustryResearchProjectRecord.status == frozen.status,
                    IndustryResearchProjectRecord.updated_at == frozen.updated_at,
                    IndustryResearchProjectRecord.source_hash == frozen.source_hash,
                    IndustryResearchProjectRecord.report_json == frozen.report_json,
                    IndustryResearchProjectRecord.evidence_snapshot_json
                    == frozen.evidence_snapshot_json,
                )
                .values(
                    status=target_status,
                    progress=100,
                    stage="synthesis",
                    message=message[:300],
                    report_json=report_json,
                    error_message=None,
                    completed_at=now,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise SnapshotRepairError(
                    "CAS 未命中：任务在修复期间已变化，数据库未更新"
                )
            stored = session.execute(
                select(
                    IndustryResearchProjectRecord.report_json,
                    IndustryResearchProjectRecord.status,
                    IndustryResearchProjectRecord.source_hash,
                    IndustryResearchProjectRecord.evidence_snapshot_json,
                ).where(IndustryResearchProjectRecord.id == frozen.row_id)
            ).one()
            if (
                _text_sha256(str(stored.report_json or "")) != report_sha256
                or str(stored.status or "") != target_status
                or str(stored.source_hash or "") != frozen.source_hash
                or str(stored.evidence_snapshot_json or "")
                != frozen.evidence_snapshot_json
            ):
                raise SnapshotRepairError("CAS 后事务内校验失败，已回滚")
            session.commit()
        except Exception:
            session.rollback()
            raise
    return report_sha256


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "从已冻结证据快照重放行业调研最终存储清洗、总编、reconcile、QA 与报告组装；"
            "不会重新采集或转写录音。"
        ),
    )
    parser.add_argument(
        "project_id",
        nargs="?",
        help=f"目标项目 ID；也可使用环境变量 {PROJECT_ID_ENV}",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="只在内存中重放并输出前后门禁摘要（默认）",
    )
    mode.add_argument(
        "--commit",
        action="store_true",
        help="成功后先备份原始行，再以 CAS 原子更新目标报告",
    )
    parser.add_argument(
        "--repair-failed-chapters",
        action="store_true",
        help=(
            "仅对存储清洗后仍失败的章节做有界 AI 修订，再运行最终总编与质量门；"
            "仍不重新采集、同步或转写"
        ),
    )
    parser.add_argument(
        "--reuse-reviewed-editorial",
        action="store_true",
        help=(
            "复用原任务已完成的独立总编结果，仅执行确定性清洗、"
            "reconcile 与 QA；不向外部模型发送冻结证据"
        ),
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help=f"持久化备份目录；默认读取 {BACKUP_DIR_ENV}，否则为 reports/repair-backups",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    project_id = str(args.project_id or os.getenv(PROJECT_ID_ENV) or "").strip()
    if not project_id:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "缺少 project_id：请传位置参数或设置 "
                        f"{PROJECT_ID_ENV}"
                    ),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,64}", project_id):
        print(
            json.dumps(
                {"ok": False, "error": "project_id 格式不合法"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    commit = bool(args.commit)
    repair_failed_chapters = bool(args.repair_failed_chapters)
    reuse_reviewed_editorial = bool(args.reuse_reviewed_editorial)
    if repair_failed_chapters and reuse_reviewed_editorial:
        print(
            json.dumps(
                {"ok": False, "error": "离线复用总编时不能启动 AI 失败章节修订"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    operation = (
        "failed-chapters+final-gate"
        if repair_failed_chapters
        else (
            "offline-reviewed-editorial+final-gate"
            if reuse_reviewed_editorial else "final-gate-only"
        )
    )
    try:
        read_db = ReadOnlyProjectDatabase(_database_path())
        try:
            service = IndustryResearchService(read_db)  # type: ignore[arg-type]
            frozen = load_frozen_project(read_db, service, project_id)
        finally:
            read_db.close()
        print(
            json.dumps(
                {
                    "event": "validated",
                    "project_id": project_id,
                    "mode": "commit" if commit else "dry-run",
                    "operation": operation,
                    "source_hash": frozen.source_hash,
                    "report_sha256": frozen.report_sha256,
                    "before": _report_summary(frozen.report),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "event": (
                        "completed_editorial_reconciliation_started"
                        if reuse_reviewed_editorial
                        else "final_editorial_review_started"
                    ),
                    "project_id": project_id,
                    "external_llm": not reuse_reviewed_editorial,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        repaired = replay_final_gate(
            frozen,
            service,
            repair_failed_chapters=repair_failed_chapters,
            reuse_reviewed_editorial=reuse_reviewed_editorial,
        )
        after = _report_summary(repaired)
        if commit and (
            after.get("qa_status") != "ready"
            or after.get("chapter_failures")
            or after.get("unsupported_claims")
            or after.get("numeric_conflicts")
            or after.get("contradictions")
        ):
            raise SnapshotRepairError(
                "最终质量门仍有未解决项，--commit 已拒绝写库；"
                "请先用 --dry-run 检查并在需要时加 --repair-failed-chapters"
            )
        changed_chapters = [
            str(after_chapter.get("chapter_id") or "")
            for before_chapter, after_chapter in zip(
                _validate_chapters(
                    frozen.report.get("chapters"),
                    research_type=frozen.research_type,
                ),
                _validate_chapters(
                    repaired.get("chapters"),
                    research_type=frozen.research_type,
                ),
            )
            if _stable_json_sha256(before_chapter)
            != _stable_json_sha256(after_chapter)
        ]
        result: Dict[str, Any] = {
            "ok": True,
            "project_id": project_id,
            "mode": "commit" if commit else "dry-run",
            "operation": operation,
            "source_hash": frozen.source_hash,
            "snapshot_unchanged": True,
            "visualizations_unchanged": True,
            "changed_chapters": changed_chapters,
            "before": _report_summary(frozen.report),
            "after": after,
            "committed": False,
        }
        if commit:
            backup_dir = args.backup_dir or _default_backup_dir()
            backup_path, backup_sha256 = persist_backup(
                frozen,
                backup_dir=backup_dir,
            )
            result["backup"] = {
                "path": str(backup_path.resolve()),
                "sha256": backup_sha256,
                "bytes": backup_path.stat().st_size,
            }
            # Only a successful in-memory replay and verified durable backup
            # may open the application's write-capable database manager.
            db = DatabaseManager.get_instance()
            result["new_report_sha256"] = commit_report_cas(
                db,
                frozen,
                repaired,
                backup_path=backup_path,
                backup_sha256=backup_sha256,
            )
            result["committed"] = True
        else:
            result["new_report_sha256"] = _text_sha256(json.dumps(
                repaired,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - operational tool must fail closed.
        message = str(exc).strip() or type(exc).__name__
        print(
            json.dumps(
                {
                    "ok": False,
                    "project_id": project_id,
                    "mode": "commit" if commit else "dry-run",
                    "operation": operation,
                    "error_type": type(exc).__name__,
                    "error": message[:1000],
                    "committed": False,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
