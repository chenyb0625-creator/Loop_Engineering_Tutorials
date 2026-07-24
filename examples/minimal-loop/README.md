# Minimal Loop

这是课程第 1 章的可运行版本，用一个故意不可靠的 Worker 演示
`AGENT_CLAIM: DONE` 为什么不能等于系统完成。

## 运行

在本目录执行：

```powershell
python reset.py
python controller.py
```

预期结果：

- Worker 会在每一轮错误地声明 `DONE`；
- Verifier 在 `value < target` 时持续返回退出码 `1`；
- Controller 只在 Verifier 返回 `0` 后写入终态 `DONE`；
- `run_state.json` 和 `loop.log` 保存机器可读终态与执行证据。

若要观察最小的 false-DONE：

```powershell
python reset.py
python worker.py
python verify.py
```

最后一个命令应输出 `VERDICT: FAIL`，即使 Worker 已经打印
`AGENT_CLAIM: DONE`。

完整解释见[第 1 章](../../docs/chapters/01-minimal-autonomous-loop.md)。
