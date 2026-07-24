# 第 03 章：构建确定性验证器与证据门

[返回课程主页](../../README.md) · [← 上一章](./02-python-project-and-git-baseline.md) · [下一章 →](./04-bounded-controller.md)

## 本章使用说明

第二章已经建立了一个真实 Python 仓库：测试能够稳定暴露常量向量的除零缺陷，Ruff 能够检查静态质量，Git 能够给出基线和差异。但此时仍然需要人分别运行命令、阅读输出并决定任务是否完成。本章要把这套人工判断封装成一个确定性 verifier。

> 本章纪律：不要把“pytest 通过”直接等同于“任务完成”，也不要让 verifier 遇到第一个失败就立刻退出。一个合格的证据门应收集全部强制检查，并明确区分 PASS、FAIL 与 ERROR。

### 学习目标

**• **能区分测试工具、检查项、证据门和系统终态，避免把四个层级混为一谈。

**• **能把自然语言验收条件写入 goal.md，并映射为固定命令和退出码。

**• **能编写 scripts/verify.py，串行运行 pytest 与 Ruff，收集 stdout、stderr、耗时和退出码。

**• **能解释 pytest 与 Ruff 不同退出码的含义，并把“验收不通过”和“验证器无法工作”分开。

**• **能生成 state/verify-latest.json 与 logs/verify-latest.log，核对证据对应的 Python 环境和 Git revision。

**• **能通过故意制造业务失败、lint 失败、无测试和旧证据，压力测试 verifier 的边界。

## 1. 从多个工具到一个证据门

第二章中你分别执行了 python -m pytest 和 python -m ruff check src tests。它们都是确定性工具，但它们还不是完整 verifier。完整 verifier 必须固定命令、聚合结果、保存证据，并给控制器返回唯一且稳定的状态信号。

| 层级 | 本章实例 | 回答的问题 |
| --- | --- | --- |
| 测试或静态工具 | pytest、Ruff | 某一类性质是否满足 |
| 检查项 Check | pytest check、ruff check | 该工具本次如何执行、结果是什么 |
| 验证器 Verifier | scripts/verify.py | 所有强制检查综合后，当前仓库是否满足验收契约 |
| 证据门 Gate | verdict + 退出码 | 控制器是否允许进入 DONE |
| 系统终态 | 后续 Controller 的 DONE / BLOCKED 等 | 下一步继续、停止还是升级 |

> 关键边界：verifier 只负责产生和聚合证据，不负责修改源码。只要验证器同时拥有“判题”和“改题”的权力，证据就失去独立性。

### 1.1 为什么不能只写一串 && 命令

**一种看似简洁但信息不足的写法**

```powershell
python -m pytest -q && python -m ruff check src tests
```

这条命令在 pytest 失败后不会继续运行 Ruff，因此你只能知道“至少有一项失败”，却不知道当前静态质量是否也有问题。它还没有统一 JSON 报告、耗时、环境指纹和 Git revision，控制器无法审计。

| 问题 | 简单命令链的表现 | 本章 verifier 的要求 |
| --- | --- | --- |
| 完整性 | 前项失败后后项不执行 | 所有强制检查均执行并记录 |
| 状态语义 | 只有整体 shell 退出码 | 明确 PASS / FAIL / ERROR |
| 可审计性 | 输出停留在终端滚屏 | 日志和 JSON 报告持久化 |
| 证据归属 | 不知道针对哪个代码版本 | 记录 commit SHA、工作区状态和环境 |
| 可组合性 | 控制器需要解析自由文本 | 固定退出码与结构化字段 |

### 1.2 本章最终产物

**目录 1　本章完成后的新增结构**

```text
statkit-lab\
├─ goal.md
├─ pyproject.toml
├─ src\statkit\...
├─ tests\test_normalize.py
├─ scripts\
│  └─ verify.py
├─ logs\                      # 运行后生成，已被 .gitignore 排除
│  └─ verify-latest.log
└─ state\                     # 运行后生成，已被 .gitignore 排除
   └─ verify-latest.json
```

本章结束时，源码中的常量向量缺陷仍然保留。因此最终 verifier 应稳定返回 FAIL：pytest 为 FAIL，Ruff 为 PASS。下一章的控制器将以这个结果作为第一轮任务证据。

## 2. 先写验收契约，再写验证脚本

验证器不是“把能想到的命令全跑一遍”。它是验收契约的可执行实现。先写清完成条件，再决定每个条件由什么工具验证；否则脚本会逐渐堆积命令，却无法说明这些命令与目标有什么关系。

| 验收条件 | 机械证据 | 强制性 | 失败后含义 |
| --- | --- | --- | --- |
| 全部行为测试通过 | python -m pytest -q 返回 0 | 强制 | 实现没有满足已写入测试的契约 |
| 静态规则通过 | python -m ruff check src tests scripts 返回 0 | 强制 | 代码存在选定规则检测到的问题 |
| 验证环境可识别 | pytest、ruff 模块可导入；Git 命令可运行 | 强制 | 无法形成可信证据，属于 ERROR |
| 证据可追踪 | 报告记录 Python、平台、revision、工作区指纹 | 强制 | 证据来源不明确或可能陈旧 |
| 不得改测试 | 后续受保护路径策略 | 本章暂不实现 | 属于 Policy，而不是普通测试失败 |

### 2.1 正向要求、禁止事项与证据对象

一个完整目标通常包含三类内容：正向要求说明必须发生什么；禁止事项说明绝不能通过什么方式达成；证据对象说明完成结论要绑定到什么代码与环境。本章先把禁止修改 tests 的规则写进 goal.md，但真正的 protected-path 检查将在后续策略章节实现。

> 不能混淆：“tests 不得修改”不是由 pytest 自己保证的。pytest 只执行当前磁盘上的测试；若代理把测试删掉，pytest 反而可能更容易通过。因此测试保护必须由控制器或策略层独立执行。

### 2.2 退出码必须先定义

| Verifier 退出码 | 终端 verdict | 语义 | 控制器未来动作 |
| --- | --- | --- | --- |
| 0 | PASS | 验证器正常运行，全部强制检查通过 | 可以继续进入审查门或 DONE |
| 1 | FAIL | 验证器正常运行，但至少一项验收检查失败 | 把失败证据交给实现代理 |
| 2 | ERROR | 验证器无法可靠完成验证，例如缺依赖、无 Git、pytest 内部错误 | 有限重试或升级，不应让代理盲修业务代码 |

FAIL 与 ERROR 的区分极其重要。测试断言失败意味着“任务尚未完成”；测试框架自身崩溃或项目中根本没有测试，意味着“我们不知道任务是否完成”。把两者都当作普通失败，会让代理在错误环境中反复改代码。

## 3. 从第二章仓库建立安全起点

### 3.1 回到 statkit-lab 并检查工作区

**操作 1　确认第二章基线**

```powershell
cd $HOME\Desktop\loop-engineering-training\chapter02\statkit-lab
.\.venv\Scripts\Activate.ps1

git status --short
git log --oneline --decorate -3
python -m pytest -q
python -m ruff check src tests
```

预期状态：git status --short 无输出；pytest 显示 1 failed、3 passed；Ruff 显示 All checks passed。若与你的结果不同，不要直接继续。先恢复第二章基线，否则本章证据无法和教程对齐。

> 恢复原则：优先使用 git status、git diff 和 git restore 判断并撤销你自己造成的修改。不要在不理解差异时直接运行 git reset --hard；它会无条件丢弃未提交工作。

### 3.2 创建本章分支

**操作 2　隔离本章改动**

```powershell
git switch -c chapter03-verifier
git branch --show-current
```

分支不是本章 verifier 的必要运行条件，但它把“第二章已知缺陷基线”和“第三章新增验证基础设施”分开。若命令提示分支已经存在，可使用 git switch chapter03-verifier。

### 3.3 再次确认解释器

**操作 3　验证依赖来自当前虚拟环境**

```powershell
python -c "import sys; print(sys.executable)"
python -c "import pytest, ruff; print(pytest.__version__); print(ruff.__version__)"
```

sys.executable 应指向本项目 .venv。若 pytest 或 ruff 无法导入，执行 python -m pip install -e ".[dev]"。Verifier 以后也会主动检查这些模块是否存在；缺失时应返回 ERROR，而不是伪装成业务测试失败。

## 4. 创建 goal.md 与 verifier 目录

### 4.1 创建文件

**操作 4　创建目标与验证脚本**

```powershell
mkdir scripts -ErrorAction SilentlyContinue
New-Item goal.md -ItemType File
New-Item scripts\verify.py -ItemType File
Get-ChildItem scripts
```

### 4.2 编写 goal.md

**文件 1　`goal.md**`

```markdown
# Goal: repair constant-vector normalization

## Required behavior

- `min_max_normalize([])` returns `[]`.
- Non-constant vectors are mapped into `[0.0, 1.0]` in the original order.
- Constant vectors return a zero vector of the same length.
- The public function name and signature remain unchanged.

## Acceptance gate

- `python scripts/verify.py` exits with code `0`.
- The verifier report has `"verdict": "PASS"`.
- Both pytest and Ruff checks have status `PASS`.

## Prohibited changes

- Do not modify files under `tests/`.
- Do not weaken Ruff or pytest configuration.
- Do not add runtime dependencies.
- Keep the implementation change minimal.
```

注意，goal.md 中的“python scripts/verify.py 返回 0”是最终机器门；具体业务条件仍然保留，防止代理只围绕一条命令做表面优化。禁止事项目前只是规范，后续还要由受保护路径策略机械执行。

## 5. 手把手编写 scripts/verify.py

下面不是让你一次性复制一个黑箱脚本，而是按职责拆成六个部分。每增加一部分，都明确它在闭环中解决什么风险。完整文件收录在附录 B。

### 5.1 导入、路径与结果结构

**代码块 1　稳定路径和结构化检查结果**

```python
from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
LOG_PATH = LOG_DIR / "verify-latest.log"
REPORT_PATH = STATE_DIR / "verify-latest.json"

CheckStatus = Literal["PASS", "FAIL", "ERROR"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: list[str]
    status: CheckStatus
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
```

ROOT 由脚本自身位置计算，不依赖你从哪个目录启动。CheckResult 把每个检查的命令、状态、退出码、耗时和输出固定下来。以后控制器不需要从一大段自由文本中猜测哪些字段存在。

### 5.2 时间、进程与退出码分类

**代码块 2　进程边界与工具特定分类**

```python
def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def run_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def classify_pytest(exit_code: int) -> CheckStatus:
    if exit_code == 0:
        return "PASS"
    if exit_code == 1:
        return "FAIL"
    return "ERROR"


def classify_ruff(exit_code: int) -> CheckStatus:
    if exit_code == 0:
        return "PASS"
    if exit_code == 1:
        return "FAIL"
    return "ERROR"
```

| 工具 | 退出码 0 | 退出码 1 | 其他退出码 |
| --- | --- | --- | --- |
| pytest | 测试全部通过 | 测试收集成功但存在失败 | 中断、内部错误、用法错误或无测试，应视为 ERROR |
| Ruff | 规则检查通过 | 发现 lint 违规 | 配置、命令或运行错误，应视为 ERROR |

不能用统一规则“非零就是 FAIL”。每个工具的退出码协议不同。Verifier 必须理解工具协议，再将其归一化为自己的 PASS、FAIL、ERROR。

### 5.3 执行单个检查

**代码块 3　把命令执行转成结构化证据**

```python
def run_check(
    name: str,
    command: list[str],
    classifier: Callable[[int], CheckStatus],
) -> CheckResult:
    started = time.perf_counter()
    completed = run_process(command)
    duration = time.perf_counter() - started

    return CheckResult(
        name=name,
        command=command,
        status=classifier(completed.returncode),
        exit_code=completed.returncode,
        duration_seconds=round(duration, 3),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
```

capture_output=True 让 verifier 可以把原始输出写入日志；check=False 防止 subprocess 在非零退出码时直接抛异常。非零退出码是被验证对象的正常信息，不应由 Python 异常机制吞掉。

### 5.4 绑定 Git revision 与工作区指纹

**代码块 4　让证据指向具体仓库状态**

```python
def read_git(command: list[str]) -> str:
    completed = run_process(["git", *command])
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.strip()


def workspace_evidence() -> dict[str, object]:
    revision = read_git(["rev-parse", "HEAD"])
    status = read_git(["status", "--porcelain=v1", "--untracked-files=all"])
    diff = read_git(["diff", "--binary", "HEAD"])
    fingerprint_input = "\n".join([revision, status, diff])

    return {
        "revision": revision,
        "workspace_clean": not bool(status),
        "status_porcelain": status.splitlines(),
        "workspace_fingerprint": hashlib.sha256(
            fingerprint_input.encode("utf-8")
        ).hexdigest(),
    }
```

commit SHA 只描述 HEAD，不能描述尚未提交的工作区。workspace_fingerprint 把 HEAD、status 和 tracked diff 合并后取哈希，用于标识“这次验证看到的仓库状态”。它不是密码学意义上的完整制品系统，但足以训练证据新鲜度意识。

> 限制说明：当前指纹只把未追踪文件的路径写入 status，没有把所有未追踪文件内容逐一哈希。生产系统应使用完整 artifact manifest、容器镜像摘要或内容寻址存储。

### 5.5 环境、日志与总体状态

**代码块 5　环境证据与门控优先级**

```python
def environment_evidence() -> dict[str, str]:
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def missing_modules() -> list[str]:
    required = ["pytest", "ruff"]
    return [name for name in required if importlib.util.find_spec(name) is None]


def overall_status(checks: list[CheckResult]) -> CheckStatus:
    if any(check.status == "ERROR" for check in checks):
        return "ERROR"
    if any(check.status == "FAIL" for check in checks):
        return "FAIL"
    return "PASS"
```

总体状态使用 ERROR > FAIL > PASS 的优先级。只要任何检查无法可靠执行，系统就不应把结果降格成普通 FAIL；因为此时连失败集合是否完整都不确定。

### 5.6 主流程：全部检查、写证据、返回单一退出码

**代码块 6　主流程骨架**

```python
def main() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    STATE_DIR.mkdir(exist_ok=True)
    started_at = utc_now()

    missing = missing_modules()
    if missing:
        message = f"Missing verifier modules: {', '.join(missing)}"
        write_report(
            status="ERROR",
            started_at=started_at,
            checks=[],
            workspace=None,
            error=message,
        )
        print("VERDICT: ERROR")
        print(f"REASON: {message}")
        return 2

    try:
        workspace = workspace_evidence()
    except RuntimeError as exc:
        write_report(
            status="ERROR",
            started_at=started_at,
            checks=[],
            workspace=None,
            error=str(exc),
        )
        print("VERDICT: ERROR")
        print(f"REASON: {exc}")
        return 2

    checks = [
        run_check(
            "pytest",
            [sys.executable, "-m", "pytest", "-q"],
            classify_pytest,
        ),
        run_check(
            "ruff",
            [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
            classify_ruff,
        ),
    ]
    verdict = overall_status(checks)
    write_report(
        status=verdict,
        started_at=started_at,
        checks=checks,
        workspace=workspace,
    )

    for check in checks:
        print(
            f"CHECK {check.name}: {check.status} "
            f"(exit={check.exit_code}, {check.duration_seconds:.3f}s)"
        )
    print(f"REPORT: {REPORT_PATH.relative_to(ROOT)}")
    print(f"VERDICT: {verdict}")

    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
```

write_report 与 combine_logs 的完整实现放在附录 B。此处最重要的不是语法，而是控制顺序：先检查 verifier 自身前提，再绑定工作区，随后运行所有强制检查，最后统一写证据并返回退出码。

## 6. 第一次运行：测试失败但 Ruff 通过

### 6.1 保存完整 verify.py 后运行

**操作 5　运行统一验证器**

```powershell
python scripts\verify.py
$LASTEXITCODE
```

在第二章的故意缺陷仍然存在时，预期终端摘要类似：

**预期输出（耗时会因机器而异）**

```text
CHECK pytest: FAIL (exit=1, 0.2xxs)
CHECK ruff: PASS (exit=0, 0.0xxs)
REPORT: state\verify-latest.json
VERDICT: FAIL

1
```

> 观察重点：Verifier 没有因为 pytest 失败而跳过 Ruff；最终退出码为 1，表示证据形成成功，但验收条件未满足。

### 6.2 为什么此时不是 ERROR

| 事实 | 证据 | 结论 |
| --- | --- | --- |
| pytest 能启动并发现 4 个测试 | 退出码 1，日志包含具体失败 | 验证机制正常，只是实现不满足契约 |
| Ruff 正常完成 | 退出码 0 | 静态质量门通过 |
| Git 与环境信息可读取 | 报告中有 revision、解释器路径 | 证据可以归属到当前环境 |
| 综合门未满足 | 至少一个 CheckStatus 为 FAIL | 总体 verdict = FAIL |

### 6.3 检查生成文件

**操作 6　检查证据工件**

```powershell
Get-Content state\verify-latest.json
Get-Content logs\verify-latest.log
git status --short
```

state/ 和 logs/ 已在第二章 .gitignore 中排除，因此运行 verifier 后 git status 不应因为报告文件而变脏。若它们出现在状态列表中，检查 .gitignore 是否仍包含 logs/ 与 state/。

## 7. 读取 JSON 证据，而不是只看终端

终端摘要适合人快速观察；JSON 报告才是后续控制器应读取的机器接口。报告至少包含总体 verdict、环境、工作区、每个检查的原始结果和日志哈希。

**报告结构节选**

```text
{
  "verdict": "FAIL",
  "environment": {
    "python_executable": "...\.venv\Scripts\python.exe",
    "python_version": "3.11.x",
    "platform": "Windows-..."
  },
  "workspace": {
    "revision": "<40-character commit SHA>",
    "workspace_clean": false,
    "status_porcelain": [
      "?? goal.md",
      "?? scripts/verify.py"
    ],
    "workspace_fingerprint": "<sha256>"
  },
  "checks": [
    {"name": "pytest", "status": "FAIL", "exit_code": 1},
    {"name": "ruff", "status": "PASS", "exit_code": 0}
  ],
  "log_sha256": "<sha256>",
  "error": null
}
```

### 7.1 用 PowerShell 提取关键字段

**操作 7　结构化读取证据**

```powershell
$report = Get-Content state\verify-latest.json -Raw | ConvertFrom-Json
$report.verdict
$report.environment.python_executable
$report.workspace.revision
$report.workspace.workspace_fingerprint
$report.checks | Format-Table name,status,exit_code,duration_seconds
```

不要依赖日志中的行号或某个固定英文句子。结构化字段是控制器与 verifier 之间的协议；日志则用于人类诊断和事后审计。两者用途不同。

### 7.2 为什么同时保存原始日志和摘要 JSON

| 工件 | 优势 | 局限 | 主要消费者 |
| --- | --- | --- | --- |
| JSON 报告 | 字段稳定，易于机器判断和统计 | 不适合容纳全部长输出 | Controller、评估脚本、仪表盘 |
| 原始日志 | 保留 traceback、lint 位置和命令输出 | 文本格式可能随工具版本变化 | 开发者、审查者、故障分析 |
| 终端摘要 | 即时、简洁 | 容易滚屏丢失，不适合作为唯一证据 | 当前操作者 |

### 7.3 证据必须新鲜

假设你先临时修复缺陷并得到 PASS，然后再次修改源码却没有重跑 verifier。state/verify-latest.json 仍然写着 PASS，但它描述的是旧工作区。任何把这个旧报告直接当成当前事实的系统都会产生 false-DONE。

> 新鲜证据规则：控制器在决定 DONE 前，应重新运行 verifier，或者至少确认报告中的 revision 与 workspace_fingerprint 和当前工作区一致。仅凭磁盘上存在一个 PASS 文件，不构成完成。

## 8. PASS、FAIL、ERROR 的状态语义

| 场景 | pytest | Ruff | 总体 verdict | 为什么 |
| --- | --- | --- | --- | --- |
| 已知业务缺陷 | FAIL / 1 | PASS / 0 | FAIL / 1 | 验证可正常执行，目标未满足 |
| 只存在 lint 违规 | PASS / 0 | FAIL / 1 | FAIL / 1 | 静态门未满足 |
| 测试与 lint 均通过 | PASS / 0 | PASS / 0 | PASS / 0 | 全部强制证据门通过 |
| tests 目录不存在 | ERROR / 5 | ERROR / 2 | ERROR / 2 | 无法形成完整测试证据 |
| pytest 未安装 | 未执行 | 未执行 | ERROR / 2 | 验证器自身前提缺失 |
| 不在 Git 仓库 | 未执行 | 未执行 | ERROR / 2 | 证据无法绑定到 revision |

### 8.1 为什么 ERROR 优先于 FAIL

如果 pytest 因用法错误退出，而 Ruff 又发现一个 lint 违规，综合状态不能写成普通 FAIL。你确实看到了一个 lint 问题，但你不知道测试门是否完成。ERROR 优先是保守的 fail-closed 设计：证据不完整时不允许进入成功路径，也不假设代理应当修改什么。

### 8.2 Verifier 自己也可能有 bug

确定性并不意味着永远正确。Verifier 可能命令写错、漏跑检查、错误解释退出码，甚至读取旧目录。因此验证器本身也要版本控制、代码审查和测试。后续生产化章节会把 verifier 视为高信任计算基的一部分，而不是普通辅助脚本。

## 9. 五个破坏与恢复实验

只跑 happy path 不能证明你理解 verifier。下面故意制造不同故障，要求你在每次实验后核对终端退出码、JSON verdict、每个检查状态和 Git diff。

### 实验一：临时修复业务缺陷，观察 PASS

在 src/statkit/normalize.py 中，计算 span 后加入：

```text
if span == 0:
    return [0.0 for _ in values]
```

**操作 8　验证修复**

```powershell
python scripts\verify.py
$LASTEXITCODE
(Get-Content state\verify-latest.json -Raw | ConvertFrom-Json).verdict
```

预期 pytest 和 Ruff 都为 PASS，总体退出码 0。随后恢复缺陷，确保下一章仍有待修任务：

**恢复并确认重新回到 `FAIL**`

```powershell
git restore src\statkit\normalize.py
python scripts\verify.py
$LASTEXITCODE
```

> 实验结论：同一个 verifier 能对仓库状态变化给出不同 verdict。完成不是脚本中的固定文字，而是当前证据的函数。

### 实验二：制造 lint 失败

在 src/statkit/normalize.py 顶部临时加入一个未使用导入，例如 import os。

**操作 9　观察多个门的独立状态**

```powershell
python scripts\verify.py
$report = Get-Content state\verify-latest.json -Raw | ConvertFrom-Json
$report.checks | Format-Table name,status,exit_code

git restore src\statkit\normalize.py
```

由于原始业务缺陷仍在，pytest 为 FAIL；新增未使用导入使 Ruff 也为 FAIL。总体仍是 FAIL，但 JSON 报告能够显示两个独立原因。

### 实验三：让测试集合消失，观察 ERROR

**操作 10　制造验证环境错误**

```powershell
Rename-Item tests tests_backup
python scripts\verify.py
$LASTEXITCODE
Rename-Item tests_backup tests
```

pytest 在没有测试时通常返回 5，Ruff 对不存在的 tests 路径也可能返回 2。两者都应被分类为 ERROR，总体退出码 2。恢复目录后再次运行，确认回到 FAIL。

> 不要误判：“没有失败测试”不等于“测试通过”。没有收集到测试时，系统失去了验证能力，必须是 ERROR。

### 实验四：观察旧 PASS 证据如何变陈旧

先再次临时修复缺陷并运行 verifier 得到 PASS，不要立即恢复；记录报告中的 workspace_fingerprint。随后向 normalize.py 末尾加入一个空行或注释，但不要重跑 verifier。

**操作 11　制造“报告 PASS、当前工作区已变化”的状态**

```powershell
$old = Get-Content state\verify-latest.json -Raw | ConvertFrom-Json
$old.verdict
$old.workspace.workspace_fingerprint

git status --short
git diff -- src\statkit\normalize.py
```

此时报告仍显示 PASS，但 git diff 已表明工作区不是报告生成时的状态。结论不是“报告撒谎”，而是“报告已经过期”。完成判断必须绑定时间和工作区。实验后执行 git restore src/statkit/normalize.py。

### 实验五：人为打印 PASS，证明文字无权改变退出码

**操作 12　区分自由文本和机械返回值**

```powershell
Write-Output "VERDICT: PASS"
$LASTEXITCODE
python scripts\verify.py
$LASTEXITCODE
```

PowerShell 的 Write-Output 可以轻易打印任何文字；代理也可以声称所有问题已修复。控制器只能信任它实际调用的 verifier 进程退出码和对应报告，而不是聊天文本或任意终端输出。

### 9.1 实验记录表

| 实验 | 预期总体状态 | 关键检查状态 | 恢复动作 |
| --- | --- | --- | --- |
| 临时修复缺陷 | PASS / 0 | pytest PASS；Ruff PASS | git restore normalize.py |
| 加入未使用 import | FAIL / 1 | pytest FAIL；Ruff FAIL | git restore normalize.py |
| 重命名 tests | ERROR / 2 | pytest / Ruff 至少一项 ERROR | 把 tests_backup 改回 tests |
| PASS 后再次编辑 | 旧报告仍为 PASS，但已陈旧 | Git diff 与报告指纹不再对应 | restore 后重新运行 verifier |
| 手工打印 PASS | 不影响 verifier | 自由文本不是证据门 | 无需恢复 |

## 10. 提交 verifier 并保留已知缺陷

### 10.1 确认业务源码已恢复

**操作 13　回到已知缺陷状态**

```powershell
git status --short
git diff -- src\statkit\normalize.py
python -m pytest -q
```

normalize.py 不应有未提交差异；pytest 应再次稳定显示 1 failed、3 passed。若仍然 PASS，说明你忘记撤销临时修复。

### 10.2 检查 verifier 自身静态质量

**操作 14　验证基础设施本身**

```powershell
python -m ruff check scripts\verify.py
python scripts\verify.py
$LASTEXITCODE
```

第一条应通过；第二条应返回 1，因为业务缺陷仍存在。这是本章正确终态，不要为了让课程“全绿”而再次修复源码。

### 10.3 提交新增文件

**操作 15　提交验证基础设施**

```powershell
git status --short
git add goal.md scripts\verify.py
git diff --cached --stat
git commit -m "chapter03: add deterministic verifier"
git status --short
git log --oneline --decorate -3
```

提交后工作区应干净。再次运行 verifier 会生成被忽略的 logs/ 和 state/，不会污染 Git。此时报告中的 workspace_clean 应为 true，revision 应等于刚刚创建的 commit。

**操作 16　核对证据与 `HEAD**`

```powershell
python scripts\verify.py
$report = Get-Content state\verify-latest.json -Raw | ConvertFrom-Json
$report.verdict
$report.workspace.workspace_clean
$report.workspace.revision
git rev-parse HEAD
```

> 本章完成状态：Git 工作区干净；goal.md 与 verify.py 已提交；verify.py 自身通过 Ruff；统一 verifier 对已知业务缺陷返回 FAIL / 1，并生成可追踪的 JSON 与日志。

## 11. 常见错误、诊断路径与验收清单

### 11.1 常见错误

| 现象 | 最可能原因 | 诊断命令 | 修正方向 |
| --- | --- | --- | --- |
| ModuleNotFoundError: pytest/ruff | 虚拟环境未激活或开发依赖未安装 | python -c "import sys; print(sys.executable)" | 激活 .venv；pip install -e ".[dev]" |
| Git command failed | 不在仓库内或 .git 缺失 | git rev-parse --show-toplevel | 回到 statkit-lab 根目录 |
| Ruff 报 scripts/verify.py 自身错误 | 复制代码时缩进、导入顺序或引号有误 | python -m ruff check scripts/verify.py | 按附录完整核对，不要随意删规则 |
| pytest 为 ERROR / 5 | tests 目录不存在或测试未被发现 | python -m pytest --collect-only -q | 恢复 tests 和 pyproject 配置 |
| JSON 无法解析 | 脚本中途崩溃或文件被手工破坏 | Get-Content state\\verify-latest.json | 先修 verifier，本轮不能作为业务 FAIL |
| 报告 revision 与 HEAD 不同 | 报告来自旧运行或已切换分支 | git rev-parse HEAD | 重新运行 verifier 获取新鲜证据 |
| workspace_clean 始终为 false | goal.md/verify.py 尚未提交或存在其他修改 | git status --short | 理解并提交/恢复差异 |

### 11.2 推荐诊断顺序

**• **先确认当前位置：git rev-parse --show-toplevel。

**• **再确认解释器：python -c "import sys; print(sys.executable)"。

**• **单独运行底层工具：python -m pytest -q 与 python -m ruff check src tests scripts。

**• **检查 verifier 退出码：$LASTEXITCODE。

**• **读取 JSON 中的 verdict、checks 和 error 字段。

**• **最后查看完整日志，不要一开始就只盯 traceback 最后一行。

### 11.3 本章自测

**问题一**

pytest 返回 1 和 pytest 返回 5，为什么不能都映射成 FAIL？

> 参考结论：退出码 1 表示测试正常执行并发现断言失败；退出码 5 表示没有收集到测试，验证能力缺失，应映射为 ERROR。

**问题二**

为什么 verifier 要继续运行 Ruff，而不是 pytest 一失败就停止？

> 参考结论：为了形成完整失败集合，避免下一轮只修一个问题后才暴露另一个问题，并支持成本与质量分析。

**问题三**

JSON 报告显示 PASS，为什么仍不能自动认定当前仓库完成？

> 参考结论：报告可能来自旧工作区。必须核对 revision、workspace_fingerprint，或在终态前重新运行 verifier。

**问题四**

为什么 tests 不得修改不能只写在 goal.md？

> 参考结论：文字约束可以被忽略或误解；必须由 Git diff 和受保护路径策略机械执行，且策略失败应 fail closed。

**问题五**

Verifier 和 Controller 的职责有什么不同？

> 参考结论：Verifier 产生当前仓库的机械证据；Controller 根据证据、预算、策略和状态决定继续、停止或升级。

### 11.4 本章通过标准

**• **□ 能从干净的第二章仓库创建 chapter03-verifier 分支。

**• **□ 能解释 goal.md 中业务契约、验收门和禁止事项的差异。

**• **□ 能运行 python scripts/verify.py，并得到预期 FAIL / 1。

**• **□ 能在 JSON 中找到 Python 解释器、revision、workspace_fingerprint 和两个 checks。

**• **□ 能临时修复缺陷得到 PASS，并恢复到已知 FAIL 基线。

**• **□ 能制造 lint 失败并解释为什么总体仍为 FAIL。

**• **□ 能让 tests 消失并观察到 ERROR / 2，而不是错误的 PASS。

**• **□ 能解释旧 PASS 报告为什么不代表当前工作区。

**• **□ 能提交 goal.md 和 scripts/verify.py，同时保持业务缺陷未修复。

> 真正掌握的标志：你不只是会运行 pytest，而是能定义一个稳定的证据协议，并预测仓库在业务失败、验证环境错误和证据陈旧时分别应产生什么状态。

## 附录 A. 完整 goal.md

```markdown
# Goal: repair constant-vector normalization

## Required behavior

- `min_max_normalize([])` returns `[]`.
- Non-constant vectors are mapped into `[0.0, 1.0]` in the original order.
- Constant vectors return a zero vector of the same length.
- The public function name and signature remain unchanged.

## Acceptance gate

- `python scripts/verify.py` exits with code `0`.
- The verifier report has `"verdict": "PASS"`.
- Both pytest and Ruff checks have status `PASS`.

## Prohibited changes

- Do not modify files under `tests/`.
- Do not weaken Ruff or pytest configuration.
- Do not add runtime dependencies.
- Keep the implementation change minimal.
```

## 附录 B. 完整 scripts/verify.py

```python
from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
LOG_PATH = LOG_DIR / "verify-latest.log"
REPORT_PATH = STATE_DIR / "verify-latest.json"

CheckStatus = Literal["PASS", "FAIL", "ERROR"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: list[str]
    status: CheckStatus
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def run_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def classify_pytest(exit_code: int) -> CheckStatus:
    if exit_code == 0:
        return "PASS"
    if exit_code == 1:
        return "FAIL"
    return "ERROR"


def classify_ruff(exit_code: int) -> CheckStatus:
    if exit_code == 0:
        return "PASS"
    if exit_code == 1:
        return "FAIL"
    return "ERROR"


def run_check(
    name: str,
    command: list[str],
    classifier: Callable[[int], CheckStatus],
) -> CheckResult:
    started = time.perf_counter()
    completed = run_process(command)
    duration = time.perf_counter() - started

    return CheckResult(
        name=name,
        command=command,
        status=classifier(completed.returncode),
        exit_code=completed.returncode,
        duration_seconds=round(duration, 3),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def read_git(command: list[str]) -> str:
    completed = run_process(["git", *command])
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.strip()


def workspace_evidence() -> dict[str, object]:
    revision = read_git(["rev-parse", "HEAD"])
    status = read_git(["status", "--porcelain=v1", "--untracked-files=all"])
    diff = read_git(["diff", "--binary", "HEAD"])
    fingerprint_input = "\n".join([revision, status, diff])

    return {
        "revision": revision,
        "workspace_clean": not bool(status),
        "status_porcelain": status.splitlines(),
        "workspace_fingerprint": hashlib.sha256(
            fingerprint_input.encode("utf-8")
        ).hexdigest(),
    }


def environment_evidence() -> dict[str, str]:
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def missing_modules() -> list[str]:
    required = ["pytest", "ruff"]
    return [name for name in required if importlib.util.find_spec(name) is None]


def combine_logs(checks: list[CheckResult]) -> str:
    sections: list[str] = []
    for check in checks:
        sections.extend(
            [
                "=" * 78,
                f"CHECK: {check.name}",
                f"COMMAND: {' '.join(check.command)}",
                f"STATUS: {check.status}",
                f"EXIT_CODE: {check.exit_code}",
                f"DURATION_SECONDS: {check.duration_seconds}",
                "--- STDOUT ---",
                check.stdout.rstrip(),
                "--- STDERR ---",
                check.stderr.rstrip(),
            ]
        )
    return "\n".join(sections).rstrip() + "\n"


def overall_status(checks: list[CheckResult]) -> CheckStatus:
    if any(check.status == "ERROR" for check in checks):
        return "ERROR"
    if any(check.status == "FAIL" for check in checks):
        return "FAIL"
    return "PASS"


def write_report(
    *,
    status: CheckStatus,
    started_at: str,
    checks: list[CheckResult],
    workspace: dict[str, object] | None,
    error: str | None = None,
) -> None:
    finished_at = utc_now()
    log_text = combine_logs(checks) if checks else ""
    LOG_PATH.write_text(log_text, encoding="utf-8")

    report = {
        "verdict": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "environment": environment_evidence(),
        "workspace": workspace,
        "checks": [asdict(check) for check in checks],
        "log_path": str(LOG_PATH.relative_to(ROOT)),
        "log_sha256": hashlib.sha256(log_text.encode("utf-8")).hexdigest(),
        "error": error,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    STATE_DIR.mkdir(exist_ok=True)
    started_at = utc_now()

    missing = missing_modules()
    if missing:
        message = f"Missing verifier modules: {', '.join(missing)}"
        write_report(
            status="ERROR",
            started_at=started_at,
            checks=[],
            workspace=None,
            error=message,
        )
        print("VERDICT: ERROR")
        print(f"REASON: {message}")
        return 2

    try:
        workspace = workspace_evidence()
    except RuntimeError as exc:
        write_report(
            status="ERROR",
            started_at=started_at,
            checks=[],
            workspace=None,
            error=str(exc),
        )
        print("VERDICT: ERROR")
        print(f"REASON: {exc}")
        return 2

    checks = [
        run_check(
            "pytest",
            [sys.executable, "-m", "pytest", "-q"],
            classify_pytest,
        ),
        run_check(
            "ruff",
            [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
            classify_ruff,
        ),
    ]
    verdict = overall_status(checks)
    write_report(
        status=verdict,
        started_at=started_at,
        checks=checks,
        workspace=workspace,
    )

    for check in checks:
        print(
            f"CHECK {check.name}: {check.status} "
            f"(exit={check.exit_code}, {check.duration_seconds:.3f}s)"
        )
    print(f"REPORT: {REPORT_PATH.relative_to(ROOT)}")
    print(f"VERDICT: {verdict}")

    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
```

## 附录 C. PowerShell 命令速查

| 目的 | 命令 |
| --- | --- |
| 进入仓库 | cd $HOME\\Desktop\\loop-engineering-training\\chapter02\\statkit-lab |
| 激活环境 | .\\.venv\\Scripts\\Activate.ps1 |
| 运行统一验证器 | python scripts\\verify.py |
| 查看退出码 | $LASTEXITCODE |
| 查看 JSON | Get-Content state\\verify-latest.json |
| 解析 JSON | $r = Get-Content state\\verify-latest.json -Raw \| ConvertFrom-Json |
| 查看 checks | $r.checks \| Format-Table name,status,exit_code,duration_seconds |
| 查看日志 | Get-Content logs\\verify-latest.log |
| 检查当前差异 | git status --short; git diff |
| 恢复源码 | git restore src\\statkit\\normalize.py |
| 检查 verifier | python -m ruff check scripts\\verify.py |
| 核对 revision | git rev-parse HEAD |

---

[返回课程主页](../../README.md) · [← 上一章](./02-python-project-and-git-baseline.md) · [下一章 →](./04-bounded-controller.md)
