---
name: red-team-review
description: Adversarially review claims for overreach, collinearity, non-convergence, data leakage, weak tests, and alternative explanations. Use before accepting results or finalizing a conclusion.
---

# When to use（何时使用）

- 一条结论即将被接受、发表或作为后续决策依据时。
- 需要主动寻找反例、替代表征与证伪机会时。

# Do not use（何时不要使用）

- 结果尚未产出，仍在执行阶段；先用 experiment-run。
- 只是登记证据；改用 evidence-update。

# Required inputs（必需输入）

- 待审查的 claim 或结论及其分类（fact/inference/hypothesis）。
- 支撑证据、实验配置与 provenance。
- 已有局限与 scope 声明。

# Procedure（步骤）

1. 列出所有待审查 claim，逐条标注其声称的强度。
2. 检查 claim 越界：结论是否超出 scope、样本区间或模型假设。
3. 检查共线性：自变量之间是否高度相关，是否把相关当作因果。
4. 检查数值未收敛：截断、网格、迭代步数、容差是否足以支撑结论。
5. 检查数据泄露：训练/验证/测试是否隔离，是否存在信息穿越。
6. 检查测试不足：验收是否可判定，是否缺少反例或对照。
7. 提出替代解释：为同一现象给出至少一个与主结论竞争的解释。
8. 不进行简单投票；对每条 claim 给出"目前证据能支持到什么程度"的明确判断。

# Verification（如何验证做对了）

- 每个审查维度都有具体证据或明确"无问题"的判断。
- 至少提出一个替代解释，并说明如何区分。
- 每条 claim 的结论是"证据支持程度"而非"赞成/反对"票数。
- 越界、未收敛与泄露若存在，均被显式列出并给出修复建议。

# Output（输出格式）

- 分维度的问题清单与逐条 claim 的支持程度判断。
- 统一记录到 `aiops/templates/agent-result.yaml` 定义的字段。
