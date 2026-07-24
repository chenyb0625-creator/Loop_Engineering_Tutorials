"""Run a bounded verify-act loop and persist its terminal evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUN_STATE_PATH = ROOT / "run_state.json"
LOG_PATH = ROOT / "loop.log"
MAX_ITERATIONS = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_script(filename: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / filename)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    if output:
        print(output)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(
            f"\n[{utc_now()}] {filename} exit_code={result.returncode}\n{output}\n"
        )
    return result


def save_terminal_state(status: str, iterations: int, verifier_code: int) -> None:
    RUN_STATE_PATH.write_text(
        json.dumps(
            {
                "status": status,
                "iterations_used": iterations,
                "max_iterations": MAX_ITERATIONS,
                "last_verifier_exit_code": verifier_code,
                "updated_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    LOG_PATH.write_text("", encoding="utf-8")
    for iteration in range(MAX_ITERATIONS + 1):
        print(f"\n--- VERIFY BEFORE ITERATION {iteration} ---")
        verifier = run_script("verify.py")

        if verifier.returncode == 0:
            save_terminal_state("DONE", iteration, verifier.returncode)
            print("\nTERMINAL STATE: DONE")
            return 0
        if verifier.returncode != 1:
            save_terminal_state("VERIFIER_ERROR", iteration, verifier.returncode)
            print("\nTERMINAL STATE: VERIFIER_ERROR")
            return 2
        if iteration >= MAX_ITERATIONS:
            save_terminal_state("BUDGET_EXHAUSTED", iteration, verifier.returncode)
            print("\nTERMINAL STATE: BUDGET_EXHAUSTED")
            return 1

        print(f"\n--- RUN WORKER ITERATION {iteration + 1} ---")
        worker = run_script("worker.py")
        if worker.returncode != 0:
            save_terminal_state("AGENT_ERROR", iteration + 1, verifier.returncode)
            print("\nTERMINAL STATE: AGENT_ERROR")
            return 2

    raise RuntimeError("unreachable control path")


if __name__ == "__main__":
    raise SystemExit(main())
