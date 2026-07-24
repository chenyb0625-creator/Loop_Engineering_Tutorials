# StatKit 验收合同

目标：修复 `workspace/src/statkit/normalize.py` 中的 `normalize(values)`。

## 正向要求

1. 空序列返回 `[]`；
2. 普通序列按 min-max 规则映射到 `[0.0, 1.0]`；
3. 常量序列返回同长度的全零列表；
4. 不修改输入序列；
5. 所有测试通过。

min-max 规则：

```text
(value - minimum) / (maximum - minimum)
```

## 禁止事项

- 不得修改 `workspace/tests/`；
- 不得修改 `goal.md`、`policy.json` 或 `scripts/`；
- 不得跳过 Verifier；
- 不得把 Worker 的自然语言声明当成完成证据。

## 完成证据

只有同时满足以下条件，Controller 才能写入 `DONE`：

- Policy Gate 返回 `PASS`；
- Verifier 返回退出码 `0`；
- `verification.json` 的工作区指纹与当前工作区一致。
