#!/usr/bin/env python3
"""Read-only host inventory for a future Mac mini migration; never starts services.

No application imports, database opens, external probes, installs or secret reads.
Docker inspection is restricted to a local Unix socket. Exit 0 means the inventory
ran, not that a deployment is ready. Use --json for a machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess


GIB = 1024 ** 3


def command(args: list[str]) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=6, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def architecture(value: str) -> str:
    return {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64",
            "amd64": "amd64"}.get(value.lower(), "unknown")


def local_docker_endpoint() -> bool:
    # Docker context overrides DOCKER_HOST. Never use a remote context even for
    # a nominally read-only command; this tool inventories this Mac only.
    context = os.environ.get("DOCKER_CONTEXT")
    host = os.environ.get("DOCKER_HOST")
    if host and not context:
        return host.startswith("unix:///")
    endpoint = command(["docker", "context", "inspect", "--format",
                        "{{.Endpoints.docker.Host}}", *([context] if context else [])])
    return bool(endpoint and endpoint.startswith("unix:///"))


def port_in_use(port: int) -> bool:
    # A connection test, not a listener or a request to the application.
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def runtime_inventory(runtime: Path) -> dict:
    required = ["data", "logs", "reports", "strategies", "config", "tyc", "longbridge_tokens"]
    missing = [name for name in required if not (runtime / name).is_dir()]
    files = {"data/stock_analysis.db": None, "config/.env": None,
             "data/.admin_password_hash": None, "data/.session_secret": 32,
             "data/.user_auth_secret": 32}
    invalid = []
    for name, expected_size in files.items():
        path = runtime / name
        try:
            size = path.stat().st_size
            if not path.is_file() or size <= 0 or (expected_size and size != expected_size):
                invalid.append(name)
        except OSError:
            invalid.append(name)
    # Only metadata is read here, not the credentials, database or .env contents.
    return {"missing_directories": missing, "missing_or_invalid_files": invalid,
            "content_not_verified": True,
            "warning": "Restore into a separate working copy; never mount the immutable backup."}


def inspect_host(disk: Path, runtime: Path | None = None, image: str | None = None,
                 port: int = 18000) -> dict:
    system, machine = platform.system(), platform.machine()
    memory = command(["sysctl", "-n", "hw.memsize"]) if system == "Darwin" else None
    free = shutil.disk_usage(disk).free
    warnings = ["This is the machine running the command, not the future target unless run there.",
                "Home-network access, power recovery, backup restore and business flows are untested."]
    if system != "Darwin":
        warnings.append("Run again on the actual Mac mini to validate macOS prerequisites.")
    if free < 20 * GIB:
        warnings.append("Less than 20 GiB free: budget raw staging, bundle, runtime copy and Docker image separately.")
    docker = {"cli_available": bool(shutil.which("docker"))}
    if docker["cli_available"]:
        docker["compose_available"] = command(["docker", "compose", "version", "--short"]) is not None
        docker["local_endpoint"] = local_docker_endpoint()
        if docker["local_endpoint"]:
            info = command(["docker", "info", "--format",
                            '{"architecture":"{{.Architecture}}","memory_bytes":{{.MemTotal}}}'])
            try:
                data = json.loads(info or "null")
                docker["daemon_reachable"] = isinstance(data, dict)
                if isinstance(data, dict):
                    docker["vm_architecture"] = architecture(str(data.get("architecture", "")))
                    docker["vm_memory_bytes"] = data.get("memory_bytes")
            except (TypeError, ValueError):
                docker["daemon_reachable"] = False
            if image and docker.get("daemon_reachable"):
                image_arch = command(["docker", "image", "inspect", "--format",
                                      "{{.Architecture}}", "--", image])
                docker["requested_image_present"] = bool(image_arch)
                docker["requested_image_native"] = bool(
                    image_arch and architecture(image_arch) == architecture(machine)
                    and architecture(machine) != "unknown")
        else:
            warnings.append("Remote/unknown Docker endpoint skipped; select a local Docker context first.")
    legacy_agents = list((Path.home() / "Library/LaunchAgents").glob("*dsa*"))
    legacy_agents += list((Path.home() / "Library/LaunchAgents").glob("*stock*"))
    if legacy_agents:
        warnings.append("Legacy login services may exist; review duplicate collectors before cutover.")
    return {"inventory_only": True, "production_ready": False,
            "host": {"system": system, "architecture": architecture(machine),
                     "memory_bytes": int(memory) if memory and memory.isdigit() else None,
                     "free_disk_bytes": free},
            "docker": docker, "cloudflared_installed": bool(shutil.which("cloudflared")),
            "loopback_port": port, "loopback_port_in_use": port_in_use(port),
            "legacy_launchagent_count": len(set(legacy_agents)),
            "runtime": runtime_inventory(runtime) if runtime else None,
            "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk", type=Path, default=Path.home(), help="Existing directory on the target disk")
    parser.add_argument("--runtime-dir", type=Path, help="Optional restored WORKING runtime copy")
    parser.add_argument("--image", help="Optional existing local image; never pulls or builds")
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not args.disk.expanduser().is_dir():
        parser.error("Use an existing --disk directory and a --port between 1 and 65535")
    result = inspect_host(args.disk.expanduser(),
                          args.runtime_dir.expanduser() if args.runtime_dir else None,
                          args.image, args.port)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        host = result["host"]
        print(f"Host: {host['system']} / {host['architecture']}")
        print(f"Memory: {(host['memory_bytes'] / GIB) if host['memory_bytes'] else 'unknown'} GiB")
        print(f"Disk free: {host['free_disk_bytes'] / GIB:.1f} GiB")
        print("Docker:", json.dumps(result["docker"]))
        print("cloudflared installed:", result["cloudflared_installed"])
        print("Port occupied:", result["loopback_port_in_use"])
        if result["runtime"]:
            print("Runtime metadata:", json.dumps(result["runtime"]))
        for warning in result["warnings"]:
            print("NOTE:", warning)
        print("Inventory only; production is NOT approved or started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
