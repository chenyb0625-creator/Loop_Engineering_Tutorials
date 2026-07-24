"""Run a bounded, policy-aware, stagnation-detecting repair loop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import (
    ROOT,
    append_event,
    run_python,
    save_run_state,
    workspace_fingerprint,
)


WORKERS = {
    "codex": ROOT / "scripts" / "workers" / "codex_worker.py",
    "fix": ROOT / "scripts" / "workers" / "fix_worker.py",
    "noop": ROOT / "scripts" / "workers" / "noop_worker.py",
    "reward-hacker": ROOT / "scripts" / "workers" / "reward_hacker.py",
}
POLICY = ROOT / "scripts" / "policy_check.py"
VERIFIER = ROOT / "scripts" / "verify.py"
VERIFICATION_REPORT = ROOT / "artifacts" / "verification.json"


def show_result(label: str, result) -> None:
    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    print(f"\n--- {label} | exit_code={result.returncode} ---")
    if output:
        print(output)


def stop(status: str, exit_code: int, **payload: object) -> int:
    save_run_state(status, **payload)
    print(f"\nTERMINAL STATE: {status}")
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=WORKERS, default="fix")
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--stagnation-limit", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_iterations < 0 or args.stagnation_limit < 1:
        raise SystemExit("budgets must be non-negative and stagnation-limit >= 1")

    last_failure_state: tuple[str, str | None] | None = None
    stagnant_rounds = 0
    append_event(
        "run_started",
        worker=args.worker,
        max_iterations=args.max_iterations,
        stagnation_limit=args.stagnation_limit,
    )

    for iteration in range(args.max_iterations + 1):
        policy = run_python(POLICY)
        show_result("POLICY", policy)
        append_event("policy_checked", iteration=iteration, exit_code=policy.returncode)
        if policy.returncode == 1:
            return stop("POLICY_VIOLATION", 2, iteration=iteration)
        if policy.returncode != 0:
            return stop("POLICY_ERROR", 2, iteration=iteration)

        verifier = run_python(VERIFIER)
        show_result("VERIFY", verifier)
        fingerprint = workspace_fingerprint()
        evidence = (
            json.loads(VERIFICATION_REPORT.read_text(encoding="utf-8"))
            if VERIFICATION_REPORT.exists()
            else {}
        )
        failure_signature = evidence.get("failure_signature")
        append_event(
            "verified",
            iteration=iteration,
            exit_code=verifier.returncode,
            workspace_fingerprint=fingerprint,
            failure_signature=failure_signature,
        )
        if verifier.returncode == 0:
            return stop(
                "DONE",
                0,
                iteration=iteration,
                workspace_fingerprint=fingerprint,
            )
        if verifier.returncode != 1:
            return stop("VERIFIER_ERROR", 2, iteration=iteration)
        if iteration >= args.max_iterations:
            return stop(
                "BUDGET_EXHAUSTED",
                1,
                iteration=iteration,
                workspace_fingerprint=fingerprint,
            )

        failure_state = (fingerprint, failure_signature)
        if failure_state == last_failure_state:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        last_failure_state = failure_state
        if stagnant_rounds >= args.stagnation_limit:
            return stop(
                "STAGNATED",
                1,
                iteration=iteration,
                stagnant_rounds=stagnant_rounds,
                workspace_fingerprint=fingerprint,
            )

        before = fingerprint
        worker = run_python(WORKERS[args.worker])
        show_result(f"WORKER {args.worker}", worker)
        after = workspace_fingerprint()
        append_event(
            "worker_finished",
            iteration=iteration + 1,
            worker=args.worker,
            exit_code=worker.returncode,
            before_fingerprint=before,
            after_fingerprint=after,
        )
        if worker.returncode != 0:
            return stop("AGENT_ERROR", 2, iteration=iteration + 1)

    raise RuntimeError("unreachable control path")


if __name__ == "__main__":
    raise SystemExit(main())
