"""Check that protected tests still match the reset-time baseline."""

from __future__ import annotations

import json

from common import (
    ARTIFACTS,
    BASELINE_PATH,
    ROOT,
    protected_snapshot,
    utc_now,
    workspace_snapshot,
)


REPORT_PATH = ARTIFACTS / "policy_report.json"


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if not BASELINE_PATH.exists():
        report = {
            "status": "ERROR",
            "reason": "missing policy baseline; run scripts/reset.py",
            "checked_at": utc_now(),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("POLICY: ERROR")
        print(report["reason"])
        return 2

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    policy = json.loads((ROOT / "policy.json").read_text(encoding="utf-8"))
    current_protected = protected_snapshot()
    protected_changed = sorted(
        path
        for path in set(baseline["protected"]) | set(current_protected)
        if baseline["protected"].get(path) != current_protected.get(path)
    )
    current_workspace = workspace_snapshot()
    changed = sorted(
        path
        for path in set(baseline["workspace"]) | set(current_workspace)
        if baseline["workspace"].get(path) != current_workspace.get(path)
    )
    allowed = set(policy["allowed_write_paths"])
    violations = sorted(path for path in changed if path not in allowed)
    report = {
        "status": "PASS" if not violations else "VIOLATION",
        "changed_paths": changed,
        "changed_protected_paths": protected_changed,
        "violations": violations,
        "checked_at": utc_now(),
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if violations:
        print("POLICY: VIOLATION")
        for path in violations:
            print(f"DISALLOWED_PATH_CHANGED: {path}")
        return 1
    print("POLICY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
