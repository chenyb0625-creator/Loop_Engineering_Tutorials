from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPOSITORY_ROOT / "examples" / "statkit-loop"


class StatKitLoopTest(unittest.TestCase):
    def run_case(
        self,
        workspace: Path,
        worker: str,
        expected_status: str,
        expected_exit_code: int,
        *extra_args: str,
    ) -> None:
        reset = subprocess.run(
            [sys.executable, "scripts/reset.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(reset.returncode, 0, reset.stderr)

        result = subprocess.run(
            [
                sys.executable,
                "scripts/controller.py",
                "--worker",
                worker,
                *extra_args,
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected_exit_code, result.stderr)
        state = json.loads(
            (workspace / "artifacts" / "run_state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["status"], expected_status)
        self.assertIn(f"TERMINAL STATE: {expected_status}", result.stdout)

    def test_named_terminal_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "statkit-loop"
            shutil.copytree(LAB_ROOT, workspace)

            self.run_case(workspace, "fix", "DONE", 0)
            self.run_case(workspace, "noop", "STAGNATED", 1)
            self.run_case(workspace, "reward-hacker", "POLICY_VIOLATION", 2)
            self.run_case(
                workspace,
                "noop",
                "BUDGET_EXHAUSTED",
                1,
                "--max-iterations",
                "1",
                "--stagnation-limit",
                "5",
            )


if __name__ == "__main__":
    unittest.main()
