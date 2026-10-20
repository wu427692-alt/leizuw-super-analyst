#!/usr/bin/env bash
set -euo pipefail

container_name="${LEZIWU_CONTAINER_NAME:-stock-server}"
release_root="${LEZIWU_STATIC_RELEASE_ROOT:-/var/www/leziwu-static-releases}"
active_link="${LEZIWU_STATIC_ROOT:-/var/www/leziwu-static}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-publish}"

if [[ $# -gt 1 || ( "${mode}" != "publish" && "${mode}" != "--check" && "${mode}" != "--publish-new-version" ) ]]; then
  echo "Usage: $0 [--check|--publish-new-version]" >&2
  exit 2
fi

if [[ "$(docker inspect -f '{{.State.Running}}' "${container_name}" 2>/dev/null || true)" != "true" ]]; then
  echo "Container missing or not running: ${container_name}" >&2
  exit 1
fi

# --check only writes this disposable snapshot, never the published release.
snapshot_dir="$(mktemp -d)"
trap 'rm -rf -- "${snapshot_dir}"' EXIT
install -d "${snapshot_dir}/static"
docker cp "${container_name}:/app/static/." "${snapshot_dir}/static/"

python3 -B - "${script_dir}" "${snapshot_dir}/static" "${active_link}" "${mode}" <<'PY'
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from check_static_assets import check_static_dir

incoming, active, mode = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path, label):
    refs, missing = check_static_dir(path)
    if not refs or missing:
        raise ValueError(f"{label}: no asset references or missing assets: {missing}")
    for ref in refs:
        candidate = path / ref.lstrip("/")
        if not candidate.resolve().is_relative_to(path.resolve()):
            raise ValueError(f"{label}: asset escapes the release: {ref}")
    print(f"{label} index SHA256={digest(path / 'index.html')} assets={','.join(refs)}")


try:
    validate(incoming, "Container")
    has_active = active.exists() or active.is_symlink()
    if mode == "--check" or (has_active and mode != "--publish-new-version"):
        validate(active, "Published")
    if has_active and (active / "index.html").is_file():
        if digest(incoming / "index.html") != digest(active / "index.html") and mode != "--publish-new-version":
            raise ValueError("Frontend entry mismatch; refusing to replace the published version. "
                             "Restore the approved frontend in the image, or explicitly use "
                             "--publish-new-version for an approved frontend release.")
    elif mode == "--check":
        raise ValueError("Published frontend is missing")

    # Content-hashed URLs must never acquire different bytes. Keep old lazy
    # chunks below so already-open tabs can finish loading their own version.
    if (active / "assets").is_dir():
        for old in (active / "assets").rglob("*"):
            if old.is_symlink():
                raise ValueError(f"Published assets contain a symlink: {old.name}")
            new = incoming / old.relative_to(active)
            if old.is_file() and new.exists() and (not new.is_file() or digest(old) != digest(new)):
                raise ValueError(f"Immutable asset collision: {old.relative_to(active)}")
except (OSError, ValueError) as exc:
    print(f"Frontend release check failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY

if [[ "${mode}" == "--check" ]]; then
  echo "Container and published frontend entries are consistent."
  exit 0
fi

if [[ -d "${active_link}/assets" ]]; then
  # Shared names were checked byte-for-byte above; copying them is harmless.
  # Avoid cp -n, whose skipped-file exit status differs across platforms.
  cp -a "${active_link}/assets/." "${snapshot_dir}/static/assets/"
fi
install -d -m 0755 "${release_root}"
release_dir="$(mktemp -d "${release_root}/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
cp -a "${snapshot_dir}/static/." "${release_dir}/"
chmod -R a+rX "${release_dir}"
next_link="${active_link}.next.${release_dir##*/}"
ln -sfn "${release_dir}" "${next_link}"
# os.replace provides atomic symlink replacement on Linux and macOS; plain
# portable mv would follow an existing directory symlink instead.
python3 - "${next_link}" "${active_link}" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
PY

echo "Published nginx frontend assets: ${active_link} -> ${release_dir}"
