from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples" / "minimal-loop"


class MinimalLoopTest(unittest.TestCase):
    def test_controller_reaches_verified_done(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "minimal-loop"
            shutil.copytree(EXAMPLE_ROOT, workspace)

            reset = subprocess.run(
                [sys.executable, "reset.py"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(reset.returncode, 0, reset.stderr)

            controller = subprocess.run(
                [sys.executable, "controller.py"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(controller.returncode, 0, controller.stderr)
            self.assertIn("AGENT_CLAIM: DONE", controller.stdout)
            self.assertIn("TERMINAL STATE: DONE", controller.stdout)

            run_state = json.loads(
                (workspace / "run_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_state["status"], "DONE")
            self.assertEqual(run_state["iterations_used"], 3)
            self.assertTrue((workspace / "loop.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
