#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI-REOS kit v1.3.2 bootstrap：安装 + 安全升级 + 本机适配生成。

用法（在目标仓库根目录执行）：
    python /path/to/AI_REOS_kit_v1.3.2/bootstrap.py --dry-run   # 预览（不询问、不改盘）
    python /path/to/AI_REOS_kit_v1.3.2/bootstrap.py             # 安装/升级

两类内容，两种策略（v1.3 核心修正——v1.2 对两者都是"存在即跳过"）：
1. 可变账本（STATE/EVIDENCE/DECISIONS/FAILURES/ASSUMPTIONS/TASK_QUEUE/CHARTER/
   policy.yaml/README/AIOS_INSTALL_REPORT）：**永不覆盖**，只补缺失；
2. 托管机制（protocols/skills/schemas/templates/checks/tests/world 工具）：按
   aiops/.reos-manifest.json 记录的上次安装哈希决定——
   ADD（新文件）/ UPDATE（kit 改了、用户没改 → 自动升级）/
   KEEP（两边一致）/ CONFLICT（用户改过且与 kit 不同 → 不覆盖，写 *.reos-new 并报告）。
   升级后 REMOVED（kit 已删除的托管文件）只提示，不偷偷删除。

v1.3.2 新增（世界观层，E-083/E-085）：
- world/tools/ 三个工具（builder/verifier/ledger reducer）按托管机制安装；
- world/coverage.yaml 与 world/ledger-policy.yaml 属项目骨架（只补缺失，永不覆盖）；
- 安装后用当前解释器生成一次初始视图（空 registry 也生成，保证 --check 一致）；
  失败不阻塞安装（世界层按 UNKNOWN 语义降级）。

其余行为：
- pre-commit：从 kit 模板渲染（版本化 aiops 副本的 __AIOS_PYTHON__ 占位符**永不回写**，
  v1.2 会把它替换掉导致换解释器后无法再更新）；已存在的用户 hook 备份为
  pre-commit.user.bak 并被链式调用；hooks 目录用 `git rev-parse --git-path hooks`
  解析（兼容 linked worktree，v1.2 拼 <repo>/.git/hooks 会 NotADirectoryError）；
- 生成 .zcode/config.json 模板说明 + policy.yaml + VERSION + .reos-manifest.json；
- --dry-run：不询问解释器、不改任何盘上内容，逐文件打印动作明细。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
KIT_VERSION = (KIT_ROOT / "VERSION").read_text(encoding="utf-8").strip() if (KIT_ROOT / "VERSION").exists() else "1.3"
MANIFEST_FILENAME = ".reos-manifest.json"
# AI-REOS 自装 hook 的识别标记：存在即直接替换，不备份不链式（v1.3.1）
AI_REOS_HOOK_MARKER = "AI-REOS Tier-0"
# 版本化 pre-commit 模板在 aiops/ 内的固定位置（迁移特例用）
PRECOMMIT_TEMPLATE_REL = "checks/hooks/pre-commit"

# 托管机制目录：支持 manifest 升级（world 下只放 tools/ 纯机制；coverage/ledger-policy
# 是项目骨架，走账本策略只补缺失——见 WORLD_SKELETON）
MECHANISM_DIRS = ["protocols", "skills", "schemas", "templates", "checks", "tests", "world"]
# 旧版迁移基线：v1.2 安装没有 manifest，无法区分"官方 v1.2 文件"与"用户改动"——
# 用官方 v1.2 哈希基线判定（命中 → UPDATE；不命中 → CONFLICT）。目录内每个 JSON 是一个旧版。
BASELINES_DIRNAME = "migration_baselines"
# 账本骨架：永不覆盖
LEDGER_FILES = [
    "STATE.md", "EVIDENCE.yaml", "DECISIONS.md", "FAILURES.md",
    "ASSUMPTIONS.md", "TASK_QUEUE.yaml", "CHARTER.md",
    "policy.yaml", "README.md", "AIOS_INSTALL_REPORT.md",
]

AGENTS_BEGIN = "<!-- BEGIN GENERATED (source: aiops/CHARTER.md) — DO NOT EDIT THIS BLOCK DIRECTLY. -->"
AGENTS_END = "<!-- END GENERATED -->"
OVERRIDES_BEGIN = "<!-- BEGIN LOCAL OVERRIDES -->"
OVERRIDES_END = "<!-- END LOCAL OVERRIDES -->"

AGENTS_BLOCK = """{begin}
## AI-REOS 真源与核心规则

Cross-tool source of truth is `aiops/`. The AI-REOS Master Spec is an installation/governance document and MUST NOT be loaded as always-on runtime context.

### Source of truth

- Governance: `aiops/CHARTER.md` and applicable `aiops/protocols/`
- Current task intent: `aiops/specs/`
- Current project index: `aiops/STATE.md`
- Recomputable facts: `aiops/EVIDENCE.yaml`
- Decisions: `aiops/DECISIONS.md`
- Rejected approaches: `aiops/FAILURES.md`
- Unverified premises: `aiops/ASSUMPTIONS.md`
- Backlog: `aiops/TASK_QUEUE.yaml`

Do not treat `STATE.md`, chat history, runtime memory, or prior agent prose as stronger evidence than recomputable sources or Evidence.

### Core behavior

1. Read only files relevant to the current task.
2. Do not broaden task scope without recording a new task in `aiops/TASK_QUEUE.yaml`.
3. Make the minimum necessary code change.
4. Prefer deterministic scripts/tests over model judgment.
5. Separate FACT, INFERENCE, HYPOTHESIS, and UNKNOWN in scientific analysis.
6. Never create Evidence from another agent's unsupported prose.
7. Do not modify raw data or result directories unless the task explicitly requires it.
8. Do not write credentials or secrets into repository files.
9. Before finalizing, run the task's verification commands.
10. Report changed files, tests, remaining uncertainty, and follow-up tasks.

### Routing

- Nontrivial code changes: `aiops/skills/code-change/`
- Scientific interpretation: `aiops/skills/scientific-analysis/`
- Running simulations/experiments: `aiops/skills/experiment-run/`
- Adding project facts: `aiops/skills/evidence-update/`
- Core claims or risky changes: `aiops/skills/red-team-review/`
- Turning a vague request into a task contract: `aiops/skills/task-intake/`

### Verification entry points

```sh
python aiops/checks/run_all_checks.py
```

### Context discipline

Do not load the full repository or all project documentation by default. Use `aiops/STATE.md` for orientation, then retrieve only task-relevant evidence and files.

- 判断环境/工具/数据/历史方案"是否存在"或要实现/改写既有设计 → 先查 `aiops/world/CURRENT.generated.md`（世界层，含检索关键词）；未命中只能记 UNKNOWN，禁止推出"不存在"。
- 跨会话/跨工具需要持久的项目事实、教训、裁定，唯一真源是 `aiops/` 账本（教训→`FAILURES.md`，结论→`EVIDENCE.yaml`）；AI 工具的私有记忆/备忘录只放工作偏好，**不得存放项目事实**。
{end}"""

OVERRIDES_BLOCK = """
{ob}
- 解释器：{py}；测试入口：`python -m unittest`（或本仓实际框架）。
- 受保护目录：见 `aiops/policy.yaml`（单一真源，守卫 hook 运行时读取）。
- 维护任务链已写入 aiops/TASK_QUEUE.yaml（每任务/每周/每月/里程碑/Spec 演进）。
{oe}"""

CHARTER_TEMPLATE = """# CHARTER — 本仓库的 AI 治理权威

版本：1（AI-REOS v{ver} 安装）
更新：{today}

本文件回答：**允许怎么工作、必须遵守什么**。它是治理权威；任务范围由 Task Spec 决定，事实真相由 Evidence 决定。
（完整设计规范见 AI_REOS_MASTER_SPEC_v{ver}.md；本文件是其运行时投影，保持精简。）

## 0. 三类权威彼此正交

1. **治理权威**：`CHARTER.md` → `protocols/` → 工具薄适配。回答"允许怎么工作"。
2. **任务权威**：已批准的 Task Spec（`specs/T-xxx.yaml`）→ 可选 Plan → 实际实现。回答"这次做什么、做到什么算完成"。
3. **事实权威**：可复算产物 + 脚本/测试 → `EVIDENCE.yaml` → `STATE.md` 摘要。回答"目前客观上知道什么"。

三者冲突时，不要让 `STATE.md`、聊天记录或 Agent 摘要覆盖更底层证据。

## 1. 硬边界（MUST NOT）

1. 不得把密钥、密码、Token、私有服务器凭据写入仓库任何文件或提示上下文；一律走环境变量或系统密钥库。
2. 不得删除或覆盖用户的既有配置，除非已备份且经人工批准。
3. 不得修改或删除原始数据与结果目录，除非任务明确要求且经批准。保护清单**单一真源**：
   `aiops/policy.yaml`（守卫 hook 运行时读取）；调整保护范围只改 policy.yaml，不改守卫脚本。
4. 不得把 Hypothesis 写成 Evidence；Agent 的自然语言结论不得升级为 Evidence。
5. 不得在未授权情况下执行：数据库迁移、生产操作、权限变更、破坏性命令、远程 push、部署、对外发消息、超预算计算。
6. 不得为了"顺手"扩大任务范围；发现无关问题只登记 `TASK_QUEUE.yaml`。

## 2. 必须遵守（MUST）

1. **先审计，后修改**：先识别结构、配置、测试入口、数据目录，再提最小改造方案。
2. **最小必要编辑**：优先 symbol / search 定位，不默认读取整个仓库或全部文档。
3. **每个改动都要有验收**：给出 changed files、理由、测试结果、未解决问题、回滚方式。
4. **科学结论必须分类**：FACT / INFERENCE / HYPOTHESIS / UNKNOWN 严格分开；相关性不得写成因果。
5. **昂贵实验先冒烟**：任何长耗时仿真 / GPU / 优化 / 大数据集任务前，先跑最便宜的 import 或单元冒烟测试。
6. **数值工作必须记账**：求解器、谐波截断、波长/频率约定、偏振、边界条件、材料模型、随机种子、设备、精度、数据集版本、输出目录。
7. **执行后必报告**：Changed files / Commands run / Test results / Remaining risks / Blocked decisions。

## 3. 产物区双区制

- `scratch/<工作线名>/`：活跃临时产物区。进行中的临时脚本与输出写这里，可随时清理。
- `archive/`：冻结封存区（write-once）。只由显式整理任务批量移入，移入后只读；活跃产出禁止写入。
- 沉淀流程在本仓首个整理任务时确定并登记到 STATE.md。

## 4. 分层 Routing

| 工作 | 首选执行 |
|---|---|
| 文件搜索、grep、列表 | 工具 |
| CSV 汇总、统计 | Python / 脚本 |
| 数值验证 | 独立脚本 + unittest |
| 非琐碎代码改动 | `aiops/skills/code-change/` 流程 |
| 科学解释/结论 | `aiops/skills/scientific-analysis/` 流程 |
| 仿真/实验 | `aiops/skills/experiment-run/` 流程 |
| 证据登记 | `aiops/skills/evidence-update/` 流程 |
| 核心主张审查 | `aiops/skills/red-team-review/` 流程 |

## 5. 确定性优先

能由脚本/hook/CI 保证的，不靠提示词：
- 提交门禁：pre-commit 运行 `aiops/checks/run_all_checks.py`（安装时自动挂载，用户 hook 链式保留）；
- 会话注入与破坏性命令守卫：支持 hooks 的工具（如 ZCode）参考 `adapters_templates/` 按需启用；
- CI 模板：`ci-template/ai-reos-ci.yml`（可选，复制到 `.github/workflows/` 后 PR/主分支强制校验）。

## 6. 需要人工批准的清单

- 删除/覆盖大量文件、受保护目录内容；
- 远程 push、部署、对外发消息；
- 权限变更、数据库迁移；
- 超预算的昂贵计算。

## 7. 运行时不加载 Master Spec

普通任务不得把 AI-REOS Master Spec 整份作为常驻上下文。
每月按 CHANGELOG 的维护节奏精简：删过时规则、重复 Skill、已被程序检查取代的 prompt 指令。
"""

STATE_TEMPLATE = """# Project State

state_id: S-001
updated: {today}

本文件只回答：现在在哪里、正在做什么、下一步是什么。目标是 1-3 页，不要写成项目百科。
事实细节以 `EVIDENCE.yaml` 为准；本文件的 "Confirmed facts" 只做索引。

## Current objective

（安装后填写：当前主线目标，1-3 句。）

## Confirmed facts

- （无。事实登记在 EVIDENCE.yaml，此处只放索引行。）

## Active hypotheses

- （无。）

## Current blockers

- （无。）

## Next 3 actions

1. （安装后填写。）

## Current code/data versions

- git: （branch / HEAD）
- 解释器: {py}
- 数据: （版本或"未变更"）
"""

EVIDENCE_TEMPLATE = """# 可复算事实记录。只记录"数据/源码/测试/可靠文献直接支持"的陈述。
# 因果解释不得写入 statement；未在本会话复算的记录必须在 limitations 中声明。
# 字段约定见 aiops/schemas/evidence.schema.json 与 aiops/protocols/evidence-protocol.md。

- schema_version: 1
  id: E-001
  type: observation
  statement: "安装验证：aiops/checks/run_all_checks.py 全部通过（安装日冒烟）。"
  source:
    file: aiops/checks/run_all_checks.py
    script: "python aiops/checks/run_all_checks.py"
    git_commit: "uncommitted"
  scope:
    cases: [安装冒烟]
    parameter_range: "N/A"
  limitations:
    - "仅验证治理文件结构齐备，不验证任何科研数值。"
  status: active
  recorded_at: "{today}"
"""

DECISIONS_TEMPLATE = """# DECISIONS — 已做选择及原因（`D-xxx`）

本文件只记录**已做出的选择**与理由。事实依据指向 `EVIDENCE.yaml`。

---

## D-001 — 采用 AI-REOS v{ver} 作为本仓治理系统

Date: {today}
Status: active
Decision: 安装 AI-REOS v{ver} kit；真源在 `aiops/`，工具侧只做薄适配；
产物区按 scratch（活跃）/archive（冻结）双区制管理。
Why: 跨工具规则漂移与仓库垃圾山是该系统针对的两个核心失败模式；
Spec 全文见 AI_REOS_MASTER_SPEC_v{ver}.md 与 CHANGELOG。
Alternatives rejected: 各工具独立维护规则（漂移）；无治理裸奔（重复踩坑）。
Revisit when: 出现 kit 未覆盖的工作模式且经实测验证后，按 CHANGELOG 流程回流 Spec。
"""

FAILURES_TEMPLATE = """# FAILURES — 已证伪或被否决的方法（`F-xxx`）

本文件用于**防止 AI 复活旧错误**。每条必须写清：被推翻的是什么、依据哪条证据、什么条件下才允许重新使用。

---

## F-001 — "活跃工作线的临时产物可以直接写进归档区"

Status: rejected
Reason: 归档区的定义是 write-once 冻结封存。活跃产出混入后生长态与封存态不可分，
归档区会重新膨胀成垃圾山（首次发现于一个力学超材料科研项目，两日两起）。
Evidence: AI_REOS_MASTER_SPEC_v{ver}.md §4；D-001。
Do not reuse unless: 不存在——活跃临时产物写 scratch/<工作线名>/，沉淀走显式整理任务。
"""

ASSUMPTIONS_TEMPLATE = """# ASSUMPTIONS — 未验证假设（`A-xxx`）

本文件记录**当前被依赖但尚未验证**的前提。假设不得被当作 Evidence；一旦被验证，
应转为 `EVIDENCE.yaml` 记录并从本文件移除。

---

## A-001 — 本机解释器路径

Status: unverified
Assumption: 本机默认解释器为安装 bootstrap 时填写的路径。
Source: bootstrap 交互输入。
Risk if wrong: pre-commit 门禁与校验脚本无法运行。
Verify how: `python aiops/checks/run_all_checks.py`。
"""

TASK_QUEUE_TEMPLATE = """# 任务队列：登记待办、无关发现与后续动作。
# 字段名与枚举值一律英文，说明文字用中文。Task Spec 模板见 aiops/specs/TEMPLATE.task.yaml。
#
# 使用规则：
#   1. 执行任务中发现的无关问题，登记到这里，不自动扩大当前任务 scope。
#   2. 进入实施的任务必须有对应 aiops/specs/T-xxx.yaml。
schema_version: 1

tasks:
  - id: T-001
    title: "MAINT-每任务：真源同步与收尾校验（常设）"
    status: ready
    priority: high
    owner: root-agent
    reason: "Master Spec §32：治理体系最大的失败模式是记录与实际脱节。"
    next_action: "每个非琐碎任务收尾时：STATE 翻新、事实登记 EVIDENCE、决策/失败入档、无关发现另立任务、run_all_checks 通过后再提交。"

  - id: T-002
    title: "MAINT-每周：scratch 沉淀 + 归档区反向扫描 + STATE 瘦身（常设）"
    status: ready
    priority: medium
    owner: root-agent
    reason: "Master Spec §32 每周档（v1.2）。"
    next_action: "每周一次：归档区内 mtime 新增文件迁回 scratch 并修路径；scratch 完成工作线批量沉淀入归档区并更新引用链接；STATE 剔除过期条目。"

  - id: T-003
    title: "MAINT-每月：always-on 精简 + 约束链盲测复跑（常设）"
    status: ready
    priority: medium
    owner: root-agent
    reason: "Master Spec §32 每月档 + Phase 7（v1.2）。"
    next_action: "每月一次：审计 always-on 文件长度；删已被程序检查取代的 prompt 指令；技能路由去重；复跑盲测探针验证约束链仍生效。"

  - id: T-004
    title: "MAINT-里程碑：科研快照 + Red Team（常设）"
    status: ready
    priority: high
    owner: root-agent
    reason: "Master Spec §32 里程碑档（v1.2）。"
    next_action: "里程碑时：冻结 STATE snapshot；记录数据/代码/环境版本；重建图表；核对 claim 有 Evidence 支撑；派独立 Red Team 攻击核心主张。"

  - id: T-005
    title: "MAINT-Spec：AI-REOS Master Spec 版本化演进（常设）"
    status: ready
    priority: high
    owner: root-agent
    reason: "系统级改动若只留在项目账本，Spec 与实施漂移（v1.2 新增）。"
    next_action: "系统级改动先记 DECISIONS 含证据；人工确认后回流 Spec（minor 升位）并在 CHANGELOG 登记；同步本仓 aiops/ 并跑 run_all_checks。"
"""

POLICY_TEMPLATE = """# AI-REOS 受保护目录清单（单一真源，v{ver}）
# guard_destructive.py 运行时读取本文件；修改保护范围只改这里，不要改守卫脚本。
schema_version: 1

protected_dirs:
  # 整目录保护：路径中出现该组件名即视为受保护（按路径组件匹配）
  components:
    - results
    - outputs
    - data
    - archive
    - runs
    - logs
    - model_parameters
    - .git
  # 子路径保护：带父目录前缀（避免误伤同名组件的其他用途）——按本项目实际填写
  subpaths: []
  # 前缀通配保护：parent/child* 形式——按本项目实际填写
  prefixes: []
"""

# 世界层项目骨架（v1.3.2，E-083/E-085）：只补缺失、永不覆盖——
# objects/ releases/ 与 *.generated.* 由项目自行生长/生成，不随 kit 分发。
COVERAGE_TEMPLATE = """# 世界观层覆盖声明（AI-REOS v{ver}；假设 D：索引必须显式声明覆盖范围与未命中语义）
# registry 未命中一律返回 UNKNOWN，绝不生成否定结论（"没找到"≠"不存在"）。
# 运行模式由本文件唯一决定：status: partial → 门禁 report-only；
# 全部对象登记完成并经 Decision 确认后改为 full → fail-closed。
schema_version: 1
coverage:
  status: partial
  scope: []                  # 按本项目需要声明覆盖 glob，如 ["docs/design/**/*.md"]
  exempt: []                 # 豁免登记：{{path, reason, owner, expires_at}}
"""

LEDGER_POLICY_TEMPLATE = """# 账本缩减与生命周期策略（canonical 原文不删，视图分层）
schema_version: 1
coverage_mode_link: "aiops/world/coverage.yaml"   # partial=report-only；full=enforce（唯一模式来源）

ledger_layers:
  active-core:
    rule: "被未完成 Task/Spec、adopted/draft binding、当前 Decision/Claim/Hypothesis/Failure rule、显式 pin 引用，或属于最近 2 个发行"
    view: "LEDGERS_CURRENT.generated.md"
  active-cold:
    rule: "仍有效但不在当前闭包；年龄未知；superseded 记录缺少完整替代映射"
    view: "默认不注入全文；按 ID/关键词召回"
  history:
    rule: "有完整 $whole 替代关系或显式 retired/aborted 且无当前引用；Task 另需 complete_age_releases >= 2"
    view: "LEDGERS_HISTORY.generated.md（原文保留）"

task_retirement:
  complete_age_releases: 2        # N=2：N=1 缺完整复核周期，N>2 延迟收缩
  reopen_rule: "reopen 后立即返回 current 层"
  baseline_rule: "首个发行（GR-0000）前已 complete 的任务登记 closed_in_release=GR-0000，不伪造完成日期"

evidence_supersession:
  schema_additions:
    - "status 增 partially_superseded"
    - "supersession[]: {{selector, successor_ids[], mode: $whole|$partial, reason, authority_ref}}"
  rules:
    - "只有 $whole 替代完整、后继存在、无环且无 current 反向引用时才允许进入 history"
    - "自由文本引用只能产生人工复核警告，不得触发自动降级"

decisions_failures:
  auto_expiry: false              # 首版不按年龄自动失效
  main_view: "只显示 ID/标题/状态/复用条件/当前引用"

known_supersessions: []           # 替代关系边：{{source, selector, successor_ids[], mode, reason, authority_ref}}
"""

INSTALL_REPORT_TEMPLATE = """# 安装报告（由 bootstrap 生成骨架，人工补全）

- 安装日期：{today}
- kit 版本：v{ver}
- 解释器：{py}
- 已安装：aiops/ 机制层 + 账本骨架 + policy.yaml + pre-commit 门禁 + AGENTS.md 区块
- 未安装（按需手工）：
  - 工具适配层：Trae（.trae/rules + skills 薄包装）、ZCode（.zcode/config.json，
    记得把模板里的 __AIOS_PYTHON__/__AIOS_PROJECT_DIR__ 换成本机绝对路径）、
    Cursor/Copilot/Gemini（参考 Spec §23）；模板见 kit 的 adapters_templates/
  - CI 门禁（可选）：把 ci-template/ai-reos-ci.yml 复制到 .github/workflows/
  - 受保护目录定制（policy.yaml 的 subpaths/prefixes）
  - 本仓首个 Task Spec
- 验证：python aiops/checks/run_all_checks.py → 应全部 PASS
"""

AIOPS_README_TEMPLATE = """# aiops/

跨工具治理真源。入口：`CHARTER.md`（规则）→ `STATE.md`（现状）→ `TASK_QUEUE.yaml`（待办）。
安装来源：AI-REOS kit v{ver}；托管机制文件清单与哈希见 `.reos-manifest.json`。
零基础教程与排障：kit 内 `TUTORIAL.md`。
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nhash(data: bytes) -> str:
    """CRLF 归一化哈希：升级决策用（zip/git 检出/工作区的行尾形态差异不应判 CONFLICT）。"""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _load_old_manifest(aiops_dir: Path) -> dict:
    """读取上次安装写入的 manifest；无/损坏返回空 dict（视为全部 ADD/CONFLICT 判定）。"""
    path = aiops_dir / MANIFEST_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
            return payload
    except (OSError, ValueError):
        pass
    return {}


def _load_baselines() -> "list[dict]":
    """载入 migration_baselines/*.json（旧版官方哈希），损坏的跳过。"""
    out = []
    base_dir = KIT_ROOT / BASELINES_DIRNAME
    if not base_dir.is_dir():
        return out
    for f in sorted(base_dir.glob("*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
                out.append(payload)  # 基线哈希由生成脚本按归一化规则产出
        except (OSError, ValueError):
            continue
    return out


def decide_upgrade(dst_exists: bool, kit_hash: str, dst_hash: str,
                   old_hash: "str | None", baseline_hashes: "set[str]") -> str:
    """
    托管文件升级决策（纯函数，v1.3.1 重构以便单测）。

    - dst 不存在 → ADD；
    - 与 kit 一致 → KEEP；
    - 与上次安装 manifest 一致（用户没改）→ UPDATE；
    - 无 manifest 但与任一官方旧版基线一致（官方 v1.2 文件，用户没改）→ UPDATE；
    - 其余（用户改过且与 kit 不同）→ CONFLICT。
    """
    if not dst_exists:
        return "ADD"
    if dst_hash == kit_hash:
        return "KEEP"
    if old_hash is not None and dst_hash == old_hash:
        return "UPDATE"
    if old_hash is None and dst_hash in baseline_hashes:
        return "UPDATE"
    return "CONFLICT"


def _iter_mechanism_files() -> "list[tuple[Path, str]]":
    """返回 [(kit 文件绝对路径, 相对 aiops 的目标路径 posix)]，稳定排序。"""
    out = []
    for d in MECHANISM_DIRS:
        src_dir = KIT_ROOT / d
        if not src_dir.is_dir():
            continue
        for f in sorted(src_dir.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            rel = (Path(d) / f.relative_to(src_dir)).as_posix()
            out.append((f, rel))
    return out


def install_mechanism(repo: Path, dry: bool, report: "list[str]") -> dict:
    """按 manifest 决策安装托管机制文件；返回新 manifest 的 files 映射。"""
    aiops = repo / "aiops"
    old = _load_old_manifest(aiops)
    old_files = old.get("files", {})
    baselines = _load_baselines()
    new_files: dict = {}
    counts = {"ADD": 0, "UPDATE": 0, "KEEP": 0, "CONFLICT": 0}
    for src, rel in _iter_mechanism_files():
        dst = aiops / rel
        kit_bytes = src.read_bytes()
        kit_hash = _nhash(kit_bytes)
        new_files[rel] = kit_hash
        dst_hash = _nhash(dst.read_bytes()) if dst.is_file() else ""
        action = decide_upgrade(
            dst.is_file(), kit_hash, dst_hash,
            old_files.get(rel),
            {b["files"][rel] for b in baselines if rel in b["files"]},
        )
        if rel == PRECOMMIT_TEMPLATE_REL and action == "CONFLICT":
            # 特例：v1.2 会把解释器渲染进这份"版本化模板"（v1.2 缺陷），所以它的
            # 本地内容永远不可能匹配任何基线。它是自家工件而非用户内容——强制 UPDATE。
            action = "UPDATE"
            report.append(f"UPDATE  {rel}（v1.2 渲染产物，重置为 kit 模板；真实 hook 由本步骤稍后渲染到 git hooks）")
        counts[action] += 1
        if action == "ADD":
            report.append(f"ADD     {rel}")
            if not dry:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        elif action == "UPDATE":
            report.append(f"UPDATE  {rel}（自上次安装后未被修改，自动升级）")
            if not dry:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        elif action == "KEEP":
            report.append(f"KEEP    {rel}")
        else:
            new_name = dst.with_name(dst.name + ".reos-new")
            report.append(
                f"CONFLICT {rel}：本地有手工修改，未覆盖；kit 新版已写到 {new_name.name}，请人工合并"
            )
            if not dry:
                dst.parent.mkdir(parents=True, exist_ok=True)
                new_name.write_bytes(kit_bytes)
    # kit 已删除的托管文件：只提示
    for rel in sorted(set(old_files) - set(new_files)):
        report.append(f"REMOVED? {rel}：当前 kit 已不含该托管文件（可能是升级删除），请人工确认后自行处理")
    if not dry:
        manifest = {
            "kit_version": KIT_VERSION,
            "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": new_files,
        }
        (aiops / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    report.append(
        f"小结：ADD={counts['ADD']} UPDATE={counts['UPDATE']} "
        f"KEEP={counts['KEEP']} CONFLICT={counts['CONFLICT']}"
    )
    return new_files


def _write_if_missing(path: Path, repo: Path, text: str, dry: bool, report: "list[str]") -> bool:
    if path.exists():
        report.append(f"KEEP    {path.relative_to(repo)}（账本/骨架永不覆盖）")
        return False
    report.append(f"ADD     {path.relative_to(repo)}")
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return True


def _git_hooks_dir(repo: Path) -> Path:
    """
    解析 hooks 目录（v1.3：兼容 linked worktree）。

    `git rev-parse --git-path hooks` 在主仓库与 linked worktree 下都返回正确的
    hooks 目录（worktree 的 .git 是文件不是目录，v1.2 直接拼 <repo>/.git/hooks
    会 NotADirectoryError）。git 不可用时回退 <repo>/.git/hooks。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=15, check=True,
        )
        hooks = Path(out.stdout.strip())
        if not hooks.is_absolute():
            hooks = repo / hooks
        return hooks
    except (OSError, subprocess.SubprocessError):
        return repo / ".git" / "hooks"


def install_pre_commit(repo: Path, py: str, dry: bool, report: "list[str]") -> None:
    """
    渲染并挂载 pre-commit 门禁（v1.3 重写）。

    - 模板永远从 KIT_ROOT/checks/hooks/pre-commit 读取：目标仓 aiops/ 里那份
      版化副本保持 __AIOS_PYTHON__ 占位符不被回写（v1.2 的缺陷：占位符被替换后
      换解释器重装无法再更新）；
    - 已存在的 hook 备份为 pre-commit.user.bak 并被新 hook 链式调用（用户自己的
      门禁不丢失）；
    - hooks 目录用 git-path 解析（worktree 兼容）。
    """
    template = KIT_ROOT / "checks" / "hooks" / "pre-commit"
    if not template.is_file():
        report.append("SKIP    pre-commit（kit 未含模板）")
        return
    hooks_dir = _git_hooks_dir(repo)
    rendered = template.read_text(encoding="utf-8").replace("__AIOS_PYTHON__", py or "python")
    hook = hooks_dir / "pre-commit"
    if hook.is_file():
        current = hook.read_text(encoding="utf-8")
        if current == rendered:
            report.append(f"KEEP    {hook}（已是当前渲染结果）")
            return
        if AI_REOS_HOOK_MARKER in current:
            # v1.3.1：这是 AI-REOS 上次自己装的 hook——直接替换，
            # 不备份不链式（v1.3.0 会把自己当用户 hook 备份并链式执行两遍旧检查）
            report.append(f"UPDATE  {hook.name}（检测到 AI-REOS marker，替换自家旧版，不链式）")
            if not dry:
                hooks_dir.mkdir(parents=True, exist_ok=True)
                hook.write_text(rendered, encoding="utf-8")
                try:
                    hook.chmod(0o755)
                except OSError:
                    pass
            return
        bak = hooks_dir / "pre-commit.user.bak"
        if not bak.exists():
            if not dry:
                shutil.copy2(hook, bak)
            report.append(f"BACKUP  既有 hook → {bak.name}（新 hook 将链式调用它）")
        else:
            report.append(f"KEEP    {bak.name}（已存在，未覆盖）")
    if not dry:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook.write_text(rendered, encoding="utf-8")
        try:
            hook.chmod(0o755)
        except OSError:
            pass
    report.append(f"INSTALL {hook}")


def _write_if_missing_repo_root(repo: Path, name: str, text: str, dry: bool, report: "list[str]") -> bool:
    path = repo / name
    if path.exists():
        report.append(f"KEEP    {name}")
        return False
    report.append(f"ADD     {name}")
    if not dry:
        path.write_text(text, encoding="utf-8")
    return True


def patch_agents(repo: Path, py: str, dry: bool, report: "list[str]") -> None:
    agents = repo / "AGENTS.md"
    block = AGENTS_BLOCK.format(begin=AGENTS_BEGIN, end=AGENTS_END)
    overrides = OVERRIDES_BLOCK.format(ob=OVERRIDES_BEGIN, oe=OVERRIDES_END, py=py or "PATH python")
    if not agents.exists():
        report.append("ADD     AGENTS.md（generated 区块 + LOCAL OVERRIDES 骨架）")
        if not dry:
            agents.write_text(block + "\n" + overrides + "\n", encoding="utf-8")
        return
    text = agents.read_text(encoding="utf-8")
    changed = False
    if AGENTS_BEGIN in text and AGENTS_END in text:
        pre = text[: text.index(AGENTS_BEGIN)]
        post = text[text.index(AGENTS_END) + len(AGENTS_END):]
        text = pre + block + post
        changed = True
        report.append("UPDATE  AGENTS.md generated 区块（其余内容保留）")
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
        changed = True
        report.append("APPEND  AGENTS.md 追加 generated 区块")
    if OVERRIDES_BEGIN not in text:
        text = text.rstrip("\n") + "\n" + overrides + "\n"
        report.append("APPEND  AGENTS.md LOCAL OVERRIDES 骨架")
    if changed and not dry:
        agents.write_text(text, encoding="utf-8")


def install_world_skeleton(repo: Path, dry: bool, report: "list[str]") -> None:
    """世界层项目骨架（只补缺失）+ 初始视图生成（v1.3.2，E-083/E-085）。

    - coverage.yaml / ledger-policy.yaml：项目骨架，永不覆盖（账本策略）；
    - objects/ releases/ 由项目自行生长，不创建空目录（工具对缺失目录返回空集）；
    - 安装后用当前解释器跑一次 builder 生成初始空视图（空 registry 也生成，
      保证 builder --check 一致与 verify 的视图检查可过）；失败只告警不阻塞。
    """
    aiops = repo / "aiops"
    for rel, tpl in (
        ("world/coverage.yaml", COVERAGE_TEMPLATE),
        ("world/ledger-policy.yaml", LEDGER_POLICY_TEMPLATE),
    ):
        _write_if_missing(aiops / rel, repo, tpl.format(ver=KIT_VERSION, today=today_str()), dry, report)
    builder = aiops / "world" / "tools" / "build_world_registry.py"
    if dry or not builder.is_file():
        return
    try:
        r = subprocess.run([sys.executable, str(builder)], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            report.append("BUILD   world/registry.generated.yaml + CURRENT.generated.md（初始视图）")
        else:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            report.append(f"WARN    世界层初始视图生成失败（{tail[-1] if tail else r.returncode}）——"
                          "不影响安装；世界层按 UNKNOWN 语义降级")
    except (OSError, subprocess.SubprocessError) as exc:
        report.append(f"WARN    世界层初始视图生成跳过（{exc}）")


def today_str() -> str:
    return datetime.date.today().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-REOS kit v1.3.2 bootstrap")
    ap.add_argument("--repo", default=".", help="目标仓库根目录（默认当前目录）")
    ap.add_argument("--python", default="", help="本机解释器路径（不填则交互询问；--dry-run 下免询问）")
    ap.add_argument("--dry-run", action="store_true", help="预览模式：不询问、不改盘，打印逐文件动作")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    report: "list[str]" = []

    if args.dry_run:
        py = args.python or "<dry-run 未询问>"
    else:
        py = args.python or input("本机 Python 解释器路径（直接回车则用 PATH 中的 python）: ").strip()

    if not (repo / ".git").exists() and not args.dry_run:
        print(f"[warn] {repo} 不是 git 仓库——pre-commit 门禁可能无法挂载")

    today = today_str()
    fmt = {"today": today, "ver": KIT_VERSION, "py": py or "PATH python"}
    aiops = repo / "aiops"

    print(f"== AI-REOS kit v{KIT_VERSION} bootstrap → {repo} {'（dry-run）' if args.dry_run else ''} ==")

    print("[1/6] 托管机制层（manifest 哈希升级）")
    install_mechanism(repo, args.dry_run, report)

    print("[2/6] 账本骨架（永不覆盖）")
    ledgers = {
        "CHARTER.md": CHARTER_TEMPLATE.format(**fmt),
        "STATE.md": STATE_TEMPLATE.format(**fmt),
        "EVIDENCE.yaml": EVIDENCE_TEMPLATE.format(**fmt),
        "DECISIONS.md": DECISIONS_TEMPLATE.format(**fmt),
        "FAILURES.md": FAILURES_TEMPLATE.format(**fmt),
        "ASSUMPTIONS.md": ASSUMPTIONS_TEMPLATE.format(**fmt),
        "TASK_QUEUE.yaml": TASK_QUEUE_TEMPLATE.format(**fmt),
        "policy.yaml": POLICY_TEMPLATE.format(**fmt),
        "README.md": AIOPS_README_TEMPLATE.format(**fmt),
        "AIOS_INSTALL_REPORT.md": INSTALL_REPORT_TEMPLATE.format(**fmt),
    }
    for name, tpl in ledgers.items():
        _write_if_missing(aiops / name, repo, tpl, args.dry_run, report)
    for f in sorted((KIT_ROOT / "specs_template").glob("*")):
        if ".example" in f.name:
            continue  # 示例不装进 specs（v1.3：避免被当成真实任务）
        _write_if_missing(aiops / "specs" / f.name, repo, f.read_text(encoding="utf-8"), args.dry_run, report)
    _write_if_missing(aiops / "experiments" / "registry.yaml", repo,
                      "schema_version: 1\nexperiments: []\n", args.dry_run, report)
    _write_if_missing(aiops / "experiments" / "TEMPLATE.experiment.yaml", repo,
                      (KIT_ROOT / "templates_ledger" / "TEMPLATE.experiment.yaml").read_text(encoding="utf-8"),
                      args.dry_run, report)

    print("[3/6] 世界层骨架与初始视图（E-083/E-085）")
    install_world_skeleton(repo, args.dry_run, report)

    print("[4/6] pre-commit 门禁（备份 + 链式 + worktree 兼容）")
    install_pre_commit(repo, py, args.dry_run, report)

    print("[5/6] AGENTS.md 区块")
    patch_agents(repo, py, args.dry_run, report)

    print("[6/6] 版本与清单")
    # VERSION 是托管元数据而非账本：每次安装安全覆盖
    if not args.dry_run:
        (aiops / "VERSION").write_text(KIT_VERSION + chr(10), encoding="utf-8")
    report.append(f"SET     aiops/VERSION = {KIT_VERSION}")
    if not args.dry_run:
        manifest = {
            "kit_version": KIT_VERSION,
            "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": _load_old_manifest(aiops).get("files", {}),
        }
        # install_mechanism 已写 manifest；此处仅确保 README 指针提示存在

    print()
    for line in report:
        print("  " + line)
    print()
    print("后续手工步骤：")
    print("  - 安装可选依赖：pip install -r " + str(KIT_ROOT / "requirements.txt") + "（PyYAML 必需；jsonschema 启用世界层 schema 校验）")
    print("  - 按项目实际填写 aiops/policy.yaml 的 subpaths/prefixes（保护清单单一真源）")
    print("  - 世界层启用：向 aiops/world/objects/ 登记对象后跑 builder 重建视图；coverage 全分类后经 Decision 升 full")
    print("  - 按所用工具从 adapters_templates/ 生成适配层（ZCode 模板需替换 __AIOS_PYTHON__/__AIOS_PROJECT_DIR__ 为绝对路径）")
    print("  - 可选：ci-template/ai-reos-ci.yml 复制到 .github/workflows/ 启用 CI 门禁")
    print("  - 跑 python aiops/checks/run_all_checks.py 确认 PASS；治理代码单测：python -m unittest discover -s aiops/tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
