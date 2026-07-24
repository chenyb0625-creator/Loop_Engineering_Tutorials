"""A deliberately unreliable worker used to demonstrate false-DONE."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"


def main() -> int:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        current_value = state["value"]
        state["value"] = current_value + 1
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"WORKER_ACTION: value {current_value} -> {state['value']}")

        # This false claim is intentional: only the verifier may prove DONE.
        print("AGENT_CLAIM: DONE")
        return 0
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"WORKER_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
