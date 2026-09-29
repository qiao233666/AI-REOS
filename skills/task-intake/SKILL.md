---
name: task-intake
description: Turn a vague or multi-goal request into a structured Task Spec with one primary objective, scope, acceptance, and budget. Use when a new task starts or requirements are ambiguous.
---

# When to use（何时使用）

- 用户给出模糊、口语化或包含多个并列子目标的请求时。
- 需要先澄清目标再决定后续编排方式时。
- 需要为 code-change 或 experiment-run 提供可验收输入时。

# Do not use（何时不要使用）

- 需求已明确且只有单一动作，直接进入 code-change 或 experiment-run。
- 只是回答一个事实性问题，不需要任务编排。

# Required inputs（必需输入）

- 原始请求文本（用户原话，不要自行改写目标）。
- 已知背景：仓库路径、相关模块、当前分支、已有产物位置。
- 约束：不可改动范围、时间与算力预算、合规与安全要求。

# Procedure（步骤）

1. 复述请求，抽出**唯一主目标**（One primary objective）；若存在多个目标，必须收敛为一个，其余降级为非目标或后续任务。
2. 显式列出范围内（in-scope）与非目标（out-of-scope）；非目标不得为空。
3. 列出必需输入与依赖：文件、数据集、解释器路径、外部服务。
4. 写出**验收条件**（acceptance）：每条都必须能被命令或脚本判定为真/假。
5. 写出验证方式（verification）：具体命令、期望输出或数值阈值。
6. 写出预算（budget）：墙钟时间、算力、最大重试次数、是否允许昂贵计算。
7. 判定路由：目标与验收条件稳定、依赖齐全时进入 PLAN；目标已知但需迭代逼近（参数搜索、调优）时进入 CONVERGE。
8. 将结果写入 `aiops/specs/T-xxx.yaml`，id 使用 `T-` 加至少三位数字。

# Output（输出格式）

- 主产物：`aiops/specs/T-xxx.yaml`。
- 执行记录按 `aiops/templates/agent-result.yaml` 的字段填写。

# Verification（验收）

验收清单见 `aiops/protocols/task-spec.md` 的"验收检查"，本文件不重复。
