"""Pretend to work without changing the workspace."""

from __future__ import annotations


def main() -> int:
    print("WORKER_ACTION: inspected files; no changes made")
    print("AGENT_CLAIM: DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
