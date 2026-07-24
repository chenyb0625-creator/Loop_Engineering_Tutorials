# 第 11 章：Git Worktree 与并行代理

[返回课程主页](../../README.md) · [← 上一章](./10-context-engineering.md) · [下一章 →](./12-failure-modes.md)

## 本章使用说明

原教程指出：多个代理若在同一工作目录编辑文件，状态会相互污染，验证结果也无法归因；Git worktree 可以让同一仓库同时拥有多个独立工作树，每个工作树检出不同分支，因而适合作为并行代理的隔离单元。本章把这条原则展开成两个可运行实验：两个代理竞争解决同一问题，以及两个代理按文件所有权并行完成不同子任务。

```text
本章核心判断
并行不是“多开几个聊天窗口”。只有当输入基线、工作区、分支、权限、验证证据和集成责任都可分离时，并行才是工程增益；否则并行只会把冲突、成本和错误放大。
```

### 学习目标

- 解释 main worktree、linked worktree、branch、HEAD、index 与共享 object database 的关系。

- 理解 worktree 能隔离哪些状态，以及它不能隔离数据库、端口、缓存、凭据和外部服务。

- 掌握 git worktree add/list/remove/prune/lock/unlock/move/repair 的安全用法。

- 在创建并行任务前验证主工作树干净、基线 revision 固定、分支名和目录名唯一。

- 实现候选竞争：从同一 base revision 创建两个候选工作树，运行同一 verifier 和 policy。

- 设计证据优先的候选选择规则，而不是让代理互相评价或按自然语言“看起来更好”选胜者。

- 理解为什么同一任务的候选通常选一个，不应把两个候选的代码自动混合。

- 实现任务分解：为代理声明文件所有权、目标测试和禁止修改路径。

- 在 integration worktree 中逐分支合并，并重新运行完整 verifier。

- 把 reviewer 放在只读或 detached worktree 中，避免与 builder 共享可写状态。

- 识别过早并行、共享核心文件、共享数据库、端口冲突、分支漂移和过度订阅等失败模式。

- 用 wall-clock speedup、parallel efficiency、冲突率、集成回归率和无效计算比例评估并行价值。

## 1. 并行的目标：减少关键路径，而不是增加代理数量

并行系统的目标是缩短从任务触发到获得可信 DONE 证据的关键路径。代理数量只是手段。若验证器、核心文件或外部服务仍是串行瓶颈，多开代理不会产生线性加速，反而增加上下文构造、环境启动、合并、复核和失败恢复成本。

**不要用“同时运行了几个代理”代替性能指标**

```text
单代理总时间：
T_serial = T_implement + T_verify + T_review

理想并行下界：
T_parallel >= max(T_agent_1, ..., T_agent_n) + T_integrate + T_full_verify

实际收益：
Speedup = T_serial / T_parallel
Parallel efficiency = Speedup / n
```

| 情形 | 是否适合并行 | 理由 |
| --- | --- | --- |
| 两个代理独立提出同一 bug 的不同修复 | 适合候选竞争 | 共享目标但不共享编辑状态；可用同一验证器选择 |
| 两个模块文件互不重叠，接口已经冻结 | 适合任务分解 | 所有权和合并边界明确 |
| 两个任务都必须修改同一核心函数 | 通常不适合 | 冲突和相关错误使集成成本高 |
| 所有代理都依赖一个慢速端到端测试环境 | 收益有限 | 验证成为串行瓶颈 |
| 任务尚未定义验收标准 | 不适合 | 并行只会产生多个不可比较答案 |

```text
压力测试
先问“瓶颈是否可分解”，再问“能否并行”。如果你不能明确写出每个代理的输入、所有权、验证门和集成协议，就还没有并行设计。
```

## 2. Git worktree 的内部模型与隔离边界

一个普通 Git 仓库有一个主工作树。git worktree add 会创建 linked worktree：它拥有独立的工作目录、HEAD 和 index，可以检出另一个分支；同时与主工作树共享对象数据库和大部分仓库级元数据。共享对象库意味着不必为每个代理完整 clone 一份历史，但也意味着 worktree 不是容器或安全边界。

**概念结构图**

```text
repository common data
├─ object database / refs / remotes / shared config
├─ main worktree
│  ├─ working directory
│  ├─ HEAD → main
│  └─ index
├─ linked worktree A
│  ├─ working directory
│  ├─ HEAD → loop/candidate-a
│  └─ index
└─ linked worktree B
   ├─ working directory
   ├─ HEAD → loop/candidate-b
   └─ index
```

| 状态对象 | 是否隔离 | 工程含义 |
| --- | --- | --- |
| 工作目录中的 tracked/untracked 文件 | 隔离 | 代理 A 的文件编辑不会直接出现在 B 的目录 |
| index / staging area | 隔离 | 每个代理可独立 git add 与 commit |
| HEAD 与当前分支 | 隔离 | 每个 worktree 可位于不同 branch 或 detached HEAD |
| Git objects、refs、remotes | 共享 | 提交立即对同一仓库其他 worktree 可见 |
| 多数仓库 config 与 hooks | 共享 | 不要假设代理可独立修改这些控制面 |
| Python venv、node_modules、build 输出 | 取决于路径 | 若位于各 worktree 内可隔离；若用全局缓存则共享 |
| 数据库、端口、云资源、凭据 | 不隔离 | 必须额外命名空间和最小权限 |

```powershell
关键限制
Git 通常不允许同一个分支同时被两个 worktree 正常检出。候选竞争应给每个候选创建独立分支；只读审查可使用 detached HEAD，避免占用候选分支。
```

## 3. 三种并行模式及其适用条件

| 模式 | 并行单元 | 集成策略 | 主要风险 |
| --- | --- | --- | --- |
| 候选竞争 | 多个代理解决同一目标 | 验证每个候选，选择一个胜者 | 候选高度同质、浪费计算、选择器薄弱 |
| 任务分解 | 代理处理互不重叠子任务 | 按所有权合并到 integration branch | 隐藏耦合、接口漂移、合并后回归 |
| 实现—审查流水线 | Builder 可写，Reviewer 只读 | findings 回到下一轮 builder | 审查上下文不独立或 reviewer 直接改代码 |

候选竞争和任务分解不能混为一谈。前者的输出彼此替代，通常只保留一个；后者的输出彼此互补，需要在集成点组合。错误地把两个候选自动合并，等于把“竞争”变成未经设计的代码拼接。

## 4. 并行前置条件与命名规范

并行调度之前，控制器必须冻结一个共同基线。若候选 A 从 revision X 出发、候选 B 从 X 之后的 dirty main 出发，则比较不再公平，验证证据也无法归因。

| 前置检查 | 机械条件 | 失败终态 |
| --- | --- | --- |
| 主工作树干净 | git status --porcelain 为空 | BLOCKED_DIRTY_BASELINE |
| 基线可解析 | git rev-parse HEAD 成功 | CONFIG_ERROR |
| 分支名唯一 | 目标 branch 未被占用 | DISPATCH_CONFLICT |
| 目录名唯一 | worktree path 不存在或可安全清理 | DISPATCH_CONFLICT |
| 验证器可在基线运行 | 能够产生确定性初始证据 | VERIFIER_ERROR |
| 任务可比较或可分解 | 存在候选选择规则或 ownership map | BLOCKED_UNSPECIFIED |

**生产环境应把 run_id 写进分支和目录，避免不同运行相互覆盖**

```text
run_id = 20260720T120000Z-ab12
base_revision = <40-char SHA>

candidate branches:
  loop/<run_id>/candidate-a
  loop/<run_id>/candidate-b

worktree paths:
  ../.loop-worktrees/<run_id>/candidate-a
  ../.loop-worktrees/<run_id>/candidate-b
```

## 5. 手动创建、检查和清理 worktree

**PowerShell / Bash 通用核心命令**

```powershell
# 从主仓库创建两个独立候选分支与工作树
git worktree add ../wt-a -b loop/candidate-a HEAD
git worktree add ../wt-b -b loop/candidate-b HEAD

# 查看 worktree、revision、branch、locked/prunable 状态
git worktree list --porcelain

# 在各自目录中工作
cd ../wt-a
git status --short
git branch --show-current

# 正常清理：先确认已提交或不再需要
git worktree remove ../wt-a
git branch -d loop/candidate-a

# 若目录被手工删除，清理残留管理记录
git worktree prune --dry-run
git worktree prune
```

不要直接用文件管理器删除 worktree 目录。正常路径是 git worktree remove；若外部因素导致目录被移动或管理信息失联，可使用 git worktree move 或 git worktree repair。位于可移动磁盘或不稳定网络路径上的 worktree 可以 lock，防止其管理记录被 prune。

| 命令 | 用途 | 注意事项 |
| --- | --- | --- |
| git worktree list --porcelain | 机器可读地列出所有工作树 | 控制器应解析此输出而非屏幕文本 |
| git worktree lock --reason ... | 防止 prune、move 或 remove | 长期任务或可移动路径适用 |
| git worktree unlock | 解除锁定 | 清理前确认任务已终止 |
| git worktree move | 由 Git 管理地移动 linked worktree | 主工作树和含 submodule 的 linked worktree 有限制 |
| git worktree repair | 修复移动或复制后的管理连接 | 修复后仍需验证 revision 与 branch |
| git worktree prune --dry-run | 预览将删除的失效元数据 | 先 dry-run，再执行 |

## 6. 实验一：候选竞争的系统架构

候选竞争适用于目标明确但解法空间不确定的任务。控制器从同一 base revision 创建多个 worktree，给每个 agent 相同 goal、verifier 和 policy，但允许不同策略或随机性。每个候选独立提交；选择器先执行硬门，再比较软指标。

**候选竞争闭环**

```text
same base revision
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
     worktree A / branch A            worktree B / branch B
       agent strategy A                 agent strategy B
              │                               │
      verifier + policy                verifier + policy
              └───────────────┬───────────────┘
                              ▼
                       evidence selector
                 hard gates → score → winner
                              │
                    cherry-pick one commit
                              ▼
                       verify on main again
```

```text
选择顺序
硬门优先：verifier 必须通过、policy 必须通过、证据必须新鲜。只有多个候选都满足硬门时，才比较 diff 大小、审查 findings、性能、成本或其他软指标。
```

## 7. 建立 candidate_lab 与初始失败

实验只依赖 Python 标准库和 Git。目标函数 min_max_normalize 在常量向量上分母为零。两个候选都会通过测试，但一个是最小修复，另一个引入额外 helper 和循环。我们用同一 verifier 和明确评分规则选择更小的通过 diff。

**目录结构**

```text
candidate_lab/
├─ src/statkit.py
├─ tests/test_statkit.py
├─ scripts/verify.py
├─ scripts/mock_candidate.py
├─ scripts/run_candidates.py
├─ artifacts/
└─ .gitignore
```

**src/statkit.py：故意保留的除零缺陷**

```python
from __future__ import annotations


def min_max_normalize(values: list[float]) -> list[float]:
    """Scale values into [0, 1].

    Contract: a non-empty constant vector is mapped to all zeros.
    """
    if not values:
        return []
    low = min(values)
    high = max(values)
    span = high - low
    if span == 0:
        return [0.0] * len(values)
    return [(value - low) / span for value in values]
```

**tests/`test_statkit.py**`

```python
from __future__ import annotations

import unittest

from src.statkit import min_max_normalize


class NormalizeTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(min_max_normalize([]), [])

    def test_regular_vector(self) -> None:
        self.assertEqual(min_max_normalize([2.0, 4.0, 6.0]), [0.0, 0.5, 1.0])

    def test_constant_vector(self) -> None:
        self.assertEqual(min_max_normalize([5.0, 5.0, 5.0]), [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
```

**初始化并观察失败**

```powershell
cd candidate_lab
git init
git config user.name "Loop Lab"
git config user.email "lab@example.com"
git add .
git commit -m "starter lab"

python scripts/verify.py
```

**预期关键输出**

```text
test_constant_vector ... ERROR
ZeroDivisionError: float division by zero
VERDICT: FAIL
```

## 8. 实现两个隔离候选代理

为了让实验可复现，本章先用 mock agent 模拟两种策略。真实系统可把这一步替换为 codex exec，但 worktree、任务包、权限、验证和选择逻辑不变。

| 候选 | 策略 | 预期特征 |
| --- | --- | --- |
| A / minimal | 只在 span == 0 时返回等长零向量 | 2 行新增，最小根因修复 |
| B / refactor | 抽 helper、改变量名、显式循环 | 功能正确，但 diff 更大、审查面更广 |

**scripts/mock_candidate.py：两种确定性候选内容**

```python
MINIMAL = '''from __future__ import annotations


def min_max_normalize(values: list[float]) -> list[float]:
    """Scale values into [0, 1].

    Contract: a non-empty constant vector is mapped to all zeros.
    """
    if not values:
        return []
    low = min(values)
    high = max(values)
    span = high - low
    if span == 0:
        return [0.0] * len(values)
    return [(value - low) / span for value in values]
'''

REFACTOR = '''from __future__ import annotations


def _constant_result(length: int) -> list[float]:
    """Build the contract-defined output for a constant vector."""
    return [0.0 for _ in range(length)]


def _scale(value: float, low: float, span: float) -> float:
    """Scale one value after the caller has validated the span."""
    return (value - low) / span


def min_max_normalize(values: list[float]) -> list[float]:
    """Scale values into [0, 1].

    Contract: a non-empty constant vector is mapped to all zeros.
    """
    if len(values) == 0:
        return []

    minimum = min(values)
    maximum = max(values)
    distance = maximum - minimum

    if distance == 0:
        return _constant_result(len(values))

    normalized: list[float] = []
    for item in values:
        normalized.append(_scale(item, minimum, distance))
    return normalized
'''
```

不要从这个实验得出“最小 diff 永远最好”。最小 diff 只是本任务的显式偏好；在安全修复、性能改造或架构迁移中，候选选择器可能需要隐藏测试、基准、静态扫描和独立审查。关键是指标必须在调度前声明，而不是看完结果后临时改规则。

## 9. 实现统一 verifier、policy 与评分

每个候选必须运行同一 verifier。实验 verifier 先 compileall，再运行 unittest，并把结果写入 artifacts/verification.json。policy 要求候选至少修改一个文件，并且所有变更都位于 src/。

**scripts/`verify.py**`

```python
from __future__ import annotations

import compileall
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    compile_ok = compileall.compile_dir(ROOT / "src", quiet=1)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    verdict = "PASS" if compile_ok and result.returncode == 0 else "FAIL"
    report = {
        "verdict": verdict,
        "compile_ok": compile_ok,
        "test_exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    (ARTIFACTS / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

**实验评分规则**

```text
hard gates:
  verifier_pass == true
  policy_pass == true
  commit != null

score:
  +1000 if verifier passed
  + 500 if policy passed
  -  20 × changed_file_count
  -   1 × (added_lines + deleted_lines)
```

```text
评分规则的局限
这个分数只用于演示“硬门 + 软排序”。它不能代表普遍代码质量。生产选择器应保存每个分量，避免一个总分掩盖关键退化；安全、正确性和合规要求通常应作为不可补偿的硬门。
```

## 10. 候选调度器 run_candidates.py

调度器负责五件事：确认主工作树干净；冻结 base revision；创建候选分支与 worktree；调用 agent、verifier 和 policy；保存报告并可选 cherry-pick 胜者。代理没有权限决定自己是否胜出。

**数据结构、命令执行与 diff 指标**

```python
@dataclass
class CandidateResult:
    name: str
    branch: str
    path: str
    verifier_pass: bool
    policy_pass: bool
    changed_files: list[str]
    added_lines: int
    deleted_lines: int
    score: int
    commit: str | None


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def ensure_clean() -> None:
    status = git("status", "--porcelain").stdout.strip()
    if status:
        raise RuntimeError("main worktree must be clean before parallel dispatch")


def remove_existing(path: Path, branch: str) -> None:
    if path.exists():
        git("worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("branch", "-D", branch, check=False)


def diff_metrics(cwd: Path) -> tuple[list[str], int, int]:
    names = [line for line in git("diff", "--name-only", cwd=cwd).stdout.splitlines() if line]
    added = deleted = 0
    for line in git("diff", "--numstat", cwd=cwd).stdout.splitlines():
        a, d, _ = line.split("\t", 2)
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            deleted += int(d)
    return names, added, deleted
```

**evaluate：创建 worktree、执行候选、验证、评分并提交**

```python
def evaluate(name: str, strategy: str, base: str) -> CandidateResult:
    branch = f"loop/candidate-{name}"
    path = WORKTREE_PARENT / name
    remove_existing(path, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-b", branch, str(path), base)

    run(
        [sys.executable, str(ROOT / "scripts" / "mock_candidate.py"),
         "--worktree", str(path), "--strategy", strategy],
        cwd=ROOT,
    )
    verify = run([sys.executable, "scripts/verify.py"], cwd=path, check=False)
    changed, added, deleted = diff_metrics(path)
    policy_pass = bool(changed) and all(p.startswith("src/") for p in changed)
    verifier_pass = verify.returncode == 0

    score = 0
    score += 1000 if verifier_pass else 0
    score += 500 if policy_pass else 0
    score -= len(changed) * 20
    score -= added + deleted

    commit = None
    if verifier_pass and policy_pass:
        git("add", "src", cwd=path)
        git("commit", "-m", f"candidate {name}: {strategy} fix", cwd=path)
        commit = git("rev-parse", "HEAD", cwd=path).stdout.strip()

    return CandidateResult(
        name=name,
        branch=branch,
        path=str(path),
        verifier_pass=verifier_pass,
        policy_pass=policy_pass,
        changed_files=changed,
        added_lines=added,
        deleted_lines=deleted,
        score=score,
        commit=commit,
    )
```

**main：并行候选报告与可选应用胜者**

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-winner", action="store_true")
    args = parser.parse_args()

    ensure_clean()
    ARTIFACTS.mkdir(exist_ok=True)
    base = git("rev-parse", "HEAD").stdout.strip()
    results = [
        evaluate("a", "minimal", base),
        evaluate("b", "refactor", base),
    ]
    eligible = [r for r in results if r.verifier_pass and r.policy_pass and r.commit]
    winner = max(eligible, key=lambda r: r.score) if eligible else None
    report = {
        "base_revision": base,
        "results": [asdict(r) for r in results],
        "winner": asdict(winner) if winner else None,
        "selection_rule": "PASS gates first; then maximize score with smaller diff penalty",
    }
    (ARTIFACTS / "candidate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.apply_winner:
        if winner is None or winner.commit is None:
            print("NO WINNER TO APPLY", file=sys.stderr)
            return 2
        git("cherry-pick", winner.commit)
        print(f"APPLIED WINNER: {winner.name} {winner.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

示例脚本为便于教学按顺序执行 A、B，但每个候选已经完全隔离。将 evaluate 调用交给线程池、进程池或队列 runner，即可真正并发。并发化之前先保证日志路径、端口、缓存和 artifacts 也按 candidate_id 隔离。

## 11. 运行候选竞争并应用胜者

**`PowerShell**`

```powershell
# 只评估，不改主分支
python scripts/run_candidates.py

# 查看工作树与报告
git worktree list --porcelain
Get-Content artifacts\candidate_report.json

# 重新从干净基线运行，并把胜者 cherry-pick 到当前分支
python scripts/run_candidates.py --apply-winner
python scripts/verify.py
git log --oneline --decorate -5
```

**本实验的预期比较**

```text
candidate A:
  verifier_pass: true
  policy_pass: true
  added_lines: 2
  deleted_lines: 0
  score: 1478

candidate B:
  verifier_pass: true
  policy_pass: true
  added_lines: 23
  deleted_lines: 5
  score: 1452

winner: candidate A
```

应用胜者后必须在主工作树重新运行 verifier。候选证据绑定的是候选 worktree 的 commit 和环境；cherry-pick 可能遇到新基线、冲突、钩子或依赖变化。没有主分支上的新鲜 PASS，就不能宣称集成完成。

```text
不要自动合并候选 A 与 B
它们是同一目标的替代解。混合两个候选会产生一个从未被任何 verifier 验证的新实现。正确动作是选择一个，或把两者的优点重新形成第三个候选并独立验证。
```

## 12. 实验二：任务分解与文件所有权

任务分解适用于输出互补而不是互斥的情况。每个代理获得一个 ownership contract：允许修改哪些文件、运行哪个目标测试、不得触碰哪些共享接口。即使各子任务都通过目标测试，Controller 仍必须在 integration worktree 合并后运行完整验证器。

**文件所有权契约**

```text
task metrics:
  owned_paths: [src/metrics.py]
  target_test: test_metrics.py

task report:
  owned_paths: [src/report.py]
  target_test: test_report.py

integration gate:
  merge both task branches
  run all tests
  inspect combined diff
  optional independent review
```

| 边界 | 为什么需要 | 机械检查 |
| --- | --- | --- |
| owned_paths | 防止代理跨模块顺手重构 | changed_paths ⊆ owned_paths |
| frozen interfaces | 降低合并后语义冲突 | API/schema tests、签名检查 |
| target verifier | 快速验证子任务自身 | 按 test pattern 或模块运行 |
| full verifier | 发现跨模块交互回归 | 集成后运行完整测试、lint、build |
| merge order | 使失败可归因、可复现 | 版本化 integration plan |

## 13. 建立 decomposition_lab

**目录结构**

```text
decomposition_lab/
├─ src/metrics.py       # 空列表除零
├─ src/report.py        # 输出格式错误
├─ tests/test_metrics.py
├─ tests/test_report.py
├─ scripts/verify.py
├─ scripts/mock_task.py
└─ scripts/run_decomposition.py
```

**src/`metrics.py**`

```python
from __future__ import annotations


def mean_or_zero(values: list[float]) -> float:
    """Return the arithmetic mean; empty input must return 0.0."""
    return sum(values) / len(values)
```

**src/`report.py**`

```python
from __future__ import annotations


def render_summary(name: str, score: float) -> str:
    """Return '<name>: <score with two decimals>'."""
    return f"{name}={score}"
```

**tests/`test_metrics.py**`

```python
from __future__ import annotations

import unittest

from src.metrics import mean_or_zero


class MetricsTests(unittest.TestCase):
    def test_regular(self) -> None:
        self.assertEqual(mean_or_zero([2.0, 4.0]), 3.0)

    def test_empty(self) -> None:
        self.assertEqual(mean_or_zero([]), 0.0)


if __name__ == "__main__":
    unittest.main()
```

**tests/`test_report.py**`

```python
from __future__ import annotations

import unittest

from src.report import render_summary


class ReportTests(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(render_summary("alpha", 3.5), "alpha: 3.50")


if __name__ == "__main__":
    unittest.main()
```

## 14. 目标验证与全局验证的区别

metrics 代理只负责 src/metrics.py，因此它在自己的 worktree 中运行 test_metrics.py；report 代理同理。目标验证回答“该子任务是否满足局部契约”，全局验证回答“所有分支组合后系统是否仍满足整体契约”。二者不能互相替代。

**scripts/verify.py：--pattern 支持目标测试，也可省略参数运行全量**

```python
from __future__ import annotations

import argparse
import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args()
    compile_ok = compileall.compile_dir(ROOT / "src", quiet=1)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", args.pattern, "-v"],
        cwd=ROOT,
        check=False,
    )
    ok = compile_ok and result.returncode == 0
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

| 阶段 | 运行位置 | 验证范围 | 通过后含义 |
| --- | --- | --- | --- |
| Task verify | 各自 task worktree | 所属模块及契约 | 候选分支可提交，但尚未集成 |
| Ownership gate | 各自 task worktree | changed_paths 与 owned_paths | 没有越界修改 |
| Merge | integration worktree | Git 合并是否成功 | 语法冲突解决，但不代表行为正确 |
| Full verify | integration worktree | 完整测试、lint、build、review | 组合状态可进入集成 PASS |

## 15. 集成工作树、合并顺序与重新验证

run_decomposition.py 从同一 base 创建两个任务 worktree。每个 task 修改自己的文件、运行目标测试、检查所有权并提交。之后创建第三个 integration worktree，按固定顺序 merge 两个任务分支，最后运行全量 verifier。

**任务所有权配置**

```text
TASKS = {
    "metrics": {"branch": "loop/task-metrics", "owned": ["src/metrics.py"], "test": "test_metrics.py"},
    "report": {"branch": "loop/task-report", "owned": ["src/report.py"], "test": "test_report.py"},
}
```

**任务调度、提交、集成与完整验证**

```python
def main() -> int:
    if git("status", "--porcelain").stdout.strip():
        raise RuntimeError("main worktree must be clean")
    ARTIFACTS.mkdir(exist_ok=True)
    base = git("rev-parse", "HEAD").stdout.strip()
    results: dict[str, dict[str, object]] = {}

    for task, spec in TASKS.items():
        path = WT_PARENT / task
        cleanup(path, str(spec["branch"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-b", str(spec["branch"]), str(path), base)
        run([sys.executable, str(ROOT / "scripts" / "mock_task.py"), "--worktree", str(path), "--task", task], cwd=ROOT)
        changed = [x for x in git("diff", "--name-only", cwd=path).stdout.splitlines() if x]
        ownership_ok = set(changed).issubset(set(spec["owned"])) and bool(changed)
        verify = run([sys.executable, "scripts/verify.py", "--pattern", str(spec["test"])], cwd=path, check=False)
        passed = verify.returncode == 0
        commit = None
        if passed and ownership_ok:
            git("add", *changed, cwd=path)
            git("commit", "-m", f"task {task}", cwd=path)
            commit = git("rev-parse", "HEAD", cwd=path).stdout.strip()
        results[task] = {"changed": changed, "ownership_ok": ownership_ok, "targeted_pass": passed, "commit": commit}

    integration_branch = "loop/integration"
    integration_path = WT_PARENT / "integration"
    cleanup(integration_path, integration_branch)
    git("worktree", "add", "-b", integration_branch, str(integration_path), base)

    merge_ok = True
    for task in ("metrics", "report"):
        branch = str(TASKS[task]["branch"])
        merge = git("merge", "--no-ff", "--no-edit", branch, cwd=integration_path, check=False)
        if merge.returncode != 0:
            merge_ok = False
            break

    full_verify = run([sys.executable, "scripts/verify.py"], cwd=integration_path, check=False) if merge_ok else None
    integration_pass = bool(full_verify and full_verify.returncode == 0)
    report = {
        "base_revision": base,
        "tasks": results,
        "integration": {
            "path": str(integration_path),
            "branch": integration_branch,
            "merge_ok": merge_ok,
            "full_verifier_pass": integration_pass,
            "head": git("rev-parse", "HEAD", cwd=integration_path).stdout.strip() if merge_ok else None,
        },
    }
    (ARTIFACTS / "decomposition_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if integration_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

**`PowerShell**`

```powershell
cd decomposition_lab
git init
git config user.name "Loop Lab"
git config user.email "lab@example.com"
git add .
git commit -m "starter lab"

python scripts/run_decomposition.py
git worktree list --porcelain
Get-Content artifacts\decomposition_report.json
```

**预期结果**

```text
tasks.metrics:
  changed: [src/metrics.py]
  ownership_ok: true
  targeted_pass: true

tasks.report:
  changed: [src/report.py]
  ownership_ok: true
  targeted_pass: true

integration:
  merge_ok: true
  full_verifier_pass: true
```

在生产系统中，不应因为文件不重叠就跳过全量验证。两个模块可能通过共享类型、配置、导入顺序、性能预算或外部副作用相互作用。Git 的文本无冲突只说明补丁能拼接，不说明行为可组合。

## 16. Reviewer、Codex CLI 与桌面 Worktree 映射

### 16.1 只读 Reviewer worktree

Reviewer 不必占用候选分支。可从候选 commit 创建 detached worktree，并以只读沙箱运行审查。这样 reviewer 看到固定 revision，又不能直接修改候选。

**PowerShell 示例**

```powershell
git worktree add --detach ../wt-review <candidate-commit>
cd ../wt-review

codex exec --sandbox read-only --ephemeral `
  "根据 goal、git diff 和 verifier 证据输出结构化 findings；不要修改文件"

cd ../main-repo
git worktree remove ../wt-review
```

### 16.2 Codex CLI 候选代理

**在每个 worktree 目录中独立调用 codex `exec**`

```python
def invoke_codex(worktree: Path, task_packet: str) -> int:
    result = subprocess.run(
        [
            "codex", "exec",
            "--sandbox", "workspace-write",
            "--ephemeral",
            "-",
        ],
        cwd=worktree,
        input=task_packet,
        text=True,
        check=False,
    )
    return result.returncode
```

OpenAI 当前文档将 codex exec 定位为脚本和 CI 中的非交互入口；Codex/ChatGPT 桌面环境也提供基于 Git worktree 的并行项目聊天。无论使用 CLI 还是桌面界面，工程原则不变：不同线程必须绑定不同工作区，最终状态仍由外部 verifier 和 integration gate 决定。

### 16.3 Subagent 与 worktree 不是同一层

| 机制 | 隔离对象 | 适合任务 | 不可替代的部分 |
| --- | --- | --- | --- |
| Subagent | 上下文、角色、工具权限 | 探索、审查、信息汇总、可并行子问题 | 不自动保证文件系统和 Git 状态隔离 |
| Git worktree | 工作目录、index、HEAD/branch | 并行代码编辑、候选分支、集成验证 | 不提供模型角色编排和外部资源隔离 |
| Container/VM | 进程、文件系统、网络、资源 | 不可信执行、依赖冲突、系统级隔离 | 成本更高，仍需 Git 与证据协议 |

## 17. Git 之外的资源命名空间

Worktree 只解决仓库文件状态的隔离。真正并行运行时，代理仍可能争用数据库、端口、Docker 容器名、临时目录、缓存目录、云资源和速率额度。若这些资源没有 candidate_id 或 task_id 命名空间，测试结果仍会互相污染。

| 共享资源 | 典型故障 | 隔离策略 |
| --- | --- | --- |
| TCP 端口 | 第二个服务启动失败或连到错误实例 | 动态端口分配，记录到 task state |
| 测试数据库 | 并行迁移、清表、脏数据串扰 | 每任务独立 schema/database，禁止生产凭据 |
| Docker 名称/网络 | 容器重名或错误停止对方容器 | 名称含 run_id，独立 network/compose project |
| 临时目录 | 覆盖日志、报告和下载文件 | tempfile + run_id/candidate_id |
| 构建缓存 | 不一致缓存导致伪 PASS 或性能噪声 | 按 revision/lockfile 指纹分区或显式冷启动 |
| GPU/CPU/内存 | 过度订阅、OOM、尾延迟暴涨 | 资源配额、并发上限、队列背压 |
| API 速率与费用 | 候选互相触发限流，成本失控 | 全局 budget ledger 与速率调度器 |

**把资源分配写入控制器状态和证据**

```text
resource_namespace = f"{run_id}-{candidate_id}"

env = {
    "TEST_DB": f"loop_{resource_namespace}",
    "COMPOSE_PROJECT_NAME": resource_namespace,
    "TMPDIR": str(run_dir / "tmp" / candidate_id),
    "PORT": str(port_allocator.acquire(candidate_id)),
}
```

## 18. 破坏实验与故障恢复

| 实验 | 操作 | 预期观察 | 正确终态/恢复 |
| --- | --- | --- | --- |
| 同分支双检出 | 让两个 worktree 使用同一 branch | Git 拒绝或迫使危险 override | 为每个 agent 创建唯一 branch |
| Dirty baseline | 主工作树保留未提交修改后调度 | 候选基线不可比较 | BLOCKED_DIRTY_BASELINE |
| 所有权越界 | metrics agent 同时改 report.py | target test 可能通过但 policy 失败 | POLICY_VIOLATION |
| 只做目标测试 | 两个 task 各自 PASS，不做全量验证 | 隐藏集成回归不会被发现 | 必须 integration full verify |
| 手工删除目录 | 直接删除 linked worktree 文件夹 | list 显示 prunable 或管理记录残留 | prune --dry-run 后 prune |
| 移动 worktree | 文件管理器直接搬目录 | Git 管理连接失效 | 优先 worktree move；否则 repair |
| 候选证据过期 | 候选验证后继续修改文件 | report 与 workspace fingerprint 不一致 | EVIDENCE_STALE，重新验证 |
| 端口冲突 | 两个 worktree 使用同一固定端口 | 随机失败或连到对方服务 | 端口分配器与资源命名空间 |
| 过度并行 | 并发数远高于 CPU/GPU/测试容量 | wall time 反而上升，超时增多 | 测吞吐后设置并发上限 |

```text
真正掌握的标志
你不仅能让两个 worktree 同时成功，还能预测它们在分支冲突、越权、证据过期、合并失败和共享资源污染时如何停止，并能留下可复盘证据。
```

### 18.1 手工冲突实验

把两个 task 的 owned_paths 都改为包含同一个 src/common.py，并让两个 mock agent 对同一行做不同修改。它们各自在分支内可以通过目标测试，但 integration merge 会出现文本冲突。不要让 agent 在没有上下文的情况下自动选一边；控制器应进入 MERGE_CONFLICT，保存两个 commit、冲突文件和 base revision，并把冲突交给专门集成任务。

**合并冲突应成为命名终态，而不是静默重试**

```powershell
if merge.returncode != 0:
    conflict_files = git("diff", "--name-only", "--diff-filter=U")
    save_state({
        "status": "MERGE_CONFLICT",
        "base_revision": base,
        "branches": branches,
        "conflict_files": conflict_files,
    })
    git("merge", "--abort")
    stop()
```

## 19. 如何科学评估并行代理

只比较“串行用了多久、并行用了多久”仍不够。并行可能通过增加总 token、重复环境启动和无效候选换取较小 wall-clock 改善。评估必须同时覆盖速度、可靠性、资源和协调成本。

| 指标 | 定义 | 解释 |
| --- | --- | --- |
| Wall-clock speedup | T_serial / T_parallel | 关键路径缩短多少 |
| Parallel efficiency | speedup / agent_count | 增加的代理是否被有效利用 |
| Candidate pass rate | 通过硬门的候选数 / 候选总数 | 并行探索是否产生有效结果 |
| Candidate diversity | 候选 diff/设计的差异度 | 多个代理是否只是复制同一思路 |
| Wasted compute ratio | 未被采用且未提供信息增益的成本 / 总成本 | 候选竞争的代价 |
| Ownership violation rate | 越界任务数 / 分解任务数 | 任务边界是否清晰 |
| Merge conflict rate | 发生冲突的 integration 次数 / 总次数 | 分解质量与耦合程度 |
| Integration regression rate | 子任务均 PASS 但全量 FAIL 的比例 | 局部验证是否掩盖系统问题 |
| False-DONE rate | 系统宣称完成但隐藏/外部验证失败 | 最重要的可靠性风险 |
| Human intervention rate | 需要人工处理的运行数 / 总运行数 | 自治程度与可维护性 |

**最小评估记录**

```text
experiment record:
  task_id
  base_revision
  mode: serial | candidate_competition | decomposition
  agent_count
  wall_clock_seconds
  total_model_calls
  total_tokens / cost
  verifier_passes
  policy_violations
  merge_conflicts
  integration_regressions
  human_minutes
  terminal_state
```

比较时要控制任务集合、模型版本、预算、验证器、环境和基线 revision。候选竞争的优势常常出现在高不确定任务，而不是所有任务。若简单修复的候选 A 已在 2 分钟内稳定完成，再开 8 个候选通常没有经济性。

## 20. 生产调度架构

**从本地脚本到生产系统的组件分离**

```text
Task graph / scheduler
        │
        ├─ baseline service: revision + clean-state gate
        ├─ worktree manager: create / lock / remove / repair
        ├─ resource allocator: CPU, GPU, ports, DB, secrets
        ├─ agent runners: one task per isolated workspace
        ├─ verifier workers: targeted + full + hidden gates
        ├─ candidate selector / integration manager
        ├─ reviewer workers: read-only snapshots
        └─ event ledger + artifact store + metrics
```

| 本章脚本 | 生产升级 | 目的 |
| --- | --- | --- |
| 本地 worktree 目录 | 短生命周期容器 + mounted worktree 或远程 runner | 进程和依赖隔离 |
| 固定两个候选 | 基于任务等级的自适应 fan-out | 避免无条件浪费计算 |
| 顺序 evaluate | 队列 + 并发 worker + 背压 | 受控并发 |
| JSON 报告 | 事务状态库 + append-only event log | 恢复、审计和统计 |
| 简单 diff 分数 | 版本化选择器 + 多级验证门 | 可比较、可回滚 |
| 手动清理 | lease/heartbeat + 垃圾回收器 | 处理崩溃和僵尸 worktree |

```text
并发上限必须由瓶颈决定
合理的 max_parallel_agents 取决于 verifier 吞吐、机器资源、API 限额和集成能力。调度器应测量队列长度、CPU/GPU 利用率、超时率和验证延迟，再动态调整，而不是固定“越多越好”。
```

## 21. 本章自测

**1.  **为什么 worktree 比在同一目录中让两个代理交替编辑更可靠？

**2.  **worktree 隔离了工作目录后，为什么测试数据库仍可能互相污染？

**3.  **候选竞争为什么通常只选一个胜者，而任务分解需要合并多个分支？

**4.  **为什么两个子任务各自的目标测试都通过，仍不能进入 DONE？

**5.  **为什么 verifier PASS 必须是候选选择的硬门，而 diff 大小只能是软指标？

**6.  **同一 branch 不能被两个 worktree 正常检出的限制，对调度器命名有什么要求？

**7.  **为什么 Reviewer 适合 detached worktree + read-only sandbox？

**8.  **如何区分并行加速和单纯增加总计算量？

### 参考结论

- 不同工作树拥有独立文件状态、index 和 HEAD，变更和证据可归因；同目录并发会产生覆盖和竞态。

- 数据库、端口、容器和云资源不属于 Git 工作树，需要额外命名空间与权限隔离。

- 候选是替代解，自动拼接会产生未验证的新解；分解任务是互补输出，必须集成。

- 局部 PASS 不能证明组合行为，必须在 integration worktree 获取新鲜全量证据。

- 正确性和策略合规不可由较小 diff 抵消；软指标只在硬门通过后排序。

- 每个并行代理必须有唯一 branch 和 worktree path，并绑定共同 base revision。

- 它固定审查快照、避免占用候选 branch，并阻止 reviewer 直接修改实现。

- 同时记录 wall-clock、总成本、并行效率、无效计算、冲突率和集成回归，而不是只看耗时。

## 22. 最终验收清单

- [ ] 能解释 linked worktree 的独立状态与共享状态。

- [ ] 能从干净主分支创建两个不同 branch 的 worktree。

- [ ] 能使用 git worktree list --porcelain 检查 revision、branch 和路径。

- [ ] 能运行 candidate_lab 初始 verifier 并复现常量向量除零。

- [ ] 能运行 run_candidates.py 并得到两个独立 commit 与 candidate_report.json。

- [ ] 能解释为什么候选 A 在本实验中胜出，以及该评分为什么不是普遍真理。

- [ ] 能用 --apply-winner cherry-pick 胜者，并在主工作树重新验证。

- [ ] 能运行 decomposition_lab，让两个 task 通过 ownership gate 和目标测试。

- [ ] 能在 integration worktree 合并两个 task branch 并运行全量 verifier。

- [ ] 能制造 ownership violation 并使系统在合并前停止。

- [ ] 能制造 merge conflict，并保存 MERGE_CONFLICT 证据而不是无限重试。

- [ ] 能说明 worktree 对端口、数据库、缓存和 GPU 不提供隔离。

- [ ] 能安全 remove、prune、lock/unlock，并知道何时使用 move/repair。

- [ ] 能用 speedup、parallel efficiency、冲突率和集成回归率评价并行是否值得。

```text
进入下一章前
保留两个实验的 candidate_report.json、decomposition_report.json、git worktree list --porcelain 输出和主分支最终验证结果。下一章将从“能并行运行”转向“如何系统识别反模式、奖励投机和失控闭环”。
```

## 附录 A：候选竞争命令速查

```powershell
# 基线检查
git status --porcelain
git rev-parse HEAD

# 创建
git worktree add ../wt-a -b loop/candidate-a HEAD
git worktree add ../wt-b -b loop/candidate-b HEAD

# 检查
git worktree list --porcelain
git -C ../wt-a status --short
git -C ../wt-a diff --numstat

# 选择后应用一个 commit
git cherry-pick <winner-commit>
python scripts/verify.py

# 清理
git worktree remove ../wt-a
git worktree remove ../wt-b
git branch -d loop/candidate-a
git branch -d loop/candidate-b
git worktree prune --dry-run
```

## 附录 B：run_candidates.py 完整代码

```python
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKTREE_PARENT = ROOT.parent / "candidate_worktrees"
ARTIFACTS = ROOT / "artifacts"


@dataclass
class CandidateResult:
    name: str
    branch: str
    path: str
    verifier_pass: bool
    policy_pass: bool
    changed_files: list[str]
    added_lines: int
    deleted_lines: int
    score: int
    commit: str | None


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def ensure_clean() -> None:
    status = git("status", "--porcelain").stdout.strip()
    if status:
        raise RuntimeError("main worktree must be clean before parallel dispatch")


def remove_existing(path: Path, branch: str) -> None:
    if path.exists():
        git("worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("branch", "-D", branch, check=False)


def diff_metrics(cwd: Path) -> tuple[list[str], int, int]:
    names = [line for line in git("diff", "--name-only", cwd=cwd).stdout.splitlines() if line]
    added = deleted = 0
    for line in git("diff", "--numstat", cwd=cwd).stdout.splitlines():
        a, d, _ = line.split("\t", 2)
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            deleted += int(d)
    return names, added, deleted


def evaluate(name: str, strategy: str, base: str) -> CandidateResult:
    branch = f"loop/candidate-{name}"
    path = WORKTREE_PARENT / name
    remove_existing(path, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-b", branch, str(path), base)

    run(
        [sys.executable, str(ROOT / "scripts" / "mock_candidate.py"),
         "--worktree", str(path), "--strategy", strategy],
        cwd=ROOT,
    )
    verify = run([sys.executable, "scripts/verify.py"], cwd=path, check=False)
    changed, added, deleted = diff_metrics(path)
    policy_pass = bool(changed) and all(p.startswith("src/") for p in changed)
    verifier_pass = verify.returncode == 0

    score = 0
    score += 1000 if verifier_pass else 0
    score += 500 if policy_pass else 0
    score -= len(changed) * 20
    score -= added + deleted

    commit = None
    if verifier_pass and policy_pass:
        git("add", "src", cwd=path)
        git("commit", "-m", f"candidate {name}: {strategy} fix", cwd=path)
        commit = git("rev-parse", "HEAD", cwd=path).stdout.strip()

    return CandidateResult(
        name=name,
        branch=branch,
        path=str(path),
        verifier_pass=verifier_pass,
        policy_pass=policy_pass,
        changed_files=changed,
        added_lines=added,
        deleted_lines=deleted,
        score=score,
        commit=commit,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-winner", action="store_true")
    args = parser.parse_args()

    ensure_clean()
    ARTIFACTS.mkdir(exist_ok=True)
    base = git("rev-parse", "HEAD").stdout.strip()
    results = [
        evaluate("a", "minimal", base),
        evaluate("b", "refactor", base),
    ]
    eligible = [r for r in results if r.verifier_pass and r.policy_pass and r.commit]
    winner = max(eligible, key=lambda r: r.score) if eligible else None
    report = {
        "base_revision": base,
        "results": [asdict(r) for r in results],
        "winner": asdict(winner) if winner else None,
        "selection_rule": "PASS gates first; then maximize score with smaller diff penalty",
    }
    (ARTIFACTS / "candidate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.apply_winner:
        if winner is None or winner.commit is None:
            print("NO WINNER TO APPLY", file=sys.stderr)
            return 2
        git("cherry-pick", winner.commit)
        print(f"APPLIED WINNER: {winner.name} {winner.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## 附录 C：run_decomposition.py 完整代码

```python
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WT_PARENT = ROOT.parent / "decomposition_worktrees"
ARTIFACTS = ROOT / "artifacts"
TASKS = {
    "metrics": {"branch": "loop/task-metrics", "owned": ["src/metrics.py"], "test": "test_metrics.py"},
    "report": {"branch": "loop/task-report", "owned": ["src/report.py"], "test": "test_report.py"},
}


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=check)


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def cleanup(path: Path, branch: str) -> None:
    if path.exists():
        git("worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("branch", "-D", branch, check=False)


def main() -> int:
    if git("status", "--porcelain").stdout.strip():
        raise RuntimeError("main worktree must be clean")
    ARTIFACTS.mkdir(exist_ok=True)
    base = git("rev-parse", "HEAD").stdout.strip()
    results: dict[str, dict[str, object]] = {}

    for task, spec in TASKS.items():
        path = WT_PARENT / task
        cleanup(path, str(spec["branch"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-b", str(spec["branch"]), str(path), base)
        run([sys.executable, str(ROOT / "scripts" / "mock_task.py"), "--worktree", str(path), "--task", task], cwd=ROOT)
        changed = [x for x in git("diff", "--name-only", cwd=path).stdout.splitlines() if x]
        ownership_ok = set(changed).issubset(set(spec["owned"])) and bool(changed)
        verify = run([sys.executable, "scripts/verify.py", "--pattern", str(spec["test"])], cwd=path, check=False)
        passed = verify.returncode == 0
        commit = None
        if passed and ownership_ok:
            git("add", *changed, cwd=path)
            git("commit", "-m", f"task {task}", cwd=path)
            commit = git("rev-parse", "HEAD", cwd=path).stdout.strip()
        results[task] = {"changed": changed, "ownership_ok": ownership_ok, "targeted_pass": passed, "commit": commit}

    integration_branch = "loop/integration"
    integration_path = WT_PARENT / "integration"
    cleanup(integration_path, integration_branch)
    git("worktree", "add", "-b", integration_branch, str(integration_path), base)

    merge_ok = True
    for task in ("metrics", "report"):
        branch = str(TASKS[task]["branch"])
        merge = git("merge", "--no-ff", "--no-edit", branch, cwd=integration_path, check=False)
        if merge.returncode != 0:
            merge_ok = False
            break

    full_verify = run([sys.executable, "scripts/verify.py"], cwd=integration_path, check=False) if merge_ok else None
    integration_pass = bool(full_verify and full_verify.returncode == 0)
    report = {
        "base_revision": base,
        "tasks": results,
        "integration": {
            "path": str(integration_path),
            "branch": integration_branch,
            "merge_ok": merge_ok,
            "full_verifier_pass": integration_pass,
            "head": git("rev-parse", "HEAD", cwd=integration_path).stdout.strip() if merge_ok else None,
        },
    }
    (ARTIFACTS / "decomposition_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if integration_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

## 附录 D：参考资料

- Git Project. git-worktree Documentation. https://git-scm.com/docs/git-worktree （访问日期：2026-07-20）

- OpenAI. Worktrees: Use Git worktrees in Codex to run chats in parallel. https://developers.openai.com/codex/environments/git-worktrees （访问日期：2026-07-20）

- OpenAI. Subagents: Run specialized agents in parallel and collect their results. https://developers.openai.com/codex/subagents （访问日期：2026-07-20）

- OpenAI. Codex CLI. https://developers.openai.com/codex/cli （访问日期：2026-07-20）

- OpenAI. Sandbox. https://developers.openai.com/codex/concepts/sandboxing （访问日期：2026-07-20）

- Loop Engineering：从零到可验证自治闭环，原教程第 8 节“并行代理与 Git Worktree”。

版本提醒：Git 和 Codex 的命令、参数与产品界面会演进。自动化脚本应固定并记录 git --version、codex --version、操作系统和环境指纹；升级后先在实验仓库回归测试，再进入生产任务。

---

[返回课程主页](../../README.md) · [← 上一章](./10-context-engineering.md) · [下一章 →](./12-failure-modes.md)
