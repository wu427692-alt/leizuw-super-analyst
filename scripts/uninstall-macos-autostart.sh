#!/bin/zsh
set -euo pipefail

LABEL="com.winner.daily-stock-analysis"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_ROOT="${HOME}/Library/Application Support/财经情报台/runtime"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
SYSTEM_APP="/Applications/财经情报台.app"
USER_APP="${HOME}/Applications/财经情报台.app"

/bin/launchctl bootout "gui/$UID/${LABEL}" >/dev/null 2>&1 || true
/bin/rm -f "${PLIST_PATH}"
/bin/rm -rf "${SYSTEM_APP}" "${USER_APP}"

restore_runtime_path() {
  local project_path="$1"
  local runtime_path="$2"
  if [[ -L "${project_path}" ]] && [[ "$(readlink "${project_path}")" == "${runtime_path}" ]]; then
    /bin/unlink "${project_path}"
    /bin/mv "${runtime_path}" "${project_path}"
  fi
}

restore_runtime_path "${PROJECT_ROOT}/.env" "${RUNTIME_ROOT}/.env"
restore_runtime_path "${PROJECT_ROOT}/data" "${RUNTIME_ROOT}/data"

echo "Removed the login background service and application launcher."
echo "The live .env and data directory were restored to the project."
echo "The deployed runtime code and logs remain at: ${RUNTIME_ROOT}"
