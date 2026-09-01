"""Run one administrator-visible production acceptance task for HMT.

This script is intentionally operational rather than part of the web API.  An
administrator maps to the legacy ``owner_id IS NULL`` research scope, so no
request identity is installed here.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import desc, select

from src.services.industry_research_service import (
    IndustryResearchService,
    IndustryResearchTaskManager,
)
from src.storage import DatabaseManager, IndustryResearchProjectRecord


OBJECTIVE_MARKER = "生产端到端验收-v29-严格零冲突终验"


def compact(row: IndustryResearchProjectRecord) -> dict:
    report = json.loads(row.report_json or "{}")
    quality = report.get("quality_assurance") or {}
    editor = report.get("editorial_review") or {}
    generation = report.get("generation") or {}
    return {
        "project_id": row.project_id,
        "status": row.status,
        "progress": int(row.progress or 0),
        "stage": row.stage,
        "message": row.message,
        "error": row.error_message,
        "generation_status": generation.get("status"),
        "narrative_chars": (
            generation.get("narrative_char_count")
            or generation.get("narrative_chars")
        ),
        "qa_status": quality.get("status"),
        "qa_score": quality.get("score"),
        "editor_release": editor.get("release_recommendation"),
        "unsupported": len(editor.get("unsupported_claims") or []),
        "numeric_conflicts": len(editor.get("numeric_conflicts") or []),
        "contradictions": len(editor.get("contradictions") or []),
    }


def main() -> None:
    db = DatabaseManager.get_instance()
    manager = IndustryResearchTaskManager.get_instance()
    with db.get_session() as session:
        existing = session.execute(
            select(IndustryResearchProjectRecord)
            .where(
                IndustryResearchProjectRecord.owner_id.is_(None),
                IndustryResearchProjectRecord.topic == "华懋科技",
                IndustryResearchProjectRecord.objective.contains(OBJECTIVE_MARKER),
                IndustryResearchProjectRecord.status.in_(("queued", "collecting", "analyzing")),
            )
            .order_by(desc(IndustryResearchProjectRecord.id))
            .limit(1)
        ).scalar_one_or_none()
    if existing is None:
        project = IndustryResearchService(db).create_project({
            "topic": "华懋科技",
            "research_type": "company",
            "lookback_days": 730,
            "query_terms": ["603306.SH", "汽车被动安全", "富创优越", "光通信"],
            "objective": (
                f"{OBJECTIVE_MARKER}：全面研究华懋科技。必须实际调用最新本地与上游数据，"
                "覆盖巨潮年报、半年报、季报、重大资产交易报告和公告，Tushare 财务、行情、"
                "估值，近两年券商研报 PDF 正文，知识星球机构段子，相关录音自动转写，互联网"
                "正文和统一情报。严格区分上市公司合并口径、拟收购标的、法定披露、机构测算"
                "与待核验传闻；机构段子和录音中的预测只能形成核验问题，不能进入财务事实、"
                "估值、情景或结论。输出不少于 2 万字、含真实可视化和逐项证据引用的标准公司"
                "研究报告，不提供买卖指令。"
            ),
        })
        project_id = str(project["project_id"])
    else:
        project_id = str(existing.project_id)
        manager.start()
        manager.enqueue(project_id)
    print(json.dumps({"created_or_attached": project_id, "owner_id": None}, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + 10_800
    last_state = None
    try:
        while time.monotonic() < deadline:
            with db.get_session() as session:
                row = session.execute(
                    select(IndustryResearchProjectRecord).where(
                        IndustryResearchProjectRecord.project_id == project_id
                    )
                ).scalar_one()
                state = compact(row)
            signature = (state["status"], state["progress"], state["message"])
            if signature != last_state:
                print(json.dumps(state, ensure_ascii=False), flush=True)
                last_state = signature
            if state["status"] in {"completed", "limited", "failed"}:
                return
            time.sleep(15)
        print(json.dumps({"project_id": project_id, "timeout": True}, ensure_ascii=False), flush=True)
    finally:
        manager.stop(timeout=5)


if __name__ == "__main__":
    main()
