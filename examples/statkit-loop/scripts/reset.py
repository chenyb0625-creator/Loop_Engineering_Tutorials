"""Restore the intentionally failing starter workspace."""

from __future__ import annotations

import json
import shutil

from common import (
    ARTIFACTS,
    BASELINE_PATH,
    ROOT,
    WORKSPACE,
    protected_snapshot,
    workspace_snapshot,
)


def main() -> int:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    if ARTIFACTS.exists():
        shutil.rmtree(ARTIFACTS)

    source_package = WORKSPACE / "src" / "statkit"
    test_root = WORKSPACE / "tests"
    source_package.mkdir(parents=True)
    test_root.mkdir(parents=True)

    shutil.copyfile(
        ROOT / "fixtures" / "starter" / "__init__.py",
        source_package / "__init__.py",
    )
    shutil.copyfile(
        ROOT / "fixtures" / "starter" / "normalize.py",
        source_package / "normalize.py",
    )
    shutil.copyfile(
        ROOT / "fixtures" / "tests" / "test_normalize.py",
        test_root / "test_normalize.py",
    )

    ARTIFACTS.mkdir(parents=True)
    BASELINE_PATH.write_text(
        json.dumps(
            {
                "protected": protected_snapshot(),
                "workspace": workspace_snapshot(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("RESET: restored failing workspace and protected-path baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
