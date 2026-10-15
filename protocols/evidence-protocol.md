# Evidence 协议

本协议规定什么能成为 Evidence 的**强制约束与验收标准**。判定来源等级与写入步骤见 `aiops/skills/evidence-update/SKILL.md`。

## 适用场景

- 任何要写入 `aiops/templates/evidence.yaml` 的结论。
- 任何要在 Decision 中作为 `why_evidence_ids` 引用的依据。

## 判定标准

1. 只有**可复算来源**能成为 Evidence：可复现的数据、源码/脚本、可重跑的测试、可靠公开文献。
2. 单次观察未复现、直觉推断、外部口述、临时日志片段**只能成为 Hypothesis**。
3. 每条 Evidence 只记录"数据直接支持"的陈述，不混入因果解释；机制解释移到 Decision 的 `why` 或标记为 Hypothesis。
4. 每条必须填写 `source`（file/script/git_commit）、`scope`（cases/parameter_range）、`limitations`、`status`、`recorded_at`。

## 必须产出什么

- 向 `aiops/EVIDENCE.yaml` 追加一条记录（第一版为**单文件**布局，字段符合 `aiops/schemas/evidence.schema.json`）。
- 在 Decision 中以 `why_evidence_ids` 引用。

### 写入入口（2026-10-15，T-084/D-040）

新增条目**必须走统一事务追加器** `aiops/checks/ledger_write.py append`
（OS 句柄锁 + 锁内取号 + 原子 os.replace + request-id 幂等），不得手工编辑账本取号：

```sh
python aiops/checks/ledger_write.py append --kind E --entry <草稿.yaml> --root aiops
python aiops/checks/ledger_write.py show --kind E --request-id <uuid> --root aiops  # 崩溃后查回编号
```

- 草稿**不携带 id**；编号由追加器在锁内从账本实际条目计算，返回后不得更改。
- 相同 request-id 重试返回原编号（幂等）；同请求号不同内容拒绝。
- 已发布编号永不回收；撞号按内容锚点+前向重编号+`ID_ALIAS.md` 处理，
  **不同编号之间的语义重复或结论冲突须另行核查，不能靠改号解决**。
- request-id 与 payload hash 以受控注释行持久化；追加/修改不得丢失既有请求记录。
- 边界声明：追加器为单工作树（canonical writer=主仓主工作树）内防撞；
  跨 worktree 只提交草稿，不在本地副本自行定号。
- 程序化校验：`check_id_uniqueness` 仅查编号重复，不验证归属、不识别语义重复——
  它是兜底不是主防线。

> 迁移触发条件（满足任一再拆分）：文件大到每次检索/编辑明显变慢；多 worktree/多人出现反复合并冲突；
> 需要独立权限或自动生成索引。迁移到 `aiops/evidence/E-xxx.yaml` 时必须保留稳定 ID，并由脚本生成汇总索引，
> 不得让 Agent 手工维护两套真源。

## 禁止事项

- 禁止把 Agent 的自然语言结论升级为 Evidence：模型说"这证明 X"不构成证据，必须落到数据/源码/测试/文献。
- 禁止在 `statement` 中夹带因果或机制解释。
- 禁止无 scope、无来源或无法复算的"证据"（`source.file` 与 `source.script` 至少填一个；`git_commit` 未知时可留空并在 limitations 说明）。
- 禁止把相关性写成因果；未收敛结果不得当作连续介质极限。
- 科学结论输出必须区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN。

## 验收检查

- [ ] 每条 Evidence 有可复算来源（file 或 script）；`git_commit` 缺失时已在 limitations 说明。
- [ ] statement 仅含直接观测，无因果措辞。
- [ ] type 在 observation | measurement | reference | artifact 之内。
- [ ] limitations 与 scope 非空，能界定适用边界。
- [ ] 若声明"本会话未复算"，必须写入 limitations。
