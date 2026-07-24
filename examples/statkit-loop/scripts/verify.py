"""Run deterministic tests and emit structured, freshness-bound evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

from common import ARTIFACTS, ROOT, SOURCE_ROOT, TEST_ROOT, utc_now, workspace_fingerprint


EVIDENCE_PATH = ARTIFACTS / "verification.json"


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if not SOURCE_ROOT.exists() or not TEST_ROOT.exists():
        print("VERDICT: ERROR")
        print("REASON: workspace missing; run scripts/reset.py")
        return 2

    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(TEST_ROOT),
        "-v",
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONIOENCODING"] = "utf-8"
    started_at = utc_now()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    finished_at = utc_now()
    output = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    verdict = "PASS" if result.returncode == 0 else "FAIL"
    failure_signature = (
        None
        if verdict == "PASS"
        else hashlib.sha256(output.encode("utf-8")).hexdigest()
    )
    evidence = {
        "verdict": verdict,
        "command": command,
        "exit_code": result.returncode,
        "failure_signature": failure_signature,
        "workspace_fingerprint": workspace_fingerprint(),
        "started_at": started_at,
        "finished_at": finished_at,
        "output": output,
    }
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output)
    print(f"VERDICT: {verdict}")
    print(f"EVIDENCE: {EVIDENCE_PATH.relative_to(ROOT)}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
