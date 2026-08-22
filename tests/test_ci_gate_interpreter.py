from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ci_gate_honors_explicit_python_interpreter(tmp_path: Path) -> None:
    call_log = tmp_path / "python-calls.log"
    interpreter = tmp_path / "python-stub"
    interpreter.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$PYTHON_STUB_LOG"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)

    env = os.environ.copy()
    env["PYTHON_BIN"] = str(interpreter)
    env["PYTHON_STUB_LOG"] = str(call_log)
    subprocess.run(
        ["bash", "scripts/ci_gate.sh", "syntax"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 4
    assert all(call.startswith("-m py_compile ") for call in calls)
