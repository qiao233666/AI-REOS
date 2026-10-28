# 开工前查历史与旧资料转换

alpha.4将正式prepare与session-context接到同一旧来源检查。旧资料保留完整原文及快照，支持全文、准确编号和路径查询；已登记来源按实现、风险及裁定分配候选机会。分类是检索视角，不判断条目真假。

history-configure保留path/format，增加可选categories（implementations/risks/decisions）和document格式（明确身份DOC-1）。来源文件必须全部可读；未命中不表示不存在。history-read按source摘要和可选source_format分页，offset为字符偏移，整个返回有预算；旧get仍可按准确版本恢复原文。

新增公开CLI：

- import-history：有plan能力时，将已登记原文逐条保存为legacy_record。身份按项目内路径与旧编号稳定映射，变化产生新版本；不伪造任务、核查或采纳。重复请求不重复导入。
- add-memory-note：key/title/text/scope/sources，登记带准确原文来源的派生短记录。源条目或明确关联变化时显示source_changed，不能把源文摘要当科学通过。
- add-history-link：source/target/relation/reason，登记已经核对原文的corrects/supersedes/qualifies/related/implementation关系。查询关联目标时补取更正，即使没有相同关键词；关联不自动判整条历史真或假。
- prework-configure：required_sources、constraints、可选required_records和handoff。明确来源缺项、固定历史条目变更或明确关联新增/变化，要求重新prepare；无关追加只提示新候选。约束以输出作用域装配，不依赖搜索排名。
- reading-usage：受管入口返回字符的累计观察及交接提醒；不是token、费用或任务终身硬封顶。旧原始get及未接入工具的输出不在该统计覆盖内。

新收据把完整历史检查保存在本地，给模型的prepare输出另按整包预算裁剪；被省略的引用可get收据恢复。通过检查证明明确来源/版本条件，不证明模型读懂或找全隐含历史。旧收据保留；配置新历史检查后需重新准备，不能补造旧检查。

新会话接手给当前任务、作用域约束与可选历史基线；默认不扫描旧全文，历史基线明确标为未重核。实际选路线时带query查询当前来源。阶段手账仍是同一正式记录的面向人展示。

会话查询、历史查询、原文分页及正式prepare默认整包预算为10000字符（包含来源、状态、截断提示和受管读取统计），可显式缩小。无query的轻量接手不会为凑满预算而读取更多材料。这个上限不是客户端全对话或累计费用封顶。

首版没有自动语义抽取全部关系、跨客户端已读同步或累计硬额度。FTS仍为底座，可选向量通道保持原配置；仅新记忆支持hybrid不能据此宣称旧历史都已向量化。未知题和已标注必需材料完整率分别统计，不把任一命中等同全部依据齐全。

迁移应先完整备份、在副本中验证来源、映射、关键更正及恢复，再切换单一正式写入口。原始科研记录不能通过“精简”删除条件或撤回历史；短记录可以修订，原文与旧版本保留。项目私有转换清单及原文不得进入通用框架发布载荷。
