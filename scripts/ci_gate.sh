#!/usr/bin/env bash

set -euo pipefail

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
      echo "backend-gate: PYTHON_BIN is not executable: ${PYTHON_BIN}" >&2
      exit 127
    fi
    return
  fi

  if [[ -n "${VIRTUAL_ENV:-}" ]] && command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "backend-gate: Python interpreter not found (tried python and python3)" >&2
    exit 127
  fi
}

resolve_python
export PYTHON_BIN

syntax_check() {
  echo "==> backend-gate: Python syntax check"
  "${PYTHON_BIN}" -m py_compile main.py src/config.py src/auth.py src/analyzer.py src/notification.py
  "${PYTHON_BIN}" -m py_compile src/storage.py src/scheduler.py src/search_service.py
  "${PYTHON_BIN}" -m py_compile src/market_analyzer.py src/stock_analyzer.py
  "${PYTHON_BIN}" -m py_compile data_provider/*.py
}

flake8_checks() {
  echo "==> backend-gate: flake8 critical checks"
  "${PYTHON_BIN}" -m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
}

deterministic_checks() {
  echo "==> backend-gate: local deterministic checks"
  ./scripts/test.sh code
  ./scripts/test.sh yfinance
}

offline_test_suite() {
  echo "==> backend-gate: offline test suite"
  "${PYTHON_BIN}" -m pytest -m "not network"
}

run_all() {
  syntax_check
  flake8_checks
  deterministic_checks
  offline_test_suite
  echo "==> backend-gate: all checks passed"
}

phase="${1:-all}"

case "$phase" in
  all)
    run_all
    ;;
  syntax)
    syntax_check
    ;;
  flake8)
    flake8_checks
    ;;
  deterministic)
    deterministic_checks
    ;;
  offline-tests)
    offline_test_suite
    ;;
  *)
    echo "Usage: $0 [all|syntax|flake8|deterministic|offline-tests]" >&2
    exit 2
    ;;
esac
