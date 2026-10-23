# 子 Agent 契约

本协议规定子 Agent 的角色边界、返回格式与并发规则。

## 适用场景

- 主 Agent 派生任意子 Agent（研究 / 实现 / 调试 / 审查 / 验证）。

## 必须做什么

1. 派生时明确单一角色：一个子 Agent 只承担一个角色。
2. 模型与思考强度（优先用工具原生能力）：
   - 若派生工具提供**模型类型**或**思考强度**设置，必须显式设置：Terra 类（跨文件实现、困难调试）取最强可用推理档；Luna 类（格式化、改名、小测试、定点检查）取例行/快速档，思考强度取够用即可。
   - 若工具不提供这类设置，则使用工具默认值，并在返回结果中注明"未指定模型"。
   - **禁止伪造或写死工具不支持的模型名**（例如硬编码某个具体版本号）；工具不支持时以默认方式执行即可。
3. 并发上限默认 2-3；同一文件、同一输出目录在同一时间只能有一个 writer。
4. 默认禁止嵌套派生：子 Agent 不得再派生子 Agent。确需时须由主 Agent 显式授权并记录。
5. 若工具支持 `fork_turns` 之类的上下文继承开关，有界任务取"不继承"；工具无此参数时省略该项。
6. 子 Agent 返回必须符合 `aiops/templates/agent-result.yaml`：
   - `status`：complete | blocked | inconclusive。
   - `answer`、`facts_used`、`new_findings`、`uncertainty`、`conflicts`、`recommended_next_action`、`files_changed`、`tests_run`。
7. 遇到硬性阻塞（缺依赖、验收不可判定）时返回 blocked，并写清尝试过的路径，不得反复重试同一手段。

## 必须产出什么

- 一份符合返回 schema 的结果。
- `files_changed` 与 `tests_run` 必须如实填写（无则空数组）。
- blocked / inconclusive 时必须给出 `recommended_next_action`。

## 禁止事项

- 禁止一个子 Agent 同时做实现与验收自审（自审不能替代独立验证）。
- 禁止并发写同一文件或同一输出目录。
- 禁止未经授权嵌套派生。
- 禁止扩大 scope：发现无关问题只登记到 Task Queue，不顺手修改。
- 科学结论输出必须区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN；相关性不得写成因果；未收敛结果不得当作连续介质极限。

## 验收检查

- [ ] 角色唯一、边界清晰。
- [ ] 模型/思考强度：工具支持时已显式设置，不支持时已注明"未指定模型"，且未写死工具不支持的模型名。
- [ ] 并发未冲突，同一文件 writer 唯一。
- [ ] 返回 YAML 字段齐全且可解析。
- [ ] blocked / inconclusive 附带了下一步动作。
