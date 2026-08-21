#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
RUN_DIR="${PROJECT_ROOT}/.run"
PID_FILE="${RUN_DIR}/server.pid"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python virtual environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

# Avoid LiteLLM's optional remote pricing-catalogue request during boot.
export LITELLM_LOCAL_MODEL_COST_MAP="${LITELLM_LOCAL_MODEL_COST_MAP:-True}"

/bin/mkdir -p "${RUN_DIR}"
if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(/bin/cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ "${EXISTING_PID}" =~ ^[0-9]+$ ]] && /bin/kill -0 "${EXISTING_PID}" 2>/dev/null; then
    EXISTING_COMMAND="$(/bin/ps -p "${EXISTING_PID}" -o command= 2>/dev/null || true)"
    if [[ "${EXISTING_COMMAND}" == *"uvicorn server:app"* ]]; then
      echo "Server process ${EXISTING_PID} is already running; refusing to start a duplicate." >&2
      exit 0
    fi
  fi
fi
/usr/bin/printf '%s\n' "$$" > "${PID_FILE}"

WEBUI_PORT="$(${PYTHON_BIN} -c 'from src.config import setup_env, get_config; setup_env(); print(get_config().webui_port)')"
if [[ ! "${WEBUI_PORT}" =~ ^[0-9]+$ ]] || (( WEBUI_PORT < 1 || WEBUI_PORT > 65535 )); then
  echo "Invalid WEBUI_PORT: ${WEBUI_PORT}" >&2
  exit 1
fi

exec "${PYTHON_BIN}" -m uvicorn server:app --host 127.0.0.1 --port "${WEBUI_PORT}"
