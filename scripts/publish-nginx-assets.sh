#!/usr/bin/env bash
set -euo pipefail

container_name="${LEZIWU_CONTAINER_NAME:-stock-server}"
release_root="${LEZIWU_STATIC_RELEASE_ROOT:-/var/www/leziwu-static-releases}"
active_link="${LEZIWU_STATIC_ROOT:-/var/www/leziwu-static}"
release_id="$(date -u +%Y%m%dT%H%M%SZ)"
release_dir="${release_root}/${release_id}"
next_link="${active_link}.next"

if ! docker inspect "${container_name}" >/dev/null 2>&1; then
  echo "Container not found: ${container_name}" >&2
  exit 1
fi

install -d -m 0755 "${release_root}" "${release_dir}"
docker cp "${container_name}:/app/static/." "${release_dir}/"

if [[ ! -s "${release_dir}/index.html" || ! -d "${release_dir}/assets" ]]; then
  echo "Published frontend bundle is incomplete: ${release_dir}" >&2
  exit 1
fi

ln -sfn "${release_dir}" "${next_link}"
mv -Tf "${next_link}" "${active_link}"

echo "Published nginx frontend assets: ${active_link} -> ${release_dir}"
