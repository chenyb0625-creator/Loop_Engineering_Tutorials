"""Deterministically verify whether the minimal loop reached its goal."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"


def main() -> int:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        value = state["value"]
        target = state["target"]

        if not isinstance(value, int) or not isinstance(target, int):
            print("VERDICT: ERROR")
            print("REASON: value and target must be integers")
            return 2

        print(f"CURRENT_STATE: value={value}, target={target}")
        if value >= target:
            print("VERDICT: PASS")
            return 0

        print("VERDICT: FAIL")
        print(f"REASON: remaining={target - value}")
        return 1
    except FileNotFoundError:
        print("VERDICT: ERROR")
        print("REASON: run reset.py before starting the example")
        return 2
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        print("VERDICT: ERROR")
        print(f"REASON: invalid state.json: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
