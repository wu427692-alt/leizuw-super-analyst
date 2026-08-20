#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python virtual environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

WEBUI_PORT="$(${PYTHON_BIN} -c 'from src.config import setup_env, get_config; setup_env(); print(get_config().webui_port)')"
if [[ ! "${WEBUI_PORT}" =~ ^[0-9]+$ ]] || (( WEBUI_PORT < 1 || WEBUI_PORT > 65535 )); then
  echo "Invalid WEBUI_PORT: ${WEBUI_PORT}" >&2
  exit 1
fi

exec "${PYTHON_BIN}" -m uvicorn server:app --host 127.0.0.1 --port "${WEBUI_PORT}"
