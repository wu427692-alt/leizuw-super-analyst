"""Deterministic, fail-closed company resolution for acquisition preflight.

No network is opened here. The existing stock index supplies verified listings;
the caller may inject an official issuer search for missing/new listings. LLM
output is a search query, never evidence that a name belongs to a ticker.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from src.data.stock_index_loader import get_stock_entity_index_records

IssuerSearch = Callable[[str], list[dict[str, Any]]]
_CODE_RE = re.compile(r"(?<![0-9A-Za-z.])(\d{6}(?:\.(?:SH|SS|SZ|BJ))?)(?![0-9A-Za-z.])", re.I)
_COMPANY_SUFFIX_RE = re.compile(r"(?:股份有限公司|股份有限责任公司|有限责任公司|有限公司|股份公司|公司)$")


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _name_key(value: Any) -> str:
    name = re.sub(r"\s+", "", _text(value)).casefold()
    return _COMPANY_SUFFIX_RE.sub("", name)


def _name_keys(value: Any) -> set[str]:
    full = re.sub(r"\s+", "", _text(value)).casefold()
    return {key for key in (full, _name_key(full)) if key}


def _canonical_code(value: Any, market: Any = "") -> str | None:
    code = _text(value).upper().replace(".SS", ".SH")
    if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code):
        return code
    market_key = _text(market).upper()
    suffix = {"SH": "SH", "SZ": "SZ", "BJ": "BJ", "SSE": "SH", "SZSE": "SZ",
              "BSE": "BJ", "SZZB": "SZ", "SHZB": "SH", "BJZB": "BJ"}.get(market_key)
    if re.fullmatch(r"\d{6}", code) and suffix:
        return f"{code}.{suffix}"
    return None


def _records(raw_records: Iterable[Any], *, source: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw in raw_records:
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            if len(raw) > 8 and raw[8] is False:
                continue
            if len(raw) > 7 and raw[7] not in {"stock", "equity", ""}:
                continue
            code, name = _canonical_code(raw[0]), _text(raw[2])
            aliases = raw[5] if len(raw) > 5 and isinstance(raw[5], list) else []
            extra: dict[str, Any] = {}
        elif isinstance(raw, Mapping):
            if raw.get("active") is False or raw.get("listed") is False:
                continue
            code = _canonical_code(raw.get("symbol") or raw.get("ts_code") or raw.get("code"), raw.get("market"))
            name = _text(raw.get("name") or raw.get("stock_name") or raw.get("zwjc"))
            aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
            aliases = [*aliases, raw.get("full_name"), raw.get("company_name")]
            extra = {key: _text(raw[key]) for key in ("org_id",) if raw.get(key)}
        else:
            continue
        if not code or not name or re.fullmatch(r"[\d.]+", name):
            continue
        record = rows.setdefault(code, {"name": name, "symbol": code, "source": source,
                                        "aliases": [], **extra})
        record["aliases"] = list(dict.fromkeys([*record["aliases"], *[_text(v) for v in aliases if _text(v)]]))
    return list(rows.values())


def _public_candidate(record: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(record[key]) for key in ("name", "symbol", "source", "org_id") if record.get(key)}


def _matching_names(query: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = _name_keys(query)
    if not keys:
        return []
    exact = [row for row in records if any(keys.intersection(_name_keys(name))
             for name in (row["name"], *row["aliases"]))]
    if exact:
        return exact
    # A short genuine company prefix is useful (国仪/华懋), arbitrary substring
    # matching is not. Multiple matches remain candidates, regardless of rank.
    prefix_keys = [key for key in keys if len(key) >= 2 and re.search(r"[\u3400-\u9fff]", key)]
    return [row for row in records if any(name_key.startswith(key)
            for name in (row["name"], *row["aliases"])
            for name_key in _name_keys(name) for key in prefix_keys)]


def _matching_code(query: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    code = _text(query).upper().replace(".SS", ".SH")
    if re.fullmatch(r"\d{6}", code):
        return [row for row in records if row["symbol"].split(".")[0] == code]
    return [row for row in records if row["symbol"] == code]


def resolve_acquisition_entities(
    *, company_names: list[str], symbols: list[str], request_text: str,
    selections: dict[str, str] | None = None, issuer_search: IssuerSearch | None = None,
    records: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Resolve selectors to verified mainland securities, never guess an exchange.

    ``entities`` retain ambiguous/unresolved/conflicting queries so the caller
    can block company-scoped tasks *before* creating work. ``selections`` are
    accepted only when the chosen symbol belongs to that query's candidates.
    ``records`` injects the same stock-index row schema for offline tests.
    """
    pool = _records(get_stock_entity_index_records() if records is None else records, source="stock_index")
    names = list(dict.fromkeys(_text(v) for v in company_names if _text(v)))
    supplied_codes = list(dict.fromkeys(_text(v).upper() for v in symbols if _text(v)))
    supplied_codes.extend(match.group(1).upper() for match in _CODE_RE.finditer(_text(request_text))
                          if match.group(1).upper() not in supplied_codes)
    # An incomplete model plan can still be repaired from explicit names in the
    # user's text. Longer aliases suppress a nested shorter alias, not other
    # companies elsewhere in the sentence.
    if not names:
        mentions = [(alias, row) for row in pool for alias in (row["name"], *row["aliases"])
                    if len(alias) >= 2 and alias in _text(request_text)]
        for alias, _row in sorted(mentions, key=lambda item: -len(item[0])):
            if not any(alias in selected for selected in names):
                names.append(alias)
    queries = [(value, "company") for value in names]
    queries.extend((value, "symbol") for value in supplied_codes)
    entities: list[dict[str, Any]] = []
    for query, kind in queries:
        match = _matching_code if kind == "symbol" else _matching_names
        candidates = match(query, pool)
        search_error = False
        if not candidates and issuer_search is not None:
            try:
                official_records = _records(issuer_search(query), source="cninfo")
                candidates = match(query, official_records)
                if kind == "company" and not candidates:
                    # Full legal-name queries can return exchange short names.
                    # Keep the official query-scoped set without ranking it;
                    # more than one listing still requires confirmation.
                    candidates = official_records
            except Exception:
                # Upstream failure is NOT an empty/valid identity; preflight
                # explicitly blocks it and permits a later revalidation.
                search_error = True
        selection = _text((selections or {}).get(query)).upper().replace(".SS", ".SH")
        user_confirmed = False
        # With no name candidates, the UI may ask the user to explicitly link
        # this query to a *verified* ticker. Existing ambiguous matches cannot
        # be replaced by an arbitrary other company, even a valid listing.
        if kind == "company" and selection and not candidates:
            selected_candidates = _matching_code(selection, pool)
            if not selected_candidates and issuer_search is not None:
                try:
                    selected_candidates = _matching_code(selection, _records(issuer_search(selection), source="cninfo"))
                except Exception:
                    search_error = True
            if len(selected_candidates) == 1:
                candidates = selected_candidates
                user_confirmed = True
        candidates = sorted(candidates, key=lambda row: row["symbol"])
        chosen = next((row for row in candidates if row["symbol"] == selection), None) if selection else None
        entity: dict[str, Any] = {"query": query, "kind": kind,
                                  "status": "unresolved", "candidates": [_public_candidate(row) for row in candidates]}
        if selection and chosen is None:
            entity.update(status="conflict", reason="selection_not_in_verified_candidates")
        elif chosen is not None or len(candidates) == 1:
            entity.update(status="resolved", **_public_candidate(chosen or candidates[0]))
            if user_confirmed:
                entity["confirmed_by_user"] = True
        elif len(candidates) > 1:
            entity.update(status="ambiguous", reason="multiple_verified_securities")
        elif search_error:
            entity["reason"] = "issuer_search_unavailable"
        else:
            entity["reason"] = "no_verified_security"
        entities.append(entity)

    company_entities = [row for row in entities if row["kind"] == "company"]
    code_entities = [row for row in entities if row["kind"] == "symbol"]
    resolved_company_codes = {row["symbol"] for row in company_entities if row["status"] == "resolved"}
    resolved_explicit_codes = {row["symbol"] for row in code_entities if row["status"] == "resolved"}
    # A supplied ticker may disambiguate a company, but cannot silently replace
    # an independently verified different company.
    for entity in company_entities:
        if entity["status"] == "ambiguous":
            matching = [row for row in entity["candidates"] if row["symbol"] in resolved_explicit_codes]
            if len(matching) == 1:
                entity.update(status="resolved", **matching[0])
                entity.pop("reason", None)
    if (len(company_entities) == 1 and len(code_entities) == 1
            and resolved_company_codes and resolved_explicit_codes
            and resolved_company_codes.isdisjoint(resolved_explicit_codes)):
        for entity in entities:
            entity.update(status="conflict", reason="company_symbol_mismatch")
            entity.pop("symbol", None)
            entity.pop("name", None)
    elif company_entities:
        covered_codes = {candidate["symbol"] for entity in company_entities
                         for candidate in entity["candidates"]}
        for entity in code_entities:
            if entity["status"] == "resolved" and entity["symbol"] not in covered_codes:
                entity.update(status="conflict", reason="symbol_not_in_named_companies")
                entity.pop("symbol", None)
                entity.pop("name", None)
    resolved = [row for row in entities if row["status"] == "resolved"]
    return {"entities": entities,
            "symbols": sorted({row["symbol"] for row in resolved}),
            "company_names": sorted({row["name"] for row in resolved}),
            "has_unresolved": any(row["status"] != "resolved" for row in entities)}
