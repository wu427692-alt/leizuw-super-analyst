#!/usr/bin/env bash
set -euo pipefail

container_name="stock-server"
project_dir="${LEZIWU_PROJECT_DIR:-/opt/financial-intelligence-platform}"
lock_file="/run/leziwu-health-guard.lock"

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
if [[ "${health_status}" == "starting" ]]; then
  exit 0
fi

for attempt_number in 1 2 3; do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1/api/health >/dev/null; then
    exit 0
  fi
  sleep 2
done

logger -t leziwu-health-guard "health probe failed three times; restarting web container"
docker restart "${container_name}" >/dev/null
