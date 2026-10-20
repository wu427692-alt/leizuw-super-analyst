#!/usr/bin/env python3
"""macOS supervisor: dedicated Keychain item -> anonymous pipe -> Docker exec.

No credential is written to argv, Docker configuration, environment, disk or
logs. Provisioning the Keychain item is deliberately outside this program.
"""
from __future__ import annotations

import argparse
import platform
import signal
import subprocess
import sys
from pathlib import Path

MAX_KEY_BYTES = 8192


def load_key(service: str, account: str, *, run=subprocess.run) -> bytes:
    result = run(["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if result.returncode != 0:
        raise RuntimeError("dedicated Keychain item unavailable")
    raw = result.stdout
    key = raw[:-1] if raw.endswith(b"\n") else raw
    if not 1 <= len(key) <= MAX_KEY_BYTES or any(byte < 33 or byte > 126 for byte in key):
        raise RuntimeError("dedicated Keychain item invalid")
    return key


def docker_command(args):
    if not Path(args.docker).is_absolute() or not 1 <= args.port <= 65535:
        raise ValueError("invalid relay configuration")
    for value in (args.service, args.account, args.context, args.container):
        if not value or value.startswith("-") or any(ord(char) < 32 for char in value):
            raise ValueError("invalid relay configuration")
    return [args.docker, "--context", args.context, "exec", "-i", "-u", "1000", "-w", "/app",
            args.container, "python", "-m", "src.services.jev_runtime_relay", "--port", str(args.port)]


def supervise(args, *, key_loader=load_key, popen=subprocess.Popen):
    command = docker_command(args)
    key = key_loader(args.service, args.account)
    process = None
    try:
        process = popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.stdin.write(key + b"\n")
        process.stdin.flush()
        key = None
        # communicate() would close stdin immediately and invalidate the relay
        # lease. Keep it open until Docker exec or the supervisor exits.
        return process.wait()
    finally:
        key = None
        if process is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Supervise a Keychain-backed Jev container relay")
    parser.add_argument("--service", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    if platform.system() != "Darwin":
        print("Jev relay host requires macOS", file=sys.stderr)
        return 1

    def stop(signum, frame):
        raise KeyboardInterrupt

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    try:
        return supervise(args)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Jev relay host unavailable; check Keychain access and container readiness", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
