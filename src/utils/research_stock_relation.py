"""Source-backed stock/report relationships; recall is not company attribution."""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, Optional

from sqlalchemy import String, and_, case, cast, func, not_, or_, select

from src.services.stock_code_utils import _infer_cn_exchange


RESEARCH_EVENT_TYPES = frozenset({
    "research_report_pdf", "institution_forecast", "institution_survey", "broker_recommendation",
})


def _symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    suffix = re.fullmatch(r"(\d{6})\.(SH|SS|SZ|BJ)", raw)
    prefix = re.fullmatch(r"(SH|SS|SZ|BJ)\.?([0-9]{6})", raw)
    if suffix:
        return f"{suffix[1]}.{suffix[2].replace('SS', 'SH')}"
    if prefix:
        return f"{prefix[2]}.{prefix[1].replace('SS', 'SH')}"
    if re.fullmatch(r"\d{6}", raw):
        return f"{raw}.{_infer_cn_exchange(raw)}"
    return ""


def _text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    plain = html.unescape(re.sub(r"<[^>]*>", " ", str(value)))
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", plain)).strip()[:limit]


def _records(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _contract(event: Dict[str, Any], metrics: dict) -> bool:
    evidence = metrics.get("_evidence")
    apis = evidence.get("origin_apis") if isinstance(evidence, dict) else []
    return (
        event.get("event_type") == "institution_forecast"
        and event.get("source_key") == "tushare.institution"
        and isinstance(apis, list) and "report_rc" in apis
    )


def research_stock_relation(
    event: Dict[str, Any], target_symbol: str, *, raw: Any = None, stock_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Prefer original subjects; mixed/conflicting subjects remain unconfirmed.

    ``symbols`` is deliberately ignored: it is a broad recall projection and
    contains companies merely mentioned in an abstract. A title mention alone
    is similarly not proof that the report studies that company.
    """
    target = _symbol(target_symbol) or str(target_symbol).strip().upper()
    result: Dict[str, Any] = {"kind": "unknown", "target_symbol": target}
    if event.get("event_type") not in RESEARCH_EVENT_TYPES:
        return result
    malformed_original = False
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except (TypeError, ValueError):
            raw = None
            malformed_original = True
    metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
    records = [*_records(raw), *_records(metrics.get("records")), metrics]
    subjects = [(str(row.get("ts_code") or "").strip(), _text(row.get("name"), 100))
                for row in records if str(row.get("ts_code") or "").strip()]
    contract_code = str(metrics.get("report_symbol") or "").strip() if _contract(event, metrics) else ""
    if contract_code:
        subjects.append((contract_code, ""))
    codes = {_symbol(code) for code, _ in subjects}
    valid_codes = codes - {""}
    # A broken field must never hide a different stock that another original
    # field explicitly identifies, allowing its numbers into this company.
    other_only = len(valid_codes) == 1 and target not in valid_codes
    if subjects and ("" in codes or len(codes) != 1 or malformed_original) and not other_only:
        return {**result, "match_field": "subject_code", "match_text": "原始研究对象代码不明确或存在冲突，关联待确认。",
                "has_subject_conflict": bool(valid_codes - {target})}
    source = next(iter(_records(raw)), {})
    name = next((name for code, name in subjects if name and _symbol(code) in valid_codes), "")
    if not name:
        for row in (source, metrics):
            row_code = str(row.get("ts_code") or "").strip()
            if not valid_codes or not row_code or _symbol(row_code) in valid_codes:
                name = _text(row.get("name"), 100)
                if name:
                    break
    if name:
        result["subject_name"] = name
    if valid_codes:
        subject = next(iter(valid_codes))
        result.update({
            "subject_symbol": subject,
            "kind": "company_direct" if subject == target else "other_company_mention",
            "match_field": "source_contract" if contract_code and len(subjects) == 1 else "subject_code",
            "match_text": f"原始研究对象：{name + ' ' if name else ''}{subject}",
        })
        if subject == target:
            return result
    else:
        report_type = _text(source.get("report_type") or metrics.get("report_type"), 80)
        industry = _text(source.get("ind_name") or metrics.get("ind_name"), 100)
        if "行业" in report_type or (industry and not any(word in report_type for word in ("个股", "公司"))):
            result.update({"kind": "industry_related", "match_field": "industry",
                           "match_text": " · ".join(value for value in (report_type, industry) if value)})

    # Explain a textual mention without converting it to a company fact.
    needles = [target, target.split(".")[0]]
    if stock_name and _text(stock_name, 100) != target:
        needles.insert(0, _text(stock_name, 100))
    for field, value in (
        ("title", source.get("title") or metrics.get("title") or event.get("title")),
        ("abstract", source.get("abstr") or metrics.get("abstr") or event.get("summary")),
    ):
        text = _text(value, 20000)
        positions = [text.lower().find(needle.lower()) for needle in needles if needle]
        positions = [position for position in positions if position >= 0]
        if positions:
            start = max(0, min(positions) - 60)
            result.update({"match_field": field, "match_text": text[start:start + 240]})
            break
    return result


def direct_research_condition(model: Any, target_symbol: str) -> Any:
    """SQL counterpart used BEFORE LIMIT so old direct reports cannot be crowded out.

    Invalid JSON is never passed to json_extract. Every explicitly supplied
    subject must agree; arrays are checked fully, not just their first row.
    No schema mutation, custom SQLite function, or source request is needed.
    """
    target = _symbol(target_symbol)
    if not target:
        return False
    code, exchange = target.split(".")
    exchanges = (exchange, "SS") if exchange == "SH" else (exchange,)
    variants = [f"{code}.{suffix}" for suffix in exchanges]
    variants += [value for suffix in exchanges for value in (f"{suffix}{code}", f"{suffix}.{code}")]
    if _infer_cn_exchange(code) == exchange:
        variants.append(code)
    raw = case((func.json_valid(model.raw_payload) == 1, model.raw_payload), else_="{}")
    metrics = case((func.json_valid(model.metrics_json) == 1, model.metrics_json), else_="{}")

    def normalized(value):
        return func.upper(func.trim(func.coalesce(cast(value, String), "")))

    raw_code = normalized(func.json_extract(raw, "$.ts_code"))
    metric_code = normalized(func.json_extract(metrics, "$.ts_code"))
    record_code = normalized(case(
        (func.json_type(metrics, "$.records") == "object", func.json_extract(metrics, "$.records.ts_code")),
        else_="",
    ))
    api_rows = func.json_each(metrics, "$._evidence.origin_apis").table_valued("value").alias("relation_apis")
    contract = and_(
        model.event_type == "institution_forecast", model.source_key == "tushare.institution",
        func.json_type(metrics, "$._evidence.origin_apis") == "array",
        select(1).select_from(api_rows).where(api_rows.c.value == "report_rc").exists(),
    )
    contract_code = normalized(case((contract, func.json_extract(metrics, "$.report_symbol")), else_=""))
    candidates = [value.in_(variants) for value in (raw_code, metric_code, record_code, contract_code)]
    consistent = [or_(value == "", value.in_(variants)) for value in (raw_code, metric_code, record_code, contract_code)]
    for name, document in (
        ("relation_raw", case((func.json_type(raw) == "array", raw), else_="[]")),
        ("relation_records", case((func.json_type(metrics, "$.records") == "array",
                                   func.json_extract(metrics, "$.records")), else_="[]")),
    ):
        rows = func.json_each(document).table_valued("value", "type").alias(name)
        value = normalized(func.json_extract(case((rows.c.type == "object", rows.c.value), else_="{}"), "$.ts_code"))
        candidates.append(select(1).select_from(rows).where(value.in_(variants)).exists())
        consistent.append(not_(select(1).select_from(rows).where(value != "", value.not_in(variants)).exists()))
    return and_(
        model.event_type.in_(RESEARCH_EVENT_TYPES),
        or_(model.raw_payload.is_(None), func.trim(model.raw_payload) == "", func.json_valid(model.raw_payload) == 1),
        or_(*candidates), *consistent,
    )
