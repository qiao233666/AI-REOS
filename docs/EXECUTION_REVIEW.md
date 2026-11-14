# 原件驱动的独立续行核查

`ExecutionReview(store).packet(segment_id, max_bytes=262144)` 返回完整正式合同、完整UTF-8任务原件、当前准确报告及证据。原件装不下时明确失败，不能用执行者摘要或静默裁剪代替。过大的证据或非文本证据只提供准确快照路径及SHA-256，并进入 `requires_read`；检查者仍须实际打开它们，不把材料包生成当作已经审核。

独立检查先读原任务，再逐项对照实际产物，核对变量、验证对象、容差、目标、证据类型与适用范围是否被未经批准地收窄。检查者用已有review能力调用 `ExecutionReview(store).record(segment_id, assessment, request_id)`；该适配仍通过原有 `Project.review` 保存一份审核，不另建审核数据库、不调用模型。

`assessment` 示例（准确引用从材料包复制，不自行猜测摘要；以下占位符不能直接提交）：

```json
{
  "schema": "aireos.execution-review/1",
  "segment_id": "EXECUTION_SEGMENT-准确ID",
  "contract_hash": "原合同摘要",
  "original": {"id": "ITEM-原件", "rev": 1, "digest": "准确摘要"},
  "report": {"id": "ITEM-报告", "rev": 1, "digest": "准确摘要"},
  "decision": "continue",
  "scope_preserved": true,
  "criteria": [
    {"criterion": "A1", "judgment": "accurate", "evidence": [], "finding": "本项如实报告未知，仍需真实来源"},
    {"criterion": "A2", "judgment": "accurate", "evidence": [], "finding": "原要求及剩余缺口没有被删除"}
  ],
  "next_step": {"criteria": ["A2"], "purpose": "核对原证据缺口", "expected": "准确来源或诚实的未知结论"},
  "reason": "局部进展可以继续，原任务尚未完成"
}
```

全部原验收项都须列出，每项 `evidence` 与报告对应项的准确 `id/rev/class` 列表一致。`judgment` 为 `accurate/overclaimed/unknown`，分别指当前报告陈述准确、夸大、尚未核清；报告诚实写未知，可以被核查为准确。continue/completed必须所有项准确且范围保持。hold/completed不发放下一段，`next_step` 为null；continue必须批准下一段对应的原验收项、目的和预期。

后续begin必须准确采用批准的计划。没有逐项意见、以通用pass替代、删验收项、误报证据、不保留原范围、核查后改计划，都不能续行。证据和审核版本变化继续沿原框架失效机制处理。审核通过仍不证明物理正确；程序不宣称能判断检查者是否读懂文件。

现有主控与执行者分工可以承担独立核查。没有具备review权限的独立身份时，严格链保持阻断；实现不会暗中派发付费模型，也不让执行者审核自己的报告。已授权的合规段可由主控连续核查推进，不要求把每个内部段作为交付重新请用户批准；外部模型派发与超原授权变更仍服从项目约定。

CLI沿用原语法：`execution-review-packet --input packet.json` 为只读；`execution-review --input assessment.json --credential-file REVIEW_KEY --request-id KEY` 保存已有独立审核。它们与 `execution-decide` 共同构成续行核查，不代替既有交付、采纳和结案。
