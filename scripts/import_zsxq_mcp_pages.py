#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import JSONL pages returned by the Knowledge Planet (ZSXQ) MCP.

Each stdin line may be either a raw ``get_group_topics`` response or a wrapper:
``{"group_id": "...", "group_name": "...", "payload": {...}}``.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.financial_data_service import (  # noqa: E402
    FinancialDataUpstreamError,
    FinancialDataValidationError,
    ResearchNoteService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import ZSXQ MCP topic pages from JSONL stdin")
    parser.add_argument("--group-id", help="Fallback group id when the topic payload omits group metadata")
    parser.add_argument("--group-name", help="Fallback group name when the topic payload omits group metadata")
    parser.add_argument(
        "--chunked-base64",
        action="store_true",
        help="Read BEGIN/base64 chunks/END frames; useful when stdin is a terminal with a short line limit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = ResearchNoteService()
    totals = {"pages": 0, "received": 0, "created": 0, "updated": 0, "unchanged": 0}
    frame_chunks = []
    input_line_number = 0
    payload_line_number = 0
    for raw_line in sys.stdin:
        input_line_number += 1
        line = raw_line
        if args.chunked_base64:
            marker = raw_line.strip()
            if marker == "BEGIN":
                frame_chunks = []
                continue
            if marker == "QUIT":
                break
            if marker != "END":
                frame_chunks.append(marker)
                continue
            try:
                line = base64.b64decode("".join(frame_chunks), validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                print(json.dumps({"ok": False, "line": input_line_number, "error": str(exc)}, ensure_ascii=False))
                return 1
            frame_chunks = []

        payload_line_number += 1
        line_number = payload_line_number
        if not line.strip():
            continue
        if line.strip() == "__END__":
            break
        try:
            parsed = json.loads(line)
            if not isinstance(parsed, dict):
                raise FinancialDataValidationError("each input line must be a JSON object")
            payload = parsed.get("payload", parsed)
            if not isinstance(payload, dict):
                raise FinancialDataValidationError("payload must be a JSON object")
            result = service.import_mcp_page(
                payload,
                group_id=str(parsed.get("group_id") or args.group_id or "").strip() or None,
                group_name=str(parsed.get("group_name") or args.group_name or "").strip() or None,
            )
        except (json.JSONDecodeError, FinancialDataValidationError, FinancialDataUpstreamError) as exc:
            print(json.dumps({"ok": False, "line": line_number, "error": str(exc)}, ensure_ascii=False), flush=True)
            return 1

        totals["pages"] += 1
        for key in ("received", "created", "updated", "unchanged"):
            totals[key] += int(result.get(key) or 0)
        print(json.dumps({"ok": True, "line": line_number, **result}, ensure_ascii=False), flush=True)

    print(json.dumps({"ok": True, "totals": totals}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
