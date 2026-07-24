# 第 04 章：外层控制器与有界调度

[返回课程主页](../../README.md) · [← 上一章](./03-deterministic-verifier.md) · [下一章 →](./05-codex-cli-integration.md)

## 本章使用说明

第三章已经把 pytest、Ruff、Git revision 和环境信息封装成统一 verifier。但 verifier 只回答“当前仓库是否通过”，它不会决定何时再次调用代理、调用多少次、代理异常时如何停止，也不会保存跨回合状态。本章要补上这层生命周期控制。

> 本章纪律：不要把 while 循环等同于控制器。一个合格的外层 loop 至少必须做到：先验证、后调用；只有新鲜 verifier 证据可以产生 DONE；每个代理调用都消耗预算；所有停止都写成命名终态。

### 学习目标

**• **能区分 verifier、agent 与 controller 的职责边界，并解释为什么模型不能拥有终止权。

**• **能把 RUNNING、DONE、BUDGET_EXHAUSTED、VERIFIER_ERROR、AGENT_ERROR、AGENT_TIMEOUT 和 CONFIG_ERROR 写成显式状态。

**• **能编写 loop_config.json，把迭代、墙钟时间和单次超时从代码常量变成可审计配置。

**• **能生成最小任务包，将 goal、仓库约束和最新失败证据交给执行代理。

**• **能实现“初始验证—调用代理—重新验证—终态判定”的有界闭环。

**• **能通过 no-op、错误退出、超时、零预算和损坏配置等实验压力测试控制器。

## 1. 从证据门到生命周期控制

Verifier 把多个检查聚合成 PASS、FAIL 或 ERROR，但它没有“下一步”概念。当 verdict=FAIL 时，系统仍需决定：是否还有预算、应该调用哪个执行器、给它看哪些证据、执行后是否重新验证，以及何时停止。Controller 正是负责这些跨回合决策的外层系统。

### 1.1 三个组件的权力边界

| 组件 | 允许做什么 | 不得拥有的权力 |
| --- | --- | --- |
| Agent / Builder | 阅读任务包；修改允许范围内的实现；输出候选结果 | 不能根据自己的完成声明把系统置为 DONE |
| Verifier | 运行固定检查；生成机械证据；返回 PASS/FAIL/ERROR | 不负责调用代理、消耗预算或选择修复策略 |
| Controller | 读取证据；调度执行器；管理状态、预算、日志和终态 | 不应自己“猜”代码正确，必须依赖 verifier |

### 1.2 本章闭环结构

**结构图 1　外层控制器的最小动作顺序**

```text
启动
  ↓
加载配置与稳定规范
  ↓
运行 verifier（第 0 次，尚未调用 agent）
  ├─ PASS  → DONE
  ├─ ERROR → VERIFIER_ERROR
  └─ FAIL
       ↓
检查迭代预算与墙钟预算
  ├─ 已耗尽 → BUDGET_EXHAUSTED
  └─ 尚可用
       ↓
构造 task packet → 调用 agent
  ├─ 超时   → AGENT_TIMEOUT
  ├─ 非零退出 → AGENT_ERROR
  └─ 正常返回 → 回到 verifier
```

> 关键判断：循环的回边不是“代理说还没做完”，而是“代理运行结束后，控制器必须重新获取新鲜验证证据”。即使 agent 输出 claim=DONE，控制器仍然回到 verifier。

### 1.3 为什么本章先不用 Codex

如果一开始就接入真实模型，调度错误、提示词问题、模型能力问题和环境问题会同时出现，你无法知道失败属于哪一层。本章使用 deterministic mock agent：它可以按模式修复、什么也不做、故意报错或睡眠。这样控制器每条分支都有可预测输入，便于单独验证。

## 2. 先定义状态机与不可破坏的不变量

状态机的价值不是画图，而是让每个停止原因可区分、可恢复、可统计。只保存 success=true/false 会丢失“该继续修业务、修验证器、改配置，还是人工介入”的决策信息。

### 2.1 本章终态

| 状态 | 触发条件 | 含义与下一步 |
| --- | --- | --- |
| RUNNING | 控制器已启动，尚未进入终态 | 中间状态；崩溃恢复时不能直接视为成功 |
| DONE | 本轮刚运行的 verifier 返回 PASS 且协议一致 | 唯一成功终态；证据必须是新鲜的 |
| BUDGET_EXHAUSTED | 代理调用数或总墙钟时间达到上限 | 保存现状后停止；不是继续无限重试 |
| VERIFIER_ERROR | 验证器超时、报告缺失、JSON 损坏或退出码与 verdict 不一致 | 当前没有可信证据，应先修验证链 |
| AGENT_ERROR | 代理以非零退出码结束 | 执行器异常；不把它伪装成普通业务失败 |
| AGENT_TIMEOUT | 单次代理调用超过 timeout | 终止本次运行，避免进程无限占用资源 |
| CONFIG_ERROR | 配置缺字段、类型错误、负预算或占位符未解析 | 在调用任何代理之前 fail closed |

### 2.2 五条控制器不变量

**1. **初始 verifier 必须先运行。若仓库原本已经通过，代理调用数应为 0。

**2. **DONE 只能由刚刚完成的 verifier PASS 产生，不能由 agent 输出、旧 JSON 或文件存在性产生。

**3. **max_iterations 统计的是代理调用次数，不统计 verifier 次数；第 0 次验证不消耗代理预算。

**4. **每次调用代理前先保存任务包与当前状态；即使后续崩溃，也能知道系统打算做什么。

**5. **所有异常都收敛到命名终态并持久化，不能让 traceback 成为唯一运行记录。

> 压力测试问题：假如 max_iterations=0，而当前 verifier=FAIL，控制器应直接输出 BUDGET_EXHAUSTED；它仍然要先运行一次 verifier，因为没有证据就不能知道当前是否已经完成。

## 3. 从第三章仓库建立安全起点

### 3.1 回到 statkit-lab

**操作 1　进入仓库并激活虚拟环境**

```powershell
cd $HOME\Desktop\loop-engineering-training\chapter02\statkit-lab
.\.venv\Scripts\Activate.ps1
 
python -c "import sys; print(sys.executable)"
git status --short
python scripts\verify.py
$LASTEXITCODE
```

安全起点应满足：Git 工作区干净；goal.md 与 scripts/verify.py 已提交；统一 verifier 返回 FAIL / 1；pytest 失败源于常量向量除零，而 Ruff 通过。若当前源码已经被你手工修复，先执行 git restore src\statkit
ormalize.py。

### 3.2 创建本章分支

**操作 2　隔离控制器基础设施**

```powershell
git switch -c chapter04-controller
# 若分支已存在：
# git switch chapter04-controller
```

> 不要跳过：本章会让 mock agent 真实修改源码。分支与 Git 基线让你可以在成功实验后恢复已知缺陷，只提交控制器基础设施，为下一章重新用 Codex 修复同一任务。

## 4. 创建控制器文件与目录结构

### 4.1 创建文件

**操作 3　新增规范、配置和脚本**

```powershell
New-Item AGENTS.md -ItemType File
New-Item loop_config.json -ItemType File
New-Item scripts\mock_agent.py -ItemType File
New-Item scripts\run_loop.py -ItemType File
 
Get-ChildItem scripts
```

### 4.2 完成后的新增结构

**目录 1　本章运行后的关键文件**

```text
statkit-lab\
├─ goal.md
├─ AGENTS.md
├─ loop_config.json
├─ scripts\
│  ├─ verify.py
│  ├─ mock_agent.py
│  └─ run_loop.py
├─ logs\
│  ├─ controller-<run_id>.jsonl
│  ├─ verifier-<run_id>-00.log
│  └─ agent-<run_id>-01.log
└─ state\
   ├─ run_state.json
   ├─ verify-<run_id>-00.json
   ├─ verify-<run_id>-01.json
   └─ task-packet-<run_id>-01.json
```

logs/ 和 state/ 已在第二章的 .gitignore 中排除。源码、配置和规范进入 Git；运行证据不污染代码 diff，但仍保留在本地供审计。生产系统会把这些工件放入数据库或 artifact store，本章先使用文件系统。

## 5. 编写 AGENTS.md 与 loop_config.json

### 5.1 AGENTS.md：跨回合稳定约束

goal.md 描述本次任务，AGENTS.md 描述仓库级长期规则。Task packet 会同时携带两者：前者防止目标漂移，后者防止每轮遗漏约束。提示词中的禁止事项仍不是机械策略；受保护路径检查会在后续章节加入。

**文件 1　`AGENTS.md**`

```markdown
# Repository instructions
 
- Treat `goal.md` as the task contract.
- Modify implementation files only when necessary.
- Do not edit `tests/`, `pyproject.toml`, or `scripts/verify.py`.
- Do not add dependencies or change the public API.
- Prefer the smallest change that satisfies the verifier.
- Never claim completion without fresh verifier evidence.
```

### 5.2 配置不是散落在代码里的魔法数字

**文件 2　`loop_config.json**`

```json
{
  "max_iterations": 3,
  "max_wall_time_seconds": 120,
  "verifier_timeout_seconds": 60,
  "agent_timeout_seconds": 20,
  "verifier_command": [
    "{python}",
    "scripts/verify.py"
  ],
  "agent_command": [
    "{python}",
    "scripts/mock_agent.py",
    "--task-packet",
    "{task_packet}",
    "--mode",
    "fix"
  ]
}
```

| 字段 | 本章取值 | 精确定义 |
| --- | --- | --- |
| max_iterations | 3 | 最多允许 3 次 agent 调用；初始 verifier 不计入 |
| max_wall_time_seconds | 120 | 从 controller 启动到终态的总墙钟预算 |
| verifier_timeout_seconds | 60 | 单次 verifier 的最长等待时间 |
| agent_timeout_seconds | 20 | 单次 agent 的最长等待时间 |
| verifier_command | python scripts/verify.py | 固定证据命令；{python} 替换为当前虚拟环境解释器 |
| agent_command | mock_agent --mode fix | 本章执行器；{task_packet} 替换为当前任务包路径 |

> 安全细节：命令在 JSON 中保存为参数数组，controller 使用 subprocess.run(command)，不使用 shell=True。这样路径与参数边界明确，也减少命令拼接和 shell 注入风险。

## 6. 用 mock agent 隔离调度逻辑

Mock agent 不是为了假装有智能，而是提供四种可重复的执行行为。只有当控制器能正确处理这些可预测分支，才值得接入输出具有随机性、成本和上下文限制的真实模型。

### 6.1 四种模式

| 模式 | 行为 | 用于验证的控制器分支 |
| --- | --- | --- |
| fix | 对 normalize.py 插入常量向量分支并返回 0 | FAIL → agent → PASS → DONE |
| noop | 不改任何文件，但仍返回 0 并声明 DONE | 代理正常返回不等于完成；最终预算耗尽 |
| error | 向 stderr 写入故意错误并返回 7 | AGENT_ERROR |
| sleep | 睡眠指定秒数 | AGENT_TIMEOUT |

### 6.2 先理解关键逻辑

**代码块 1　fix 模式只产生候选变更**

```text
old = "    span = high - low\n\n    return ...\n"
new = (
    "    span = high - low\n\n"
    "    if span == 0:\n"
    "        return [0.0] * len(values)\n\n"
    "    return ...\n"
)
TARGET_PATH.write_text(source.replace(old, new), encoding="utf-8")
```

Mock agent 最终仍打印 claim="DONE"。这个字段刻意保留，用来检查 controller 是否会错误相信执行器。正确 controller 完全不解析该 claim，而是在 agent 返回后重新运行 verifier。完整文件见附录 C。

### 6.3 保存并先做静态检查

**操作 4　检查 mock agent 和 controller 文件的基本语法**

```powershell
python -m ruff check scripts\mock_agent.py scripts\run_loop.py
python -m py_compile scripts\mock_agent.py scripts\run_loop.py
```

此时 run_loop.py 尚未写完时，先只检查 mock_agent.py；完成第 7 节后再对两者执行。不要等到整个闭环运行时才发现缩进或引号错误。

## 7. 手把手编写 scripts/run_loop.py

完整 controller 较长，但每一部分只承担一个控制职责。下面按模块拆解；附录 D 提供可直接复制的完整版本。

### 7.1 路径、终态与配置对象

**代码块 2　把稳定路径和状态词固定下来**

```python
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
LATEST_VERIFY_REPORT = STATE_DIR / "verify-latest.json"
RUN_STATE_PATH = STATE_DIR / "run_state.json"
 
TerminalStatus = Literal[
    "RUNNING", "DONE", "BUDGET_EXHAUSTED", "VERIFIER_ERROR",
    "AGENT_ERROR", "AGENT_TIMEOUT", "CONFIG_ERROR",
]
 
@dataclass(frozen=True)
class LoopConfig:
    max_iterations: int
    max_wall_time_seconds: float
    verifier_timeout_seconds: float
    agent_timeout_seconds: float
    verifier_command: list[str]
    agent_command: list[str]
```

使用 dataclass 的目的不是炫技，而是让“配置已经通过类型与范围检查”和“原始任意 JSON”分开。后续函数只接收 LoopConfig，不必在每个调用点重复猜字段是否存在。

### 7.2 配置校验必须在调用代理之前

**代码块 3　拒绝负预算、空命令和错误类型**

```text
max_iterations = data.get("max_iterations")
if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
    raise ConfigError("max_iterations must be an integer")
if max_iterations < 0:
    raise ConfigError("max_iterations cannot be negative")
 
verifier_command = require_command(data, "verifier_command")
agent_command = require_command(data, "agent_command")
```

> 为什么 bool 要单独排除：在 Python 中 bool 是 int 的子类，isinstance(True, int) 为 True。若不排除，true 可能被错误接受为 1 次迭代。配置验证要表达业务语义，而不只是通过语言层面的宽松类型检查。

### 7.3 命令占位符与进程边界

**代码块 4　使用当前 Python，并统一处理 `timeout**`

```text
replacements = {"{python}": sys.executable}
if task_packet is not None:
    replacements["{task_packet}"] = str(task_packet)
expanded = [replacements.get(part, part) for part in command]
 
completed = subprocess.run(
    expanded,
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
    timeout=timeout_seconds,
)
```

{python} 最终展开为 sys.executable，因此 verifier 和 agent 与 controller 使用同一个虚拟环境。check=False 允许非零退出码作为协议数据返回；TimeoutExpired 则被归一化为 timed_out=True。

### 7.4 为什么事件日志使用 JSONL

**代码块 5　每个状态变化追加一行事件**

```text
record = {"timestamp": utc_now(), "event": event, **payload}
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
```

JSONL 每行都是独立 JSON 对象。即使进程中途崩溃，已写入的前几行仍可读取；同时可以按 event 过滤、统计每轮耗时或重建状态演化。run_state.json 保存最新快照，`controller-*.jsonl` 保存事件历史，两者用途不同。

### 7.5 新鲜 verifier：先删除 latest，再要求本轮重新生成

**代码块 6　拒绝旧报告和协议不一致**

```text
LATEST_VERIFY_REPORT.unlink(missing_ok=True)
result = run_process(command, timeout_seconds)
 
if not LATEST_VERIFY_REPORT.exists():
    return "ERROR", None, result
 
report = json.loads(LATEST_VERIFY_REPORT.read_text(encoding="utf-8"))
verdict = report.get("verdict")
expected = {0: "PASS", 1: "FAIL", 2: "ERROR"}.get(result.exit_code)
if verdict != expected:
    return "ERROR", report, result
```

删除 verify-latest.json 不是为了清理目录，而是为了证明随后读取的报告由当前子进程生成。退出码与 JSON verdict 必须一致：例如进程返回 1 却写 PASS，说明验证协议已经损坏，不能继续当作普通 FAIL。

### 7.6 任务包只携带下一步真正需要的信息

**代码块 7　构造本轮 task `packet**`

```text
packet = {
    "run_id": run_id,
    "iteration": iteration,
    "remaining_iterations_after_this_call": config.max_iterations - iteration,
    "goal": GOAL_PATH.read_text(encoding="utf-8"),
    "repository_instructions": INSTRUCTIONS_PATH.read_text(encoding="utf-8"),
    "latest_verifier_report": verifier_report,
    "controller_rule": (
        "Implement a minimal candidate change. Your completion claim is advisory; "
        "the controller will rerun the verifier."
    ),
}
```

这里没有拼接全部历史聊天、全部仓库文件或此前每轮 stdout。目标、稳定约束、最新证据、当前迭代与剩余预算足以驱动本实验。以后接入真实模型时，可以对 latest_verifier_report 做摘要，但不能丢失失败测试和禁止事项。

### 7.7 主循环：预算检查必须位于动作之前

**代码块 8　核心控制顺序（伪代码）**

```markdown
while True:
    if wall_budget_exhausted():
        return BUDGET_EXHAUSTED
 
    verdict = run_fresh_verifier()
    if verdict == "PASS":
        return DONE
    if verdict == "ERROR":
        return VERIFIER_ERROR
 
    if iterations_used >= max_iterations:
        return BUDGET_EXHAUSTED
 
    task_packet = build_task_packet()
    iterations_used += 1
    agent_result = run_agent(task_packet)
 
    if agent_result.timed_out:
        return AGENT_TIMEOUT
    if agent_result.exit_code != 0:
        return AGENT_ERROR
 
    # 不在这里信任 agent claim；自然回到循环顶部重新验证
```

> 一个容易写错的细节：不要在 agent 返回 0 后直接 DONE，也不要把 iterations_used 放在 verifier 前无条件加一。本章定义的一次 iteration 是一次实际 agent 调用；初始验证和最终验证都不消耗该预算。

### 7.8 保存完整文件并检查

**操作 5　复制附录 D 后执行**

```powershell
python -m ruff check src tests scripts
python -m py_compile scripts\run_loop.py scripts\mock_agent.py
python scripts\verify.py
$LASTEXITCODE
```

预期 verifier 仍为 FAIL / 1。若 Ruff 因 run_loop.py 或 mock_agent.py 失败，先修脚本自身；否则 controller 会把基础设施缺陷与业务缺陷一起交给 agent，任务边界被污染。

## 8. 第一次运行：一轮修复后进入 DONE

### 8.1 启动闭环

**操作 6　使用 fix 模式运行 `controller**`

```powershell
python scripts\run_loop.py
$LASTEXITCODE
```

预期终端只打印最终终态；详细过程写入 JSONL 与每个子进程日志。典型输出：

**预期输出**

```text
TERMINAL STATE: DONE
REASON: fresh deterministic verifier evidence passed
```

### 8.2 实际发生了几次验证

| 顺序 | 动作 | 结果 | 是否消耗 agent 迭代 |
| --- | --- | --- | --- |
| 0 | 运行 verifier | FAIL：常量向量测试失败 | 否 |
| 1 | 构造 task packet，调用 mock agent | normalize.py 增加 span == 0 分支 | 是，iterations_used=1 |
| 2 | 重新运行 verifier | pytest 与 Ruff 均 PASS | 否 |
| 3 | Controller 写终态 | DONE | 无新调用 |

> 观察重点：mock agent 输出 claim=DONE 发生在第 1 次 agent 调用结束时；真正的 DONE 发生在第 2 次 verifier PASS 之后。这两个时刻必须分开。

## 9. 读取 run_state、任务包与事件日志

### 9.1 查看最终状态快照

**操作 7　解析 `run_state.json**`

```powershell
$s = Get-Content state\run_state.json -Raw | ConvertFrom-Json
$s.status
$s.iterations_used
$s.verifier_runs
$s.last_verdict
$s.terminal_reason
$s.event_log
```

**核心字段示例**

```json
{
  "status": "DONE",
  "iterations_used": 1,
  "max_iterations": 3,
  "verifier_runs": 2,
  "last_verdict": "PASS",
  "terminal_reason": "fresh deterministic verifier evidence passed"
}
```

### 9.2 查看本轮任务包

**操作 8　定位最新 task `packet**`

```powershell
$packet = Get-ChildItem state\task-packet-*.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
 
Get-Content $packet.FullName
```

检查 latest_verifier_report：它应是第一次 FAIL 证据，而不是修复后的 PASS。任务包描述“为什么需要行动”；下一轮 verifier 描述“行动后是否成功”。不要用修复后的证据倒写此前任务包。

### 9.3 查看事件序列

**操作 9　按顺序查看 controller 事件**

```powershell
$log = (Get-Content state\run_state.json -Raw | ConvertFrom-Json).event_log
Get-Content $log | ForEach-Object { $_ | ConvertFrom-Json } |
  Format-Table timestamp,event,sequence,iteration,verdict,status
```

**预期事件顺序**

```text
run_started
verifier_finished (sequence=0, verdict=FAIL)
agent_started      (iteration=1)
agent_finished     (exit_code=0)
verifier_finished (sequence=1, verdict=PASS)
terminal           (status=DONE)
```

### 9.4 检查代码差异

**操作 10　不要只看终态，核对变更范围**

```powershell
git status --short
git diff -- src\statkit\normalize.py
git diff -- tests pyproject.toml scripts\verify.py
```

预期只有 normalize.py 增加三行逻辑。此时 controller 尚未实现受保护路径策略，所以你仍需人工检查后两个 diff 为空。后续章节会把这条人工检查机械化。

## 10. 七个破坏与恢复实验

只展示 fix→DONE 不能证明控制器可靠。下面每个实验都先恢复已知缺陷，并在结束后检查 state/run_state.json。修改 loop_config.json 前建议复制一份 loop_config.backup.json，或使用 git restore loop_config.json 恢复。

### 10.1 实验一：仓库已经 PASS，不应调用代理

保留第 8 节修复后的源码，再次运行 controller：

**操作 11　验证零 agent 调用的 `DONE**`

```powershell
python scripts\run_loop.py
$s = Get-Content state\run_state.json -Raw | ConvertFrom-Json
$s.status
$s.iterations_used
$s.verifier_runs
```

预期：DONE、iterations_used=0、verifier_runs=1。若仍调用 agent，说明初始验证顺序错误。

### 10.2 实验二：max_iterations=0

**操作 12　零代理预算仍需先验证**

```powershell
git restore src\statkit\normalize.py
(Get-Content loop_config.json -Raw) -replace '"max_iterations": 3', '"max_iterations": 0' |
  Set-Content loop_config.json -Encoding utf8
 
python scripts\run_loop.py
Get-Content state\run_state.json
```

预期：先产生一次 FAIL 证据，然后 BUDGET_EXHAUSTED；iterations_used=0。完成后 git restore loop_config.json。

### 10.3 实验三：noop agent 反复声称 DONE

**操作 13　把 fix 改为 `noop**`

```powershell
git restore src\statkit\normalize.py
(Get-Content loop_config.json -Raw) -replace '"fix"', '"noop"' |
  Set-Content loop_config.json -Encoding utf8
 
python scripts\run_loop.py
Get-Content state\run_state.json
```

预期：每次 agent 返回 0，但源码不变；控制器持续获得 FAIL，3 次调用后进入 BUDGET_EXHAUSTED。本章尚未实现停滞检测，因此它会用完预算；第 6 章会让相同失败更早进入 STAGNATED。

### 10.4 实验四：agent 非零退出

**操作 14　把模式改为 `error**`

```powershell
git restore loop_config.json
(Get-Content loop_config.json -Raw) -replace '"fix"', '"error"' |
  Set-Content loop_config.json -Encoding utf8
 
python scripts\run_loop.py
Get-Content state\run_state.json
```

预期：AGENT_ERROR，iterations_used=1，last_agent_exit_code=7；控制器不应再运行第二次 verifier。

### 10.5 实验五：agent 超时

**操作 15　构造 sleep 模式与 1 秒 `timeout**`

```powershell
git restore loop_config.json
(Get-Content loop_config.json -Raw) `
  -replace '"agent_timeout_seconds": 20', '"agent_timeout_seconds": 1' `
  -replace '"fix"', '"sleep", "--sleep-seconds", "3"' |
  Set-Content loop_config.json -Encoding utf8
 
python scripts\run_loop.py
Get-Content state\run_state.json
```

预期：AGENT_TIMEOUT，last_agent_exit_code=null，last_agent_timed_out=true。完成后恢复配置。

### 10.6 实验六：验证器无法工作

**操作 16　临时移走 tests 目录**

```powershell
git restore loop_config.json
git restore src\statkit\normalize.py
Rename-Item tests tests_backup
 
python scripts\run_loop.py
Get-Content state\run_state.json
 
Rename-Item tests_backup tests
```

pytest 在无测试时返回特殊退出码，第三章 verifier 将其归类为 ERROR。预期 controller 进入 VERIFIER_ERROR，并且 iterations_used=0：证据系统坏了时不应让 agent 猜着修业务。

### 10.7 实验七：损坏配置

**操作 17　让 max_iterations 成为负数**

```powershell
Copy-Item loop_config.json loop_config.backup.json
(Get-Content loop_config.json -Raw) -replace '"max_iterations": 3', '"max_iterations": -1' |
  Set-Content loop_config.json -Encoding utf8
 
python scripts\run_loop.py
Get-Content state\run_state.json
 
Move-Item loop_config.backup.json loop_config.json -Force
```

预期：CONFIG_ERROR，任何 verifier 与 agent 都不应启动。配置错误应 fail closed，而不是“使用一个猜测默认值继续”。

| 实验 | 预期终态 | 应重点核对的字段 |
| --- | --- | --- |
| 仓库已 PASS | DONE | iterations_used=0；verifier_runs=1 |
| max_iterations=0 | BUDGET_EXHAUSTED | iterations_used=0；last_verdict=FAIL |
| noop | BUDGET_EXHAUSTED | iterations_used=3；每轮 verifier 仍 FAIL |
| error | AGENT_ERROR | last_agent_exit_code=7 |
| sleep | AGENT_TIMEOUT | last_agent_timed_out=true |
| tests 缺失 | VERIFIER_ERROR | iterations_used=0 |
| 负预算 | CONFIG_ERROR | 没有 run_started 事件 |

## 11. 恢复已知缺陷并提交控制器基础设施

第 8 节的成功实验修改了 normalize.py，但下一章要把 mock agent 替换为 Codex，让真实代理完成同一修复。因此本章最终提交只包含控制器、配置和仓库约束，不提交 mock 产生的业务修复。

### 11.1 恢复与检查

**操作 18　回到故意失败状态**

```powershell
git restore loop_config.json
git restore src\statkit\normalize.py
python scripts\verify.py
$LASTEXITCODE
 
git status --short
python -m ruff check src tests scripts
```

预期 verifier=FAIL / 1，Ruff 全部通过。git status 应只显示 AGENTS.md、loop_config.json、mock_agent.py 和 run_loop.py 的新增或修改；logs/ 与 state/ 不应出现。

### 11.2 提交本章

**操作 19　提交控制器骨架**

```powershell
git add AGENTS.md loop_config.json scripts\mock_agent.py scripts\run_loop.py
git diff --cached --stat
git commit -m "chapter04: add bounded outer loop controller"
git status --short
git log --oneline --decorate -4
```

> 本章完成状态：工作区干净；统一 verifier 对已知业务缺陷返回 FAIL；控制器与 mock agent 通过 Ruff；fix/noop/error/sleep/零预算/验证器错误/配置错误分支均得到预期命名终态。

## 12. 常见错误、诊断路径与验收清单

### 12.1 常见错误

| 现象 | 最可能原因 | 诊断与修正 |
| --- | --- | --- |
| 一启动就 CONFIG_ERROR | JSON 有 BOM/逗号错误、字段缺失或类型不对 | Get-Content loop_config.json；用 ConvertFrom-Json 检查 |
| verifier 明明 PASS 却 VERIFIER_ERROR | 退出码与报告不一致，或 latest 报告未由本轮生成 | 查看 verifier 进程日志和 state/verify-latest.json |
| mock fix 后仍 FAIL | 源码不处于教程起始版本，字符串替换没有命中；或 Ruff 报脚本错误 | 查看 agent 日志、git diff、verify archive |
| 运行很久无终态 | timeout 或墙钟预算过大；子进程卡住 | 检查 run_state.updated_at 与 controller JSONL 最后一条事件 |
| iterations_used 比预期多 1 | 把 verifier 次数也计入了 agent 预算 | 核对主循环中递增位置，只在 agent 调用前加 1 |
| 第二次运行仍调用 agent | 成功修复未保留，或初始 verifier 没有先执行 | 先手动 verify；查看事件第一条动作是否 verifier |
| Git 显示 logs/state | .gitignore 不完整或文件已被跟踪 | git check-ignore -v；必要时 git rm --cached |

### 12.2 本章自测

**1. **为什么 controller 启动后必须先运行 verifier？

**参考结论：**因为当前仓库可能已经完成；先调用 agent 会产生不必要变更和成本，也破坏“动作由证据触发”的原则。

**2. **为什么 agent 返回 0 不能直接 DONE？

**参考结论：**0 只说明执行器进程正常结束，不说明任务验收条件满足；必须重新运行 verifier。

**3. **BUDGET_EXHAUSTED 是否等于系统失败？

**参考结论：**它表示系统在预设边界内未完成并安全停止；这是可控终态，优于无限重试。

**4. **为什么 verifier 报告缺失要进入 VERIFIER_ERROR，而不是继续下一轮？

**参考结论：**因为控制器无法获得可信状态；继续行动会在不可观测条件下放大错误。

**5. **run_state.json 和 controller JSONL 有什么区别？

**参考结论：**前者是最新快照，便于快速恢复和读取；后者是追加事件历史，便于审计与重放。

### 12.3 通过标准

- [ ] 能画出 initial verify → agent → re-verify 的控制顺序，并指出 DONE 的唯一来源。

- [ ] 能解释 max_iterations 与 verifier_runs 为什么是两个不同计数器。

- [ ] 能运行 fix 模式得到 DONE，且 iterations_used=1、verifier_runs=2。

- [ ] 能运行 noop 模式得到 BUDGET_EXHAUSTED，而不是相信 mock agent 的 DONE 声明。

- [ ] 能制造 AGENT_ERROR、AGENT_TIMEOUT、VERIFIER_ERROR 与 CONFIG_ERROR。

- [ ] 能在 state/ 中找到新鲜 verifier archive 与对应 task packet。

- [ ] 能从 JSONL 重建一次运行的事件顺序。

- [ ] 能恢复 normalize.py 的已知缺陷，只提交控制器基础设施。

## 附录 A. 完整 AGENTS.md

**文件 A1　`AGENTS.md**`

```markdown
# Repository instructions
 
- Treat `goal.md` as the task contract.
- Modify implementation files only when necessary.
- Do not edit `tests/`, `pyproject.toml`, or `scripts/verify.py`.
- Do not add dependencies or change the public API.
- Prefer the smallest change that satisfies the verifier.
- Never claim completion without fresh verifier evidence.
```

## 附录 B. 完整 loop_config.json

**文件 B1　`loop_config.json**`

```json
{
  "max_iterations": 3,
  "max_wall_time_seconds": 120,
  "verifier_timeout_seconds": 60,
  "agent_timeout_seconds": 20,
  "verifier_command": [
    "{python}",
    "scripts/verify.py"
  ],
  "agent_command": [
    "{python}",
    "scripts/mock_agent.py",
    "--task-packet",
    "{task_packet}",
    "--mode",
    "fix"
  ]
}
```

## 附录 C. 完整 mock_agent.py

Mock agent 仅用于验证调度分支，不代表生产实现。下一章会用 Codex CLI 替换 agent_command。

**文件 C1　scripts/`mock_agent.py**`

```python
from __future__ import annotations
 
import argparse
import json
import sys
import time
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
TARGET_PATH = ROOT / "src" / "statkit" / "normalize.py"
 
 
def load_task_packet(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read task packet: {exc}") from exc
 
 
def apply_known_fix() -> bool:
    old = """    span = high - low\n\n    return [(value - low) / span for value in values]\n"""
    new = (
        "    span = high - low\n\n"
        "    if span == 0:\n"
        "        return [0.0] * len(values)\n\n"
        "    return [(value - low) / span for value in values]\n"
    )
 
    source = TARGET_PATH.read_text(encoding="utf-8")
    if new in source:
        return False
    if old not in source:
        raise RuntimeError("Expected starter implementation was not found")
 
    TARGET_PATH.write_text(source.replace(old, new), encoding="utf-8")
    return True
 
 
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic stand-in for an AI agent")
    parser.add_argument("--task-packet", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("fix", "noop", "error", "sleep"),
        default="fix",
    )
    parser.add_argument("--sleep-seconds", type=float, default=5.0)
    return parser.parse_args()
 
 
def main() -> int:
    args = parse_args()
 
    try:
        packet = load_task_packet(args.task_packet)
        iteration = packet.get("iteration")
 
        if args.mode == "error":
            print("MOCK_AGENT_ERROR: deliberate failure", file=sys.stderr)
            return 7
 
        if args.mode == "sleep":
            print(f"MOCK_AGENT_SLEEP: {args.sleep_seconds}s")
            time.sleep(args.sleep_seconds)
            return 0
 
        changed = apply_known_fix() if args.mode == "fix" else False
        result = {
            "agent": "mock",
            "mode": args.mode,
            "iteration": iteration,
            "changed": changed,
            "changed_files": [str(TARGET_PATH.relative_to(ROOT))] if changed else [],
            "claim": "DONE",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
 
    except RuntimeError as exc:
        print(f"MOCK_AGENT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"MOCK_AGENT_ERROR: {exc}", file=sys.stderr)
        return 2
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

## 附录 D. 完整 run_loop.py

以下代码已按本章 statkit-lab 环境运行验证。复制时保持缩进，不要把代码块中的反斜杠或引号改成全角字符。

**文件 D1　scripts/`run_loop.py**`

```python
from __future__ import annotations
 
import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
 
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
GOAL_PATH = ROOT / "goal.md"
INSTRUCTIONS_PATH = ROOT / "AGENTS.md"
LATEST_VERIFY_REPORT = STATE_DIR / "verify-latest.json"
RUN_STATE_PATH = STATE_DIR / "run_state.json"
 
TerminalStatus = Literal[
    "RUNNING",
    "DONE",
    "BUDGET_EXHAUSTED",
    "VERIFIER_ERROR",
    "AGENT_ERROR",
    "AGENT_TIMEOUT",
    "CONFIG_ERROR",
]
 
 
class ConfigError(ValueError):
    """Raised when loop_config.json is incomplete or unsafe."""
 
 
@dataclass(frozen=True)
class LoopConfig:
    max_iterations: int
    max_wall_time_seconds: float
    verifier_timeout_seconds: float
    agent_timeout_seconds: float
    verifier_command: list[str]
    agent_command: list[str]
 
 
@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
 
 
def utc_now() -> str:
    return datetime.now(UTC).isoformat()
 
 
def make_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
 
 
def read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc
 
    if not isinstance(data, dict):
        raise ConfigError(f"Expected a JSON object in {path.relative_to(ROOT)}")
    return data
 
 
def require_positive_number(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive number")
    return float(value)
 
 
def require_command(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{key} must be a non-empty list")
    if not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{key} must contain non-empty strings")
    return list(value)
 
 
def load_config(path: Path) -> LoopConfig:
    data = read_json(path)
    max_iterations = data.get("max_iterations")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
        raise ConfigError("max_iterations must be an integer")
    if max_iterations < 0:
        raise ConfigError("max_iterations cannot be negative")
 
    return LoopConfig(
        max_iterations=max_iterations,
        max_wall_time_seconds=require_positive_number(
            data,
            "max_wall_time_seconds",
        ),
        verifier_timeout_seconds=require_positive_number(
            data,
            "verifier_timeout_seconds",
        ),
        agent_timeout_seconds=require_positive_number(
            data,
            "agent_timeout_seconds",
        ),
        verifier_command=require_command(data, "verifier_command"),
        agent_command=require_command(data, "agent_command"),
    )
 
 
def expand_command(command: list[str], *, task_packet: Path | None = None) -> list[str]:
    replacements = {"{python}": sys.executable}
    if task_packet is not None:
        replacements["{task_packet}"] = str(task_packet)
 
    expanded = [replacements.get(part, part) for part in command]
    unresolved = [part for part in expanded if part.startswith("{") and part.endswith("}")]
    if unresolved:
        raise ConfigError(f"Unresolved command placeholders: {unresolved}")
    return expanded
 
 
def run_process(command: list[str], timeout_seconds: float) -> ProcessResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
        return ProcessResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=round(time.perf_counter() - started, 3),
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return ProcessResult(
            command=command,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(time.perf_counter() - started, 3),
            timed_out=True,
        )
 
 
def append_event(log_path: Path, event: str, **payload: object) -> None:
    record = {"timestamp": utc_now(), "event": event, **payload}
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
 
 
def write_process_log(path: Path, result: ProcessResult) -> None:
    text = "\n".join(
        [
            f"COMMAND: {' '.join(result.command)}",
            f"EXIT_CODE: {result.exit_code}",
            f"TIMED_OUT: {result.timed_out}",
            f"DURATION_SECONDS: {result.duration_seconds}",
            "--- STDOUT ---",
            result.stdout.rstrip(),
            "--- STDERR ---",
            result.stderr.rstrip(),
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
 
 
def save_state(state: dict[str, object]) -> None:
    state["updated_at"] = utc_now()
    RUN_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
 
 
def finish(
    state: dict[str, object],
    status: TerminalStatus,
    reason: str,
    event_log: Path,
) -> int:
    state["status"] = status
    state["terminal_reason"] = reason
    save_state(state)
    append_event(event_log, "terminal", status=status, reason=reason)
    print(f"TERMINAL STATE: {status}")
    print(f"REASON: {reason}")
    return 0 if status == "DONE" else 1
 
 
def remaining_wall_time(started: float, budget_seconds: float) -> float:
    return budget_seconds - (time.perf_counter() - started)
 
 
def bounded_timeout(requested: float, remaining: float) -> float:
    return max(0.001, min(requested, remaining))
 
 
def run_verifier(
    *,
    config: LoopConfig,
    run_id: str,
    sequence: int,
    remaining: float,
    event_log: Path,
) -> tuple[str, dict[str, object] | None, ProcessResult]:
    LATEST_VERIFY_REPORT.unlink(missing_ok=True)
    command = expand_command(config.verifier_command)
    result = run_process(
        command,
        bounded_timeout(config.verifier_timeout_seconds, remaining),
    )
    process_log = LOG_DIR / f"verifier-{run_id}-{sequence:02d}.log"
    write_process_log(process_log, result)
 
    if result.timed_out:
        append_event(event_log, "verifier_timeout", sequence=sequence)
        return "ERROR", None, result
 
    if not LATEST_VERIFY_REPORT.exists():
        append_event(event_log, "verifier_report_missing", sequence=sequence)
        return "ERROR", None, result
 
    try:
        report = json.loads(LATEST_VERIFY_REPORT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        append_event(event_log, "verifier_report_invalid", sequence=sequence)
        return "ERROR", None, result
 
    if not isinstance(report, dict):
        append_event(event_log, "verifier_report_invalid", sequence=sequence)
        return "ERROR", None, result
 
    verdict = report.get("verdict")
    expected = {0: "PASS", 1: "FAIL", 2: "ERROR"}.get(result.exit_code)
    if verdict not in {"PASS", "FAIL", "ERROR"} or verdict != expected:
        append_event(
            event_log,
            "verifier_protocol_error",
            sequence=sequence,
            exit_code=result.exit_code,
            verdict=verdict,
        )
        return "ERROR", report, result
 
    archive_path = STATE_DIR / f"verify-{run_id}-{sequence:02d}.json"
    archive_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    append_event(
        event_log,
        "verifier_finished",
        sequence=sequence,
        verdict=verdict,
        exit_code=result.exit_code,
        report=str(archive_path.relative_to(ROOT)),
    )
    return str(verdict), report, result
 
 
def build_task_packet(
    *,
    run_id: str,
    iteration: int,
    config: LoopConfig,
    verifier_report: dict[str, object],
) -> Path:
    packet = {
        "run_id": run_id,
        "iteration": iteration,
        "remaining_iterations_after_this_call": config.max_iterations - iteration,
        "goal": GOAL_PATH.read_text(encoding="utf-8"),
        "repository_instructions": INSTRUCTIONS_PATH.read_text(encoding="utf-8"),
        "latest_verifier_report": verifier_report,
        "controller_rule": (
            "Implement a minimal candidate change. Your completion claim is advisory; "
            "the controller will rerun the verifier."
        ),
    }
    path = STATE_DIR / f"task-packet-{run_id}-{iteration:02d}.json"
    path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
 
 
def run_agent(
    *,
    config: LoopConfig,
    run_id: str,
    iteration: int,
    task_packet: Path,
    remaining: float,
    event_log: Path,
) -> ProcessResult:
    command = expand_command(config.agent_command, task_packet=task_packet)
    append_event(
        event_log,
        "agent_started",
        iteration=iteration,
        task_packet=str(task_packet.relative_to(ROOT)),
    )
    result = run_process(
        command,
        bounded_timeout(config.agent_timeout_seconds, remaining),
    )
    process_log = LOG_DIR / f"agent-{run_id}-{iteration:02d}.log"
    write_process_log(process_log, result)
    append_event(
        event_log,
        "agent_finished",
        iteration=iteration,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        log=str(process_log.relative_to(ROOT)),
    )
    return result
 
 
def run_loop(config: LoopConfig) -> int:
    STATE_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
 
    run_id = make_run_id()
    event_log = LOG_DIR / f"controller-{run_id}.jsonl"
    started_monotonic = time.perf_counter()
    state: dict[str, object] = {
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "iterations_used": 0,
        "max_iterations": config.max_iterations,
        "verifier_runs": 0,
        "last_verdict": None,
        "terminal_reason": None,
        "event_log": str(event_log.relative_to(ROOT)),
        "config": asdict(config),
    }
    save_state(state)
    append_event(event_log, "run_started", config=asdict(config))
 
    while True:
        remaining = remaining_wall_time(
            started_monotonic,
            config.max_wall_time_seconds,
        )
        if remaining <= 0:
            return finish(
                state,
                "BUDGET_EXHAUSTED",
                "wall-clock budget exhausted before verification",
                event_log,
            )
 
        verifier_sequence = int(state["verifier_runs"])
        verdict, report, verifier_process = run_verifier(
            config=config,
            run_id=run_id,
            sequence=verifier_sequence,
            remaining=remaining,
            event_log=event_log,
        )
        state["verifier_runs"] = verifier_sequence + 1
        state["last_verdict"] = verdict
        state["last_verifier_exit_code"] = verifier_process.exit_code
        save_state(state)
 
        if verdict == "PASS":
            return finish(
                state,
                "DONE",
                "fresh deterministic verifier evidence passed",
                event_log,
            )
 
        if verdict == "ERROR" or report is None:
            return finish(
                state,
                "VERIFIER_ERROR",
                "verifier could not produce trustworthy evidence",
                event_log,
            )
 
        iterations_used = int(state["iterations_used"])
        if iterations_used >= config.max_iterations:
            return finish(
                state,
                "BUDGET_EXHAUSTED",
                "maximum number of agent calls reached",
                event_log,
            )
 
        remaining = remaining_wall_time(
            started_monotonic,
            config.max_wall_time_seconds,
        )
        if remaining <= 0:
            return finish(
                state,
                "BUDGET_EXHAUSTED",
                "wall-clock budget exhausted before agent call",
                event_log,
            )
 
        iteration = iterations_used + 1
        task_packet = build_task_packet(
            run_id=run_id,
            iteration=iteration,
            config=config,
            verifier_report=report,
        )
        state["iterations_used"] = iteration
        state["last_task_packet"] = str(task_packet.relative_to(ROOT))
        save_state(state)
 
        agent_result = run_agent(
            config=config,
            run_id=run_id,
            iteration=iteration,
            task_packet=task_packet,
            remaining=remaining,
            event_log=event_log,
        )
        state["last_agent_exit_code"] = agent_result.exit_code
        state["last_agent_timed_out"] = agent_result.timed_out
        save_state(state)
 
        if agent_result.timed_out:
            return finish(
                state,
                "AGENT_TIMEOUT",
                "agent exceeded its per-call timeout",
                event_log,
            )
 
        if agent_result.exit_code != 0:
            return finish(
                state,
                "AGENT_ERROR",
                f"agent exited with code {agent_result.exit_code}",
                event_log,
            )
 
 
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded outer agent loop")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "loop_config.json",
    )
    return parser.parse_args()
 
 
def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config.resolve())
        return run_loop(config)
    except ConfigError as exc:
        STATE_DIR.mkdir(exist_ok=True)
        state = {
            "status": "CONFIG_ERROR",
            "updated_at": utc_now(),
            "terminal_reason": str(exc),
        }
        RUN_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("TERMINAL STATE: CONFIG_ERROR")
        print(f"REASON: {exc}")
        return 2
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

## 附录 E. PowerShell 命令速查

| 目的 | 命令 |
| --- | --- |
| 进入仓库 | cd $HOME\\Desktop\\loop-engineering-training\\chapter02\\statkit-lab |
| 激活虚拟环境 | .\\.venv\\Scripts\\Activate.ps1 |
| 确认基础失败 | python scripts\\verify.py; $LASTEXITCODE |
| 运行闭环 | python scripts\\run_loop.py |
| 查看终态 | Get-Content state\\run_state.json |
| 查看最新任务包 | Get-ChildItem state\\`task-packet-*.json` \| Sort LastWriteTime -Desc \| Select -First 1 |
| 查看事件日志 | $s=Get-Content state\\run_state.json -Raw\|ConvertFrom-Json; Get-Content $s.event_log |
| 核对源码差异 | git diff -- src\\statkit\\normalize.py |
| 恢复业务缺陷 | git restore src\\statkit\\normalize.py |
| 恢复配置 | git restore loop_config.json |
| 检查所有脚本 | python -m ruff check src tests scripts |
| 提交本章 | git add AGENTS.md loop_config.json scripts\\mock_agent.py scripts\\run_loop.py |

---

[返回课程主页](../../README.md) · [← 上一章](./03-deterministic-verifier.md) · [下一章 →](./05-codex-cli-integration.md)
