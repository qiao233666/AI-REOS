---
name: experiment-run
description: Prepare, execute or resume a numerical experiment while recording full provenance, then verify and register it. Use when running or continuing a computation.
---

# When to use（何时使用）

- Task Spec 要求执行、续跑或对比数值实验时。
- 需要为结果建立可复现的 provenance 记录时。

# Do not use（何时不要使用）

- 只需要改代码；改用 code-change。
- 只需要解释已有结果；改用 scientific-analysis。

# Required inputs（必需输入）

- Experiment Spec：目的、假设、自变量与因变量、成功判据。
- 运行环境：解释器绝对路径、设备、并发与线程配置。
- 预算：墙钟时间上限、是否允许昂贵计算。

# Procedure（步骤）

1. 准备 Experiment Spec，写入 `aiops/experiments/`，id 使用 `EXP-` 加至少三位数字。
2. 逐项登记记账项（provenance）：求解器、谐波截断、波长/频率约定、偏振、边界条件、材料模型、随机种子、设备、精度、数据集版本、输出目录。
3. **先跑最便宜的冒烟测试**：小规模、短时长，确认导入、路径与数值接口正确。
4. 冒烟通过后再执行完整实验；若预算不足则停在冒烟结果并上报。
5. 执行或续跑，将 stdout/stderr 与关键指标落盘到输出目录。
6. 验证：对照 acceptance 逐条核对，检查数值收敛性并与已知参考对比。
7. 登记：把实验 id、状态与结果路径写入 `aiops/experiments/registry.yaml`。
8. 记录失败或中止原因，状态取 planned/running/complete/failed/aborted 之一。

# Output（输出格式）

- 实验元数据文件与 `registry.yaml` 登记项。
- 统一记录到 `aiops/templates/agent-result.yaml` 定义的字段。

# Verification（验收）

验收清单见 `aiops/protocols/experiment-protocol.md` 的"验收检查"，本文件不重复。
