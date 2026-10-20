#!/usr/bin/env bash
set -euo pipefail

container_name="stock-server"
project_dir="${LEZIWU_PROJECT_DIR:-/opt/financial-intelligence-platform}"
lock_file="${LEZIWU_GUARD_LOCK_FILE:-/run/leziwu-health-guard.lock}"
health_url="${LEZIWU_GUARD_HEALTH_URL:-http://127.0.0.1:8000/health}"

exec 9>"${lock_file}"
flock -n 9 || exit 0

container_status="$(docker inspect -f '{{.State.Status}}' "${container_name}" 2>/dev/null || true)"
if [[ -z "${container_status}" ]]; then
  logger -t leziwu-health-guard "web container missing; recreating it"
  cd "${project_dir}"
  docker compose -f docker/docker-compose.yml -f docker/docker-compose.cloud.yml up -d --no-deps server
  exit 0
fi

if [[ "${container_status}" != "running" ]]; then
  logger -t leziwu-health-guard "web container state=${container_status}; restarting it"
  docker restart "${container_name}" >/dev/null
  exit 0
fi

health_status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_name}")"
probe_identity="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "${container_name}")"
if [[ "${health_status}" == "starting" ]]; then
  exit 0
fi

# A broken local probe must not restart a healthy application. The health
# endpoint is direct to the API, independent of Nginx redirects and proxies.
python_bin="$(command -v python3 || true)"
if [[ -z "${python_bin}" ]] || ! "${python_bin}" -c 'import json' >/dev/null 2>&1; then
  logger -t leziwu-health-guard "health probe unavailable: Python JSON parser; application left running"
  exit 1
fi

for attempt_number in 1 2 3; do
  if curl --fail --silent --show-error --noproxy '*' --max-time 5 \
      --write-out '\n%{http_code}' "${health_url}" | "${python_bin}" -c '
import json, sys
try:
    body, status = sys.stdin.read().rsplit("\n", 1)
    payload = json.loads(body)
    healthy = status == "200" and isinstance(payload, dict) and payload.get("status") == "ok"
except (ValueError, TypeError):
    healthy = False
sys.exit(0 if healthy else 1)
'; then
    exit 0
  fi
  sleep 2
done

# A deploy/restart can happen while the three probes run. Their failures
# describe the old process, not the replacement still warming up.
current_identity="$(docker inspect -f '{{.Id}}|{{.State.StartedAt}}' "${container_name}" 2>/dev/null || true)"
current_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_name}" 2>/dev/null || true)"
if [[ "${current_identity}" != "${probe_identity}" || "${current_health}" == "starting" ]]; then
  logger -t leziwu-health-guard "container changed or is starting during probes; deferring recovery"
  exit 0
fi

logger -t leziwu-health-guard "health probe failed three times; restarting web container"
docker restart "${container_name}" >/dev/null
