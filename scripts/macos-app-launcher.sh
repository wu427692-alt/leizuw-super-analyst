#!/bin/zsh
set -u

CONTENTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(/bin/cat "${CONTENTS_DIR}/Resources/project-root.txt" 2>/dev/null)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
LAUNCH_LABEL="com.winner.daily-stock-analysis"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  /usr/bin/osascript -e 'display alert "财经情报台无法启动" message "项目目录或 Python 虚拟环境不存在，请重新运行 macOS 自启动安装脚本。" as critical'
  exit 1
fi

cd "${PROJECT_ROOT}" || exit 1
WEBUI_PORT="$(${PYTHON_BIN} -c 'from src.config import setup_env, get_config; setup_env(); print(get_config().webui_port)' 2>/dev/null)"
WEBUI_URL="http://127.0.0.1:${WEBUI_PORT}"

if ! /usr/bin/curl --silent --fail --max-time 1 "${WEBUI_URL}/api/health" >/dev/null 2>&1; then
  /bin/launchctl kickstart -k "gui/$UID/${LAUNCH_LABEL}" >/dev/null 2>&1 || true
fi

for _ in {1..60}; do
  if /usr/bin/curl --silent --fail --max-time 1 "${WEBUI_URL}/api/health" >/dev/null 2>&1; then
    /usr/bin/open "${WEBUI_URL}"
    exit 0
  fi
  /bin/sleep 0.5
done

/usr/bin/osascript -e 'display alert "财经情报台后台尚未就绪" message "请检查项目 logs/launchd.stderr.log，或重新运行 macOS 自启动安装脚本。" as warning'
exit 1
