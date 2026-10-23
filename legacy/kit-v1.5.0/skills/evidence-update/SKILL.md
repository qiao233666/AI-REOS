---
name: evidence-update
description: Add or modify evidence records only when a traceable source exists, using deprecation instead of silent overwrite. Use when updating the evidence ledger.
---

# When to use（何时使用）

- 产生了新的可追踪来源（脚本、日志、实验 id、文献）需要登记为证据时。
- 需要修订或作废既有证据条目时。

# Do not use（何时不要使用）

- 只有口头结论、没有可追溯来源；此时不得写入证据。
- 需要分析或解释结果；改用 scientific-analysis。

# Required inputs（必需输入）

- 候选证据：类型、陈述、来源、状态。
- 来源凭据：文件路径、脚本名、实验 id 或引用条目。
- 现有 `aiops/EVIDENCE.yaml` 内容与已用 id 集合。

# Procedure（步骤）

1. 确认来源可追踪；无可追踪来源时终止，不得凭印象新增。
2. 判定操作类型：新增（add）还是修订（revise）还是作废（deprecate）。
3. 新增时分配唯一 id，格式 `E-` 加至少三位数字，禁止复用已存在 id。
4. 修订时保留原 id，更新 statement/source/status 并注明修订理由。
5. 作废时置 status 为 deprecated，并保留原条目，禁止静默覆盖或删除。
6. 写入前执行 schema 校验：字段 `schema_version`(==1)、`id`、`type`、`statement`、`source`、`status` 必须齐全。
7. 通过校验后写回 `aiops/EVIDENCE.yaml`，保持其为 YAML 列表。
8. 若该证据影响 STATE.md 的 Confirmed facts，同步核对引用 id 是否存在。

# Output（输出格式）

- 更新后的 `aiops/EVIDENCE.yaml`。
- 统一记录到 `aiops/templates/agent-result.yaml` 定义的字段。

# Verification（验收）

验收清单见 `aiops/protocols/evidence-protocol.md` 的"验收检查"，本文件不重复。
