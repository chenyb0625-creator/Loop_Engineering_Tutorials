# 第 02 章：建立真实 Python 项目与 Git 基线

[返回课程主页](../../README.md) · [← 上一章](./01-minimal-autonomous-loop.md) · [下一章 →](./03-deterministic-verifier.md)

## 本章使用说明

第一章用 value、target 和退出码训练了控制权边界；本章不再使用玩具状态，而是创建一个真实 Python 包。项目包含源码、测试、虚拟环境和 Git 基线，并故意保留一个可复现的边界条件缺陷。后续章节会在这个仓库上加入统一验证器、控制器和 Codex。

> 本章纪律：不要提前修复常量向量的归一化缺陷。你现在的目标不是让测试通过，而是建立一个“失败可复现、变更可归因、状态可恢复”的可靠起点。

### 学习目标

- 能从空目录创建采用 src 布局的 Python 包，并理解包、模块与导入路径的关系。

- 能把自然语言需求写成测试契约，而不是依赖“代码看起来合理”。

- 能使用虚拟环境和 editable install，确认命令执行在正确解释器与正确项目中。

- 能建立 Git 基线，并区分 HEAD、暂存区和工作区。

- 能使用 status、diff、diff --cached、show、restore 等命令获取证据和回滚变更。

- 能解释为什么后续控制器必须依赖 Git，而不是只读取代理的文字报告。

## 1. 从玩具状态到真实仓库

第一章中的 worker 只修改 state.json，verify.py 只比较 value 与 target。这个结构帮助我们看清控制权，但它没有代码依赖、测试发现、包导入、文件差异和回滚问题。真实编码代理面对的不是一个整数，而是一个随时可能被误改的仓库。

**第一章训练“谁有权判定完成”；第二章训练“完成证据从哪里产生”。**

| 第一章抽象对象 | 第二章真实对象 | 工程含义 |
| --- | --- | --- |
| state.json | Git 工作区中的源码与测试 | 任务状态不再是单一数值，而是多个文件的组合 |
| worker.py | 未来的 Codex / 实现代理 | 动作会编辑源码，甚至可能误改测试 |
| verify.py | pytest 与后续统一 verifier | 目标是否满足要由机械工具判断 |
| run_state.json | Git revision + 测试证据 + 终态 | 证据必须绑定具体代码版本 |
| 手工重置状态 | git restore / reset / checkout | 失败后必须可恢复到已知基线 |

### 1.1 本章最终产物

**目录 1　本章完成后的仓库结构**

```text
chapter02\statkit-lab\
├─ .git\
├─ .gitignore
├─ .venv\
├─ pyproject.toml
├─ README.md
├─ src\
│  └─ statkit\
│     ├─ __init__.py
│     └─ normalize.py
└─ tests\
   └─ test_normalize.py
```

其中测试应稳定呈现“3 个通过、1 个失败”，失败原因是常量向量导致分母为 0。这个失败不是环境事故，而是后续闭环要修复的已知任务。

### 1.2 什么叫“可靠起点”

| 要求 | 可观察证据 | 不合格表现 |
| --- | --- | --- |
| 环境可复现 | python、pip、pytest 均来自 .venv | 系统 Python 与虚拟环境混用 |
| 失败可复现 | 重复运行 pytest 得到同一失败测试 | 有时通过、有时失败或依赖网络 |
| 差异可归因 | git diff 能准确显示修改文件和行 | 没有基线，不知道代理改了什么 |
| 状态可恢复 | git restore 后回到已知文件内容 | 只能手工猜测并撤销 |
| 契约明确 | 测试写清输入、输出和边界行为 | 只写“归一化应该正常工作” |

## 2. 为什么自治闭环必须先有可复现基线

没有 Git 基线和稳定测试时，控制器无法回答三个基本问题：本轮之前仓库是什么状态？代理具体改变了什么？改变后目标是否更接近完成？如果这三个问题不可回答，循环次数再多也只是反复调用模型。

### 2.1 Git 在 Loop Engineering 中不是“备份工具”

| 能力 | 普通开发用途 | 在自治闭环中的控制用途 |
| --- | --- | --- |
| revision | 记录版本历史 | 把测试证据绑定到具体 commit SHA |
| diff | 代码审查 | 检查代理是否修改受保护路径或扩大变更范围 |
| restore | 撤销误改 | 发生策略违规时自动回滚 |
| branch/worktree | 并行开发 | 隔离多个代理，避免状态互相污染 |
| status | 查看未提交文件 | 控制器判断仓库是否干净、是否存在未追踪工件 |

### 2.2 一个关键区分：失败基线与坏基线

本实验会提交一个已知失败的测试基线。这里容易产生误解：测试失败是否意味着基线不可靠？答案是否定的。可靠性要求的是状态已知且可复现，而不是所有状态必须是成功状态。

> 失败基线：已知哪个测试失败、为什么失败、每次都能复现，并且仓库能够恢复到该状态。它可以成为有效实验起点。

> 坏基线：失败集合不稳定、依赖未记录、工作区已有不明修改、无法确定错误来自代码还是环境。这样的基线不能用于评估代理。

生产仓库通常应保持主分支为绿色；本章故意保存失败状态，只是为了构造可控实验。不要把这种做法机械迁移到团队主分支。

## 3. 创建工作目录与虚拟环境

### 3.1 检查 Python、Git 和当前位置

**操作 1　检查基础环境**

```powershell
python --version
git --version
Get-Location
```

建议使用 Python 3.11 或更高版本。若 PowerShell 无法识别 python，可尝试 py --version，并在后续命令中把 python 替换为 py。

### 3.2 创建独立实验目录

**操作 2　创建本章仓库**

```powershell
cd $HOME\Desktop
mkdir loop-engineering-training -ErrorAction SilentlyContinue
cd loop-engineering-training
mkdir chapter02 -ErrorAction SilentlyContinue
cd chapter02
mkdir statkit-lab
cd statkit-lab
Get-Location
```

> 路径检查：确认终端最后一级目录确实是 statkit-lab。后续大多数“找不到 pyproject.toml”或“不是 Git 仓库”错误，本质上都是命令在错误目录执行。

### 3.3 检查是否误入另一个 Git 仓库

**操作 3　检查父目录是否已经是 Git 仓库**

```powershell
git rev-parse --show-toplevel
```

如果命令返回了某个上级路径，说明你正在已有仓库内部创建嵌套实验。初学阶段不建议这样做。换到一个不受 Git 管理的目录重新创建，避免外层仓库与本章仓库的状态混在一起。若提示“not a git repository”，在本步骤反而是正常结果。

### 3.4 创建并激活虚拟环境

**操作 4　创建虚拟环境并确认解释器**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable)"
python -m pip --version
```

正确时，sys.executable 的路径应位于当前项目的 .venv\Scripts\python.exe。以后安装依赖和运行测试都使用 python -m pip、python -m pytest，而不是直接依赖 PATH 中的 pip 或 pytest。

> 为什么使用 python -m：它强制模块由当前 python 解释器执行，能够减少“pip 安装到 A 环境、pytest 却从 B 环境启动”的混乱。

### 3.5 PowerShell 阻止激活脚本时

**仅当前终端临时放行**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

这里使用 Process 作用域，只影响当前 PowerShell 会话；关闭窗口后自动失效。不要为了一个实验把整台机器永久设为不受限制。

## 4. 建立 src 布局与项目配置

### 4.1 创建目录和空文件

**操作 5　创建项目骨架**

```powershell
mkdir src
mkdir src\statkit
mkdir tests

New-Item pyproject.toml -ItemType File
New-Item README.md -ItemType File
New-Item .gitignore -ItemType File
New-Item src\statkit\__init__.py -ItemType File
New-Item src\statkit\normalize.py -ItemType File
New-Item tests\test_normalize.py -ItemType File

Get-ChildItem -Recurse -Force
```

### 4.2 为什么采用 src 布局

若包目录直接放在项目根目录，Python 可能因为当前工作目录而“碰巧”导入源码，即使项目根本没有正确安装。src 布局把可导入包放到 src/statkit，迫使我们通过安装流程建立导入路径，从而更早暴露打包配置错误。

| 布局 | 潜在行为 | 对训练的影响 |
| --- | --- | --- |
| 根目录 statkit/ | 从项目根运行时可能直接导入 | 错误配置可能被当前目录掩盖 |
| src/statkit/ | 未安装时通常不能直接导入 | 迫使我们验证安装和环境是否正确 |

### 4.3 编写 pyproject.toml

**文件 1　`pyproject.toml**`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "statkit-loop-lab"
version = "0.1.0"
description = "A small repository for Loop Engineering practice"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "ruff>=0.6",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"

[tool.ruff]
target-version = "py311"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

| 配置块 | 作用 | 后续闭环会怎样使用 |
| --- | --- | --- |
| build-system | 声明构建后端 | editable install 能找到并安装包 |
| project | 项目元数据与 Python 版本 | 控制器可记录环境约束 |
| optional-dependencies.dev | 测试与静态检查工具 | 统一安装开发依赖 |
| setuptools.packages.find | 从 src 发现包 | 避免导入路径靠当前目录碰巧成立 |
| pytest | 固定测试目录与报告选项 | verifier 可直接运行稳定命令 |
| ruff | 固定 lint 规则 | 防止每轮使用不同代码质量标准 |

### 4.4 编写 .gitignore

**文件 2　`.gitignore**`

```text
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
build/
dist/
*.egg-info/
logs/
state/
```

tests/ 和 src/ 绝不能被忽略，它们是任务定义和实现对象。logs/、state/ 会在后续章节由控制器生成，因此现在预先排除，避免运行工件污染源码差异。

## 5. 编写 statkit 源码与 API 契约

### 5.1 先确定边界行为，而不是先写实现

我们要实现 min–max normalization：对输入 x，计算 (x - min) / (max - min)。普通向量没有歧义，但常量向量满足 max = min，分母为 0。此时“正确答案”并不是数学公式自动给出的，而是 API 设计选择。

| 可选契约 | 优点 | 风险或代价 |
| --- | --- | --- |
| 抛出 ValueError | 显式拒绝不可区分的输入 | 调用方必须处理异常 |
| 全部返回 0.0 | 保持输出在 [0, 1]，实现稳定 | 把所有常量映射到下界是人为约定 |
| 全部返回 0.5 | 表达位于区间中点 | 同样是人为约定，可能不符合下游习惯 |
| 原样返回 | 保留数值 | 输出不再保证位于 [0, 1] |

> 本实验选择：空输入返回空列表；常量向量返回等长的 0.0 列表。这个选择必须写进 README 和测试，不能只存在于你的脑子里。

### 5.2 编写公共 API

**文件 3　src/statkit/`__init__.py**`

```python
from statkit.normalize import min_max_normalize

__all__ = ["min_max_normalize"]
```

__all__ 不是完成任务的必要条件，但它明确说明了包希望暴露的公共接口。测试从 statkit 导入函数，而不是依赖内部文件路径，这能防止未来重构模块时破坏外部 API。

### 5.3 编写带已知缺陷的实现

**文件 4　src/statkit/`normalize.py**`

```python
from __future__ import annotations

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
```

> 不要修复：span == 0 时当前实现会触发 ZeroDivisionError。缺陷是故意保留的，后续章节需要它来验证控制器是否能根据机械证据驱动修复。

### 5.4 编写 README.md

**文件 5　`README.md**`

```markdown
# statkit-loop-lab

A minimal Python repository used for Loop Engineering practice.

## API contract

`min_max_normalize(values)` must:

- return an empty list for empty input;
- map a non-constant vector into the range `[0.0, 1.0]`;
- preserve element order;
- return a zero vector for constant input;
- keep the public function name and signature stable.

## Known starter defect

The initial implementation divides by zero for constant vectors. This defect
is intentional and must remain reproducible until the later repair chapter.
```

## 6. 编写测试并观察真实失败

### 6.1 测试应验证契约，不应复制实现

一个薄弱测试常见的写法是：在测试中再次写一遍与实现相同的公式，然后比较两边结果。这样实现和测试可能共享同一个错误假设。更好的测试直接陈述外部行为：典型输入得到什么结果，边界输入应如何处理。

**文件 6　tests/`test_normalize.py**`

```python
from __future__ import annotations

import pytest

from statkit import min_max_normalize


def test_normalizes_regular_vector() -> None:
    result = min_max_normalize([0.0, 5.0, 10.0])
    assert result == pytest.approx([0.0, 0.5, 1.0])


def test_preserves_order_with_negative_values() -> None:
    result = min_max_normalize([-5.0, 0.0, 5.0])
    assert result == pytest.approx([0.0, 0.5, 1.0])


def test_empty_vector_returns_empty_list() -> None:
    assert min_max_normalize([]) == []


def test_constant_vector_returns_zeros() -> None:
    result = min_max_normalize([7.0, 7.0, 7.0])
    assert result == pytest.approx([0.0, 0.0, 0.0])
```

### 6.2 安装开发依赖与 editable package

**操作 6　安装项目**

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -c "import statkit; print(statkit.__file__)"
```

最后一条命令应打印当前项目下 src\statkit\__init__.py 的路径。editable install 并不是复制一份源码到 site-packages，而是建立指向开发目录的可编辑安装关系，因此修改 src 中的代码后无需反复安装。

> 环境证据：只有当 statkit.__file__ 指向当前项目，才能确认测试针对的是你刚刚创建的代码，而不是系统里某个同名旧包。

### 6.3 运行测试

**操作 7　观察已知失败**

```powershell
python -m pytest
```

预期结果是 3 passed、1 failed。不同 pytest 版本的排版可能不同，但失败测试名和异常类型应保持一致：

**预期证据（节选）**

```text
FAILED tests/test_normalize.py::test_constant_vector_returns_zeros
ZeroDivisionError: float division by zero

1 failed, 3 passed
```

### 6.4 如何读取 traceback

| 阅读层次 | 本实验看到的内容 | 你能得出的结论 |
| --- | --- | --- |
| 失败测试 | test_constant_vector_returns_zeros | 违反的是常量向量契约 |
| 调用位置 | tests/test_normalize.py 中调用函数 | 测试输入为三个相同的 7.0 |
| 异常源 | normalize.py 的除法表达式 | span 为 0，而实现没有分支处理 |
| 异常类型 | ZeroDivisionError | 不是断言精度问题，也不是导入错误 |

注意：本章只要求你形成诊断，不要求实施修复。能够准确描述错误机制与能够修改代码是两件不同的能力；自治闭环必须先有前者作为证据。

### 6.5 可选：运行 Ruff

**操作 8　检查静态质量**

```powershell
python -m ruff check src tests
```

在文件内容准确复制的情况下，Ruff 应返回成功。此时仓库同时存在“lint 通过、测试失败”的状态，这说明单一工具不能代表整体完成。后续 verifier 会把多个检查组合成证据门。

## 7. 初始化 Git 并提交已知缺陷基线

### 7.1 初始化仓库

**操作 9　建立 Git 仓库**

```powershell
git init
git status --short
```

status --short 会用简洁符号显示未追踪文件。由于 .gitignore 已排除 .venv 和缓存，列表应主要包含 pyproject.toml、README.md、src 和 tests。若 .venv 出现在列表中，先检查 .gitignore 是否位于仓库根目录且拼写正确。

### 7.2 首次提交身份错误的处理

如果 git commit 提示缺少 user.name 或 user.email，可只为当前实验仓库设置本地身份。更稳妥的做法是使用你自己的真实配置；以下仅示范语法：

**仅在 Git 要求时执行**

```powershell
git config --local user.name "Your Name"
git config --local user.email "your-email@example.com"
```

### 7.3 检查、暂存并提交

**操作 10　创建基线提交**

```powershell
git status
git add .
git status
git diff --cached --stat
git commit -m "chapter02: create reproducible failing baseline"
git log --oneline --decorate -5
git status --short
```

最后的 git status --short 应无输出，表示工作区和暂存区均与 HEAD 一致。测试仍然失败，但仓库状态是干净且已知的。

> 基线定义：本章基线 = 当前 HEAD + 可复现的 pytest 失败 + 干净工作区。三者缺一不可。

### 7.4 记录 commit SHA

**操作 11　获取版本指纹**

```powershell
git rev-parse HEAD
git show --stat --oneline HEAD
```

commit SHA 是本次源码快照的标识。未来 verifier 输出“测试通过”时，必须同时记录它针对哪个 SHA；否则测试可能来自旧分支、旧缓存或另一工作树。

## 8. 理解 HEAD、暂存区与工作区

许多 Git 误操作来自把“文件”想成只有一个版本。实际上，常规工作流程中至少同时存在三个快照。控制器检查差异时也必须明确比较的是哪两个快照。

**图 1　Git 的三个核心快照**

```powershell
git add                 git commit
工作区 Working Tree  ─────────→  暂存区 Index  ─────────→  HEAD
      ↑                              │                      │
      └──────── git restore ─────────┘                      │
      └────────────── git restore --source=HEAD ────────────┘
```

| 快照 | 保存的内容 | 常用查看命令 | 控制器关心的问题 |
| --- | --- | --- | --- |
| HEAD | 最后一次提交的仓库状态 | git show HEAD | 基线是什么 |
| Index | 准备进入下一次提交的内容 | git diff --cached | 哪些修改已被暂存 |
| Working Tree | 磁盘上的当前文件 | git diff / git status | 代理刚刚实际改了什么 |

### 8.1 三个 diff 命令不要混淆

| 命令 | 比较对象 | 典型用途 |
| --- | --- | --- |
| git diff | 工作区 vs 暂存区 | 查看尚未 git add 的修改 |
| git diff --cached | 暂存区 vs HEAD | 提交前审查已暂存内容 |
| git diff HEAD | 工作区/暂存区整体 vs HEAD | 控制器检查本轮相对基线的全部差异 |
| git diff --name-only HEAD | 只输出变化文件名 | 检查受保护路径和变更范围 |

### 8.2 为什么后续策略检查使用文件差异

提示词可以要求代理“不要修改 tests”，但提示词不是强制机制。控制器应在代理运行后执行 git diff --name-only HEAD，并根据路径规则判断是否发生 POLICY_VIOLATION。Git 在这里提供的是可执行策略证据，而不是道德提醒。

## 9. 六个 Git 破坏与恢复实验

> 实验原则：每个实验都先观察 status 和 diff，再恢复。不要跳过观察直接撤销，否则你只学会了命令，没有建立状态模型。

### 9.1 实验一：修改源码并查看未暂存差异

**①  **在 normalize.py 的文档字符串后临时增加一行注释，例如 # temporary experiment。

```powershell
git status --short
git diff -- src\statkit\normalize.py
```

预期 status 左侧出现 M，diff 以 -/+ 行显示 HEAD 与工作区的差异。然后恢复：

```powershell
git restore src\statkit\normalize.py
git status --short
```

恢复后 status 应再次为空。

### 9.2 实验二：篡改测试并理解“受保护路径”

**①  **把 test_constant_vector_returns_zeros 中的期望暂时改成 [1.0, 1.0, 1.0]，或者直接注释掉该测试。

```powershell
git diff --name-only HEAD
git diff -- tests\test_normalize.py
```

你应看到 tests/test_normalize.py 位于变化列表。后续控制器将把 tests/ 设为 protected_paths，检测到任何变更就停止，而不是让代理通过删除测试“完成任务”。

**恢复并确认已知失败仍然存在**

```powershell
git restore tests\test_normalize.py
python -m pytest
```

### 9.3 实验三：理解暂存区

**①  **再次给 normalize.py 增加临时注释，然后执行以下命令。

```powershell
git add src\statkit\normalize.py
git status --short
git diff
git diff --cached
```

此时 git diff 应为空，因为工作区与暂存区一致；git diff --cached 会显示修改，因为暂存区与 HEAD 不同。取消暂存但保留工作区修改：

```powershell
git restore --staged src\statkit\normalize.py
git status --short
git diff
```

最后彻底恢复：

```powershell
git restore src\statkit\normalize.py
```

### 9.4 实验四：误删源码并恢复

```powershell
Remove-Item src\statkit\normalize.py
git status --short
git diff --stat
git restore src\statkit\normalize.py
python -m pytest
```

Git 能恢复已追踪文件，是因为 HEAD 中保存了完整快照。对未追踪文件，restore 无能为力；这就是为什么关键工件必须及时纳入版本控制。

### 9.5 实验五：创建未追踪文件

```powershell
Set-Content scratch.txt "temporary note"
git status --short
git diff
Remove-Item scratch.txt
git status --short
```

git diff 默认不显示未追踪文件内容，只在 status 中标记 ??。因此控制器只运行 git diff 仍可能漏掉新文件，必须同时检查 status 或使用 git ls-files --others --exclude-standard。

> 不要盲用 git clean -fd：该命令会删除所有未追踪文件和目录，可能包含你尚未备份的数据。初学阶段优先明确指定 Remove-Item；自动控制器使用清理命令前也必须有路径白名单。

### 9.6 实验六：验证基线完整性

**最终核验**

```powershell
git status --short
git rev-parse HEAD
python -m pytest
python -m ruff check src tests
```

合格结果应满足：status 为空；HEAD 与之前记录的 SHA 一致；pytest 稳定为常量向量测试失败；Ruff 通过。若任一条件不满足，不要进入下一章。

## 10. 常见错误与诊断路径

| 现象 | 最可能原因 | 诊断命令 | 修正 |
| --- | --- | --- | --- |
| ModuleNotFoundError: statkit | 未安装项目或环境错误 | python -c "import sys; print(sys.executable)" | 激活 .venv 后重新 pip install -e ".[dev]" |
| 找不到 pyproject.toml | 命令在错误目录 | Get-Location; Get-ChildItem | cd 到 statkit-lab 根目录 |
| pytest 命令不存在 | 使用了裸 pytest 或依赖未安装 | python -m pip show pytest | 使用 python -m pytest；安装 dev 依赖 |
| .venv 出现在 git status | .gitignore 位置或内容错误 | Get-Content .gitignore | 把 .gitignore 放在仓库根并恢复暂存 |
| git commit 缺少身份 | 本地未配置 user.name/email | git config --local --list | 设置仓库本地身份 |
| 所有测试都通过 | 你提前修复了 bug 或测试未被发现 | python -m pytest -vv | 恢复 normalize.py 与测试文件 |
| 失败测试每次不同 | 环境或测试有非确定性 | 重复运行 pytest，检查随机/时间/网络依赖 | 先消除非确定性再做 loop |

### 10.1 Windows 换行警告是否严重

Git 可能提示 LF will be replaced by CRLF。这通常是换行规范提示，不等同于提交失败。真正需要关注的是：团队是否有一致的 .gitattributes 和 CI 环境，以及 diff 是否出现整文件无意义变化。本实验先不要因为警告随意修改全局 core.autocrlf。

### 10.2 为什么“我看代码知道怎么修”仍不算完成

诊断是提出可证伪假设：span 为 0 导致除法异常；修复是改变实现；完成则要求测试与策略证据通过。三者不能混为一谈。未来代理可能很快提出正确原因，但控制器仍必须让它实施修改并重新验证。

## 11. 本章自测与验收清单

### 11.1 思考题

**问题一：为什么 src 布局比把 statkit 直接放在根目录更适合训练可复现工程？**

参考结论：它减少当前目录对导入的隐式帮助，迫使安装配置真实生效。

**问题二：常量向量返回零是数学必然结论吗？**

参考结论：不是。它是 API 契约选择，必须由需求、README 和测试共同定义。

**问题三：为什么测试失败的仓库仍可作为可靠基线？**

参考结论：因为失败已知、稳定、可复现，并且版本与环境清楚；可靠不等于成功。

**问题四：git diff 与 git diff --cached 分别比较什么？**

参考结论：前者比较工作区与暂存区；后者比较暂存区与 HEAD。

**问题五：为什么只运行 git diff 可能漏掉代理创建的新文件？**

参考结论：未追踪文件不在普通 diff 中，必须结合 status 或 ls-files --others。

**问题六：为什么后续测试证据必须记录 commit SHA？**

参考结论：否则无法确认测试结果对应哪一份代码、分支和工作树。

### 11.2 通过标准

- [ ] 当前目录存在 pyproject.toml、src/statkit 和 tests。

- [ ] python 解释器路径位于项目 .venv 中。

- [ ] statkit.__file__ 指向当前仓库的 src/statkit。

- [ ] python -m pytest 稳定得到 1 failed、3 passed。

- [ ] 失败测试是 test_constant_vector_returns_zeros，异常为 ZeroDivisionError。

- [ ] python -m ruff check src tests 通过。

- [ ] git status --short 无输出。

- [ ] git log 至少包含 chapter02: create reproducible failing baseline。

- [ ] 能够解释 HEAD、Index 和 Working Tree 的区别。

- [ ] 完成六个破坏实验，并能恢复到原始 SHA。

> 真正掌握的标志：你不仅能创建项目，还能故意破坏源码、测试、暂存区和未追踪文件，并准确预测 Git 会显示什么、应使用什么命令恢复。只会复制最终目录，不算完成。

## 附录 A. 完整文件内容

当你怀疑某个文件复制错误时，使用本附录逐项核对。核对后仍要运行测试和 Git 命令，因为“文本看起来一致”不能替代执行证据。

### A.1 pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "statkit-loop-lab"
version = "0.1.0"
description = "A small repository for Loop Engineering practice"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "ruff>=0.6",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"

[tool.ruff]
target-version = "py311"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

### A.2 src/statkit/__init__.py

```python
from statkit.normalize import min_max_normalize

__all__ = ["min_max_normalize"]
```

### A.3 src/statkit/normalize.py

```python
from __future__ import annotations

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
```

### A.4 tests/test_normalize.py

```python
from __future__ import annotations

import pytest

from statkit import min_max_normalize


def test_normalizes_regular_vector() -> None:
    result = min_max_normalize([0.0, 5.0, 10.0])
    assert result == pytest.approx([0.0, 0.5, 1.0])


def test_preserves_order_with_negative_values() -> None:
    result = min_max_normalize([-5.0, 0.0, 5.0])
    assert result == pytest.approx([0.0, 0.5, 1.0])


def test_empty_vector_returns_empty_list() -> None:
    assert min_max_normalize([]) == []


def test_constant_vector_returns_zeros() -> None:
    result = min_max_normalize([7.0, 7.0, 7.0])
    assert result == pytest.approx([0.0, 0.0, 0.0])
```

### A.5 .gitignore

```text
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
build/
dist/
*.egg-info/
logs/
state/
```

## 附录 B. PowerShell 命令速查

| 目标 | 命令 |
| --- | --- |
| 激活环境 | .\\.venv\\Scripts\\Activate.ps1 |
| 确认解释器 | python -c "import sys; print(sys.executable)" |
| 安装项目 | python -m pip install -e ".[dev]" |
| 运行测试 | python -m pytest |
| 运行 Ruff | python -m ruff check src tests |
| 查看短状态 | git status --short |
| 查看未暂存差异 | git diff |
| 查看已暂存差异 | git diff --cached |
| 查看相对 HEAD 的全部文件变化 | git diff --name-only HEAD |
| 恢复工作区文件 | git restore <path> |
| 取消暂存 | git restore --staged <path> |
| 查看当前 SHA | git rev-parse HEAD |
| 查看最近提交 | git log --oneline --decorate -5 |

## 下一章预告

第 03 章将在当前仓库中编写统一确定性验证器 scripts/verify.py，把 pytest、Ruff、退出码和结构化日志组合成一个证据门。届时“测试失败”不再只是人阅读终端，而会成为控制器可消费的机器信号。

---

[返回课程主页](../../README.md) · [← 上一章](./01-minimal-autonomous-loop.md) · [下一章 →](./03-deterministic-verifier.md)
