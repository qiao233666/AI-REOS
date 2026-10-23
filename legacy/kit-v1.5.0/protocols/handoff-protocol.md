# Handoff 协议

本协议规定任务收尾与交接方式，使用 `aiops/templates/handoff.md`。

## 适用场景

- 任一任务完成、阻塞或需要转交时。
- 阶段切换、上下文清理前。

## 必须做什么

1. 使用 Handoff 模板，填写全部章节：
   - Status / What changed / Files changed / Verification（command + PASS|FAIL）/
     Scientific impact（New Evidence、Updated Decision、Deprecated）/ Remaining uncertainty /
     Follow-up tasks / Reproduce。
2. Verification 必须给出可重跑命令与实际结果，禁止只写"已通过"。
3. Scientific impact 中明确列出：新增 Evidence id、更新的 Decision 状态、被弃用的旧结论。
4. Reproduce 段给出从干净环境复现的最短路径（解释器路径、命令顺序）。

## 必须产出什么

- 一份填好的 handoff 文档。
- 更新的 Evidence / Decision / registry 引用（如有）。

## 禁止事项

- 禁止用自然语言结论替代可复算的 Verification。
- 禁止把未完成工作写成已完成；未收敛写 UNKNOWN。
- 禁止遗漏 Remaining uncertainty 与 Follow-up tasks。
- 科学结论输出必须区分 FACT / INFERENCE / HYPOTHESIS / UNKNOWN；相关性不得写成因果；未收敛结果不得当作连续介质极限。

## 验收检查

- [ ] 模板八段齐全。
- [ ] 每条 Verification 有命令与 PASS | FAIL。
- [ ] Scientific impact 三类（New / Updated / Deprecated）均已检查。
- [ ] Reproduce 段命令可直接执行。
