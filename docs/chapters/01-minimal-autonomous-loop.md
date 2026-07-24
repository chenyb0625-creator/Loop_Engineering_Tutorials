# 第 01 章：不接入 AI，先跑通最小自治闭环

[返回课程主页](../../README.md) · [下一章 →](./02-python-project-and-git-baseline.md)

## 本章使用说明

**本教程按“每章一份独立 Markdown、每章一个可运行产物”的方式组织。** 本章暂时不调用大模型，而是用一个故意不可靠的 worker 模拟代理，以便把控制器、验证器、终态和预算的职责拆开观察。

> 本章硬性要求：不要只阅读代码。必须逐条执行命令、观察退出码、制造失败，并核对 run_state.json 和 loop.log。只跑通 happy path 不算掌握。

| 完成本章后，你应能够 | 可验证产物 |
| --- | --- |
| 区分代理自我声明与系统真实终态 | 亲眼观察 AGENT_CLAIM: DONE 与 VERDICT: FAIL 同时出现 |
| 编写独立的确定性验证器 | verify.py 返回 0 / 1 / 2 三类退出码 |
| 编写有预算的外层控制器 | controller.py 能输出 DONE 或 BUDGET_EXHAUSTED |
| 区分不同失败终态 | 能制造并解释 AGENT_ERROR、VERIFIER_ERROR |
| 保留可审计证据 | 生成 state.json、run_state.json、loop.log |

## 1. 为什么第一章故意不用大模型

一开始就接入 Codex，最容易形成的错误直觉是：模型输出了“已完成”，所以任务已经完成。

**Worker 的声明 ≠ 系统状态**

**Verifier 的证据 → Controller 的状态判断**

本章用一个极其简单的状态任务训练这个边界：当前 value 为 0，目标是让它达到 3。worker 每轮只把 value 增加 1，但它无论做了多少，都故意打印“AGENT_CLAIM: DONE”。控制器不能相信它，只能相信验证器。

> 关键思想：Loop Engineering 的核心并不是“循环调用模型”，而是把验证权、权限边界和终止权从模型手中拿回到外部控制器。

### 1.1 三个角色的职责边界

| 角色 | 本章实现 | 可以做什么 | 不能决定什么 |
| --- | --- | --- | --- |
| Worker | worker.py | 读取状态并实施候选动作 | 不能决定系统是否 DONE |
| Verifier | verify.py | 依据明确规则检查目标是否满足 | 不负责修复或规划下一步 |
| Controller | controller.py | 调度、记录证据、执行预算和终态策略 | 不能把代理语言当作完成证据 |

### 1.2 为什么这个玩具任务有工程价值

真实编码任务中的“测试是否通过”“受保护路径是否被修改”“是否达到预算上限”，与本章的 value、target 和退出码在控制结构上完全同构。先在最小环境里理解控制权，再替换 Action 模块为 Codex，复杂度会低得多。

## 2. 最小闭环的系统结构

**图 1　第一章最小闭环**

```text
┌─────────────────┐
                 │   Controller    │
                 │   外部控制器     │
                 └────────┬────────┘
                          │
                    运行 verify.py
                          │
              ┌───────────┴───────────┐
              │                       │
            PASS                    FAIL
              │                       │
           DONE                  运行 worker.py
                                      │
                                修改 state.json
                                      │
                               回到下一轮验证
```

| 文件 | 职责 | 本章完成后应看到 |
| --- | --- | --- |
| state.json | 保存任务当前状态 | value、target |
| worker.py | 模拟代理动作 | 每轮 value + 1，并错误声明 DONE |
| verify.py | 独立验证目标 | PASS / FAIL / ERROR 与退出码 |
| controller.py | 外层调度和终态控制 | DONE / BUDGET_EXHAUSTED / AGENT_ERROR |
| run_state.json | 保存机器可读终态 | status、iterations_used、最后验证码 |
| loop.log | 保存每轮命令证据 | 时间、命令输出、退出码 |

### 2.1 本章的终态集合

| 终态 | 触发条件 | 含义 |
| --- | --- | --- |
| DONE | Verifier 返回 0 | 目标由机械证据证明已经满足 |
| BUDGET_EXHAUSTED | Verifier 仍失败，迭代达到上限 | 任务未完成，但系统按策略正确停止 |
| AGENT_ERROR | Worker 进程返回非 0 | 执行代理发生异常 |
| VERIFIER_ERROR | Verifier 返回非 0/1 的错误码 | 验证器自身或输入状态异常 |

## 3. 环境准备与目录创建

### 3.1 检查 Python

在 Windows PowerShell 中执行：

```powershell
python --version
```

建议使用 Python 3.11 或更高版本。若系统无法识别 python，可尝试：

```powershell
py --version
```

> 命令约定：下文统一使用 python。若你的系统只识别 py，可将所有 python 替换为 py。

### 3.2 创建实验目录

```powershell
cd $HOME\Desktop
mkdir loop-engineering-training
cd loop-engineering-training
mkdir chapter01
cd chapter01
```

### 3.3 创建文件

```powershell
New-Item state.json -ItemType File
New-Item worker.py -ItemType File
New-Item verify.py -ItemType File
New-Item controller.py -ItemType File
New-Item README.md -ItemType File
```

检查目录：

```powershell
Get-ChildItem
```

预期结构：

```text
chapter01/
├─ controller.py
├─ README.md
├─ state.json
├─ verify.py
└─ worker.py
```

使用 VS Code 打开当前目录：

```powershell
code .
```

未安装 VS Code 时，可以用记事本逐个编辑，例如：

```powershell
notepad state.json
```

## 4. 编写状态、Worker 与 Verifier

### 4.1 定义任务状态：state.json

```json
{
  "value": 0,
  "target": 3
}
```

这里定义了最小任务：当前状态为 value = 0，目标状态为 value ≥ 3。真实代码任务中，它可以被替换为“当前测试失败，目标是测试和静态检查全部通过”。

### 4.2 编写模拟代理：worker.py

```python
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"


def main() -> int:
    """模拟一个执行代理：每轮只把 value 增加 1。"""

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

        current_value = state["value"]
        state["value"] = current_value + 1

        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"WORKER_ACTION: value {current_value} -> {state['value']}")

        # 故意制造错误完成声明。
        # 无论是否真正达到目标，worker 都声称已经完成。
        print("AGENT_CLAIM: DONE")

        return 0

    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"WORKER_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

> 不要修正这句：print("AGENT_CLAIM: DONE") 是本章的故意设计。它用来证明：代理的语言输出不能拥有终止权。

### 4.3 编写独立验证器：verify.py

```python
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"


def main() -> int:
    """检查当前状态是否满足 value >= target。"""

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

        value = state["value"]
        target = state["target"]

        if not isinstance(value, int) or not isinstance(target, int):
            print("VERDICT: ERROR")
            print("REASON: value 和 target 必须是整数")
            return 2

        print(f"CURRENT_STATE: value={value}, target={target}")

        if value >= target:
            print("VERDICT: PASS")
            return 0

        print("VERDICT: FAIL")
        print(f"REASON: 当前还差 {target - value}")
        return 1

    except FileNotFoundError:
        print("VERDICT: ERROR")
        print("REASON: state.json 不存在")
        return 2

    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        print("VERDICT: ERROR")
        print(f"REASON: state.json 格式错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

### 4.4 为什么验证器要返回退出码

| 退出码 | 语义 | 控制器动作 |
| --- | --- | --- |
| 0 | PASS：目标满足 | 进入 DONE |
| 1 | FAIL：目标未满足，但可继续 | 在预算允许时运行 Worker |
| 2 | ERROR：验证器或输入异常 | 进入 VERIFIER_ERROR |

自然语言可能变化、模糊或解析失败；退出码是机器可读、确定且可组合的控制信号。

## 5. 手动观察 false-DONE

### 5.1 第一次验证

```powershell
python verify.py
```

预期输出：

```text
CURRENT_STATE: value=0, target=3
VERDICT: FAIL
REASON: 当前还差 3
```

查看 PowerShell 中上一条程序的退出码：

```powershell
$LASTEXITCODE
```

应输出 1。

### 5.2 单独运行一次 Worker

```powershell
python worker.py
```

预期输出：

```text
WORKER_ACTION: value 0 -> 1
AGENT_CLAIM: DONE
```

### 5.3 再次验证

```powershell
python verify.py
```

预期输出：

```text
CURRENT_STATE: value=1, target=3
VERDICT: FAIL
REASON: 当前还差 2
```

> 你刚刚观察到的事实：代理已经声明 DONE，但机械验证仍然 FAIL。这就是最小 false-DONE。它比普通失败更危险，因为错误结果可能被误送入后续流程。

| 信号 | 值 | 是否可信 |
| --- | --- | --- |
| 代理自我声明 | AGENT_CLAIM: DONE | 不可作为完成条件 |
| 机械验证 | VERDICT: FAIL，exit code = 1 | 决定系统仍未完成 |
| 真实状态 | value = 1 < target = 3 | 应继续循环 |

## 6. 编写外层控制器：controller.py

控制器的职责是：每轮先验证；只有验证失败且预算允许时才运行 Worker；对命令输出和退出码留痕；最终写入命名终态。

```python
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final


ROOT: Final[Path] = Path(__file__).resolve().parent

VERIFY_SCRIPT: Final[Path] = ROOT / "verify.py"
WORKER_SCRIPT: Final[Path] = ROOT / "worker.py"

RUN_STATE_PATH: Final[Path] = ROOT / "run_state.json"
LOG_PATH: Final[Path] = ROOT / "loop.log"

MAX_ITERATIONS: Final[int] = 5


def utc_now() -> str:
    """返回带时区的 UTC 时间。"""

    return datetime.now(timezone.utc).isoformat()


def append_log(title: str, content: str) -> None:
    """把每轮命令输出写入证据日志。"""

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(f"\n{'=' * 70}\n")
        log_file.write(f"{title}\n")
        log_file.write(f"timestamp={utc_now()}\n")
        log_file.write(f"{'=' * 70}\n")
        log_file.write(content)
        log_file.write("\n")


def run_python_script(script_path: Path) -> subprocess.CompletedProcess[str]:
    """使用当前 Python 环境执行指定脚本。"""

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    combined_output = "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part.strip()
    )

    if combined_output:
        print(combined_output)

    append_log(
        title=f"RUN {script_path.name} | exit_code={result.returncode}",
        content=combined_output,
    )

    return result


def save_terminal_state(
    status: str,
    iterations_used: int,
    verifier_exit_code: int,
) -> None:
    """保存机器可读的最终状态。"""

    run_state = {
        "status": status,
        "iterations_used": iterations_used,
        "max_iterations": MAX_ITERATIONS,
        "last_verifier_exit_code": verifier_exit_code,
        "updated_at": utc_now(),
    }

    RUN_STATE_PATH.write_text(
        json.dumps(run_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    """运行最小的外层自治闭环。"""

    LOG_PATH.write_text("", encoding="utf-8")

    print("LOOP START")
    print(f"MAX_ITERATIONS: {MAX_ITERATIONS}")

    for iteration in range(MAX_ITERATIONS + 1):
        print(f"\n--- VERIFY BEFORE ITERATION {iteration} ---")

        verifier_result = run_python_script(VERIFY_SCRIPT)

        if verifier_result.returncode == 0:
            save_terminal_state(
                status="DONE",
                iterations_used=iteration,
                verifier_exit_code=verifier_result.returncode,
            )

            print("\nTERMINAL STATE: DONE")
            return 0

        if verifier_result.returncode not in {0, 1}:
            save_terminal_state(
                status="VERIFIER_ERROR",
                iterations_used=iteration,
                verifier_exit_code=verifier_result.returncode,
            )

            print("\nTERMINAL STATE: VERIFIER_ERROR")
            return 2

        if iteration >= MAX_ITERATIONS:
            save_terminal_state(
                status="BUDGET_EXHAUSTED",
                iterations_used=iteration,
                verifier_exit_code=verifier_result.returncode,
            )

            print("\nTERMINAL STATE: BUDGET_EXHAUSTED")
            return 1

        print(f"\n--- RUN WORKER ITERATION {iteration + 1} ---")

        worker_result = run_python_script(WORKER_SCRIPT)

        if worker_result.returncode != 0:
            save_terminal_state(
                status="AGENT_ERROR",
                iterations_used=iteration + 1,
                verifier_exit_code=verifier_result.returncode,
            )

            print("\nTERMINAL STATE: AGENT_ERROR")
            return 2

    raise RuntimeError("控制流进入了不应到达的位置")


if __name__ == "__main__":
    raise SystemExit(main())
```

### 6.1 控制器代码逐块解释

| 模块 | 职责 | 为什么不能交给 Worker |
| --- | --- | --- |
| 常量与路径 | 固定 verifier、worker、日志和预算 | 防止目标和执行边界在每轮漂移 |
| run_python_script | 统一执行子进程并捕获 stdout/stderr/退出码 | 执行证据必须由外层环境收集 |
| append_log | 保存时间、命令和原始输出 | 代理不能自行决定保留哪些失败证据 |
| save_terminal_state | 写入结构化终态 | 终态不能只存在于模型自然语言中 |
| main | 验证→动作→再验证，并执行预算策略 | 模型无权突破预算或跳过验证 |

### 6.2 为什么是“先验证，再行动”

每轮开始时先验证，可以处理三类情况：任务本来已经完成；上一次 Worker 已经使目标满足；控制器从中断中恢复后需要重新获取新鲜证据。若先调用 Worker，可能在已完成状态上制造不必要的改动。

## 7. 运行完整闭环并核验证据

### 7.1 重置状态

由于前面已经手动运行过一次 Worker，请将 state.json 重置为：

```json
{
  "value": 0,
  "target": 3
}
```

### 7.2 运行控制器

```powershell
python controller.py
```

预期输出应表现为三次 Worker 动作、四次验证，最后进入 DONE：

```text
LOOP START
MAX_ITERATIONS: 5

--- VERIFY BEFORE ITERATION 0 ---
CURRENT_STATE: value=0, target=3
VERDICT: FAIL
REASON: 当前还差 3

--- RUN WORKER ITERATION 1 ---
WORKER_ACTION: value 0 -> 1
AGENT_CLAIM: DONE

--- VERIFY BEFORE ITERATION 1 ---
CURRENT_STATE: value=1, target=3
VERDICT: FAIL
REASON: 当前还差 2

--- RUN WORKER ITERATION 2 ---
WORKER_ACTION: value 1 -> 2
AGENT_CLAIM: DONE

--- VERIFY BEFORE ITERATION 2 ---
CURRENT_STATE: value=2, target=3
VERDICT: FAIL
REASON: 当前还差 1

--- RUN WORKER ITERATION 3 ---
WORKER_ACTION: value 2 -> 3
AGENT_CLAIM: DONE

--- VERIFY BEFORE ITERATION 3 ---
CURRENT_STATE: value=3, target=3
VERDICT: PASS

TERMINAL STATE: DONE
```

> 核心观察：Worker 在第 1 轮就声明 DONE，但 Controller 直到第 3 轮获得 Verifier PASS 才进入 DONE。

### 7.3 检查结构化终态

```powershell
Get-Content run_state.json
Get-Content state.json
Get-Content loop.log
```

run_state.json 应类似：

```json
{
  "status": "DONE",
  "iterations_used": 3,
  "max_iterations": 5,
  "last_verifier_exit_code": 0,
  "updated_at": "..."
}
```

### 7.4 哪些证据才能支持“完成”

| 证据 | 作用 | 是否足够 |
| --- | --- | --- |
| AGENT_CLAIM: DONE | 只说明 Worker 自己认为完成 | 否 |
| state.json 中 value=3 | 说明状态数据达到目标 | 需要与验证器一起看 |
| Verifier 返回 0 | 机械规则确认目标满足 | 是，本章的强制门 |
| run_state.json status=DONE | 控制器已根据证据写入终态 | 是，作为系统交付状态 |
| loop.log | 支持审计每一轮发生了什么 | 不是终态门，但必须保留 |

### 7.5 映射到 Loop 的八个组成部分

| 要素 | 本章实现 | 解释 |
| --- | --- | --- |
| Trigger | 手动运行 controller.py | 为什么启动 |
| Goal | value ≥ target | 什么叫完成 |
| State | state.json、run_state.json | 系统当前在哪 |
| Action | worker.py | 谁实施候选动作 |
| Verification | verify.py | 凭什么继续或停止 |
| Policy | 错误立即停止、预算不可突破 | 什么绝不能被模型绕过 |
| Memory | state.json、loop.log | 下一轮和复盘需要保留什么 |
| Budget | MAX_ITERATIONS = 5 | 最多允许尝试多少轮 |

## 8. 破坏实验：证明系统会正确失败

> 实验原则：真正掌握闭环的标志，不是只会让它成功，而是能预测它在预算不足、代理异常和验证器异常时如何停止。

### 8.1 破坏实验 A：预算耗尽

把 state.json 改为：

```json
{
  "value": 0,
  "target": 10
}
```

保持 MAX_ITERATIONS = 5，运行：

```powershell
python controller.py
```

预期终态：

> TERMINAL STATE: BUDGET_EXHAUSTED

这不是“系统失败得不好”，而是系统在无法于预算内证明完成时，按策略保存证据并停止。

### 8.2 破坏实验 B：Worker 异常

在 worker.py 的 main() 开头临时加入：

```python
def main() -> int:
    return 2
```

重置 state.json 为 target=3，然后运行 controller.py。预期终态：

> TERMINAL STATE: AGENT_ERROR

实验结束后删除临时的 return 2。

### 8.3 破坏实验 C：Verifier 输入异常

把 state.json 故意写成非法 JSON，例如：

```text
{
  "value": 0,
  "target": 3,
}
```

末尾多余逗号会导致 JSON 解析失败。运行 controller.py，预期终态：

> TERMINAL STATE: VERIFIER_ERROR

### 8.4 三种终态不能混为一个 success=false

| 终态 | 根因 | 合理下一步 |
| --- | --- | --- |
| BUDGET_EXHAUSTED | 代理能运行，但预算内未完成 | 保存证据，人工判断是否扩大预算或拆分任务 |
| AGENT_ERROR | 代理进程异常 | 检查命令、依赖、超时和权限 |
| VERIFIER_ERROR | 验证器或输入状态异常 | 先修复验证基础设施，不能继续让代理修改任务 |

## 9. 常见错误、思考题与验收

### 9.1 常见错误排查

| 现象 | 常见原因 | 处理方法 |
| --- | --- | --- |
| python 无法识别 | Python 未安装或未加入 PATH | 尝试 py；重新安装并勾选 Add Python to PATH |
| 中文输出乱码 | 终端编码或 Python 环境问题 | 优先使用新版 PowerShell/Windows Terminal；代码已显式使用 UTF-8 |
| state.json 解析失败 | 引号、逗号或括号错误 | 使用严格 JSON；不要写注释或尾随逗号 |
| 每次立即 DONE | state.json 没有重置，value 已达到 target | 将 value 改回 0 再运行 |
| 没有 run_state.json | controller.py 在写入终态前崩溃 | 先看终端 traceback 和 loop.log |
| Worker 声明 DONE 但控制器继续 | 这是本章预期行为 | 不要“修复”它 |

### 9.2 思考题

**1. 为什么控制器不能看到 AGENT_CLAIM: DONE 就终止？**

参考结论：因为它只是 L0 自我声明，不能证明目标满足。

**2. 为什么 verify.py 要返回退出码，而不仅打印文字？**

参考结论：退出码是确定、机器可读且不依赖语言表达的控制信号。

**3. 为什么 MAX_ITERATIONS 是可靠性组件，而不仅是省钱？**

参考结论：它限制错误累积、无效重试、状态漂移和潜在破坏范围。

**4. 没有使用 AI，为什么仍属于 Loop Engineering 的基础模型？**

参考结论：因为触发、目标、状态、动作、验证、策略、记忆和预算已经形成外层控制系统。

**5. 为什么本章先验证后行动？**

参考结论：避免在已完成状态上继续修改，并支持中断恢复后重新获得新鲜证据。

### 9.3 本章验收清单

- [ ] 能运行 verify.py，并解释退出码 0、1、2。

- [ ] 能观察到 Worker 声明 DONE，但 Verifier 仍为 FAIL。

- [ ] 能运行 controller.py 得到 DONE。

- [ ] 能在 target=10 时得到 BUDGET_EXHAUSTED。

- [ ] 能制造 Worker 异常并得到 AGENT_ERROR。

- [ ] 能制造非法 JSON 并得到 VERIFIER_ERROR。

- [ ] 能解释 state.json、run_state.json 和 loop.log 的不同职责。

- [ ] 能说明为什么 Controller 而不是 Worker 拥有终止权。

### 9.4 实验记录表

| 实验 | 预期终态 | 实际终态 | 是否符合 | 备注 |
| --- | --- | --- | --- | --- |
| 正常目标 target=3 | DONE |  |  |  |
| 预算不足 target=10 | BUDGET_EXHAUSTED |  |  |  |
| Worker 返回 2 | AGENT_ERROR |  |  |  |
| state.json 非法 | VERIFIER_ERROR |  |  |  |

### 9.5 本章尚未解决的问题

本章控制器仍然不能检测停滞、不能保护 tests/ 等路径、不能绑定 Git revision、不能中断恢复，也没有独立 Reviewer。这些不是缺陷遗漏，而是后续章节将逐层引入的工程能力。

> 下一章目标：把这个玩具闭环升级为真实的 Git + pytest Python 项目：建立版本基线、加入故意设置的边界条件 bug，并把“完成”改写为可重复的测试证据。

## 附录 A：本章完整目录检查

```text
chapter01/
├─ controller.py
├─ loop.log          # 第一次运行后生成
├─ README.md
├─ run_state.json    # 第一次运行后生成
├─ state.json
├─ verify.py
└─ worker.py
```

## 附录 B：建议写入 README.md 的内容

````powershell
# Chapter 01 - Minimal Engineered Loop

## Goal
Make `value >= target` while the controller retains verification and stopping authority.

## Run
```powershell
python controller.py
```

## Evidence
- `state.json`: task state
- `run_state.json`: terminal state
- `loop.log`: per-iteration evidence

## Expected terminal states
- `DONE`
- `BUDGET_EXHAUSTED`
- `AGENT_ERROR`
- `VERIFIER_ERROR`
````

**— 第 01 章结束 —**

---

[返回课程主页](../../README.md) · [下一章 →](./02-python-project-and-git-baseline.md)
