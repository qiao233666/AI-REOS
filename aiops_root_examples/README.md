# aiops — AI-REOS 跨工具真源

本目录是本仓库唯一的 AI 治理 / 状态 / 证据真源（AI-REOS v1.1 落地）。
它**工具无关**：Trae、Codex、Claude Code、Cursor、Copilot、Gemini CLI 都只读同一份内容。

## 加载边界

- 本目录**不是**每轮必读材料。普通任务只读 `STATE.md` 定位，再按需取用相关 Evidence / Skill / Task Spec。
- 安装与治理文档（AI-REOS Master Spec）不属于日常运行时上下文。

## 目录索引

| 路径 | 作用 | 读取时机 |
|---|---|---|
| `CHARTER.md` | 治理权威：允许怎么工作、必须遵守什么 | 争议、边界、安全判断时 |
| `STATE.md` | 当前工作索引与摘要（1-3 页） | 每个任务入口 |
| `EVIDENCE.yaml` | 可复算事实（`E-xxx`） | 需要事实支撑时按 id 取用 |
| `DECISIONS.md` | 已做选择及原因（`D-xxx`） | 需要知道"为什么这样定" |
| `FAILURES.md` | 已证伪/失败方法（`F-xxx`） | 防止复活旧错误 |
| `ASSUMPTIONS.md` | 未验证假设（`A-xxx`） | 判断结论可靠性 |
| `TASK_QUEUE.yaml` | 待办与登记（`T-xxx`） | 排程、发现无关问题时登记 |
| `protocols/` | 工作协议（任务/上下文/证据/代码/实验/交接） | 执行对应流程前 |
| `skills/` | 6 个高价值可重复流程 | 由 Skill 路由触发 |
| `specs/` | Task Spec / Plan 模板 | 起草任务时 |
| `schemas/` | 机器校验约定（task / evidence / experiment） | 写入结构化记录前 |
| `templates/` | agent-result / decision / evidence / handoff 模板 | 产出记录时 |
| `experiments/` | Experiment Spec 与注册表 | 昂贵实验前后 |
| `checks/` | 可执行校验（schema 驱动）+ pre-commit 门禁 | 提交前自动 / 手动 |
| `adapters/` | 跨工具薄适配清单 | 仅安装或升级时 |

## 权威层级（冲突时以此为准）

```text
可复算数据 / 源码 / 测试 / 可靠文献
        ↓
      Evidence
        ↓
  Decision / Failure
        ↓
      STATE 摘要
        ↓
    Agent prose / chat
```

`STATE.md`、聊天记录、runtime memory 都**不得**覆盖更底层证据。

## 验证入口

```powershell
# 一次运行全部校验（治理状态 / Task Spec / 实验元数据 / claim / adapter 漂移）
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/run_all_checks.py

# 单项校验（run_all_checks 已包含，一般无需单独跑）
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/verify_ai_state.py
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/verify_task_specs.py
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/verify_experiment_metadata.py
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/verify_claims.py
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/check_adapter_drift.py

# 安装/重装 pre-commit 门禁（.git/hooks 不进版本库，换机器后需重装）
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe aiops/checks/install_hooks.py

# 项目既有冒烟测试（Tier 0，约 5-12 s）
C:/Users/25151/.conda/envs/RCWA_electronic/python.exe -m unittest tests.test_refactor
```

**门禁行为**：`pre-commit` 会运行 `run_all_checks`，任一项失败即阻止提交；确需跳过用 `git commit --no-verify`。

## schema 的地位

`aiops/schemas/*.json` 是**可执行约定**，校验脚本在运行时读取它们（字段、类型、枚举、id 格式均来自 schema），
因此 schema 是结构化记录的唯一真源，脚本内不重复字段清单。
校验器为纯标准库实现的 draft-07 子集；**遇到不支持的关键字会报错并使校验失败**，不会静默跳过。

## 语言约定

- YAML/JSON 字段名与枚举值：英文。
- 正文、注释、docstring：中文。
- Skill front matter 的 `name` / `description`：英文短句（用于路由，不占上下文）。
