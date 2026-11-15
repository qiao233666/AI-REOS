# 原有审核机制与执行材料衔接

审核职责沿用项目原规定：superagent子代理由项目执行层核查；执行层的阶段产物交给项目规定的审核渠道。superagent的accepted只代表该子包被执行层接受，不表示母任务完成，也不是项目科学结论采纳。

控制层消费原有Project.review/record-reviews意见，保留既有method/checks、准确版本、reviewer及pass/limit/fail/unknown语义。它不另设审核JSON、审核模型、审核渠道，也不要求每个子代理先通过项目级正式裁定才能被执行层验收。

执行段的任务、原件、证据和预算仍须准确绑定。需要续行或完成裁定时，复用对应阶段的准确质量意见；旧版本、非独立意见、最新拒绝意见和未满足的原验收项不能被子包accepted绕过。阶段划分及渠道授权由项目原约定决定。下一段目的保留追溯记录，不与审核文本做逐字匹配。

可选的只读ExecutionMaterials(store).packet(segment_id, max_bytes=262144)及CLI execution-materials汇集完整任务合同、UTF-8任务原件、准确阶段报告和证据文件。它不写审核记录、不产生裁定、不调用模型。原件不能完整装入预算时明确失败，过大或非文本证据给出准确快照、摘要与requires_read定位，不把材料包生成当作已经审核。

原任务覆盖、证据类别真实性及科学判断由项目既有审核执行。框架不保证审核者必然读懂文件，也不会把一个物理证据标签自动当成真实FE/RCWA结果。

后续整链修正：受管阶段材料已接入原prepare-external-review。原run-external-review保留正式external_run身份，项目适配沿aiops.dispatch使用原模型、权限和授权。review/record-reviews的provenance只携带该既有运行ID，原method/checks和意见语义保留。执行控制核对准确原件/合同/报告、实际stdin和完成证据，固定轨迹摘要；整改新报告清除旧裁定。不能从一个普通目录声称“这里有审核日志”就发放下一段。

这是派发材料和运行身份的衔接，不新增审核员、审核渠道、报告JSON或子代理项目级审核。真实提供方、新ZCode会话和重载MCP的整体宿主验收仍未完成。

## 显式审核预算

原 `run-external-review` 可传入 `budget`，字段为 `input_tokens`、`output_tokens`、`max_context_tokens`、`packet_bytes`。前两项必须为正整数，后两项可为 null。显式预算写入已有 external_run 及幂等请求，并原样传给项目 codex-review 派发器，避免回退到默认额度；同一请求修改预算不得重放。未传该字段的旧调用保持兼容。

显式预算只适用于已接入项目派发器的材料包。没有用量核对通道时拒绝，不能仅保存一个数字就宣称已限制。当前额度仍为 turn.completed 后的事后核对，不是执行中硬实时停止。
