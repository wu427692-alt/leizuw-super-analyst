#!/bin/zsh
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer only supports macOS." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LABEL="com.winner.daily-stock-analysis"
RUNTIME_ROOT="${HOME}/Library/Application Support/财经情报台/runtime"
PYTHON_BIN="${RUNTIME_ROOT}/.venv/bin/python"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
APP_NAME="财经情报台.app"
APP_PARENT="${DSA_APP_DIR:-/Applications}"

if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  echo "Missing virtual environment: ${PROJECT_ROOT}/.venv/bin/python" >&2
  exit 1
fi
if [[ ! -f "${PROJECT_ROOT}/static/index.html" ]]; then
  echo "Missing Web build: ${PROJECT_ROOT}/static/index.html" >&2
  echo "Run npm --prefix apps/dsa-web run build first." >&2
  exit 1
fi
if [[ ! -w "${APP_PARENT}" ]]; then
  APP_PARENT="${HOME}/Applications"
fi

APP_PATH="${APP_PARENT}/${APP_NAME}"
LOG_DIR="${RUNTIME_ROOT}/logs"
RUNNER="${RUNTIME_ROOT}/scripts/run-macos-background.sh"
APP_LAUNCHER="${PROJECT_ROOT}/scripts/macos-app-launcher.sh"
PLIST_TMP="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/dsa-launchagent.XXXXXX")"

cleanup() {
  /bin/rm -f "${PLIST_TMP}"
}
trap cleanup EXIT

/bin/launchctl bootout "gui/$UID/${LABEL}" >/dev/null 2>&1 || true
for _ in {1..60}; do
  if ! /bin/launchctl print "gui/$UID/${LABEL}" >/dev/null 2>&1; then
    break
  fi
  /bin/sleep 0.25
done
if /bin/launchctl print "gui/$UID/${LABEL}" >/dev/null 2>&1; then
  echo "Existing LaunchAgent did not finish unloading within 15 seconds." >&2
  exit 1
fi

/bin/mkdir -p "${LAUNCH_AGENTS_DIR}" "${APP_PARENT}" "${RUNTIME_ROOT}" "${LOG_DIR}" "${RUNTIME_ROOT}/scripts"

for directory in .venv api bot data_provider src static strategies templates; do
  /usr/bin/ditto "${PROJECT_ROOT}/${directory}" "${RUNTIME_ROOT}/${directory}"
done
# Keep content-hashed chunks from earlier builds so a page that was already
# open during an upgrade can still lazy-load its routes. Vite changes the
# filename whenever content changes; index.html itself is always replaced.
for filename in main.py server.py; do
  /usr/bin/install -m 0644 "${PROJECT_ROOT}/${filename}" "${RUNTIME_ROOT}/${filename}"
done
/usr/bin/install -m 0755 "${PROJECT_ROOT}/scripts/run-macos-background.sh" "${RUNNER}"

share_runtime_path() {
  local source_path="$1"
  local runtime_path="$2"
  if [[ -L "${source_path}" ]]; then
    if [[ "$(readlink "${source_path}")" != "${runtime_path}" ]]; then
      echo "Unexpected symlink target: ${source_path}" >&2
      exit 1
    fi
    return
  fi
  if [[ -e "${runtime_path}" ]]; then
    echo "Refusing to overwrite existing runtime data: ${runtime_path}" >&2
    exit 1
  fi
  if [[ ! -e "${source_path}" ]]; then
    echo "Missing required runtime data: ${source_path}" >&2
    exit 1
  fi
  /bin/mv "${source_path}" "${runtime_path}"
  /bin/ln -s "${runtime_path}" "${source_path}"
}

share_runtime_path "${PROJECT_ROOT}/.env" "${RUNTIME_ROOT}/.env"
share_runtime_path "${PROJECT_ROOT}/data" "${RUNTIME_ROOT}/data"

WEBUI_PORT="$(cd "${RUNTIME_ROOT}" && "${PYTHON_BIN}" -c 'from src.config import setup_env, get_config; setup_env(); print(get_config().webui_port)')"
if [[ ! "${WEBUI_PORT}" =~ ^[0-9]+$ ]] || (( WEBUI_PORT < 1 || WEBUI_PORT > 65535 )); then
  echo "Invalid WEBUI_PORT: ${WEBUI_PORT}" >&2
  exit 1
fi

/usr/bin/plutil -create xml1 "${PLIST_TMP}"
/usr/bin/plutil -insert Label -string "${LABEL}" "${PLIST_TMP}"
/usr/bin/plutil -insert ProgramArguments -json "[\"/bin/zsh\",\"${RUNNER}\"]" "${PLIST_TMP}"
/usr/bin/plutil -insert WorkingDirectory -string "${RUNTIME_ROOT}" "${PLIST_TMP}"
/usr/bin/plutil -insert RunAtLoad -bool true "${PLIST_TMP}"
/usr/bin/plutil -insert KeepAlive -bool true "${PLIST_TMP}"
/usr/bin/plutil -insert ProcessType -string Background "${PLIST_TMP}"
/usr/bin/plutil -insert LimitLoadToSessionType -string Aqua "${PLIST_TMP}"
/usr/bin/plutil -insert ThrottleInterval -integer 10 "${PLIST_TMP}"
/usr/bin/plutil -insert StandardOutPath -string "${LOG_DIR}/launchd.stdout.log" "${PLIST_TMP}"
/usr/bin/plutil -insert StandardErrorPath -string "${LOG_DIR}/launchd.stderr.log" "${PLIST_TMP}"
/usr/bin/plutil -insert EnvironmentVariables -json "{\"PYTHONUNBUFFERED\":\"1\",\"PATH\":\"${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\"}" "${PLIST_TMP}"
/usr/bin/plutil -lint "${PLIST_TMP}"
/usr/bin/install -m 0644 "${PLIST_TMP}" "${PLIST_PATH}"

/bin/rm -rf "${APP_PATH}"
/bin/mkdir -p "${APP_PATH}/Contents/MacOS" "${APP_PATH}/Contents/Resources"
/usr/bin/install -m 0755 "${APP_LAUNCHER}" "${APP_PATH}/Contents/MacOS/launcher"
/usr/bin/printf '%s\n' "${RUNTIME_ROOT}" > "${APP_PATH}/Contents/Resources/project-root.txt"
/usr/bin/printf '%s\n' "${WEBUI_PORT}" > "${APP_PATH}/Contents/Resources/webui-port.txt"

/usr/bin/plutil -create xml1 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleDevelopmentRegion -string zh_CN "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleDisplayName -string 财经情报台 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleExecutable -string launcher "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIdentifier -string com.winner.daily-stock-analysis.launcher "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleInfoDictionaryVersion -string 6.0 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleName -string 财经情报台 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundlePackageType -string APPL "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleShortVersionString -string 1.0 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleVersion -string 1 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIconFile -string AppIcon "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert LSMinimumSystemVersion -string 12.0 "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert NSHighResolutionCapable -bool true "${APP_PATH}/Contents/Info.plist"
/usr/bin/plutil -insert LSUIElement -bool true "${APP_PATH}/Contents/Info.plist"

ICONSET_PATH="${PROJECT_ROOT}/docs/assets/dsa_vi/darklogo.iconset"
if [[ -d "${ICONSET_PATH}" ]]; then
  /usr/bin/iconutil -c icns "${ICONSET_PATH}" -o "${APP_PATH}/Contents/Resources/AppIcon.icns"
fi
/usr/bin/codesign --force --deep --sign - "${APP_PATH}" >/dev/null

if ! /usr/bin/defaults read com.apple.dock persistent-apps 2>/dev/null | /usr/bin/grep -Fq "com.winner.daily-stock-analysis.launcher"; then
  APP_URL="$(${PYTHON_BIN} -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).as_uri())' "${APP_PATH}")"
  /usr/bin/defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>${APP_URL}</string><key>_CFURLStringType</key><integer>15</integer></dict><key>file-label</key><string>财经情报台</string></dict><key>tile-type</key><string>file-tile</string></dict>"
  /usr/bin/killall Dock >/dev/null 2>&1 || true
fi

if ! /bin/launchctl bootstrap "gui/$UID" "${PLIST_PATH}"; then
  # launchd can briefly retain the old job after bootout during a hot upgrade.
  # Accept a job that became visible despite the error; otherwise retry once.
  /bin/sleep 2
  if ! /bin/launchctl print "gui/$UID/${LABEL}" >/dev/null 2>&1; then
    /bin/launchctl bootstrap "gui/$UID" "${PLIST_PATH}"
  fi
fi
/bin/launchctl enable "gui/$UID/${LABEL}"
/bin/launchctl kickstart -k "gui/$UID/${LABEL}"

WEBUI_URL="http://127.0.0.1:${WEBUI_PORT}"
READY=false
for _ in {1..180}; do
  if /usr/bin/curl --silent --fail --max-time 2 "${WEBUI_URL}/api/health" >/dev/null 2>&1; then
    READY=true
    break
  fi
  /bin/sleep 0.5
done
if [[ "${READY}" != true ]]; then
  echo "LaunchAgent was installed but the service did not become healthy within 90 seconds." >&2
  /usr/bin/tail -n 40 "${LOG_DIR}/launchd.stderr.log" >&2 || true
  exit 1
fi

echo "Installed LaunchAgent: ${PLIST_PATH}"
echo "Installed application: ${APP_PATH}"
echo "Dock application ready: 财经情报台"
echo "Installed runtime: ${RUNTIME_ROOT}"
echo "Health check passed: ${WEBUI_URL}/api/health"
echo "Web interface: ${WEBUI_URL}"
