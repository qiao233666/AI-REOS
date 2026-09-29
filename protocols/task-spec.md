# Task Spec 协议

本协议规定 Task Spec 的**强制约束与验收标准**。具体步骤与 PLAN / CONVERGE 判定见 `aiops/skills/task-intake/SKILL.md`。

## 适用场景

- 需求涉及跨文件改动、接口或 schema 变更、昂贵实验批次。
- 需求描述含糊，存在多种合理解读。
- 预计改动超过 1 个关键文件，或需要子 Agent 协作。

琐碎、单文件、可逆的小改动可直接执行，不强制写 Task Spec。

## 必须产出什么

- 一份 `aiops/specs/<id>.task.yaml`，字段符合 `aiops/schemas/task.schema.json`。
- 若 `plan.required` 为 true，一份 `aiops/specs/<id>.plan.md`（见 `TEMPLATE.plan.md`）。
- 若 `convergence.required` 为 true，`convergence.result` 从 `pending` 起记，并在收尾时回填。

## 禁止事项

- 禁止把过程写成交付物（例如"研究了 X"不是 WHAT）。
- 禁止使用不可判定的验收条件（例如"效果更好"）。
- 禁止隐瞒假设：未写出的假设不算已声明。
- 禁止 `scope.exclude` 留空却实际做了额外改动。
- 科学结论输出必须区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN，不得混为一谈。
- 相关性不得写成因果；未收敛结果不得当作连续介质极限（continuum limit）。

## 验收检查

- [ ] 四段（WHAT/WHY/SCOPE/ACCEPTANCE）齐全且无歧义。
- [ ] 每条 acceptance 都能对应到一条 verification.commands 或 invariants。
- [ ] risk、budget、convergence 已显式填写（未使用时写明 false/null 及原因）。
- [ ] 键名与枚举值均英文，且与 `aiops/schemas/task.schema.json` 一致。
