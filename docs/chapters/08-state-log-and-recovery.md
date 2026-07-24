# 第 08 章：状态日志与可恢复执行

[返回课程主页](../../README.md) · [← 上一章](./07-protected-paths-and-diff-policy.md) · [下一章 →](./09-independent-reviewer.md)

## 本章使用说明

前七章已经建立了目标、验证器、控制器、预算、停滞检测和策略门。但这些能力仍可能只存在于一个正在运行的 Python 进程中。一旦电脑重启、终端被关闭、网络中断或 CLI 异常退出，系统若不知道“刚才进行到哪里、哪些动作可能已经发生、旧证据是否仍有效”，就只能盲目重做或错误地沿用旧结论。

> 本章核心命题：恢复不是从旧状态“接着跑”这么简单。正确恢复必须先重建事实、识别未完成副作用、重新获取新鲜证据，再决定继续、重试、回滚或终止。

### 学习目标

**• **能区分进程内变量、状态快照、事件账本、验证证据、运行日志和长期项目知识。

**• **能解释为什么 `write_text(json.dumps(state))` 可能留下半写文件，以及原子替换能解决什么、不能解决什么。

**• **能实现临时文件、flush、fsync 与 os.replace 组成的原子 JSON 快照。

**• **能用 append-only JSONL 事件账本保存 sequence、previous_hash、event_hash 和完整 checkpoint。

**• **能解释 ledger-first 与 snapshot-first 的崩溃窗口，并把账本设为恢复真源、快照设为读取缓存。

**• **能为每次运行绑定 run_id、iteration、revision、workspace fingerprint、环境和 evidence_id。

**• **能在 VERIFYING、RUNNING_AGENT、TERMINAL 等阶段崩溃后，通过协调算法恢复。

**• **能证明旧 PASS 不能直接复用，恢复后必须重新运行策略和 verifier。

**• **能使用独占锁防止两个控制器同时操作同一工作树，并正确处理陈旧锁。

**• **能通过四个崩溃注入点、损坏快照、篡改账本和终态失效实验验证恢复设计。

**• **能解释 at-least-once、幂等键、outbox pattern 与“exactly once”宣传之间的边界。

**• **能设计日志脱敏、保留期、artifact store、数据库事务和生产级可观测性升级路径。

## 1. 为什么“能运行很久”不等于“能可靠恢复”

单次运行成功只说明 happy path 可走通。可靠系统还必须回答：进程在任意一条指令之后停止时，重启者如何判断已经发生了什么。尤其是代理、验证器和外部服务都可能产生副作用，状态文件中写着 RUNNING 并不能告诉你动作是尚未开始、执行了一半，还是已经完成但来不及记录。

### 1.1 四个典型崩溃窗口

| 崩溃位置 | 仓库/外部世界可能状态 | 仅看旧快照的误判 | 恢复所需动作 |
| --- | --- | --- | --- |
| 记录“即将运行 verifier”之后、真正执行之前 | 代码未变，验证器未运行 | 把 VERIFYING 当成已有结果 | 重新运行 verifier |
| verifier 已完成、状态尚未更新 | 已有一份证据文件，但快照仍显示 VERIFYING | 忽略证据或把旧证据当新证据 | 核对证据归属，仍建议重新验证 |
| 记录“即将调用 agent”之后、真正调用之前 | 代理可能未启动 | 盲目增加 iteration 或认为代理失败 | 重新检查仓库，再决定是否调用 |
| agent 已修改代码、状态尚未更新 | 候选修改真实存在，iteration 尚未增加 | 重复调用代理，放大 diff 或重复副作用 | 先 policy + verifier，必要时再调用 |

### 1.2 “继续运行”不是默认正确动作

恢复算法的第一职责不是恢复吞吐，而是恢复可信度。若无法证明账本完整、工作树归属明确或外部副作用可去重，系统应进入 BLOCKED、HUMAN_REVIEW 或 CORRUPTION，而不是为了显得自治而继续。

> 判断标准：恢复后的第一轮是 reconciliation（协调），不是 ordinary retry（普通重试）。它必须比较持久化记录与当前现实，而不是服从旧 phase。

## 2. 六类信息：状态、事件、证据、日志、工件与知识

把所有内容都写进一个 run_state.json 会迅速失控。不同数据有不同生命周期、可信度和访问模式，必须分层。

| 类别 | 典型内容 | 主要用途 | 是否是恢复真源 |
| --- | --- | --- | --- |
| 状态快照 Snapshot | 当前 status、phase、iteration、pending_action、最新证据指针 | 快速读取当前视图 | 通常不是；可由账本重建 |
| 事件账本 Ledger | 每次 checkpoint、策略违规、人工批准、重试决策 | 审计、重放、恢复 | 本章设计中是 |
| 验证证据 Evidence | 命令、退出码、stdout/stderr、revision、环境、哈希 | 证明 gate 结果 | 对“是否通过”是事实来源，但必须新鲜 |
| 运行日志 Logs | 终端输出、调试信息、trace、时延、token | 诊断和指标 | 否，可能不完整或不可解析 |
| 工件 Artifacts | task packet、agent JSONL、diff、报告、模型输出 | 复盘和交付 | 按工件类型决定 |
| 项目知识 Knowledge | 构建命令、架构约束、长期规则、已验证经验 | 重建下一轮上下文 | 不是某次运行状态 |

### 2.1 权威层级

**图 1　推荐的事实优先级**

```text
当前仓库/外部系统事实
        ↓
新鲜的确定性证据
        ↓
校验通过的事件账本
        ↓
可重建的状态快照
        ↓
运行日志与代理自然语言声明
```

越靠下越适合解释“发生了什么”，越不适合直接决定 DONE。代理说“已完成”与日志里出现 PASS 都不能替代带 revision 和环境指纹的证据对象。

## 3. 运行身份与证据绑定

恢复必须先回答“这是哪一次运行的哪一次尝试”。只用 iteration=1 不够，因为不同 run 都可能有第 1 轮，重试也可能在同一 iteration 产生多个 verifier 证据。

| 标识 | 作用 | 本章示例 |
| --- | --- | --- |
| run_id | 一次外层任务生命周期的稳定身份 | 随机 UUID；resume 保持不变 |
| iteration | 成功完成的代理动作数量 | 代理崩溃前不应盲目增加 |
| event_sequence | 账本中的严格递增位置 | 1, 2, 3… |
| evidence_id | 一次具体 verifier 调用 | run_id + iteration + 随机后缀 |
| revision | 证据对应的 Git HEAD | commit SHA |
| workspace_fingerprint | 未提交变更及关键文件内容指纹 | status + src 文件哈希 |
| environment fingerprint | 解释结果的运行环境 | Python、OS、锁文件、容器镜像等 |

### 3.1 为什么只记录 commit SHA 不够

代理通常在未提交工作树中修改代码。HEAD 可能始终不变，而实际文件已经变化。因此证据至少要同时绑定 revision 与 workspace fingerprint。生产系统还应加入依赖锁文件哈希、容器镜像 digest、测试数据版本和 feature flags。

> 新鲜证据：“五分钟前测试通过”不是永久事实。只要代码、测试、依赖、环境或外部数据发生变化，旧 PASS 就可能失效。

## 4. 可恢复状态机：phase、pending_action 与 checkpoint

status 表示宏观生命周期，phase 表示当前执行位置，pending_action 表示可能尚未确认的副作用。三者分开，才能在重启时做保守推断。

**图 2　最小可恢复控制流**

```text
READY
  ↓ checkpoint(pending=verifier)
VERIFYING ──crash──→ RECOVERING ──fresh verify──┐
  ↓ verifier complete                           │
READY                                            │
  ↓ checkpoint(pending=agent)                    │
RUNNING_AGENT ─crash→ RECOVERING ─policy+verify─┘
  ↓ agent complete
READY → VERIFYING → DONE / RETRY / TERMINAL
```

### 4.1 checkpoint 放在哪里

| 边界 | 先记录什么 | 动作后记录什么 | 恢复含义 |
| --- | --- | --- | --- |
| 调用 verifier 前 | phase=VERIFYING, pending=verifier | 证据路径、退出码、phase=READY | 崩溃时重新验证 |
| 调用 agent 前 | phase=RUNNING_AGENT, pending=agent | iteration+1, pending=None | 崩溃时先检查实际 diff |
| 进入 DONE 前 | 最新证据已绑定当前工作树 | status=DONE, phase=TERMINAL | resume 仍要新鲜复验 |
| 策略违规 | 保存违规路径和当前指纹 | status=POLICY_VIOLATION | 保留现场，不继续写入 |

### 4.2 pending_action 不是“动作肯定发生了”

它只表示控制器跨过了“准备执行”的持久化边界。进程可能在真正调用前死亡，也可能动作已经完成但确认写入尚未发生。因此 pending_action 的恢复语义是“结果未知，需要协调”，而不是自动重放。

## 5. 原子状态快照：避免半写和撕裂更新

最天真的写法会直接覆盖目标文件。进程在写入中途退出、电源中断或磁盘错误时，run_state.json 可能只剩半个 JSON；另一个读取者也可能看到中间状态。

**反例 1　直接覆盖**

```text
STATE_PATH.write_text(
    json.dumps(state, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

### 5.1 原子替换协议

**1. **在目标文件同一目录创建临时文件，确保 rename/replace 不跨文件系统。

**2. **写入完整 JSON，flush 用户态缓冲，再 fsync 文件描述符。

**3. **用 os.replace(temp, target) 原子替换旧快照。

**4. **在支持的平台上 fsync 父目录，降低目录项更新在崩溃后丢失的风险。

**5. **finally 删除残留临时文件。

**代码 1　scripts/`state_store.py**`

```python
from __future__ import annotations
 
import json
import os
import tempfile
from pathlib import Path
from typing import Any
 
 
class StateStoreError(RuntimeError):
    pass
 
 
def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Durably replace a JSON snapshot without exposing a half-written file."""
 
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temp_path.unlink(missing_ok=True)
 
 
def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateStoreError(f"state snapshot does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateStoreError(f"state snapshot is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateStoreError("state snapshot must be a JSON object")
    return value
```

### 5.2 原子不等于事务

os.replace 保证读取者看到旧文件或新文件之一，不保证“账本、快照、证据文件、Git 变更”作为一个整体同时提交。跨多个资源的一致性仍需要账本、恢复协调或数据库事务。

> Windows 注意：同目录 os.replace 可用，但目录 fsync 的接口与 Unix 不同。本章代码在 Windows 跳过目录 fsync；生产环境应通过数据库、容器卷和故障注入验证实际持久性语义。

## 6. 事件账本：append-only、序号与哈希链

快照只保留“现在是什么”，账本保留“如何到达现在”。本章用一行一个 JSON 事件的 JSONL 文件作为最小 append-only ledger。每个事件包含前一事件哈希，形成可检测篡改和截断异常的链。

**示例 1　一个账本事件**

```json
{
  "sequence": 4,
  "previous_hash": "5d0a...",
  "event_type": "STATE_CHECKPOINT",
  "payload": {
    "reason": "before agent",
    "state": {"phase": "RUNNING_AGENT", "pending_action": "agent"}
  },
  "event_hash": "b879..."
}
```

**代码 2　scripts/`event_ledger.py**`

```python
from __future__ import annotations
 
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable
 
GENESIS_HASH = "0" * 64
 
 
class LedgerCorruption(RuntimeError):
    pass
 
 
def canonical_bytes(event_without_hash: dict[str, Any]) -> bytes:
    return json.dumps(
        event_without_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
 
 
def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerCorruption(f"invalid JSON at ledger line {line_number}: {exc}") from exc
        if event.get("sequence") != len(events) + 1:
            raise LedgerCorruption(f"unexpected sequence at ledger line {line_number}")
        if event.get("previous_hash") != previous_hash:
            raise LedgerCorruption(f"hash-chain break at ledger line {line_number}")
        claimed_hash = event.get("event_hash")
        without_hash = dict(event)
        without_hash.pop("event_hash", None)
        actual_hash = hashlib.sha256(canonical_bytes(without_hash)).hexdigest()
        if claimed_hash != actual_hash:
            raise LedgerCorruption(f"event hash mismatch at ledger line {line_number}")
        previous_hash = actual_hash
        events.append(event)
    return events
 
 
def append_event(path: Path, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    events = read_events(path)
    previous_hash = events[-1]["event_hash"] if events else GENESIS_HASH
    event_without_hash = {
        "sequence": len(events) + 1,
        "previous_hash": previous_hash,
        "event_type": event_type,
        "payload": payload,
    }
    event = {
        **event_without_hash,
        "event_hash": hashlib.sha256(canonical_bytes(event_without_hash)).hexdigest(),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event
 
 
def latest_checkpoint(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for event in events:
        if event.get("event_type") == "STATE_CHECKPOINT":
            payload = event.get("payload", {})
            state = payload.get("state")
            if isinstance(state, dict):
                latest = state
    return latest
```

### 6.1 哈希链能证明什么

| 能力 | 能否做到 | 说明 |
| --- | --- | --- |
| 检测事件内容被直接编辑 | 可以 | event_hash 不再匹配 |
| 检测中间行被删除或重排 | 可以 | sequence 或 previous_hash 断裂 |
| 证明文件从未被有权限的人整体重写 | 不可以 | 攻击者可重新计算整条链 |
| 提供法律意义上的不可抵赖 | 不可以 | 需要外部签名、WORM 存储或可信时间戳 |
| 替代数据库事务 | 不可以 | 它只是最小审计与恢复机制 |

### 6.2 不要在账本中保存模型隐藏推理

账本应保存控制决策、命令结果、结构化 findings 和外部可观察证据，不保存不可验证的隐式思维过程。长期保存完整 prompt/response 也会增加敏感信息和提示注入传播风险。

## 7. 账本优先、快照重建与一致性协调

一次 checkpoint 同时需要追加账本和更新快照。没有事务数据库时，两步之间必然存在崩溃窗口。本章选择 ledger-first：先持久化完整 checkpoint 事件，再原子替换快照。

**代码 3　checkpoint 的提交顺序**

```text
event = append_event(
    LEDGER_PATH,
    "STATE_CHECKPOINT",
    {"reason": reason, "state": state},
)
state["event_sequence"] = event["sequence"]
atomic_write_json(STATE_PATH, state)
```

### 7.1 两种顺序的故障结果

| 顺序 | 中间崩溃后 | 恢复策略 | 主要风险 |
| --- | --- | --- | --- |
| snapshot → ledger | 快照可能领先账本 | 需决定是否信任无法审计的快照 | 历史缺口、难以重放 |
| ledger → snapshot | 账本可能领先快照 | 读取最新 checkpoint，重建快照 | 账本必须校验并包含足够状态 |

### 7.2 快照是物化视图，不是唯一真源

启动时读取并校验账本；若快照缺失、JSON 损坏或 event_sequence 落后于账本，就从最新 STATE_CHECKPOINT 重建。若账本损坏，不能反过来“相信快照继续”，因为你无法确认快照的来源链。安全默认应停止并人工审计。

**代码 4　从账本选择最新 `checkpoint**`

```python
def load_or_rebuild_state() -> tuple[dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    events = read_events(LEDGER_PATH)
    ledger_state = latest_checkpoint(events)
    snapshot: dict[str, Any] | None = None
    try:
        snapshot = load_json(STATE_PATH)
    except StateStoreError as exc:
        notes.append(str(exc))
    ledger_sequence = events[-1]["sequence"] if events else 0
    snapshot_sequence = snapshot.get("event_sequence", 0) if snapshot else 0
    if ledger_state is not None and (snapshot is None or ledger_sequence > snapshot_sequence):
        rebuilt = {**ledger_state, "event_sequence": ledger_sequence}
        atomic_write_json(STATE_PATH, rebuilt)
        snapshot = rebuilt
        notes.append("snapshot rebuilt from the latest valid ledger checkpoint")
    return snapshot, notes
```

## 8. 新鲜证据原则：为什么旧 PASS 必须失效

恢复时最危险的捷径是：快照写着 DONE，或者 last_evidence_path 指向 PASS，于是控制器直接返回成功。这个做法默认代码、测试、依赖、环境和外部输入均未变化，通常无法证明。

### 8.1 恢复终态的正确语义

**图 3　DONE 不是不可撤销标签**

```text
旧 snapshot: DONE
        ↓ resume
RECOVERING
        ↓ 校验 ledger + 当前仓库 + policy
重新运行 verifier
   ├─ PASS → 新的 DONE（新 evidence_id）
   └─ FAIL → READY / RETRY / BLOCKED
```

本章控制器在 `--resume` 时即使读到 DONE，也先改为 RECOVERING，再运行 fresh verifier。这样 DONE 是“在某一证据时刻成立的结论”，不是永久属性。

### 8.2 证据对象最低字段

| 字段 | 目的 |
| --- | --- |
| evidence_id / created_at | 区分具体调用并判断时间关系 |
| command / exit_code / stdout / stderr | 可复现检查和诊断 |
| revision + workspace_fingerprint | 绑定代码事实 |
| environment | 解释跨机器、依赖和平台差异 |
| report_sha256 | 检测证据文件内容变化 |
| artifact path | 让状态只保存指针，不膨胀为日志仓库 |

**代码 5　scripts/`verify.py**`

```python
from __future__ import annotations
 
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "evidence"
 
 
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
 
 
def run(command: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
 
 
def git_text(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return "UNAVAILABLE"
    return completed.stdout.strip()
 
 
def workspace_fingerprint() -> str:
    status = git_text("status", "--porcelain=v1", "--untracked-files=all")
    digest = hashlib.sha256(status.encode("utf-8"))
    for path in sorted((ROOT / "src").rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
 
 
def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    pytest_result = run([sys.executable, "-m", "pytest", "-q"])
    ruff_result = run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"])
    passed = pytest_result["exit_code"] == 0 and ruff_result["exit_code"] == 0
    report = {
        "schema_version": 1,
        "evidence_id": os.environ.get("LOOP_EVIDENCE_ID", "manual"),
        "created_at": utc_now(),
        "verdict": "PASS" if passed else "FAIL",
        "revision": git_text("rev-parse", "HEAD"),
        "workspace_fingerprint": workspace_fingerprint(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "checks": {
            "pytest": pytest_result,
            "ruff": ruff_result,
        },
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    output = EVIDENCE_DIR / f"verify-{report['evidence_id']}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"EVIDENCE_PATH: {output.relative_to(ROOT).as_posix()}")
    print(f"VERDICT: {report['verdict']}")
    return 0 if passed else 1
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

## 9. 执行锁与并发控制

两个控制器同时操作同一工作树会导致 iteration、账本序号、状态快照和 diff 归因相互覆盖。最小实现可用 O_CREAT | O_EXCL 原子创建 lock 文件；第二个进程发现文件已存在就拒绝启动。

**代码 6　独占锁核心**

```python
def acquire_lock(force_unlock: bool) -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if force_unlock:
        LOCK_PATH.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            f"run lock exists: {LOCK_PATH}. Inspect it; use --force-unlock only after confirming no live controller."
        ) from exc
    payload = json.dumps({"pid": os.getpid(), "created_at": utc_now()}, ensure_ascii=False)
    os.write(fd, payload.encode("utf-8"))
    os.fsync(fd)
    return fd
 
 
def release_lock(fd: int) -> None:
    os.close(fd)
    LOCK_PATH.unlink(missing_ok=True)
```

### 9.1 陈旧锁不是自动删除理由

进程被强杀时 lock 可能残留。仅凭文件“很旧”就自动删除有竞态风险：慢任务或挂起进程仍可能活着。生产系统应使用租约、心跳、数据库 advisory lock 或编排器 ownership。本章要求操作者确认没有活跃控制器后，显式使用 `--force-unlock`。

> 并发边界：一个工作树一个 writer。需要并行时，应使用独立 Git worktree、独立 state/ledger 命名空间和独立锁，而不是共享目录里的多个 agent。

## 10. 手把手建立第 08 章实验仓库

本实验继续使用常量向量归一化缺陷，但控制器已经升级为可恢复版本。Mock Agent 的代码修改是幂等的：补丁已存在时返回 no-op，便于观察恢复时是否重复调用。

### 10.1 目录结构

**目录 1　`chapter08-lab**`

```text
chapter08-lab/
├─ AGENTS.md
├─ goal.md
├─ loop_config.json
├─ pyproject.toml
├─ src/statkit.py
├─ tests/test_statkit.py
├─ scripts/
│  ├─ verify.py
│  ├─ mock_agent.py
│  ├─ state_store.py
│  ├─ event_ledger.py
│  ├─ run_loop.py
│  └─ inspect_run.py
├─ state/       # 快照、锁、task packet
├─ logs/        # append-only 事件账本
└─ evidence/    # 每次 verifier 的独立证据文件
```

### 10.2 创建环境

**操作 1　PowerShell 初始化**

```powershell
cd path\to\chapter08-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
 
git init
git add .
git commit -m "chapter 8 starter"
```

### 10.3 清理上一次运行产物

**操作 2　重置实验**

```powershell
git restore src\statkit.py
Remove-Item state\run_state.json -ErrorAction SilentlyContinue
Remove-Item state\run.lock -ErrorAction SilentlyContinue
Remove-Item state\task_packet.json -ErrorAction SilentlyContinue
Remove-Item logs\events.jsonl -ErrorAction SilentlyContinue
Remove-Item evidence\*.json -ErrorAction SilentlyContinue
```

不要删除 `.gitkeep`。运行产物被 `.gitignore` 排除，避免策略门把状态文件视为代理修改。生产系统更适合把状态目录放在仓库外或独立 artifact volume。

### 10.4 先确认失败基线

**操作 3　手动验证**

```powershell
python scripts\verify.py
$LASTEXITCODE
```

应看到 `VERDICT: FAIL` 和退出码 1。若一开始就是 PASS，后续恢复实验没有有效故障目标。

## 11. 正常路径：从 FAIL 到 DONE

**操作 4　运行可恢复控制器**

```powershell
python scripts\run_loop.py
python scripts\inspect_run.py
Get-Content logs\events.jsonl
```

### 11.1 预期控制序列

**输出摘要**

```text
RECOVERY_NOTE: state snapshot does not exist ...
VERDICT: FAIL
AGENT_ACTION: patched constant-vector boundary case
AGENT_CLAIM: DONE
VERDICT: PASS
TERMINAL STATE: DONE
```

第一次启动没有快照是正常情况，不是错误。控制器先 checkpoint 新运行，再验证、调用代理、重新验证，最后保存 DONE。inspect_run.py 还会校验整条事件哈希链；只有账本可读且哈希连续，才输出 LEDGER_HEAD_HASH。

### 11.2 检查状态而不是只看终端

| 检查项 | 合理结果 |
| --- | --- |
| status / phase | DONE / TERMINAL |
| iteration | 1；表示一个代理动作完成 |
| last_evidence_path | 指向第二次、PASS 的证据 |
| event_sequence | 与账本最后事件序号一致 |
| workspace_fingerprint | 与 DONE 时工作树匹配 |
| terminal_reason | fresh verifier evidence passed |

**代码 7　scripts/`inspect_run.py**`

```python
from __future__ import annotations
 
import json
from pathlib import Path
 
from event_ledger import read_events
from state_store import load_json
 
ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state" / "run_state.json"
LEDGER_PATH = ROOT / "logs" / "events.jsonl"
 
 
def main() -> int:
    state = load_json(STATE_PATH)
    events = read_events(LEDGER_PATH)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"LEDGER_EVENTS: {len(events)}")
    if events:
        print(f"LEDGER_HEAD_HASH: {events[-1]['event_hash']}")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

## 12. 崩溃实验一：验证器前后恢复

### 12.1 在“已记录 VERIFYING、尚未调用 verifier”时崩溃

**操作 5　注入崩溃**

```powershell
# 先执行“操作 2”重置实验
python scripts\run_loop.py --crash-at after_checkpoint_before_verifier
Get-Content state\run_state.json
python scripts\run_loop.py --resume
```

崩溃后快照应显示 phase=VERIFYING、pending_action=verifier。resume 不能假设 verifier 已经运行，而是把状态改为 RECOVERING 并重新获取证据。

### 12.2 在 verifier 完成、checkpoint 尚未写入时崩溃

**操作 6　证据文件可能存在但状态落后**

```powershell
# 重置后执行
python scripts\run_loop.py --crash-at after_verifier_before_checkpoint
Get-ChildItem evidence
Get-Content state\run_state.json
python scripts\run_loop.py --resume
```

此时 evidence 目录里可能已有本次 verifier 报告，但状态仍表示 VERIFYING。最保守的恢复策略仍是重新运行 verifier。原因是孤立证据可能缺少已确认的调用归属，也可能在证据写入后、状态恢复前仓库发生变化。

> 不要“捡到 PASS 就用”：artifact 的存在不等于它已被控制器接受。接受证据需要检查 run_id、evidence_id、revision、指纹、命令、哈希与账本因果关系。

## 13. 崩溃实验二：代理前后恢复

### 13.1 记录 RUNNING_AGENT 后、代理尚未执行

**操作 7　代理调用前崩溃**

```powershell
# 重置后执行
python scripts\run_loop.py --crash-at after_checkpoint_before_agent
Get-Content state\run_state.json
python scripts\run_loop.py --resume
```

resume 看到 pending_action=agent，但不能直接把 iteration 加 1。它先重新验证；若仍 FAIL，才重新构造 task packet 并调用代理。

### 13.2 代理已修改代码、checkpoint 尚未确认

**操作 8　最关键的恢复实验**

```powershell
# 重置后执行
python scripts\run_loop.py --crash-at after_agent_before_checkpoint
Get-Content state\run_state.json
git diff -- src\statkit.py
python scripts\run_loop.py --resume
python scripts\inspect_run.py
```

你应看到快照仍为 RUNNING_AGENT、iteration=0，但 git diff 已包含正确补丁。恢复后控制器先 fresh verify，立即得到 PASS 并进入 DONE，不会因为 iteration 尚未增加就再次调用代理。这个实验体现了“现实优先于旧状态”。

### 13.3 iteration 为什么仍可能是 0

iteration 在本章定义为“被控制器确认完成的代理动作数”，而不是“可能发生过的调用次数”。崩溃窗口中代理可能执行过，但未提交确认。恢复后通过 verifier 直接 DONE 时，保留 iteration=0 是一种保守语义；生产指标可另外记录 agent_attempt_count，避免把两种概念混在一起。

## 14. 损坏快照：从账本重建

快照是缓存，因此损坏不应让整个运行历史丢失。先完成一次正常运行，再故意写入非法 JSON。

**操作 9　破坏 `run_state.json**`

```powershell
Set-Content state\run_state.json "{broken"
python scripts\run_loop.py --resume
python scripts\inspect_run.py
```

预期出现两条 RECOVERY_NOTE：快照不是合法 JSON；快照已从最新有效 ledger checkpoint 重建。随后终态被重新验证。

### 14.1 为什么不直接删除损坏文件并新建 run

新建 run 会丢失原 run_id、预算消耗、已发生代理动作和审计链，还可能重复外部副作用。恢复应尽量保持同一运行身份；只有历史不可验证时才转入人工处置。

## 15. 篡改账本：拒绝不可信历史

复制账本作为备份，然后修改第一行的任意字符。因为 event_hash 是对完整事件的摘要，下一次读取应立即失败。

**操作 10　PowerShell 篡改账本**

```powershell
Copy-Item logs\events.jsonl logs\events.backup.jsonl
$content = Get-Content logs\events.jsonl -Raw
$content = $content.Replace("new run initialized", "new run initialized TAMPERED")
Set-Content logs\events.jsonl $content -NoNewline
 
python scripts\run_loop.py --resume
$LASTEXITCODE
```

预期终态为 LEDGER_CORRUPTION，退出码 4。控制器不能用“快照看起来正常”作为继续理由。恢复实验后，将 backup 复制回原文件。

### 15.1 哈希链不是防攻击存储

拥有写权限的人可以修改所有事件并重新计算链。因此生产环境要把账本复制到代理不可写的集中存储，或使用数据库审计表、对象锁、签名和外部时间戳。

> Fail closed：历史完整性无法证明时，停止比“尽量恢复”更可靠。否则攻击、磁盘损坏和程序 bug 都可能被解释成合法历史。

## 16. 终态失效：DONE 后仓库变化怎么办

完成一次正常运行后，手动把 src/statkit.py 恢复为有 bug 的版本，但保留 run_state.json 和账本。此时旧状态仍写着 DONE，当前事实已经不满足目标。

**操作 11　让旧 DONE 失效**

```powershell
git restore src\statkit.py
python scripts\run_loop.py --resume
```

正确行为是：resume 将 DONE 重开为 RECOVERING；fresh verifier 返回 FAIL；控制器继续代理修复并产生新的 PASS。若系统直接输出旧 DONE，说明它把状态标签置于仓库事实之上。

### 16.1 人工修改与新任务的边界

本章为了训练允许对同一 run 复验并继续。生产系统通常还需要策略：若终态后 revision 或需求版本发生变化，应创建新的 run_id，并保留原 run 为历史记录；否则一个 run 会跨越多个任务版本，指标和审计意义变得模糊。

## 17. 锁冲突与陈旧锁实验

**操作 12　模拟已有控制器**

```powershell
Set-Content state\run.lock '{"pid": 99999, "created_at": "manual"}'
python scripts\run_loop.py --resume
 
# 确认没有真实控制器后：
python scripts\run_loop.py --resume --force-unlock
```

第一次调用应拒绝运行。`--force-unlock` 不是普通重试参数，而是带人工责任的恢复动作；应把操作者、原因和时间写入审计事件。本章最小代码为了简洁未记录这类管理事件，生产版本必须补上。

## 18. 外部副作用、幂等性与 outbox

代码修改可以通过仓库状态协调，但发送邮件、创建 Issue、扣费、提交数据库写入等外部动作更困难。崩溃可能发生在“外部系统已经接受请求”和“本地记录完成”之间。

### 18.1 At-least-once 是常见现实

**图 4　不可消除的确认窗口**

```text
controller → POST /create_issue → external system created issue
                          ↓
                     process crashes
                          ↓
resume cannot know whether request succeeded
                          ↓
blind retry may create duplicate issue
```

| 机制 | 适用条件 | 作用 | 残余问题 |
| --- | --- | --- | --- |
| 幂等键 idempotency key | 外部 API 支持按 key 去重 | 同一逻辑动作重复请求只生效一次 | key 生命周期和请求语义必须稳定 |
| 先查询后创建 | 外部对象可按唯一标识查询 | 降低重复概率 | 查询与创建之间仍有竞态 |
| Transactional outbox | 本地 DB 可事务写状态与 outbox | 把待发送意图与业务状态原子提交 | 消费者仍需幂等 |
| 人工批准 | 高风险、低频动作 | 把不可逆决策交给人 | 降低自治和吞吐 |
| 补偿事务 | 副作用可逆 | 重复或失败后执行反向动作 | 补偿本身也可能失败 |

### 18.2 “Exactly once”通常是有条件的

在单一事务系统内部可以获得强语义；跨网络、队列、数据库和 SaaS API 时，所谓 exactly-once 往往依赖幂等消费、去重表和特定故障模型。设计文档必须写清保障范围，不应把营销术语当作普遍事实。

**示例 2　稳定幂等键**

```markdown
idempotency_key = sha256(
    f"{run_id}:{logical_action}:{target_resource}".encode()
).hexdigest()
 
# 重试时复用同一个 key，而不是每次生成新 UUID。
```

## 19. 上下文卫生：恢复状态不等于恢复全部聊天

恢复控制器需要完整事实，但下一轮模型上下文不需要完整历史。把 events.jsonl、所有 stdout 和旧 prompt 全部重新塞给模型，会增加成本、陈旧信息和提示注入风险。

### 19.1 每轮重建最小 task packet

| 应注入 | 通常不应注入 |
| --- | --- |
| 当前 goal 与版本 | 所有历史 goal 副本 |
| 稳定项目约束 AGENTS.md | 完整聊天记录 |
| 最新 verifier 失败摘要与证据路径 | 全部原始日志 |
| 最近已尝试方案的结构化摘要 | 模型隐式推理过程 |
| 当前 iteration、预算与允许路径 | 与本轮无关的仓库文件 |
| 独立 reviewer 的未解决 findings | 已关闭、已过时的 findings |

**代码 8　最小 task `packet**`

```python
def write_task_packet(state: dict[str, Any]) -> None:
    packet = {
        "run_id": state["run_id"],
        "iteration": state["iteration"] + 1,
        "goal": (ROOT / "goal.md").read_text(encoding="utf-8"),
        "constraints": (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        "last_evidence_path": state.get("last_evidence_path"),
    }
    atomic_write_json(TASK_PACKET_PATH, packet)
```

### 19.2 长期记忆的进入门槛

只有通过验证、review 和人工/策略接受的经验，才应转为项目规则、测试或文档。一次崩溃中的临时猜测、代理自我解释和未验证 workaround 不应进入长期知识。

## 20. 日志安全、隐私与保留策略

可恢复系统会积累大量 prompt、源码片段、命令输出、环境变量和错误堆栈。没有数据治理时，可靠性基础设施本身会成为秘密泄露面。

| 风险 | 控制措施 |
| --- | --- |
| 环境变量中包含 token/密码 | 白名单记录环境字段；禁止 dump 全部 os.environ |
| 命令输出含凭据或个人数据 | 采集前脱敏；原始日志分级访问；短保留期 |
| prompt 包含不可信仓库内容 | 标注为数据；reviewer 只读；避免跨项目复用 |
| 日志无限增长 | 按 run 分区、压缩、TTL、对象存储 lifecycle |
| 删除日志破坏审计 | 定义合规保留期；摘要和原始数据分层保留 |
| 代理可以改本地 logs | 集中远端 sink、只追加 API、独立凭据和网络策略 |

### 20.1 建议保留层级

**• **长期：终态、配置版本、revision、证据摘要、成本、人工批准和安全事件。

**• **中期：结构化事件、review findings、失败签名和性能 trace。

**• **短期：完整 stdout/stderr、模型 JSONL、原始 prompt 和临时工件。

**• **禁止：明文凭据、无必要的个人数据和模型隐藏推理。

## 21. 生产架构升级与恢复指标

### 21.1 从本地文件到生产组件

| 实验组件 | 生产替代 | 目的 |
| --- | --- | --- |
| run_state.json | 事务数据库中的 run/state 表 | 并发更新、CAS、查询和索引 |
| events.jsonl | append-only 事件表/日志服务 | 可靠追加、分区、重放和审计 |
| evidence/*.json | 对象存储 + 内容哈希 | 大工件、不可变版本、生命周期 |
| run.lock | DB advisory lock / lease / orchestrator ownership | 心跳、故障转移、避免 split-brain |
| subprocess | 隔离 runner、容器、队列 worker | 资源限制、弹性、取消和重试 |
| print 日志 | OpenTelemetry trace + metrics + centralized logs | 跨阶段因果和 SLO |
| 单文件 checkpoint | 事务 + outbox | 状态与待发送事件原子提交 |

### 21.2 恢复质量指标

| 指标 | 定义 | 为什么重要 |
| --- | --- | --- |
| Recovery success rate | 可恢复故障中最终正确继续或安全停止的比例 | 只测 happy path 会严重高估可靠性 |
| Mean time to reconcile | 重启到获得可信新状态的时间 | 比“进程重启速度”更有意义 |
| Stale evidence acceptance rate | 错误复用过期 PASS 的比例 | 应接近 0 |
| Duplicate side-effect rate | 恢复后产生重复外部动作的比例 | 衡量幂等与 outbox |
| State/ledger divergence | 快照落后、领先或无法重建的频率 | 暴露持久化设计缺陷 |
| Manual recovery rate | 需要人工修复锁、账本或工作树的比例 | 衡量自治边界 |
| False-DONE after recovery | 恢复后宣称 DONE 但隐藏验证失败的比例 | 最危险的恢复错误 |

### 21.3 故障注入矩阵

生产前不要只运行单元测试。至少在 checkpoint 前后、证据写入、账本追加、快照替换、网络请求、队列确认、锁续租和磁盘空间耗尽位置做故障注入，并验证终态与副作用。

## 22. 本章最小实现的能力边界

| 限制 | 后果 | 升级方向 |
| --- | --- | --- |
| JSONL 每次 append 前重读全部事件 | 长运行性能退化 | 数据库、索引或保存可信 head metadata |
| 本地哈希链可被整体重写 | 无法抵御拥有文件写权限的攻击者 | 远端不可变存储、签名、WORM |
| 锁无心跳和 PID 活性校验 | 陈旧锁需人工判断 | lease、heartbeat、advisory lock |
| workspace fingerprint 只覆盖 status 和 src/*.py | 依赖、生成文件和外部数据变化可能漏检 | 可配置 manifest、Merkle tree、环境 digest |
| 策略解析为简化版 git status | 复杂 rename/path 字符可能处理不足 | 复用第 07 章 porcelain -z 严格解析器 |
| 未实现 reviewer 和隐藏测试 | DONE 只过确定性可见门 | 第 09 章加入独立审查 |
| 外部副作用没有事务 | 崩溃重试可能重复 | 幂等 API、outbox、补偿和人工门 |
| 单机单工作树 | 不能水平扩展 | 队列、容器、分布式状态和 worktree 隔离 |

> 批判性结论：“有状态文件”不是可恢复；“有日志”不是可审计；“能重试”不是幂等；“旧测试通过”不是当前正确。每个词都必须对应明确故障模型和机械保证。

## 23. run_loop.py 关键结构拆解

### 23.1 启动与恢复入口

**代码 9　execute 开头**

```python
def execute(args: argparse.Namespace) -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    state, recovery_notes = load_or_rebuild_state()
    for note in recovery_notes:
        print(f"RECOVERY_NOTE: {note}")
 
    if args.resume:
        if state is None:
            raise RuntimeError("--resume requested but no recoverable state exists")
        state = resume_reconcile(state)
    else:
        if state is not None and state.get("status") not in TERMINAL_STATES:
            raise RuntimeError("a non-terminal run already exists; use --resume")
        state = checkpoint(initial_state(config), "new run initialized")
```

顺序不可随意交换：先拿锁，再校验账本和加载状态；resume 时协调中断 phase；新运行才初始化 run_id。若历史损坏，最外层捕获 LedgerCorruption 并停止。

### 23.2 每轮 verifier gate

**代码 10　验证、证据和终态**

```text
while True:
        state = checkpoint(
            {**state, "status": "RUNNING", "phase": "VERIFYING", "pending_action": "verifier"},
            "before verifier",
        )
        maybe_crash(args.crash_at, "after_checkpoint_before_verifier")
        try:
            verifier_code, evidence_path = run_verifier(state, config["verifier_timeout_seconds"])
        except subprocess.TimeoutExpired:
            state = checkpoint(
                {
                    **state,
                    "status": "VERIFIER_ERROR",
                    "phase": "TERMINAL",
                    "pending_action": None,
                    "terminal_reason": "verifier timeout",
                },
                "verifier timeout",
            )
            return 2
        maybe_crash(args.crash_at, "after_verifier_before_checkpoint")
        state = checkpoint(
            {**state, "phase": "READY", "pending_action": None, "last_evidence_path": evidence_path},
            "verifier completed",
        )
 
        if verifier_code == 0:
            state = checkpoint(
                {
                    **state,
                    "status": "DONE",
                    "phase": "TERMINAL",
                    "terminal_reason": "fresh verifier evidence passed",
                },
                "done gate passed",
            )
            print("TERMINAL STATE: DONE")
            return 0
        if verifier_code not in {0, 1}:
            state = checkpoint(
                {
                    **state,
                    "status": "VERIFIER_ERROR",
                    "phase": "TERMINAL",
                    "terminal_reason": f"verifier exit code {verifier_code}",
                },
                "verifier error",
            )
            print("TERMINAL STATE: VERIFIER_ERROR")
            return 2
        if state["iteration"] >= state["max_iterations"]:
            state = checkpoint(
                {
                    **state,
                    "status": "BUDGET_EXHAUSTED",
                    "phase": "TERMINAL",
                    "terminal_reason": "maximum iterations reached",
                },
                "iteration budget exhausted",
            )
            print("TERMINAL STATE: BUDGET_EXHAUSTED")
            return 1
```

### 23.3 Agent side effect 与确认

**代码 11　代理前后 `checkpoint**`

```text
write_task_packet(state)
        state = checkpoint(
            {**state, "phase": "RUNNING_AGENT", "pending_action": "agent"},
            "before agent",
        )
        maybe_crash(args.crash_at, "after_checkpoint_before_agent")
        try:
            agent_code = run_agent(config["agent_timeout_seconds"])
        except subprocess.TimeoutExpired:
            state = checkpoint(
                {
                    **state,
                    "status": "AGENT_TIMEOUT",
                    "phase": "TERMINAL",
                    "pending_action": None,
                    "terminal_reason": "agent timeout",
                },
                "agent timeout",
            )
            print("TERMINAL STATE: AGENT_TIMEOUT")
            return 2
        maybe_crash(args.crash_at, "after_agent_before_checkpoint")
        if agent_code != 0:
            state = checkpoint(
                {
                    **state,
                    "status": "AGENT_ERROR",
                    "phase": "TERMINAL",
                    "pending_action": None,
                    "terminal_reason": f"agent exit code {agent_code}",
                },
                "agent error",
            )
            print("TERMINAL STATE: AGENT_ERROR")
            return 2
        violations = protected_changes(config)
        if violations:
            state = checkpoint(
                {
                    **state,
                    "status": "POLICY_VIOLATION",
                    "phase": "TERMINAL",
                    "pending_action": None,
                    "terminal_reason": f"protected paths changed: {violations}",
                },
                "post-agent policy violation",
            )
            print("TERMINAL STATE: POLICY_VIOLATION")
            return 3
        state = checkpoint(
            {
                **state,
                "phase": "READY",
                "pending_action": None,
                "iteration": state["iteration"] + 1,
            },
            "agent completed",
        )
```

最关键的恢复性质来自：agent 前写 pending checkpoint；agent 后先检查策略；只有安全返回后才增加 iteration。若崩溃发生在中间，resume 通过仓库事实和 verifier 协调。

## 24. 强化实验：不要只跑成功路径

| 实验 | 操作 | 应观察到 |
| --- | --- | --- |
| 快照缺失 | 删除 run_state.json 后 --resume | 从账本重建 |
| 快照非法 JSON | 写入 `{broken` | 报告损坏并重建 |
| 账本内容篡改 | 替换第一行文本 | LEDGER_CORRUPTION，拒绝继续 |
| 锁冲突 | 手工创建 run.lock | 第二控制器拒绝运行 |
| Verifier 前崩溃 | --crash-at after_checkpoint_before_verifier | resume 重新验证 |
| Verifier 后崩溃 | --crash-at after_verifier_before_checkpoint | 不直接信孤立 artifact |
| Agent 前崩溃 | --crash-at after_checkpoint_before_agent | 验证后决定是否重调 |
| Agent 后崩溃 | --crash-at after_agent_before_checkpoint | 先验证现有 diff，避免重复调用 |
| DONE 后代码回退 | git restore src/statkit.py 后 --resume | 旧 DONE 失效并重新修复 |
| 证据文件被编辑 | 修改 report 后校验 sha256（自行补脚本） | 应拒绝该 evidence |

### 24.1 必做扩展题

**• **给 evidence loader 增加 report_sha256 校验，并验证状态指向的证据确实属于当前 run_id。

**• **把 agent_attempt_count 与 confirmed_iteration 分开，比较四个崩溃点下的值。

**• **把第 07 章严格 policy parser 接入本章，确保 untracked、rename、delete 和 symlink 都被恢复流程观察。

**• **为 force-unlock 写一个管理事件，包含操作者、原因和旧锁内容。

**• **使用 SQLite 实现 run、event、outbox 三张表，并在一个事务中 checkpoint + outbox。

**• **设计一个会发送“外部消息”的 mock action，证明无幂等键时 after-send crash 会重复。

## 25. 本章自测

### 问题 1　为什么不能只保存 run_state.json？

**参考结论：**快照没有完整历史，可能半写、落后或被覆盖；无法解释崩溃窗口，也难以审计。

### 问题 2　为什么账本先写、快照后写？

**参考结论：**账本包含完整 checkpoint，可在快照落后或损坏时重建；反向顺序会产生无审计快照。

### 问题 3　为什么看到 phase=RUNNING_AGENT 不能直接再调用 agent？

**参考结论：**代理可能已产生副作用但未确认；应先检查仓库/外部系统并重新验证。

### 问题 4　为什么 resume DONE 仍要运行 verifier？

**参考结论：**DONE 只对旧 revision、工作树和环境成立，当前事实可能变化。

### 问题 5　哈希链为什么不能防止有权限的人整体伪造账本？

**参考结论：**攻击者可修改所有事件并重新计算哈希，需要外部签名或不可变存储。

### 问题 6　原子替换解决了什么？

**参考结论：**避免读取半写快照；不提供跨账本、证据和外部副作用的多资源事务。

### 问题 7　iteration 与 agent attempt 为什么应分开？

**参考结论：**崩溃窗口中调用可能发生但未确认，两者混合会让预算和指标失真。

### 问题 8　幂等键必须何时生成？

**参考结论：**在逻辑动作首次确定时生成并持久化，重试必须复用同一键。

### 问题 9　为什么完整日志不应全部放回模型上下文？

**参考结论：**成本、陈旧信息、敏感数据和提示注入会累积，应重建最小任务包。

### 问题 10　账本损坏时为什么应 fail closed？

**参考结论：**无法证明历史和状态来源，继续可能重复副作用或接受伪造 DONE。

## 26. 本章验收清单

- [ ] 能用自己的话区分 snapshot、ledger、evidence、log、artifact 和 knowledge。

- [ ] 能解释 atomic write 的每一步以及其事务边界。

- [ ] 能运行正常闭环并通过 inspect_run.py 校验账本。

- [ ] 能在四个 crash-at 位置中至少完成三个恢复实验。

- [ ] 能证明 after_agent_before_checkpoint 恢复时没有再次调用代理。

- [ ] 能破坏 run_state.json 并从账本重建。

- [ ] 能篡改 events.jsonl 并观察到 LEDGER_CORRUPTION。

- [ ] 能制造锁冲突，并解释何时才允许 force-unlock。

- [ ] 能在 DONE 后修改仓库，证明旧 PASS 不被直接复用。

- [ ] 能解释哈希链、原子替换、锁和幂等键各自不能解决什么。

- [ ] 能提出一个数据库事务 + outbox 的生产升级方案。

- [ ] 能为自己的真实项目定义恢复 SLO 与 false-DONE-after-recovery 指标。

> 真正掌握的标志：你可以在任意 checkpoint 前后终止进程，并在不依赖“我记得刚才做了什么”的情况下，依据账本、仓库和新鲜证据解释系统为什么继续或停止。

## 附录 A　完整配置与实验文件

**A.1　`loop_config.json**`

```json
{
  "schema_version": 1,
  "max_iterations": 3,
  "agent_timeout_seconds": 30,
  "verifier_timeout_seconds": 60,
  "protected_paths": [
    "tests",
    "scripts/verify.py",
    "scripts/state_store.py",
    "scripts/event_ledger.py",
    "scripts/run_loop.py",
    "goal.md",
    "AGENTS.md",
    "loop_config.json"
  ],
  "allowed_write_roots": ["src"]
}
```

**A.2　src/statkit.py（故意缺陷版）**

```python
from __future__ import annotations
 
 
def min_max_normalize(values: list[float]) -> list[float]:
    """Scale values to [0, 1]. Constant vectors should map to all zeros."""
 
    if not values:
        return []
 
    lower = min(values)
    upper = max(values)
    span = upper - lower
    return [(value - lower) / span for value in values]
```

**A.3　tests/`test_statkit.py**`

```python
from statkit import min_max_normalize
 
 
def test_empty_vector() -> None:
    assert min_max_normalize([]) == []
 
 
def test_regular_vector() -> None:
    assert min_max_normalize([2.0, 4.0, 6.0]) == [0.0, 0.5, 1.0]
 
 
def test_constant_vector() -> None:
    assert min_max_normalize([3.0, 3.0, 3.0]) == [0.0, 0.0, 0.0]
```

**A.4　scripts/`mock_agent.py**`

```python
from __future__ import annotations
 
import argparse
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "statkit.py"
 
 
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-packet", type=Path, required=True)
    args = parser.parse_args()
 
    _ = args.task_packet.read_text(encoding="utf-8")
    text = TARGET.read_text(encoding="utf-8")
    marker = "    span = upper - lower\n"
    replacement = "    span = upper - lower\n    if span == 0:\n        return [0.0] * len(values)\n"
    if "if span == 0:" not in text:
        if marker not in text:
            print("AGENT_ERROR: expected marker is missing")
            return 2
        TARGET.write_text(text.replace(marker, replacement), encoding="utf-8")
        print("AGENT_ACTION: patched constant-vector boundary case")
    else:
        print("AGENT_ACTION: patch already present; no-op")
    print("AGENT_CLAIM: DONE")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

## 附录 B　run_state.json 字段词典

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| schema_version | int | 状态结构版本；升级时必须迁移 |
| run_id | string | 本次运行稳定身份 |
| status | enum | RUNNING、RECOVERING、DONE 或命名失败终态 |
| phase | enum | READY、VERIFYING、RUNNING_AGENT、TERMINAL 等执行位置 |
| iteration | int | 控制器已确认的代理动作数 |
| max_iterations | int | 迭代预算 |
| pending_action | string/null | 可能处于不确定窗口的动作 |
| last_evidence_path | string/null | 最近被 checkpoint 记录的证据指针 |
| revision | string | checkpoint 时 HEAD |
| workspace_fingerprint | string | checkpoint 时工作树指纹 |
| event_sequence | int | 对应账本位置 |
| terminal_reason | string/null | 进入终态或 recovery 的机械原因 |
| started_at / updated_at | RFC3339 string | 运行和状态更新时间 |

## 附录 C　PowerShell 命令速查

**C.1　正常运行**

```powershell
python scripts\verify.py
python scripts\run_loop.py
python scripts\inspect_run.py
```

**C.2　恢复**

```powershell
python scripts\run_loop.py --resume
python scripts\run_loop.py --resume --force-unlock
```

**C.3　四个崩溃注入点**

```powershell
python scripts\run_loop.py --crash-at after_checkpoint_before_verifier
python scripts\run_loop.py --crash-at after_verifier_before_checkpoint
python scripts\run_loop.py --crash-at after_checkpoint_before_agent
python scripts\run_loop.py --crash-at after_agent_before_checkpoint
```

**C.4　检查证据**

```powershell
Get-Content state\run_state.json
Get-Content logs\events.jsonl
Get-ChildItem evidence
git status --short
git diff
```

## 附录 D　参考资料与延伸阅读

**• **[1] Python Standard Library: os.replace, os.fsync, tempfile.mkstemp。用于理解原子替换和持久化边界。

**• **[2] Git Documentation: git-status porcelain format。用于脚本化读取仓库状态。

**• **[3] SQLite Documentation: Atomic Commit、Transactions 与 WAL。用于从文件状态升级到事务状态存储。

**• **[4] Martin Kleppmann, Designing Data-Intensive Applications。事件日志、复制、一致性和故障模型。

**• **[5] Pat Helland, Life beyond Distributed Transactions: an Apostate’s Opinion。跨服务副作用、幂等和补偿。

**• **[6] OpenTelemetry Specification。生产系统中的 traces、metrics、logs 关联。

**• **[7] 原始讲义《Loop Engineering：从提示词到可验证自治闭环》中的状态、记忆、恢复和新鲜证据章节。

## 本章结语

可靠自治的分水岭，不是代理能连续工作多少小时，而是系统在任意中断后能否重建可信事实。状态快照提供速度，事件账本提供历史，证据提供可证伪性，锁提供单写者边界，恢复协调把这些组件重新连接。下一章将在此基础上加入独立只读 Reviewer，构建 Builder—Verifier—Reviewer 双门闭环。

---

[返回课程主页](../../README.md) · [← 上一章](./07-protected-paths-and-diff-policy.md) · [下一章 →](./09-independent-reviewer.md)
