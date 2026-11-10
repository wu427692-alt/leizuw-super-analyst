"""Read-only, source-attributed context for research-capable agent tools.

This module opens no network/database, invokes no model, and creates no jobs.
The stock catalog is a local read; all source access is caller-injected.
Preview budgets are never export limits or claims of exhaustive reading.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import quote


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    return unicodedata.normalize("NFKC", _text(value)).casefold()


def _catalog_names(records: Iterable[Any]) -> list[str]:
    names = []
    for row in records:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            if (len(row) > 8 and row[8] is False) or (len(row) > 7 and row[7] not in {"stock", "equity", ""}):
                continue
            values = [row[2], *(row[5] if len(row) > 5 and isinstance(row[5], list) else [])]
        elif isinstance(row, dict):
            if row.get("active") is False or row.get("listed") is False:
                continue
            values = [row.get("name") or row.get("stock_name") or row.get("zwjc"),
                      row.get("full_name"), row.get("company_name"),
                      *(row.get("aliases") if isinstance(row.get("aliases"), list) else [])]
        else:
            continue
        names.extend(_text(value) for value in values if len(_text(value)) >= 2)
    return list(dict.fromkeys(names))


def _literal_mention(name: str, query: str) -> bool:
    needle, haystack = _key(name), _key(query)
    if not needle:
        return False
    if needle.isascii():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack))
    return needle in haystack


def prepare_research_targets(query, local_queries=None, symbol=None, records=None) -> dict:
    """Resolve only this request's grounded names/codes; never search all rows.

    Model-suggested local_queries must be short literal terms from this query
    or verified canonical names of entities actually mentioned in it. No
    historical conversation or neighbouring industry expansion is consulted.
    """
    question = _text(query)
    catalog_error = None
    try:
        from src.data.stock_index_loader import get_stock_entity_index_records
        from src.services.acquisition_entity_resolver import resolve_acquisition_entities
        pool = list(get_stock_entity_index_records() if records is None else records)
        mentions = []
        for name in sorted(_catalog_names(pool), key=len, reverse=True):
            if _literal_mention(name, question) and not any(_key(name) in _key(prior) for prior in mentions):
                mentions.append(name)
        # An explicit code can disambiguate a company, but an unrelated stale
        # code cannot switch the current question to another stock silently.
        supplied = _text(symbol)
        bound_symbol = supplied if (not question or _literal_mention(supplied, question) or mentions) else ""
        current_codes = re.findall(r"(?<![0-9A-Za-z.])(\d{6}(?:\.(?:SH|SS|SZ|BJ))?)(?![0-9A-Za-z.])", question, re.I)
        # We already performed boundary-aware name extraction; do not let the
        # resolver repeat unbounded alias-substring extraction (JFA/NOTJFA).
        resolution = resolve_acquisition_entities(company_names=mentions,
                                                   symbols=[*current_codes, *([bound_symbol] if bound_symbol else [])],
                                                   request_text="", records=pool)
        if mentions and bound_symbol and not _literal_mention(bound_symbol, question):
            current = resolve_acquisition_entities(company_names=mentions, symbols=current_codes, request_text="", records=pool)
            supplied_resolution = resolve_acquisition_entities(company_names=[], symbols=[bound_symbol], request_text="", records=pool)
            allowed = {candidate["symbol"] for entity in current["entities"] for candidate in entity["candidates"]}
            if not supplied_resolution["symbols"] or not set(supplied_resolution["symbols"]).issubset(allowed):
                resolution = {"entities": [{**entity, "status": "conflict", "reason": "current_name_symbol_mismatch"}
                                            for entity in current["entities"]],
                              "symbols": [], "company_names": [], "has_unresolved": True}
    except (ImportError, OSError, ValueError, TypeError) as exc:
        resolution = {"entities": [], "symbols": [], "company_names": [], "has_unresolved": True}
        catalog_error = type(exc).__name__
    targets, seen = [], set()
    for entity in resolution["entities"]:
        if entity.get("status") != "resolved" or entity["symbol"] in seen:
            continue
        seen.add(entity["symbol"])
        matched_terms = list(dict.fromkeys(row["query"] for row in resolution["entities"]
                                          if row.get("status") == "resolved" and row.get("symbol") == entity["symbol"]))
        targets.append({"query": entity["name"], "name": entity["name"], "symbol": entity["symbol"],
                        "matched_term": entity["query"], "matched_terms": matched_terms,
                        "source": entity.get("source", "stock_index")})
    verified_names = {_key(target["name"]) for target in targets}
    supplied_terms = local_queries if isinstance(local_queries, (list, tuple)) else [local_queries] if isinstance(local_queries, str) else []
    candidates, rejected = [], []
    for value in supplied_terms:
        term = _text(value)
        if (not term or len(term) > 80 or len(term.split()) > 5
                or not (_literal_mention(term, question) or _key(term) in verified_names)):
            if term:
                rejected.append(term)
            continue
        candidates.append(term)
    accepted_local_terms = bool(candidates)
    candidates.extend(target["name"] for target in targets)
    candidates.extend(term for target in targets for term in target["matched_terms"])
    if not targets:
        # An ambiguous/conflicting ticker must not stop a still-valid exact
        # company-name keyword lookup or broaden to arbitrary candidates.
        candidates.extend(mentions if catalog_error is None else [])
    if not candidates and question:
        candidates.append(question)  # exact fallback, never an empty/all-library query
    if not candidates and _text(symbol) and targets:
        candidates.extend(target["name"] for target in targets)
    unique, keys = [], set()
    for value in candidates:
        if _key(value) not in keys:
            unique.append(value)
            keys.add(_key(value))
    return {"query": question, "targets": targets, "entities": resolution["entities"],
            "symbols": [target["symbol"] for target in targets], "local_queries": unique[:4],
            "need_refinement": bool(not targets and not accepted_local_terms) or bool(resolution["has_unresolved"]),
            "resolution_status": "catalog_unavailable" if catalog_error else "resolved" if targets else "keyword_only",
            "catalog_error_type": catalog_error, "rejected_local_queries": rejected,
            "overflow": {"queries_omitted": max(0, len(unique) - 4), "not_export_limit": True}}


def _note(item: Any) -> dict:
    if not isinstance(item, dict):
        return {}
    nested = item.get("note") if isinstance(item.get("note"), dict) else {}
    return {**item, **nested}


def _file_index(note: dict, content: str) -> bool:
    files = note.get("files")
    if not isinstance(files, list) or not files:
        return False
    body = content.strip()
    if not body or body in {"「文件」", "「录音」", "[文件]", "[录音]", "【文件】", "【录音】"}:
        return True
    names = {_text(file.get("name") or file.get("file_name") or file.get("filename"))
             for file in files if isinstance(file, dict)} - {""}
    return body in names


def format_essay_read(note, topic_id, offset=0, max_chars=6000) -> dict:
    """Return an exact original-text span, not an AI summary or attachment."""
    row = _note(note)
    identifier = _text(topic_id)
    if not identifier or (row.get("topic_id") and _text(row["topic_id"]) != identifier):
        return {"ok": False, "status": "topic_mismatch", "topic_id": identifier, "attachments_read": False}
    if type(offset) is not int or type(max_chars) is not int or offset < 0 or not 1 <= max_chars <= 8000:
        return {"ok": False, "status": "invalid_span", "topic_id": identifier, "attachments_read": False}
    content = row.get("content") if isinstance(row.get("content"), str) else ""
    files = row.get("files") if isinstance(row.get("files"), list) else []
    index_only = _file_index(row, content)
    if not content and not index_only:
        return {"ok": False, "status": "missing_original", "topic_id": identifier,
                "content_length": 0, "read_complete_current_response": False, "attachments_read": False}
    if offset > len(content):
        return {"ok": False, "status": "invalid_span", "topic_id": identifier,
                "content_length": len(content), "attachments_read": False}
    end = min(len(content), offset + max_chars)
    return {"ok": True, "status": "source_only" if index_only else "ok", "topic_id": identifier,
            "title": _text(row.get("title")), "source": _text(row.get("group_name") or row.get("source")),
            "author": _text(row.get("author_name") or row.get("author")),
            "published_at": row.get("created_at") or row.get("published_at"),
            "url": row.get("url") or row.get("original_url"),
            "detail_url": "/api/v1/financial-data/research-notes/" + quote(identifier, safe=""),
            "content_kind": "file_index" if index_only else "full_text" if offset == 0 and end == len(content) else "text_span",
            "text": content[offset:end], "span": {"char_start": offset, "char_end": end},
            "content_length": len(content), "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "next_offset": end if end < len(content) else None, "has_more": end < len(content),
            "reached_end": end == len(content),
            "read_complete_current_response": not index_only and offset == 0 and end == len(content),
            "attachments_read": False, "attachment_count": len(files)}


def _unwrap(result: Any) -> tuple[dict, Optional[str]]:
    if not isinstance(result, dict):
        return {}, "invalid_response"
    if result.get("ok") is False:
        return {}, _text(result.get("status")) or "error"
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    return data, None


def _excerpt(text: str, terms: list[str], focus: list[str], budget: int = 900) -> tuple[str, int, int, int]:
    positions = [0]
    needles = list(dict.fromkeys([*focus, *terms]))
    for term in needles:
        if not term:
            continue
        cursor = 0
        for _ in range(8):
            match = re.search(re.escape(term), text[cursor:], re.I)
            if match is None:
                break
            literal = cursor + match.start()
            positions.append(literal)
            cursor = literal + max(1, len(term))
    best = (0, 0, min(len(text), budget))
    for position in positions:
        start = max(0, min(position - budget // 4, len(text) - budget))
        end = min(len(text), start + budget)
        snippet = text[start:end].casefold()
        score = sum(4 for term in focus if term and term.casefold() in snippet) + sum(1 for term in terms if term and term.casefold() in snippet)
        if score > best[0]:
            best = score, start, end
    score, start, end = best
    return text[start:end], start, end, score


def _question_terms(question: str, entity_terms: list[str]) -> list[str]:
    """Literal question anchors for excerpts only, never recall expansion.

    This is not a sentiment dictionary or an intent router. If an anchor does
    not occur in the returned original it contributes nothing to selection.
    """
    remainder = question
    for term in sorted(entity_terms, key=len, reverse=True):
        if term:
            remainder = re.sub(re.escape(term), " ", remainder, flags=re.I)
    generic = {"今天", "最近", "现在", "为什么", "什么", "怎么", "如何", "帮我", "一下", "原因", "分析", "看看", "是否", "研究"}
    candidates = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u3400-\u9fff]{2,}", remainder):
        if token.isascii():
            candidates.append(token)
        else:
            candidates.extend(token[index:index + 2] for index in range(len(token) - 1))
    return list(dict.fromkeys(term for term in candidates if term not in generic))[:6]


def build_research_context(args, *, essay_search: Callable, forum_search: Callable, records=None) -> dict:
    """Collect bounded research previews while preserving source coverage.

    Callbacks receive dictionaries. Each essay query is full scope/page 1/size6;
    returned context is at most eight 900-character excerpts, not eight allowed
    matches. No callback is invoked with an empty/unfiltered query.
    """
    args = args if isinstance(args, dict) else {}
    prepared = prepare_research_targets(args.get("query"), args.get("local_queries"), args.get("symbol"), records)
    question, queries = prepared["query"], prepared["local_queries"]
    raw_focus = args.get("focus_terms") if isinstance(args.get("focus_terms"), (list, tuple)) else []
    focus = [_text(value) for value in raw_focus if isinstance(value, str) and 0 < len(_text(value)) <= 40][:6]
    if not focus:
        focus = _question_terms(question, queries)
    totals, unique, indexes = [], {}, {}
    for term in queries:
        filters = {"query": term, "query_scope": "full", "analysis_status": "essay", "page": 1, "page_size": 6}
        filters.update({key: args[key] for key in ("days", "start_date", "end_date") if args.get(key) is not None})
        try:
            response = essay_search(filters)
            data, error = _unwrap(response)
        except Exception as exc:
            totals.append({"query": term, "status": "error", "total": None, "page": 1,
                           "page_size": 6, "has_more": None, "error_type": type(exc).__name__})
            continue
        if error:
            totals.append({"query": term, "status": error, "total": None, "page": 1, "page_size": 6, "has_more": None})
            continue
        if not isinstance(data.get("items"), list):
            totals.append({"query": term, "status": "invalid_response", "total": None, "page": 1, "page_size": 6, "has_more": None})
            continue
        effective = data.get("filters")
        if isinstance(effective, dict) and _text(effective.get("query")) != term:
            totals.append({"query": term, "status": "filter_mismatch", "total": None, "page": 1, "page_size": 6, "has_more": None})
            continue
        total = data.get("total")
        total = total if type(total) is int and total >= 0 else None
        items = data["items"]
        if any(not isinstance(item, dict) or not _text(_note(item).get("topic_id")) for item in items):
            totals.append({"query": term, "status": "invalid_response", "total": None,
                           "page": 1, "page_size": 6, "has_more": None})
            continue
        has_more = data.get("has_more") if isinstance(data.get("has_more"), bool) else (len(items) < total if total is not None else None)
        totals.append({"query": term, "status": "ok" if items else "empty", "total": total,
                       "page": data.get("page", 1), "page_size": data.get("page_size", 6), "has_more": has_more,
                       "requested_filters": dict(filters), "effective_filters": effective if isinstance(effective, dict) else None})
        for item in items:
            row = _note(item)
            identifier = _text(row.get("topic_id"))
            if not identifier:
                continue
            text = row.get("content") if isinstance(row.get("content"), str) else ""
            index_only = _file_index(row, text)
            excerpt, start, end, score = _excerpt(text, [term, *(target["name"] for target in prepared["targets"])], focus)
            entry = {"topic_id": identifier, "title": _text(row.get("title")),
                     "source": _text(row.get("group_name") or row.get("source")),
                     "published_at": row.get("created_at") or row.get("published_at"),
                     "url": row.get("url") or row.get("original_url"),
                     "detail_url": "/api/v1/financial-data/research-notes/" + quote(identifier, safe=""),
                     "excerpt": excerpt, "excerpt_start": start, "excerpt_end": end,
                     "content_length": len(text), "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                     "content_kind": "file_index" if index_only else "missing_original" if not text else "full_text" if end - start == len(text) else "excerpt",
                     "read_complete_current_response": bool(text) and not index_only and start == 0 and end == len(text),
                     "attachments_read": False, "detail_tool_args": {"topic_id": identifier, "offset": 0, "max_chars": 6000},
                     "_score": score}
            bucket = indexes if index_only else unique
            if identifier not in bucket or bucket[identifier]["_score"] < score:
                bucket[identifier] = entry
    selected = sorted(unique.values(), key=lambda item: -item["_score"])[:8]
    sources = [*selected, *list(indexes.values())[:8]]
    for entry in sources:
        entry.pop("_score", None)
    successful = [item for item in totals if item["status"] in {"ok", "empty"}]
    status = ("error" if not successful else "partial" if len(successful) < len(totals)
              else "ok" if unique or indexes else "empty") if totals else "needs_refinement"
    forums, forum_queries = [], set()
    forum_targets = prepared["targets"] or [{"query": term} for term in queries]
    for target in forum_targets[:4]:
        keyword, code = target["query"], target.get("symbol")
        identity = code or keyword
        if not keyword or identity in forum_queries:
            continue
        forum_queries.add(identity)
        filters = {"query": "" if code else keyword, "days": args.get("days", 7), "page": 1, "page_size": 4}
        filters.update({key: args[key] for key in ("start_date", "end_date") if args.get(key) is not None})
        if code:
            filters["symbol"] = code
        if args.get("platform"):
            filters["platform"] = args["platform"]
        try:
            response = forum_search(filters)
            data, error = _unwrap(response)
            data = dict(data)
            if isinstance(data.get("items"), list):
                trimmed = []
                for row in data["items"][:4]:
                    if not isinstance(row, dict):
                        continue
                    safe = {key: value for key, value in row.items() if key not in {"raw", "raw_payload", "content", "summary", "text", "excerpt"}}
                    body = row.get("excerpt") or row.get("content") or row.get("summary") or row.get("text") or ""
                    safe["excerpt"] = str(body)[:600]
                    safe["preview_only"] = True
                    trimmed.append(safe)
                data["items"] = trimmed
            forums.append({"target": keyword, "symbol": code, "query": keyword,
                           "status": error or _text(data.get("status")) or "ok", "data": data})
        except Exception as exc:
            forums.append({"target": keyword, "symbol": code, "query": keyword,
                           "status": "error", "data": {}, "error_type": type(exc).__name__})
    return {"ok": bool(successful or any(item["status"] in {"ok", "partial", "empty", "stale"} for item in forums)),
            "query": question, "targets": prepared["targets"], "local_queries": queries,
            "entities": prepared["entities"], "rejected_local_queries": prepared["rejected_local_queries"],
            "resolution_status": prepared["resolution_status"], "catalog_error_type": prepared["catalog_error_type"],
            "need_refinement": prepared["need_refinement"], "excerpt_focus_terms": focus,
            "institutional": {"status": status, "total_by_query": totals, "retrieved": len(selected),
                              "source_retrieved_unique": len(unique), "items": selected,
                              "source_indexes": list(indexes.values())[:8]},
            "forums": forums, "preview_only": True,
            "overflow": {**prepared["overflow"], "institutional_items_omitted": max(0, len(unique) - 8),
                         "source_indexes_omitted": max(0, len(indexes) - 8), "forum_targets_omitted": max(0, len(forum_targets) - 4)},
            "boundaries": ["机构资料与论坛观点是来源线索，不是已证实的股价原因或投资事实。",
                           "每源总数、预览条数和已读文字覆盖分开；预览预算不是下载篇数限制。",
                           "未读附件/录音不算已阅读全文；本工具不转写、不分析全库、不提交下载。"]}
