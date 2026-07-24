# StatKit Loop Lab

这是新版主课程使用的贯穿实验。你会修复一个真实的 Python 函数，并逐步给它加上：

- 验收合同；
- 独立 Verifier；
- 有界 Controller；
- 受保护测试策略；
- 停滞检测；
- 结构化证据与命名终态。

## 三分钟运行

在本目录执行：

```powershell
python scripts/reset.py
python scripts/verify.py
python scripts/controller.py --worker fix
```

第一次验证应失败；Controller 调用一次修复 Worker 后，应进入 `DONE`。

如果已经安装并登录 Codex CLI，可以把模拟 Worker 换成真实代理：

```powershell
python scripts/reset.py
python scripts/controller.py --worker codex
```

Controller 仍然会独立执行 Policy Gate 和 Verifier；Codex 的最终回复不会直接变成
`DONE`。

## 三个故障实验

```powershell
# 1. Worker 什么都不做：系统应进入 STAGNATED
python scripts/reset.py
python scripts/controller.py --worker noop

# 2. Worker 篡改测试：系统应进入 POLICY_VIOLATION
python scripts/reset.py
python scripts/controller.py --worker reward-hacker

# 3. 不允许足够轮数：系统应进入 BUDGET_EXHAUSTED
python scripts/reset.py
python scripts/controller.py --worker noop --max-iterations 1 --stagnation-limit 5
```

运行证据位于 `artifacts/`，包括：

- `verification.json`：最近一次验证命令、退出码和工作区指纹；
- `policy_report.json`：受保护文件检查结果；
- `ledger.jsonl`：按时间追加的控制事件；
- `run_state.json`：最终命名状态。

课程入口见[实践课索引](../../docs/course/README.md)。
