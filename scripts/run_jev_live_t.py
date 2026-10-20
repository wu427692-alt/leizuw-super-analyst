"""Explicit bounded paid paper run. This command has no broker integration."""
import argparse
import json
import os
from pathlib import Path

from src.services.jev_live_t_engine import LiveTConfig
from src.services.jev_live_t_service import run_live_paper
from src.services.jev_live_t_store import LiveTStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--owner", required=True, help="admin or user:<approved numeric account id>")
    parser.add_argument("--base-value", type=float, required=True)
    parser.add_argument("--cash", type=float, required=True)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--max-requests", type=int, default=120)
    parser.add_argument("--max-cost-usd", type=float, default=.1)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--audit-file", type=Path, required=True)
    parser.add_argument("--limits-file", type=Path)
    parser.add_argument("--context", choices=("market-news", "price-volume"), default="market-news",
                        help="live public HS300/ZZ1000/CPO/news, or the legacy price-volume baseline")
    parser.add_argument("--confidence-sizing", action="store_true",
                        help="model action decides direction; confidence sizes 100-share lots instead of gating trades")
    args = parser.parse_args()
    args.audit_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.audit_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as audit_stream:
        def audit(kind, item):
            audit_stream.write(json.dumps({"kind": kind, **item}, ensure_ascii=False, allow_nan=False) + "\n")
            audit_stream.flush()
            if kind in ("started", "decision", "finished"):
                print(json.dumps({"event": kind, "run_id": item.get("run_id", item.get("run", {}).get("run_id")),
                                  "action": item.get("decision", {}).get("action"),
                                  "submission": item.get("submission", {}).get("reason")}), flush=True)
        run = run_live_paper(LiveTConfig(symbol=args.symbol, initial_base_value=args.base_value,
                                       initial_cash=args.cash, slippage_bps=1, latency_ms=1000,
                                       confidence_sizing=args.confidence_sizing),
                             owner=args.owner, store=LiveTStore(args.data_dir), duration_seconds=args.duration,
                             max_model_requests=args.max_requests, max_cost_usd=args.max_cost_usd,
                             enriched_context=args.context == "market-news",
                             limits=json.loads(args.limits_file.read_text()) if args.limits_file else None, audit=audit)
    print(json.dumps({key: run[key] for key in ("run_id", "status", "stop_reason", "usage", "account")}, ensure_ascii=False))
    return 1 if run["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
