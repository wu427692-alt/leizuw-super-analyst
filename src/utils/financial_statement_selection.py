# -*- coding: utf-8 -*-
"""Deterministic selection of independently dated financial source rows."""

from __future__ import annotations

from datetime import datetime
import json
import hashlib
import re
from typing import Any, Dict, Iterable, Optional


# Python's whitespace set, explicit so SQLite trim uses the same contract.
FINANCIAL_WHITESPACE = " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
FINANCIAL_SECTIONS = ("indicator", "income", "balance_sheet", "cashflow", "forecast")


def financial_calendar_date(value: Any) -> Optional[str]:
    """Return a validated compact calendar date, never an inferred timestamp."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    raw = str(value).strip(FINANCIAL_WHITESPACE)
    # Match SQLite/source YYYYMMDD digits exactly. strptime accepts some
    # Unicode year digits, which must not enter only the Python selection path.
    if not re.fullmatch(r"[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
        return None
    compact = raw.replace("-", "")
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError:
        return None
    return compact


def financial_announcement_date(row: Dict[str, Any]) -> Optional[str]:
    """Prefer valid actual disclosure date; fall back to the announcement date."""
    return financial_calendar_date(row.get("f_ann_date")) or financial_calendar_date(row.get("ann_date"))


def financial_statement_scope_rank(row: Dict[str, Any]) -> Optional[int]:
    """Classify comparable consolidated statements without guessing unknowns.

    Tushare income/cashflow docs 33/44: 1 is consolidated, 4 adjusted
    consolidated, 5 the pre-adjustment original. Types 2/3 are single-quarter
    variants and 6-12 parent-company variants. Missing type is legacy unknown,
    not evidence that its period is cumulative or its entity consolidated.
    Balance-sheet scope concerns the reporting entity, not a cumulative flow.
    """
    value = row.get("report_type")
    if isinstance(value, bool):
        return None
    kind = str(value).strip(FINANCIAL_WHITESPACE) if value is not None else ""
    return {"1": 2, "4": 2, "5": 1, "": 0}.get(kind)


def financial_selection_rank(row: Any, *, statement: bool = False) -> Optional[tuple]:
    """Apply the local period/scope/latest-marker/disclosure selection policy.

    Tushare cashflow documents update_flag=1 as latest; income only calls it an
    update marker. Prioritizing an explicit 1 across these endpoints is our
    declared selection policy, not an officially specified universal ordering.
    Report types 1 and 4 share a scope tier and neither wins a date/marker tie.
    Equal ranks with different source content are unresolved conflicts, not
    proof that a more populated or lexically greater row is a newer revision.
    """
    if not isinstance(row, dict):
        return None
    period = financial_calendar_date(row.get("end_date"))
    scope = financial_statement_scope_rank(row) if statement else 0
    if period is None or scope is None:
        return None
    flag = row.get("update_flag")
    updated = int(type(flag) in (str, int) and str(flag).strip(FINANCIAL_WHITESPACE) == "1")
    return period, scope, updated, financial_announcement_date(row) or ""


def financial_content_identity(source_key: str, symbols: Iterable[str], metrics: Any) -> str:
    """Immutable version identity for the five source tables, not derived text."""
    source = metrics if isinstance(metrics, dict) else {}
    payload = [source_key, sorted(set(symbols)), {key: source.get(key, {}) for key in FINANCIAL_SECTIONS}]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "financial-v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
