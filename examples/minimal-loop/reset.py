"""Reset the minimal loop to a clean, reproducible state."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    shutil.copyfile(ROOT / "state.example.json", ROOT / "state.json")
    for generated_file in ("run_state.json", "loop.log"):
        path = ROOT / generated_file
        if path.exists():
            path.unlink()
    print("RESET: state.json restored; generated evidence removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
