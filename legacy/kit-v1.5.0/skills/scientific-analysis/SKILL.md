---
name: scientific-analysis
description: Produce evidence-driven mechanism and numerical explanations, forcing explicit separation of fact, inference, hypothesis, and unknown. Use when interpreting results or explaining causes.
---

# When to use（何时使用）

- 需要解释某个数值现象或物理机制的成因时。
- 需要把观测结果整理为可追溯的分析结论时。
- 需要判断某个结论目前被证据支持到什么程度时。

# Do not use（何时不要使用）

- 只是执行实验或跑脚本，尚未产出观测数据；改用 experiment-run。
- 只是登记或修改证据条目；改用 evidence-update。

# Required inputs（必需输入）

- 待解释的现象：现象描述、观测数据或日志、实验配置与 provenance。
- 现有证据列表（`aiops/EVIDENCE.yaml`）与相关 claim 记录。
- 已知边界条件：求解器、截断、材料模型、单位与精度。

# Procedure（步骤）

1. 复述待解释现象，明确观测量、单位与分析范围。
2. 将每一条陈述强制归类为 FACT / INFERENCE / HYPOTHESIS / UNKNOWN 四类之一。
3. 对每条 FACT 标注可追踪来源（文件、脚本、实验 id 或证据 id），无来源者不得标记为 FACT。
4. 对每条 INFERENCE 写明由哪些 FACT 推出，以及推理链的每一步。
5. 对每条 HYPOTHESIS 使用假设性措辞，明确它不是 fact，并给出可证伪的判据。
6. 若声称因果关系，必须给出可区分实验（能区分因果与相关性的对照或干预）。
7. 写出 scope 限制：结论适用的参数区间、几何/材料假设与不适用情形。
8. 汇总"当前证据能支持到什么程度"，并列出 UNKNOWN 清单与下一步验证动作。

# Verification（如何验证做对了）

- 每条陈述都有明确分类，不存在未标注类型的断言。
- 没有 FACT 缺来源；没有 HYPOTHESIS 被写成 fact 的句式。
- 每条因果声称都配有可区分实验。
- scope 限制非空，且明确写出不适用范围。
- UNKNOWN 被显式保留，未被默认填成结论。

# Output（输出格式）

- 分类后的分析与"证据支持程度"结论。
- 统一记录到 `aiops/templates/agent-result.yaml` 定义的字段。
