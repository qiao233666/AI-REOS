# 指针文件：非琐碎任务开始先读 aiops/STATE.md

本文件是 Trae 常驻规则（每次会话全文注入）。只放指针，不放正文——真源在 aiops/。

## 强制

- 每次非琐碎任务开始，先读一次 `aiops/STATE.md`（当前目标/已确认事实/活跃假设/阻塞项）。
- 收尾：提交由 pre-commit 门禁自动跑 `aiops/checks/run_all_checks.py`；只读任务无需手动跑。

## 按需读取的触发条件

- 要下科学结论 → `aiops/EVIDENCE.yaml`（证据）+ `aiops/FAILURES.md`（已被证伪的做法，勿重蹈覆辙）
- 要改代码 → `aiops/protocols/code-change-protocol.md`
- 要跑仿真/批量实验 → `aiops/protocols/experiment-protocol.md`
- 派子代理 → `aiops/protocols/subagent-contract.md`；任务交接 → `aiops/protocols/handoff-protocol.md`
- 依据不充分的前提 → `aiops/ASSUMPTIONS.md`；历史决策 → `aiops/DECISIONS.md`

## 边界

- 普通小改动（改注释、改文案）不必读 aiops/。
- 不要把 AI_REOS_MASTER_SPEC 整份当常驻上下文——那是安装/升级时才读的设计文档。
