"""Optional Codex CLI action adapter for the Loop Lab."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PROMPT = """\
You are the Action module inside a controlled repair loop.

Read goal.md and inspect the current workspace. Fix the implementation in
workspace/src/statkit/normalize.py.

Hard boundaries:
- You may modify only workspace/src/statkit/normalize.py.
- Do not modify workspace/tests, goal.md, policy.json, scripts, or artifacts.
- Do not decide whether the system is DONE. The external verifier and
  controller own the terminal state.
- Keep the change minimal and do not add dependencies.

When finished, summarize the candidate change. Your summary is not completion
evidence.
"""


def main() -> int:
    executable = shutil.which("codex")
    if not executable:
        print("WORKER_ERROR: codex CLI was not found", file=sys.stderr)
        return 2

    result = subprocess.run(
        [
            executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--json",
            PROMPT,
        ],
        cwd=ROOT,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
