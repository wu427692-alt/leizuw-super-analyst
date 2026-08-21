#!/bin/zsh
set -u

CONTENTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(/bin/cat "${CONTENTS_DIR}/Resources/project-root.txt" 2>/dev/null)"
PORT_FILE="${CONTENTS_DIR}/Resources/webui-port.txt"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
LAUNCH_LABEL="com.winner.daily-stock-analysis"
LAUNCH_AGENT="${HOME}/Library/LaunchAgents/${LAUNCH_LABEL}.plist"

show_error() {
  local message="$1"
  /usr/bin/osascript -e "display alert \"财经情报台无法启动\" message \"${message}\" as critical"
}

service_ready() {
  /usr/bin/curl --silent --fail --max-time 2 "${WEBUI_URL}/api/health" >/dev/null 2>&1
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  show_error "后台运行环境不存在，请重新运行 macOS 自启动安装脚本。"
  exit 1
fi

if [[ ! -f "${PORT_FILE}" ]]; then
  show_error "应用端口文件不存在，请重新运行 macOS 自启动安装脚本。"
  exit 1
fi
WEBUI_PORT="$(/usr/bin/tr -d '[:space:]' < "${PORT_FILE}")"
case "${WEBUI_PORT}" in
  ''|*[!0-9]*)
    show_error "应用端口文件无效，请重新运行 macOS 自启动安装脚本。"
    exit 1
    ;;
esac
if (( WEBUI_PORT < 1 || WEBUI_PORT > 65535 )); then
  show_error "应用端口超出有效范围，请重新运行 macOS 自启动安装脚本。"
  exit 1
fi
WEBUI_URL="http://127.0.0.1:${WEBUI_PORT}"

if ! service_ready; then
  if ! /bin/launchctl print "gui/$UID/${LAUNCH_LABEL}" >/dev/null 2>&1; then
    if [[ ! -f "${LAUNCH_AGENT}" ]] || ! /bin/launchctl bootstrap "gui/$UID" "${LAUNCH_AGENT}" >/dev/null 2>&1; then
      show_error "登录启动项不存在或无法加载，请重新运行 macOS 自启动安装脚本。"
      exit 1
    fi
  fi
  /bin/launchctl enable "gui/$UID/${LAUNCH_LABEL}" >/dev/null 2>&1 || true
  /bin/launchctl kickstart -k "gui/$UID/${LAUNCH_LABEL}" >/dev/null 2>&1 || true
fi

for _ in {1..180}; do
  if service_ready; then
    if [[ "${DSA_LAUNCHER_NO_OPEN:-0}" != "1" ]]; then
      /usr/bin/open "${WEBUI_URL}"
    fi
    exit 0
  fi
  /bin/sleep 0.5
done

show_error "后台服务在 90 秒内未就绪。日志位置：~/Library/Application Support/财经情报台/runtime/logs/launchd.stderr.log"
exit 1
