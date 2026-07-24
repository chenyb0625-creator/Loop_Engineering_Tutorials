"""Apply the valid min-max normalization repair."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "workspace" / "src" / "statkit" / "normalize.py"

IMPLEMENTATION = '''"""Min-max normalization."""

from __future__ import annotations


def normalize(values: list[float]) -> list[float]:
    if not values:
        return []

    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum
    if span == 0:
        return [0.0 for _ in values]

    return [(value - minimum) / span for value in values]
'''


def main() -> int:
    TARGET.write_text(IMPLEMENTATION, encoding="utf-8")
    print("WORKER_ACTION: repaired normalize() with min-max scaling")
    print("AGENT_CLAIM: DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
