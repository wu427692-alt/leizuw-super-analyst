"""Print bounded, read-only evidence excerpts for one research project."""

from __future__ import annotations

import json
import os

from sqlalchemy import select

from src.storage import DatabaseManager, IndustryResearchProjectRecord


def snippets(text: str, needles: tuple[str, ...], radius: int = 280) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for needle in needles:
        start = 0
        hits: list[str] = []
        while len(hits) < 3:
            index = text.find(needle, start)
            if index < 0:
                break
            hits.append(text[max(0, index - radius): index + len(needle) + radius])
            start = index + len(needle)
        if hits:
            output[needle] = hits
    return output


def main() -> None:
    project_id = os.environ["PROJECT_ID"]
    focus_id = os.getenv("FOCUS_ID", "").strip()
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        row = session.execute(
            select(IndustryResearchProjectRecord).where(
                IndustryResearchProjectRecord.project_id == project_id
            )
        ).scalar_one()
    snapshot = json.loads(row.evidence_snapshot_json or "{}")
    if os.getenv("SUMMARY_ONLY", "").strip().lower() in {"1", "true", "yes"}:
        audio_rows = [
            item for item in snapshot.get("evidence") or []
            if isinstance(item, dict) and str(item.get("kind") or "") == "audio_transcript"
        ]
        unsafe_needles = ("110.32%", "剩余58%", "订单三倍", "张坤", "20亿")
        print(json.dumps({
            "project_id": project_id,
            "prompt_version": (json.loads(row.report_json or "{}")).get("prompt_version"),
            "primary_precedence": snapshot.get("primary_precedence"),
            "governing_statutory_facts": snapshot.get("governing_statutory_facts") or [],
            "audio": [{
                "evidence_id": item.get("evidence_id"),
                "model_summary_chars": len(str(item.get("model_summary") or "")),
                "confirmed": (item.get("hypothesis_projection") or {}).get("confirmed"),
                "suppressed_count": (item.get("hypothesis_projection") or {}).get("suppressed_count"),
                "unsafe_model_hits": snippets(str(item.get("model_summary") or ""), unsafe_needles, radius=80),
            } for item in audio_rows],
        }, ensure_ascii=False, indent=2, default=str))
        return
    selected = []
    needles = (
        "125,897,911.25", "125897911.25", "1.26", "股份支付", "42.16%", "57.84%",
        "控股子公司", "全资子公司", "张坤", "臧琨", "110.32%", "三倍",
    )
    for item in snapshot.get("evidence") or []:
        evidence_id = str(item.get("evidence_id") or "")
        if focus_id and evidence_id != focus_id:
            continue
        if not focus_id and evidence_id not in {
            "filing:1225505930", "filing:1225532560",
        } and str(item.get("kind") or "") != "audio_transcript":
            continue
        raw = str(item.get("summary") or "")
        document = str(item.get("document_text") or "")
        model = str(item.get("model_summary") or "")
        selected.append({
            "evidence_id": evidence_id,
            "kind": item.get("kind"),
            "title": item.get("title"),
            "summary_chars": len(raw),
            "document_chars": len(document),
            "model_chars": len(model),
            "summary_hits": snippets(raw, needles),
            "document_hits": snippets(document, needles),
            "model_hits": snippets(model, needles),
            "projection": item.get("hypothesis_projection"),
        })
    print(json.dumps({
        "project_id": project_id,
        "prompt_version": (json.loads(row.report_json or "{}")).get("prompt_version"),
        "primary_precedence": snapshot.get("primary_precedence"),
        "items": selected,
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
