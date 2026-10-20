"""Exercise guard control flow without contacting Docker or restarting services."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("mode,restarts", [("deploy", False), ("failure", True), ("healthy", False)])
def test_guard_does_not_restart_replacement_container(tmp_path, mode, restarts):
    # Bash functions override the external tools; all files stay in tmp_path.
    wrapper = r'''
docker() {
  case "$*" in
    *State.Status*) echo running ;;
    *State.Health*) echo healthy ;;
    *State.StartedAt*)
      if [[ "$MODE" == deploy && -f "$PROBE_SEEN" ]]; then echo new; else echo old; fi ;;
    restart*) echo restart >> "$ACTIONS" ;;
    *) return 1 ;;
  esac
}
curl() { touch "$PROBE_SEEN"; if [[ "$MODE" == healthy ]]; then printf '{"status":"ok"}\n200'; else return 1; fi; }
logger() { :; }
sleep() { :; }
flock() { :; }
export -f docker curl logger sleep flock
bash "$GUARD_SCRIPT"
'''
    actions = tmp_path / "actions"
    env = {**os.environ, "MODE": mode, "ACTIONS": str(actions),
           "PROBE_SEEN": str(tmp_path / "seen"),
           "LEZIWU_GUARD_LOCK_FILE": str(tmp_path / "lock"),
           "GUARD_SCRIPT": str(Path(__file__).resolve().parents[1] / "docker/systemd/leziwu-health-guard.sh")}
    result = subprocess.run(["bash", "-c", wrapper], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert actions.exists() is restarts
