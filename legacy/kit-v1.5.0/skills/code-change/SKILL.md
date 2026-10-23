---
name: code-change
description: Make a minimal, scoped code change and verify it with local tests and static checks. Use when a Task Spec requires editing source code or fixing a bug.
---

# When to use（何时使用）

- Task Spec 已明确，需要修改源码、修复缺陷或调整接口时。
- 需要在不扩大范围的前提下提交可审阅的补丁时。

# Do not use（何时不要使用）

- 需求仍在澄清阶段，尚未产出 Task Spec。
- 任务目标是运行实验而非改代码，改用 experiment-run。
- 需要改动仓库既有规则文件（AGENTS.md、.trae/**、.codex/**）时，先回到 intake 重新界定范围。

# Required inputs（必需输入）

- Task Spec（含主目标与验收条件）。
- 允许修改的文件白名单；白名单外的文件一律不动。
- 目标环境的解释器路径与测试命令。

# Procedure（步骤）

1. 搜索相关代码：定位调用链、数据流与已有测试，记录涉及的文件与行。
2. 设计最小 patch：只改达成验收条件所必需的代码，不做顺带重构。
3. 应用改动：保持既有代码风格、命名与注释语言。
4. 运行局部测试：先跑与被改模块直接相关的用例。
5. 执行静态检查：语法编译与类型/格式检查（如 `py_compile`、语言内置 linter）。
6. 运行相关回归：只跑受影响范围，避免触发昂贵或全量任务。
7. 输出 diff：给出改动文件清单与关键差异。
8. handoff：按执行结果模板移交，写明命令、结果与剩余风险。

# Output（输出格式）

- 改动文件清单、命令与输出要点、剩余风险。
- 统一记录到 `aiops/templates/agent-result.yaml` 定义的字段。

# Verification（验收）

验收清单见 `aiops/protocols/code-change-protocol.md` 的"验收检查"，本文件不重复。
