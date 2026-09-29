# AI-REOS Master Spec 变更日志（CHANGELOG）

本文件记录 `AI_REOS_MASTER_SPEC` 的版本化演进。Spec 是整个科研系统的设计起点；
所有**系统级**改动（目录布局、强制层、维护机制、适配层模式）在项目里验证有效后，
必须回流到 Spec 正文并在此登记——否则 Spec 与实施漂移，后续安装/升级会丢失这些改进。

## 演进流程（Spec-driven change）

1. **提案**：系统级改动先在项目 `aiops/DECISIONS.md` 记为 D-xxx（含验证证据）；
2. **回流**：经人工确认有通用价值后，改写 Spec 对应章节（版本号 minor 升位），
   本 CHANGELOG 追加变更条目（含来源项目与证据指针）；
3. **同步**：若改动影响已安装的 `aiops/` 结构，同步更新本仓库安装并跑 `run_all_checks`；
4. **禁止**：跳过 DECISIONS 直接改 Spec（无证据）；或只改 DECISIONS 不回流 Spec（漂移）。

## 文件角色

| 文件 | 角色 |
|---|---|
| `docs/design/AI_REOS_MASTER_SPEC_v<x.y>.md` | 当前有效版本（演进基准，进版本库） |
| `aiops/CHARTER.md` | 运行时治理权威（Spec 的运行时投影，精简） |
| 本文件 | 版本间变更记录 |

---

## v1.2 — 2026-09-29（基于 RCWA_metamaterial 项目实测）

来源证据：`aiops/DECISIONS.md` D-012/D-014/D-015、`aiops/EVIDENCE.yaml` E-009/E-010/E-011/E-013、`aiops/FAILURES.md` F-007。

### 1. 临时产物生命周期：scratch（活跃）与 archive（冻结）双区制

- 新增 §4 真源目录约定：`scratch/<工作线名>/` 为唯一合法的活跃临时产物区；
  `archive/` 为 write-once 冻结封存区，禁止运行时写入。
- 沉淀必须走显式整理任务（T-xxx 合同），并同步更新引用链接。
- 依据：活跃产出写入 archive 两次（F-007 及其复发），破坏"可整体删除"语义。

### 2. 确定性执行层扩展（§14）：会话注入与写路径治理

- PreToolUse 守卫 hook 从"可选项"升级为推荐默认：对受保护目录的破坏性删除
  程序化拒绝（exit 2），内置 canary 锚点供活体验证。
- 新增 SessionStart 注入模式：状态指针（读 STATE + 触发表）由 hook 在每次会话
  启动时确定性注入，不依赖模型自觉（ZCode `.zcode/config.json` 可进版本库）。
- 守卫边界如实声明：拦删除不拦写入；写路径治理靠目录语义 + 例行反向扫描。
- 依据：E-010/E-011（门禁与守卫实测拦截），E-013（运行时挂载验证）。

### 3. 维护节奏落地为常设任务链（§32 具体化）

- 四档节奏（每任务/每周/每月/里程碑）各落为 TASK_QUEUE 常设任务
  （T-020~T-023 模式），执行经验追加在任务字段内随任务累积。
- 每周档增加"archive 反向扫描"（发现冻结区内新增文件→迁移→修路径）。
- 依据：T-020~T-023 建立；F-007 当日复发证明纯文档约定不足。

### 4. 多会话/多工作线并发登记防撞

- 同仓库多个 Agent 会话并行时，登记 Evidence/Decision 前先查当前最大编号；
  各 worktree 工作线优先在各自分支记账，定期合流到主分支真源。
- 依据：E-013~E-019 与新条目撞号的真实事件。

### 5. Spec 演进机制本身（本节）

- 新增本 CHANGELOG 与演进流程；Spec 文件从 Temp 单点改为仓库内版本化
  （`docs/design/AI_REOS_MASTER_SPEC_v<x.y>.md`），新版本落盘后旧版本保留。
