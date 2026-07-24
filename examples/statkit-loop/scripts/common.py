"""Shared paths, hashing, process, and ledger helpers for the Loop Lab."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
SOURCE_ROOT = WORKSPACE / "src"
TEST_ROOT = WORKSPACE / "tests"
ARTIFACTS = ROOT / "artifacts"
LEDGER_PATH = ARTIFACTS / "ledger.jsonl"
RUN_STATE_PATH = ARTIFACTS / "run_state.json"
BASELINE_PATH = ARTIFACTS / "policy_baseline.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def is_runtime_file(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "missing"
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and not is_runtime_file(item)
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def protected_snapshot() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted(TEST_ROOT.rglob("*"))
        if path.is_file() and not is_runtime_file(path)
    }


def workspace_snapshot() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted(WORKSPACE.rglob("*"))
        if path.is_file() and not is_runtime_file(path)
    }


def workspace_fingerprint() -> str:
    digest = hashlib.sha256()
    for root in (SOURCE_ROOT, TEST_ROOT):
        digest.update(tree_hash(root).encode("ascii"))
    return digest.hexdigest()


def append_event(event: str, **payload: object) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": utc_now(), "event": event, **payload}
    with LEDGER_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def save_run_state(status: str, **payload: object) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    state = {"status": status, "updated_at": utc_now(), **payload}
    RUN_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    append_event("terminal", **state)


def run_python(script: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
