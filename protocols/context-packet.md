# Context Packet 协议

Context Packet 是主 Agent 交给子 Agent 的输入包。目标是"刚好够用"：让子 Agent 无需反查即可完成任务，同时控制 token 预算。

## 适用场景

- 派生任何子 Agent 时（研究、实现、审查、验证）。
- 需要向外部协作者交接离散任务时。

## 必须做什么

1. 按以下标准字段组织输入包（键名英文，内容中文）：
   - `task_id`：对应 Task Spec 的 id。
   - `goal`：一句话目标（可观测结果）。
   - `scope`：include 与 exclude。
   - `inputs`：允许读取的文件、已有 Evidence id、外部来源。
   - `constraints`：硬性约束（禁止改动、环境、编译器路径等）。
   - `acceptance`：验收条件。
   - `budget`：token 上限、工具调用上限、时间上限。
   - `output`：期望的返回 schema（默认 `aiops/templates/agent-result.yaml`）。
2. 控制预算：目标 1k-5k token。超出时先做摘要或只摘取相关片段，而不是整文件粘贴。
3. 只放"完成任务所需"的信息；不要塞入整段聊天历史。
4. 明确子 Agent 只能读取 `inputs` 中列出的文件，扩大范围需回报。

## 必须产出什么

- 一份结构化输入包（可内联在派生消息中，或存为临时文件）。
- 一份对应的返回 schema 引用，保证返回可解析。

## 禁止事项

- 禁止把猜测当作输入事实写入；不确定的写 UNKNOWN。
- 禁止超预算塞入大段日志、二进制或数据集内容。
- 禁止省略 constraints，导致子 Agent 误改已冻结文件。
- 输入包中的科学陈述同样要区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN。
- 禁止把相关性描述为因果；禁止把未收敛结果当作连续介质极限。

## 验收检查

- [ ] 八个标准字段齐全。
- [ ] 输入包 token 量在 1k-5k 区间（明显超限时已做摘要）。
- [ ] constraints 覆盖所有"禁止触碰"的文件与操作。
- [ ] output 指向的 schema 存在且字段匹配。
