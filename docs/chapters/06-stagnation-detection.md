# 第 06 章：停滞检测与失败签名

[返回课程主页](../../README.md) · [← 上一章](./05-codex-cli-integration.md) · [下一章 →](./07-protected-paths-and-diff-policy.md)

## 本章使用说明

第 04 章已经提供迭代预算、墙钟预算和单次调用超时；第 05 章把真实 Codex CLI 接入了 Action 层。这些机制能保证系统最终停止，却不能回答一个更关键的问题：代理连续失败时，下一轮是否仍值得执行。

> 本章核心命题：“尚未完成”不等于“应该再试一次”。只有在证据表明状态发生了有意义变化，或策略已经明确改变时，继续调用代理才有工程依据。

### 学习目标

**·** 能区分预算耗尽、进程超时、同一失败重复、工作区无变化和失败模式循环，不再把它们都归为“再试一次”。

**·**能从 verifier 的结构化报告构造稳定的 failure descriptor，并对其做规范化哈希。

**·**能解释为什么时间戳、耗时、绝对路径、行列号和随机标识不应直接进入失败签名。

**·**能升级 workspace fingerprint，使未跟踪文件的内容变化也进入证据。

**·**能维护 same_failure_repeats、no_change_rounds、signature history 和 progress observations。

**·**能检测 A→B→A→B 等周期性失败，并将其与单一失败重复区分。

**·**能把近期尝试摘要写回 task packet，要求代理改变问题表示，而不是原样重复。

**·**能运行 progressive、no-op、unrelated-edit、oscillating 和 slow agent 五类实验，并预测正确终态。

**·**能校准阈值，识别过早 STAGNATED 与过迟停止两类风险。

## 1. 为什么“重试”不是一种策略

一个有界循环即使不会无限运行，也可能在预算内重复同一错误。若每轮任务包、上下文、权限、模型和验证信号都没有变化，再次调用本质上是在重复同一个实验，而不是提出新的干预。概率性模型偶尔可能“碰巧换一种写法”，但这不是可依赖的控制逻辑。

### 1.1 四种表面相似、工程含义不同的失败

| 现象                                 | 观测证据                                 | 正确终态/动作                | 错误处理               |
| ------------------------------------ | ---------------------------------------- | ---------------------------- | ---------------------- |
| 代理进程没有返回                     | timed_out=true                           | AGENT_TIMEOUT；保存部分日志  | 不断放大单轮 timeout   |
| 代理返回 0，但仓库完全没变           | workspace fingerprint 相同               | 有限容忍后 STAGNATED         | 只看 agent exit code   |
| 仓库变了，但同一测试仍以同一原因失败 | fingerprint 变化、failure signature 相同 | 识别无效变更；改变策略或停止 | 把“有 diff”当作进展  |
| 失败在 A 和 B 之间来回切换           | signature history 呈周期                 | STAGNATED；要求新诊断/分解   | 因为每轮错误不同就继续 |
| 失败持续变化且逐步减少               | 失败集合/质量向量改善                    | 允许继续                     | 阈值过低导致过早停止   |

### 1.2 停止策略的目标不是节省 token，而是阻断错误放大

重复失败会诱发三类风险：第一，代理不断扩大 diff，试图绕开根因；第二，陈旧假设被写回上下文并强化；第三，人工看到“运行了很多轮”后产生虚假的可靠性感。停滞检测因此属于可靠性组件，不只是成本控制。

> 判断标准：成熟的 loop 不以“运行得久”为目标，而以“成功、失败、停滞、越权和不确定时采取不同且可审计的行为”为目标。

## 2. 把进展定义为可观测量

模型内部是否“想得更深入”无法由外部控制器可靠观测。控制器只能依据工件和机械证据判断进展。因此，本章将进展拆成两个彼此独立的轴：任务失败状态是否变化，以及仓库状态是否变化。

### 2.1 两轴观察模型

| 失败签名 | 工作区指纹 | 进展分类                         | 含义                               |
| -------- | ---------- | -------------------------------- | ---------------------------------- |
| 相同     | 相同       | NO_WORKSPACE_CHANGE              | 代理没有产生可观察修改，失败也没变 |
| 相同     | 不同       | SAME_FAILURE_DIFFERENT_WORKSPACE | 修改发生了，但没有触及可见根因     |
| 不同     | 不同       | DIFFERENT_FAILURE                | 状态变化；可能进步，也可能回归     |
| 周期重复 | 通常不同   | FAILURE_CYCLE_PERIOD_k           | 系统在有限失败状态间振荡           |
| PASS     | 任意       | DONE                             | 新鲜 verifier 通过，停止           |

### 2.2 “错误变了”不能自动解释为“进步了”

DIFFERENT_FAILURE 只表示观测状态改变。代理可能修复一个测试并破坏三个测试，也可能从功能错误转成 lint 错误。真正的质量改进需要更丰富的 progress vector，例如失败测试数量、阻塞严重度、覆盖率、性能退化和审查 findings 数量。本章先实现可靠的停滞下界：至少识别明显没有进步和明显循环。

**概念式 1　最小进展观测向量**

```text
O_k = (
  failure_signature_k,
  workspace_fingerprint_k,
  failing_check_count_k,
  iteration_k,
  elapsed_time_k
)
 
仅凭 O_k != O_{k-1} 不能证明质量提高；
但 O_k 长期相同，足以证明当前策略没有产生可观察进展。
```

## 3. 失败签名：从易变日志到稳定描述符

最粗糙的做法是直接对 verifier 完整 stdout 做 SHA-256。问题在于测试耗时、临时目录、绝对路径、行号、内存地址和运行顺序都可能变化，使同一根因每轮得到不同哈希。停滞检测因此需要先构造稳定 descriptor，再哈希。

### 3.1 应保留与应丢弃的信息

| 信息                              | 是否进入 descriptor   | 原因                                          |
| --------------------------------- | --------------------- | --------------------------------------------- |
| 失败检查名称（pytest/ruff/build） | 保留                  | 区分故障层级                                  |
| 失败测试 node id                  | 保留                  | 定位稳定验收单元                              |
| 异常类型与核心消息                | 保留                  | 区分 ZeroDivisionError、AssertionError 等根因 |
| 静态检查规则码和消息              | 保留                  | 例如 F821、E501                               |
| 绝对仓库路径                      | 替换为<ROOT></root>   | 不同机器路径不应改变语义                      |
| 耗时、时间戳、随机 run id         | 删除/占位             | 每轮天然变化                                  |
| 十六进制内存地址                  | 替换                  | 进程级噪声                                    |
| 行号与列号                        | 通常归一化            | 无关插入行不应制造新失败类型                  |
| 完整成功输出                      | 不进入失败 descriptor | 对失败类型没有判别价值                        |

### 3.2 先输出可读 descriptor，再计算哈希

不要只保存一个 64 位十六进制字符串。哈希适合快速比较，descriptor 才适合人类审计。run_state 中应同时保存 last_failure_signature 与 last_failure_descriptor。否则发生误判时无法知道控制器到底把哪些信息视为“相同”。

**代码 1　日志规范化函数**

```python
def normalize_failure_text(text: str) -> str:
    normalized = text.replace(str(ROOT), "<ROOT>").replace("\\", "/")
    normalized = re.sub(r"\x1b\[[0-9;]*m", "", normalized)
    normalized = re.sub(r"0x[0-9a-fA-F]+", "<HEX>", normalized)
    normalized = re.sub(r"\b\d+(?:\.\d+)?s\b", "<TIME>", normalized)
    normalized = re.sub(r"\bin \d+(?:\.\d+)? seconds?\b", "in <TIME>", normalized)
    normalized = re.sub(r":\d+:\d+:", ":<LINE>:<COL>:", normalized)
    normalized = re.sub(r"\r\n?", "\n", normalized)
    lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    return "\n".join(lines[-120:])
```

**代码 2　构造 descriptor 与稳定签名**

```python
def failure_descriptor(report: dict[str, object]) -> dict[str, object]:
    checks = report.get("checks")
    failures: list[dict[str, object]] = []
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict) or item.get("status") == "PASS":
                continue
            stdout = str(item.get("stdout", ""))
            stderr = str(item.get("stderr", ""))
            combined = normalize_failure_text("\n".join([stdout, stderr]))
            tokens: list[str] = []
            for line in combined.splitlines():
                stripped = line.strip()
                if stripped.startswith("FAILED "):
                    tokens.append(stripped)
                elif stripped.startswith("E   "):
                    tokens.append(stripped)
                elif re.match(r"^.+:<LINE>:<COL>: [A-Z]\d{3} ", stripped):
                    tokens.append(stripped)
            if not tokens:
                tokens = combined.splitlines()[-20:]
            failures.append(
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "exit_code": item.get("exit_code"),
                    "tokens": tokens,
                }
            )
 
    failures.sort(key=lambda item: str(item.get("name")))
    return {"verdict": report.get("verdict"), "failures": failures}
 
 
def failure_signature(report: dict[str, object]) -> tuple[str, dict[str, object]]:
    descriptor = failure_descriptor(report)
    canonical = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return signature, descriptor
```

### 3.3 为什么不能完全依赖正则提取

本章代码针对 pytest 与 Ruff 提取高价值 token，并保留一个通用 fallback。真实生产系统应让 verifier 直接输出结构化失败对象，例如 failed_test_ids、error_class、rule_codes，而不是让 Controller 长期解析人类日志。日志解析是过渡方案，不是最理想协议。

> 压力测试：若你在 pytest 输出格式升级后无法稳定提取失败，问题不应通过“放宽到整段文本哈希”解决；应升级 verifier 的结构化 schema。

## 4. 工作区指纹：识别真正的零修改

failure signature 回答“失败是否相同”；workspace fingerprint 回答“仓库可观察状态是否改变”。两者不能互相替代。代理可能改了 README、注释或无关文件，但测试根因不变；也可能完全没写文件。

### 4.1 第 03 章指纹的一个隐藏缺陷

原 verifier 用 revision + git status + git diff 构造指纹。对于未跟踪文件，git status 只记录“?? filename”，不会包含文件内容。同一个未跟踪文件连续追加内容时，status 字符串不变，指纹会错误地认为工作区没有变化。

**反例 1　未跟踪文件内容变化但 status 不变**

```text
第一次：?? attempt-notes.txt
第二次：?? attempt-notes.txt
 
文件内容已经从 1 行变为 2 行，
但仅使用 status 文本时，两次 fingerprint 输入相同。
```

### 4.2 把未跟踪文件内容哈希纳入证据

**代码 3　升级 scripts/verify.py 的 workspace `evidence**`

```python
def untracked_file_evidence() -> list[dict[str, str]]:
    output = read_git(["ls-files", "--others", "--exclude-standard", "-z"])
    paths = [item for item in output.split("\x00") if item]
    evidence: list[dict[str, str]] = []
    for relative in sorted(paths):
        path = ROOT / relative
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            evidence.append({"path": relative, "sha256": digest})
    return evidence
 
 
def workspace_evidence() -> dict[str, object]:
    revision = read_git(["rev-parse", "HEAD"])
    status = read_git(["status", "--porcelain=v1", "--untracked-files=all"])
    diff = read_git(["diff", "--binary", "HEAD"])
    untracked = untracked_file_evidence()
    fingerprint_input = json.dumps(
        {
            "revision": revision,
            "status": status,
            "diff": diff,
            "untracked": untracked,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
 
    return {
        "revision": revision,
        "workspace_clean": not bool(status),
        "status_porcelain": status.splitlines(),
        "untracked_files": untracked,
        "workspace_fingerprint": hashlib.sha256(
            fingerprint_input.encode("utf-8")
        ).hexdigest(),
    }
```

生产环境还应限制未跟踪文件大小与数量，避免对大型数据集做全量哈希；可使用允许目录、大小上限、Git LFS 指针或 artifact manifest。本实验仓库很小，因此直接读取文件字节。

### 4.3 不要让控制器自己的日志污染指纹

logs/ 与 state/ 必须在 .gitignore 中。否则每次 verifier 写报告都会改变工作区，NO_WORKSPACE_CHANGE 永远不可能出现。可观察性工件应持久化，但它们不应被错误地计入“代理修改了任务仓库”。

## 5. 扩展配置、状态与终态

### 5.1 新增 STAGNATED 与五个策略参数

**代码 4　`loop_config.json**`

```json
{
  "max_iterations": 5,
  "max_same_failure_repeats": 2,
  "max_no_change_rounds": 2,
  "max_cycle_period": 3,
  "cycle_repetitions": 2,
  "history_limit": 12,
  "max_wall_time_seconds": 1800,
  "verifier_timeout_seconds": 120,
  "agent_timeout_seconds": 120,
  "verifier_command": [
    "{python}",
    "scripts/verify.py"
  ],
  "agent_command": [
    "{python}",
    "scripts/progressive_agent.py",
    "--task-packet",
    "{task_packet}"
  ]
}
```

| 参数                     | 含义                                  | 实验值 | 风险                                   |
| ------------------------ | ------------------------------------- | ------ | -------------------------------------- |
| max_same_failure_repeats | 基线之后允许同一签名重复多少次        | 2      | 过小会误杀慢进展；过大浪费预算         |
| max_no_change_rounds     | 允许连续多少次 agent 调用不改变工作区 | 2      | 诊断型代理可能首轮不写文件             |
| max_cycle_period         | 最多检测多长的循环周期                | 3      | 过大增加状态需求，且可能误识别偶然模式 |
| cycle_repetitions        | 同一周期至少重复几次才判定            | 2      | 2 次灵敏但更易误报；3 次更保守         |
| history_limit            | 保留多少个观测对象                    | 12     | 防止 state 和 task packet 无限增长     |

### 5.2 为什么计数叫 repeats，而不是 count

基线第一次观察到失败 A 时，重复次数是 0；代理调用后仍为 A，重复次数才变为 1。这种定义让 max_same_failure_repeats=2 的含义清楚：允许两次失败代理尝试，第三次验证相同后停止。

**示例 1　重复计数的 `off-by-one**`

```text
验证序列：A  →  A  →  A
agent 调用：  1      2
repeats：  0      1      2
 
阈值 = 2 时，在第二次失败代理调用后进入 STAGNATED。
```

### 5.3 run_state 中新增的审计字段

| 字段                       | 用途                                             |
| -------------------------- | ------------------------------------------------ |
| last_failure_signature     | 快速比较最新失败类型                             |
| last_failure_descriptor    | 供人审计签名具体包含什么                         |
| same_failure_repeats       | 连续同签名重复次数                               |
| last_workspace_fingerprint | 比较仓库是否发生可观察变化                       |
| no_change_rounds           | 连续零修改 agent 回合数                          |
| failure_signature_history  | 循环检测的最小历史                               |
| detected_cycle_period      | 若存在，记录周期长度                             |
| last_progress_class        | 最新一轮的解释性分类                             |
| progress_observations      | 绑定签名、指纹、迭代和 descriptor 的近期审计记录 |

## 6. 实现进展分类器

update_progress_state 是本章核心。它不决定终态，而是把本轮 verifier 结果转换成稳定状态。终态策略另由 stagnation_reason 读取这些状态。分离“观测更新”和“策略判定”有利于单元测试与阈值调整。

**代码 5　`update_progress_state**`

```python
def update_progress_state(
    state: dict[str, object],
    report: dict[str, object],
    config: LoopConfig,
) -> dict[str, object]:
    signature, descriptor = failure_signature(report)
    fingerprint = workspace_fingerprint(report)
    previous_signature = state.get("last_failure_signature")
    previous_fingerprint = state.get("last_workspace_fingerprint")
    agent_calls = int(state.get("iterations_used", 0))
 
    same_failure_repeats = int(state.get("same_failure_repeats", 0))
    if previous_signature == signature:
        same_failure_repeats += 1
    else:
        same_failure_repeats = 0
 
    no_change_rounds = int(state.get("no_change_rounds", 0))
    if agent_calls > 0 and previous_fingerprint == fingerprint:
        no_change_rounds += 1
    elif previous_fingerprint is not None:
        no_change_rounds = 0
 
    history = list(state.get("failure_signature_history", []))
    history.append(signature)
    history = history[-config.history_limit :]
    cycle_period = detect_repeating_cycle(
        history,
        max_period=config.max_cycle_period,
        repetitions=config.cycle_repetitions,
    )
 
    if agent_calls == 0:
        progress_class = "BASELINE_FAILURE"
    elif previous_fingerprint == fingerprint:
        progress_class = "NO_WORKSPACE_CHANGE"
    elif previous_signature == signature:
        progress_class = "SAME_FAILURE_DIFFERENT_WORKSPACE"
    elif cycle_period is not None:
        progress_class = f"FAILURE_CYCLE_PERIOD_{cycle_period}"
    else:
        progress_class = "DIFFERENT_FAILURE"
 
    observation = {
        "verifier_run": int(state.get("verifier_runs", 0)),
        "after_agent_iteration": agent_calls,
        "failure_signature": signature,
        "workspace_fingerprint": fingerprint,
        "progress_class": progress_class,
        "descriptor": descriptor,
    }
    observations = list(state.get("progress_observations", []))
    observations.append(observation)
    observations = observations[-config.history_limit :]
 
    state.update(
        {
            "last_failure_signature": signature,
            "last_failure_descriptor": descriptor,
            "same_failure_repeats": same_failure_repeats,
            "last_workspace_fingerprint": fingerprint,
            "no_change_rounds": no_change_rounds,
            "failure_signature_history": history,
            "detected_cycle_period": cycle_period,
            "last_progress_class": progress_class,
            "progress_observations": observations,
        }
    )
    return observation
```

### 6.1 分类顺序为什么不能随意交换

若 fingerprint 相同，应优先标记 NO_WORKSPACE_CHANGE；否则同一失败也会被笼统归入 SAME_FAILURE_DIFFERENT_WORKSPACE。若检测到周期，应在“不同失败”之后把本轮提升为 FAILURE_CYCLE_PERIOD_k。分类是解释层，真正停止仍由策略层执行。

### 6.2 历史必须有上限

progress_observations 可能包含失败 token 和 descriptor。若不截断，长任务会让 run_state 和下一轮 task packet 线性膨胀。本章只保留 history_limit 个观测，并在 task packet 中进一步只注入最近 5 个。完整原始 verifier 报告仍单独归档。

## 7. 同一失败与无变化停止策略

**代码 6　`stagnation_reason**`

```python
def stagnation_reason(state: dict[str, object], config: LoopConfig) -> str | None:
    repeats = int(state.get("same_failure_repeats", 0))
    if repeats >= config.max_same_failure_repeats and config.max_same_failure_repeats > 0:
        return (
            "same normalized failure signature repeated "
            f"{repeats} times after the baseline observation"
        )
 
    no_change = int(state.get("no_change_rounds", 0))
    if no_change >= config.max_no_change_rounds and config.max_no_change_rounds > 0:
        return f"workspace fingerprint did not change for {no_change} agent rounds"
 
    cycle_period = state.get("detected_cycle_period")
    if isinstance(cycle_period, int):
        return (
            f"failure signatures entered a repeating cycle with period {cycle_period} "
            f"for {config.cycle_repetitions} repetitions"
        )
    return None
```

### 7.1 为什么先检查 same failure，再检查 no change

同一失败签名重复是更一般的停滞：它既覆盖“完全没改”，也覆盖“改了很多无关内容”。若两条规则同时满足，优先返回 same failure 能更准确地指出任务层根因没有改变。no change 则是对零动作的更具体补充。

### 7.2 阈值为 0 的语义

本章实现把 0 解释为关闭该检测器，而不是“一次也不允许”。这便于做对照实验，但生产配置应谨慎开放。若关闭所有停滞检测，系统仍会被 max_iterations 或 wall-clock budget 停止，只是终态会退化为 BUDGET_EXHAUSTED。

> 不要混淆：STAGNATED 表示当前问题表示和策略没有产生可观察进展；BUDGET_EXHAUSTED 只表示资源上限已到。前者应触发策略改变，后者未必说明为什么失败。

## 8. 检测 A↔B 循环和有限状态振荡

只比较相邻签名无法识别 A→B→A→B。此时每轮都“与上一轮不同”，但系统实际上在两个坏状态间振荡。这在代理反复加入/删除补丁、切换两种错误假设时很常见。

**代码 7　有限周期检测**

```python
def detect_repeating_cycle(
    history: list[str],
    *,
    max_period: int,
    repetitions: int,
) -> int | None:
    for period in range(2, max_period + 1):
        needed = period * repetitions
        if len(history) < needed:
            continue
        tail = history[-needed:]
        pattern = tail[:period]
        if all(
            tail[index : index + period] == pattern
            for index in range(0, needed, period)
        ):
            return period
    return None
```

### 8.1 周期检测示例

| 签名历史尾部     | period=2, repetitions=2           | 判定              |
| ---------------- | --------------------------------- | ----------------- |
| A, B, A          | 长度不足 4                        | 不停止            |
| A, B, A, B       | [A,B] 重复两次                    | STAGNATED         |
| A, B, C, A, B, C | period=3 重复两次                 | STAGNATED         |
| A, A, A          | period 1 不由 cycle detector 处理 | 交给 same_failure |
| A, B, A, C       | 没有完整重复                      | 继续观察          |

### 8.2 为什么本章从 period=2 开始

周期 1 就是同一失败连续重复，已有更直接的 same_failure_repeats。分开处理能给出更清晰的 terminal_reason。真实系统还可检测非连续回归、例如 A→B→C→A，但那更像状态图分析，不能仅凭两次出现就认定周期。

## 9. 把尝试历史写回下一轮任务包

检测到一次相同失败后，不应立刻把同一 prompt 再发一遍。下一轮必须看到：失败签名已重复几次、工作区是否变化、最近采取过什么策略，以及控制器明确要求不要原样重复。

**代码 8　build_task_packet 的进展摘要**

```python
def build_task_packet(
    *,
    run_id: str,
    iteration: int,
    config: LoopConfig,
    verifier_report: dict[str, object],
    state: dict[str, object],
) -> Path:
    observations = list(state.get("progress_observations", []))[-5:]
    packet = {
        "run_id": run_id,
        "iteration": iteration,
        "remaining_iterations_after_this_call": config.max_iterations - iteration,
        "goal": GOAL_PATH.read_text(encoding="utf-8"),
        "repository_instructions": INSTRUCTIONS_PATH.read_text(encoding="utf-8"),
        "latest_verifier_report": verifier_report,
        "progress_summary": {
            "same_failure_repeats": state.get("same_failure_repeats", 0),
            "no_change_rounds": state.get("no_change_rounds", 0),
            "detected_cycle_period": state.get("detected_cycle_period"),
            "recent_observations": observations,
        },
        "controller_rule": (
            "Do not repeat an unchanged strategy. Use the latest failure signature and "
            "recent attempts to make the smallest root-cause change. Your completion "
            "claim is advisory; the controller will rerun the verifier."
        ),
    }
    path = STATE_DIR / f"task-packet-{run_id}-{iteration:02d}.json"
    path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
```

### 9.1 摘要而不是完整历史

任务包只注入最近 5 个 observation，而不是整段 JSONL、全部工具事件和全部模型输出。控制器需要帮助代理避免重复，不是让上下文被历史淹没。完整日志继续留在 logs/ 和 state/ 供审计。

### 9.2 “改变策略”必须可操作

只写“不要重复”仍然太抽象。更成熟的路由策略应在重复失败时改变至少一个变量：缩小目标、增加诊断测试、切换只读诊断代理、提供新观测、使用另一模型，或升级人工。第 06 章先实现 stop；后续生产架构可实现自动 strategy escalation。

## 10. 接入主循环：判定顺序为何重要

停滞判定必须发生在“verifier=FAIL 之后、下一次 agent 调用之前”。若放在 agent 之后，系统会多浪费一轮；若放在 verifier 之前，就没有新鲜失败证据。DONE 仍然优先于所有停滞判断。

**代码 9　主循环中的插入位置**

```text
verdict, report = run_verifier(...)
 
if verdict == "PASS":
    return DONE
if verdict == "ERROR":
    return VERIFIER_ERROR
 
observation = update_progress_state(state, report, config)
reason = stagnation_reason(state, config)
if reason is not None:
    return STAGNATED
 
if iterations_used >= max_iterations:
    return BUDGET_EXHAUSTED
 
build_task_packet(...)
run_agent(...)
```

### 10.1 DONE 为什么必须先判定

假设前两轮失败签名相同，第三轮 agent 修复后 verifier=PASS。若控制器先读取旧的 same_failure_repeats 并判 STAGNATED，就会拒绝真实成功。因此，PASS 是最新事实，应立即覆盖之前的失败历史。

### 10.2 STAGNATED 与预算谁先判定

本章先判停滞，再判迭代预算。这样在最后一次验证同时满足“重复失败”和“调用上限”时，系统给出更具诊断价值的 STAGNATED。如果组织更关心配额审计，也可反过来，但必须在规范中固定，不要让不同实现随意变化。

## 11. 手把手建立第 06 章实验环境

### 11.1 创建分支并恢复失败基线

**操作 1　从第 05 章继续**

```powershell
cd $HOME\Desktop\loop-engineering-training\chapter02\statkit-lab
.\.venv\Scripts\Activate.ps1
 
git status --short
git switch chapter05-codex-cli
git switch -c chapter06-stagnation
```

先把 normalize.py 恢复成常量向量除零版本。第 06 章所有实验必须从 verifier=FAIL 开始，否则无法观察失败签名。

**操作 2　恢复缺陷并确认 `verifier**`

```powershell
git restore src\statkit\normalize.py
python scripts\verify.py
$LASTEXITCODE
```

### 11.2 新增实验文件

**操作 3　创建脚本与配置**

```powershell
New-Item scripts\no_op_agent.py -ItemType File
New-Item scripts\unrelated_edit_agent.py -ItemType File
New-Item scripts\progressive_agent.py -ItemType File
New-Item scripts\oscillating_agent.py -ItemType File
New-Item scripts\slow_agent.py -ItemType File
New-Item scripts\reset_chapter06.py -ItemType File
 
New-Item config-no-op.json -ItemType File
New-Item config-unrelated.json -ItemType File
New-Item config-cycle.json -ItemType File
New-Item config-timeout.json -ItemType File
```

**目录 1　本章新增或修改的文件**

```text
statkit-lab/
├─ loop_config.json                 # 默认 progressive happy path
├─ config-no-op.json
├─ config-unrelated.json
├─ config-cycle.json
├─ config-timeout.json
├─ scripts/
│  ├─ run_loop.py                   # 新增 signature / stagnation / cycle
│  ├─ verify.py                     # 未跟踪文件内容进入 fingerprint
│  ├─ progressive_agent.py
│  ├─ no_op_agent.py
│  ├─ unrelated_edit_agent.py
│  ├─ oscillating_agent.py
│  ├─ slow_agent.py
│  └─ reset_chapter06.py
└─ state/run_state.json             # 新增 progress observations
```

### 11.3 每次实验前重置

**代码 10　`reset_chapter06.py**`

```python
from __future__ import annotations
 
import shutil
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "statkit" / "normalize.py"
 
BUG = '''from __future__ import annotations
 
from collections.abc import Sequence
 
 
def min_max_normalize(values: Sequence[float]) -> list[float]:
    """Scale values into the inclusive range [0.0, 1.0].
 
    Empty input returns an empty list. Constant input is expected to return
    a list of zeros, but the starter implementation intentionally contains
    a division-by-zero bug for that case.
    """
    if not values:
        return []
 
    low = min(values)
    high = max(values)
    span = high - low
    return [(value - low) / span for value in values]
'''
 
 
def main() -> int:
    TARGET.write_text(BUG, encoding="utf-8")
    for relative in [
        "attempt-notes.txt",
        "state/progressive-agent-count.txt",
        "state/oscillating-agent-count.txt",
    ]:
        (ROOT / relative).unlink(missing_ok=True)
    for directory in [ROOT / "logs", ROOT / "state"]:
        directory.mkdir(exist_ok=True)
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    print("CHAPTER06_RESET: OK")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

重置脚本恢复 starter bug，删除 agent 计数器、attempt-notes、旧 state 和 logs。若不重置，progressive/oscillating agent 的计数会延续，你得到的终态将不可复现。

**操作 4　验证代码质量与基线**

```powershell
python scripts\reset_chapter06.py
python -m ruff check src tests scripts
python scripts\verify.py
```

预期：Ruff PASS，pytest FAIL，统一 VERDICT: FAIL。

## 12. 成功实验：允许一次失败，但最终 DONE

progressive_agent 第一次只改 docstring，失败签名不变；第二次才加入 constant-vector guard。该实验验证控制器不会在一次重复后过早停止。

**代码 11　`progressive_agent.py**`

```python
from __future__ import annotations
 
import argparse
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
COUNTER = ROOT / "state" / "progressive-agent-count.txt"
TARGET = ROOT / "src" / "statkit" / "normalize.py"
 
BUG = '''from __future__ import annotations
 
from collections.abc import Sequence
 
 
def min_max_normalize(values: Sequence[float]) -> list[float]:
    """Scale values into the inclusive range [0.0, 1.0]."""
    if not values:
        return []
 
    low = min(values)
    high = max(values)
    span = high - low
    return [(value - low) / span for value in values]
'''
 
FIX = '''from __future__ import annotations
 
from collections.abc import Sequence
 
 
def min_max_normalize(values: Sequence[float]) -> list[float]:
    """Scale values into the inclusive range [0.0, 1.0]."""
    if not values:
        return []
 
    low = min(values)
    high = max(values)
    span = high - low
    if span == 0:
        return [0.0 for _ in values]
 
    return [(value - low) / span for value in values]
'''
 
 
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-packet", type=Path, required=True)
    parser.parse_args()
    count = int(COUNTER.read_text(encoding="utf-8")) if COUNTER.exists() else 0
    count += 1
    COUNTER.write_text(str(count), encoding="utf-8")
 
    if count == 1:
        TARGET.write_text(BUG.replace('"""Scale', '"""Normalize'), encoding="utf-8")
        print("ACTION: changed wording only; bug remains")
    else:
        TARGET.write_text(FIX, encoding="utf-8")
        print("ACTION: applied constant-vector guard")
    print("AGENT_CLAIM: candidate_ready")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

### 12.1 运行默认配置

**操作 5　执行渐进式 happy `path**`

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config loop_config.json
Get-Content state\run_state.json
```

**预期摘要**

```text
第 0 次 verifier：FAIL，progress=BASELINE_FAILURE
第 1 次 agent：只改说明文字
第 1 次复验：FAIL，same_failure_repeats=1
第 2 次 agent：加入 span == 0 分支
第 2 次复验：PASS
TERMINAL STATE: DONE
iterations_used: 2
```

### 12.2 为什么阈值不是越小越安全

若把 max_same_failure_repeats 改为 1，控制器会在第一次无效尝试后 STAGNATED，第二次真正修复没有机会执行。停滞阈值是在“浪费预算”和“允许渐进诊断”之间做权衡，必须通过任务集校准，而不是凭感觉设置。

## 13. 四组停滞与超时破坏实验

每个实验先预测终态，再运行。不要只看终端最后一句；必须检查 run_state 中的重复计数、指纹、历史和 terminal_reason。

### 13.1 实验 A：代理返回 0，但完全不改仓库

**代码 12　`no_op_agent.py**`

```python
from __future__ import annotations
 
import argparse
from pathlib import Path
 
 
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-packet", type=Path, required=True)
    args = parser.parse_args()
    print(f"READ_TASK_PACKET: {args.task_packet}")
    print("AGENT_CLAIM: candidate_ready")
    print("ACTION: no repository changes")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

**操作 6　运行 no-op 场景**

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-no-op.json
Get-Content state\run_state.json
```

预期：两次 agent 都 exit=0，但 fingerprint 不变、failure signature 相同；终态 STAGNATED。这直接证明“进程成功”和“任务进展”是不同变量。

### 13.2 实验 B：每轮都改无关文件

**代码 13　`unrelated_edit_agent.py**`

```python
from __future__ import annotations
 
import argparse
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "attempt-notes.txt"
 
 
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-packet", type=Path, required=True)
    parser.parse_args()
    with NOTES.open("a", encoding="utf-8") as handle:
        handle.write("another unrelated attempt\n")
    print("ACTION: edited attempt-notes.txt only")
    print("AGENT_CLAIM: candidate_ready")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

**操作 7　运行无关修改场景**

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-unrelated.json
Get-Content state\run_state.json
```

预期：workspace fingerprint 每轮变化，progress_class 为 SAME_FAILURE_DIFFERENT_WORKSPACE；same_failure_repeats 达阈值后 STAGNATED。该实验用于反驳“有 diff 就有进展”。

### 13.3 实验 C：失败模式在 A 与 B 之间振荡

**代码 14　`oscillating_agent.py**`

```python
from __future__ import annotations
 
import argparse
from pathlib import Path
 
ROOT = Path(__file__).resolve().parents[1]
COUNTER = ROOT / "state" / "oscillating-agent-count.txt"
TARGET = ROOT / "src" / "statkit" / "normalize.py"
 
MODE_A = '''from __future__ import annotations
 
from collections.abc import Sequence
 
 
def min_max_normalize(values: Sequence[float]) -> list[float]:
    """Scale values into the inclusive range [0.0, 1.0]."""
    if not values:
        return []
    low = min(values)
    high = max(values)
    span = high - low
    return [(value - low) / span for value in values]
'''
 
MODE_B = '''from __future__ import annotations
 
from collections.abc import Sequence
 
 
def min_max_normalize(values: Sequence[float]) -> list[float]:
    """Incorrectly return zeros for every non-empty vector."""
    if not values:
        return []
    return [0.0 for _ in values]
'''
 
 
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-packet", type=Path, required=True)
    parser.parse_args()
    count = int(COUNTER.read_text(encoding="utf-8")) if COUNTER.exists() else 0
    count += 1
    COUNTER.write_text(str(count), encoding="utf-8")
    if count % 2 == 1:
        TARGET.write_text(MODE_B, encoding="utf-8")
        print("ACTION: switched to failure mode B")
    else:
        TARGET.write_text(MODE_A, encoding="utf-8")
        print("ACTION: switched to failure mode A")
    print("AGENT_CLAIM: candidate_ready")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

**操作 8　运行周期场景**

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-cycle.json
Get-Content state\run_state.json
```

**预期签名历史**

```text
A = constant vector → ZeroDivisionError
B = every non-empty vector → wrong assertion
 
history: A, B, A, B
cycle_period: 2
TERMINAL STATE: STAGNATED
```

### 13.4 实验 D：代理单轮超时

**代码 15　`slow_agent.py**`

```python
from __future__ import annotations
 
import argparse
import time
from pathlib import Path
 
 
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-packet", type=Path, required=True)
    parser.parse_args()
    print("ACTION: entering a deliberately slow call", flush=True)
    time.sleep(5)
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
```

**操作 9　运行 timeout 场景**

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-timeout.json
Get-Content state\run_state.json
```

config-timeout.json 将 agent_timeout_seconds 设为 0.2，而 slow_agent 睡眠 5 秒。预期终态 AGENT_TIMEOUT，iterations_used=1，last_agent_timed_out=true。它不是 STAGNATED，因为尚未获得下一轮 verifier 证据。

### 13.5 对照实验：关闭停滞检测

复制 config-no-op.json，把 max_same_failure_repeats 和 max_no_change_rounds 改为 0，同时令 max_iterations=3。运行后预期 BUDGET_EXHAUSTED。这个对照证明停滞检测提供了比预算终止更具体的诊断。

**配置片段**

```text
"max_iterations": 3,
"max_same_failure_repeats": 0,
"max_no_change_rounds": 0
```

## 14. 阈值校准、误判风险与诊断矩阵

### 14.1 两类误判

| 误判                        | 表现                           | 原因                                                       | 修正                                      |
| --------------------------- | ------------------------------ | ---------------------------------------------------------- | ----------------------------------------- |
| 假停滞（false stagnation）  | 本来可在下一轮完成，却提前停止 | 阈值过低；签名过度归一化；任务天然分阶段                   | 提高阈值；加入质量向量；改变任务分解      |
| 漏停滞（missed stagnation） | 反复失败仍持续调用             | 日志噪声导致签名每轮不同；周期窗口太短；无关 diff 伪装进展 | 结构化 verifier；规范化噪声；加入循环检测 |

### 14.2 建议起点不是通用真理

| 任务类型          | same failure repeats 起点 | no change 起点 | 说明                             |
| ----------------- | ------------------------- | -------------- | -------------------------------- |
| 单函数边界 bug    | 1–2                      | 1              | 目标小、反馈快，不应多轮原样失败 |
| 跨模块重构        | 2–3                      | 1–2           | 可能先诊断和搭建接口             |
| 依赖/构建故障     | 2                         | 1              | 相同环境错误通常不会靠重试消失   |
| 性能优化          | 3+                        | 1–2           | 指标可能有噪声，应结合置信区间   |
| 外部服务/网络任务 | 不直接使用同一策略        | 按重试退避     | 应区分暂态基础设施错误与任务错误 |

### 14.3 暂态错误不应进入任务停滞签名

网络 502、包索引超时、CI runner 中断等属于基础设施层。它们应有独立、次数很少、带退避的 process retry；不能与“代码仍然测试失败”的 task iteration 混为一谈。否则 same_failure_repeats 会把服务故障误解释为实现停滞。

### 14.4 诊断矩阵

| run_state 现象                   | 最可能含义                         | 下一步                                           |
| -------------------------------- | ---------------------------------- | ------------------------------------------------ |
| NO_WORKSPACE_CHANGE 多次         | 代理未编辑、只输出建议或权限不足   | 检查 sandbox、task packet 和 agent final message |
| SAME_FAILURE_DIFFERENT_WORKSPACE | 修改未触及根因，或在无关范围内重构 | 缩小 diff；增加根因诊断；保护变更范围            |
| DIFFERENT_FAILURE 且失败数增加   | 回归，而非进步                     | 回滚候选；把质量向量加入策略                     |
| FAILURE_CYCLE_PERIOD_2           | 两个补丁/假设来回切换              | 冻结当前分支；启动独立诊断或人工复盘             |
| AGENT_TIMEOUT                    | 进程未在单轮 SLA 内结束            | 读取部分事件；缩小任务；不要直接加无限 timeout   |
| BUDGET_EXHAUSTED 且无 stagnation | 阈值关闭或失败持续变化             | 检查是否真实进步、回归或签名噪声                 |

> 批判性提醒：“错误每轮都不一样”不一定说明系统在探索；也可能说明代理不断制造新回归。停滞检测只能识别明显下界，不能替代质量评估。

## 15. 提交、验收和课后自测

### 15.1 检查证据文件

**操作 10　查看状态与事件**

```powershell
Get-Content state\run_state.json
Get-ChildItem state\verify-*.json
Get-ChildItem logs\controller-*.jsonl
Get-Content logs\controller-*.jsonl | Select-String "progress_observed|terminal"
```

至少确认：terminal_reason 与实验一致；failure history 长度合理；descriptor 能读懂；no-change 和 same-failure 没有混淆；DONE 运行的最后 verifier exit code 为 0。

### 15.2 恢复默认配置并提交

**操作 11　最终质量检查**

```powershell
python scripts\reset_chapter06.py
python -m ruff check src tests scripts
python scripts\run_loop.py --config loop_config.json
python scripts\verify.py
 
git diff --check
git status --short
git diff -- tests goal.md AGENTS.md
 
git add scripts\run_loop.py scripts\verify.py `
        scripts\progressive_agent.py scripts\no_op_agent.py `
        scripts\unrelated_edit_agent.py scripts\oscillating_agent.py `
        scripts\slow_agent.py scripts\reset_chapter06.py `
        loop_config.json config-no-op.json config-unrelated.json `
        config-cycle.json config-timeout.json
 
git commit -m "chapter06: add stagnation and cycle detection"
```

### 15.3 本章验收清单

- [ ] 能解释 why retry 与 strategy change 的差异。
- [ ] 能从同一 pytest 根因得到稳定且可读的 failure descriptor。
- [ ] 能说明完整日志哈希为什么会产生假“不同失败”。
- [ ] 能让未跟踪文件内容变化进入 workspace fingerprint。
- [ ] 能解释 same_failure_repeats 的基线和 off-by-one 语义。
- [ ] 能区分 NO_WORKSPACE_CHANGE 与 SAME_FAILURE_DIFFERENT_WORKSPACE。
- [ ] 能从 A,B,A,B 历史检测 period=2 循环。
- [ ] 能把近期 observations 写入 task packet，而不注入全部历史。
- [ ] 能运行 progressive 场景并得到 DONE。
- [ ] 能运行 no-op、unrelated 和 cycle 场景并得到 STAGNATED。
- [ ] 能运行 slow agent 并得到 AGENT_TIMEOUT。
- [ ] 能解释 STAGNATED 与 BUDGET_EXHAUSTED 的不同后续动作。
- [ ] 能指出“不同失败”仍可能是回归，不能自动视作进步。

> 下一章预告：第 07 章将把“绝不能做什么”从提示词升级为确定性策略：保护 tests、验证脚本、依赖配置和受限路径，检测越权修改并进入 POLICY_VIOLATION。

## 附录 A　核心文件职责

| 文件                       | 本章变化                             | 职责                           |
| -------------------------- | ------------------------------------ | ------------------------------ |
| scripts/run_loop.py        | 新增签名、进展状态、循环与 STAGNATED | 外层控制器和停止策略           |
| scripts/verify.py          | 未跟踪文件内容哈希                   | 提供可信 workspace fingerprint |
| loop_config.json           | 新增五个停滞参数                     | 默认渐进成功实验               |
| config-no-op.json          | 无修改 agent                         | 验证 no-change 与 same-failure |
| config-unrelated.json      | 无关文件持续变化                     | 验证 diff 不等于进展           |
| config-cycle.json          | A/B 失败振荡                         | 验证周期检测                   |
| config-timeout.json        | 极短 agent timeout                   | 验证进程超时终态               |
| scripts/reset_chapter06.py | 可重复重置                           | 避免计数器与状态污染实验       |

## 附录 B　关键配置对照

**B.1 no-op 配置**

```json
{
  "max_iterations": 8,
  "max_same_failure_repeats": 2,
  "max_no_change_rounds": 2,
  "max_cycle_period": 3,
  "cycle_repetitions": 2,
  "history_limit": 12,
  "max_wall_time_seconds": 1800,
  "verifier_timeout_seconds": 120,
  "agent_timeout_seconds": 120,
  "verifier_command": ["{python}", "scripts/verify.py"],
  "agent_command": ["{python}", "scripts/no_op_agent.py", "--task-packet", "{task_packet}"]
}
```

**B.2 unrelated-edit 配置**

```json
{
  "max_iterations": 8,
  "max_same_failure_repeats": 2,
  "max_no_change_rounds": 4,
  "max_cycle_period": 3,
  "cycle_repetitions": 2,
  "history_limit": 12,
  "max_wall_time_seconds": 1800,
  "verifier_timeout_seconds": 120,
  "agent_timeout_seconds": 120,
  "verifier_command": ["{python}", "scripts/verify.py"],
  "agent_command": ["{python}", "scripts/unrelated_edit_agent.py", "--task-packet", "{task_packet}"]
}
```

**B.3 cycle 配置**

```json
{
  "max_iterations": 8,
  "max_same_failure_repeats": 8,
  "max_no_change_rounds": 8,
  "max_cycle_period": 3,
  "cycle_repetitions": 2,
  "history_limit": 12,
  "max_wall_time_seconds": 1800,
  "verifier_timeout_seconds": 120,
  "agent_timeout_seconds": 120,
  "verifier_command": ["{python}", "scripts/verify.py"],
  "agent_command": ["{python}", "scripts/oscillating_agent.py", "--task-packet", "{task_packet}"]
}
```

**B.4 timeout 配置**

```json
{
  "max_iterations": 3,
  "max_same_failure_repeats": 3,
  "max_no_change_rounds": 3,
  "max_cycle_period": 3,
  "cycle_repetitions": 2,
  "history_limit": 12,
  "max_wall_time_seconds": 30,
  "verifier_timeout_seconds": 120,
  "agent_timeout_seconds": 0.2,
  "verifier_command": ["{python}", "scripts/verify.py"],
  "agent_command": ["{python}", "scripts/slow_agent.py", "--task-packet", "{task_packet}"]
}
```

## 附录 C　PowerShell 命令速查

**速查 1　默认成功流程**

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config loop_config.json
Get-Content state\run_state.json
```

**速查 2　停滞与超时实验**

```powershell
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-no-op.json
 
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-unrelated.json
 
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-cycle.json
 
python scripts\reset_chapter06.py
python scripts\run_loop.py --config config-timeout.json
```

**速查 3　审计进展事件**

```powershell
Get-Content state\run_state.json
Get-Content logs\controller-*.jsonl | Select-String "progress_observed"
Get-Content logs\controller-*.jsonl | Select-String "terminal"
```

## 附录 D　课后自测

**1. **为什么对完整 verifier stdout 直接哈希，会把同一根因误判成不同失败？

**2. **workspace fingerprint 改变而 failure signature 不变，说明了什么？

**3. **为什么第一次观察到失败 A 时 same_failure_repeats 应为 0？

**4. **A,B,A,B 和 A,A,A 应由同一个检测器处理吗？为什么？

**5. **为什么 AGENT_TIMEOUT 不能直接判为 STAGNATED？

**6. **DIFFERENT_FAILURE 为什么不能自动视作进展？

**7. **阈值过低和过高分别产生什么风险？

**8. **为什么 logs/ 和 state/ 不应进入 workspace fingerprint？

### 参考答案要点

**• **完整输出包含耗时、路径、行号和随机标识等易变噪声；应先提取稳定语义 token，再哈希。

**• **代理确实改了仓库，但可见失败根因没有变化；可能是无关修改或未触及根因。

**• **repeats 表示基线之后的重复次数；基线只是首次观测，不是一次失败尝试的重复。

**• **A,A,A 是 period 1，由 same-failure 处理；A,B,A,B 是 period 2，由 cycle detector 处理，便于给出不同诊断。

**• **超时只说明本次调用没有完成，尚未获得新的 verifier 状态；它属于进程生命周期故障。

**• **错误改变可能来自真实进步，也可能来自新增回归；需要失败数量、严重度等质量向量判断方向。

**• **过低导致 false stagnation，阻断可完成任务；过高导致漏停滞、成本和错误累积。

**• **这些是控制器自身产生的审计工件，每轮天然变化；计入后会污染“代理是否修改任务仓库”的判断。

## 附录 E　课程依据

本章在《Loop Engineering：从零到可验证自治闭环》关于停止条件、预算、失败签名和 STAGNATED 终态的基础上，扩展为可运行实验：加入规范化 descriptor、未跟踪文件指纹、周期检测、进展分类和五类代理压力测试。

---

[返回课程主页](../../README.md) · [← 上一章](./05-codex-cli-integration.md) · [下一章 →](./07-protected-paths-and-diff-policy.md)
