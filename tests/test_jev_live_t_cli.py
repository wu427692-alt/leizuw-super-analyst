"""Explicit sizing mode reaches the existing bounded runner without live calls."""
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_preserves_budget_and_passes_confidence_sizing(tmp_path, monkeypatch, enabled):
    path = Path(__file__).parents[1] / "scripts" / "run_jev_live_t.py"
    spec = importlib.util.spec_from_file_location("jev_live_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []

    def run(config, **options):
        seen.append((config, options))
        return {"run_id": "synthetic", "status": "completed", "stop_reason": "duration_complete",
                "usage": {"cost_usd": 0}, "account": None}

    monkeypatch.setattr(module, "run_live_paper", run)
    args = [str(path), "--symbol", "603306.SH", "--owner", "admin", "--base-value", "8000000",
            "--cash", "4000000", "--max-cost-usd", "9.991924282", "--duration", "300",
            "--max-requests", "120", "--data-dir", str(tmp_path / "runs"),
            "--audit-file", str(tmp_path / "audit.jsonl")]
    monkeypatch.setattr(sys, "argv", args + (["--confidence-sizing"] if enabled else []))
    assert module.main() == 0
    config, options = seen[0]
    assert config.confidence_sizing is enabled
    assert (config.initial_base_value, config.initial_cash, config.order_quantity) == (8000000, 4000000, 1000)
    assert options["enriched_context"] is True
    assert options["max_cost_usd"] == 9.991924282
    assert options["max_model_requests"] == 120 and options["duration_seconds"] == 300
