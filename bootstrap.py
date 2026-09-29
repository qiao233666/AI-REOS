#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI-REOS kit bootstrap：把 kit 安装到目标仓库，生成本机适配层。

用法（在目标仓库根目录执行）：
    python /path/to/AI_REOS_kit_v1.2/bootstrap.py --dry-run   # 预览
    python /path/to/AI_REOS_kit_v1.2/bootstrap.py             # 安装

做什么：
1. 拷贝 kit 的机制层到 <repo>/aiops/（protocols/skills/schemas/templates/checks，
   不覆盖已存在的同名文件——可重复运行、增量安装）；
2. 生成本机账本骨架（STATE/EVIDENCE/DECISIONS/FAILURES/ASSUMPTIONS/TASK_QUEUE/
   CHARTER），已存在的一律跳过（不覆盖项目记忆）；
3. 交互式询问本机解释器路径，写入：
   - aiops/checks/hooks/pre-commit（替换占位符 __AIOS_PYTHON__）
   - .git/hooks/pre-commit（git 门禁，立即生效）
4. 生成根 AGENTS.md 的 AI-REOS generated 区块（已存在区块则更新，原文保留）；
5. 打印后续手工步骤（工具适配层：Trae/ZCode/Cursor 等按需生成）。

设计原则（AI-REOS Master Spec §0/§23）：
- 真源一份（aiops/），工具适配只做薄 delta；
- 不删除、不覆盖用户既有配置；
- 安装完成后必须能跑通 aiops/checks/run_all_checks.py。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent

# 机制层：整体拷贝，文件已存在则跳过
MECHANISM_DIRS = ["protocols", "skills", "schemas", "templates", "checks"]

# 账本骨架：不存在才生成
LEDGER_FILES = [
    "STATE.md",
    "EVIDENCE.yaml",
    "DECISIONS.md",
    "FAILURES.md",
    "ASSUMPTIONS.md",
    "TASK_QUEUE.yaml",
]

AGENTS_BEGIN = "<!-- BEGIN GENERATED (source: aiops/CHARTER.md) — DO NOT EDIT THIS BLOCK DIRECTLY. -->"
AGENTS_END = "<!-- END GENERATED -->"
OVERRIDES_BEGIN = "<!-- BEGIN LOCAL OVERRIDES -->"
OVERRIDES_END = "<!-- END LOCAL OVERRIDES -->"

OVERRIDES_BLOCK = """
{ob}
- 解释器：{py}；测试入口：`python -m unittest`（或本仓实际框架）。
- 受保护结果目录：（在 CHARTER.md §7 登记后，此处列一份清单）
- 维护任务链已写入 aiops/TASK_QUEUE.yaml（每任务/每周/每月/里程碑/Spec 演进）。
{oe}"""

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
{end}"""

CHARTER_TEMPLATE = """# CHARTER — 本仓库的 AI 治理权威

版本：1（AI-REOS v1.2 安装）
更新：{today}

本文件回答：**允许怎么工作、必须遵守什么**。它是治理权威；任务范围由 Task Spec 决定，事实真相由 Evidence 决定。
（完整设计规范见 AI_REOS_MASTER_SPEC_v1.2.md；本文件是其运行时投影，保持精简。）

## 0. 三类权威彼此正交

1. **治理权威**：`CHARTER.md` → `protocols/` → 工具薄适配。回答"允许怎么工作"。
2. **任务权威**：已批准的 Task Spec（`specs/T-xxx.yaml`）→ 可选 Plan → 实际实现。回答"这次做什么、做到什么算完成"。
3. **事实权威**：可复算产物 + 脚本/测试 → `EVIDENCE.yaml` → `STATE.md` 摘要。回答"目前客观上知道什么"。

三者冲突时，不要让 `STATE.md`、聊天记录或 Agent 摘要覆盖更底层证据。

## 1. 硬边界（MUST NOT）

1. 不得把密钥、密码、Token、私有服务器凭据写入仓库任何文件或提示上下文；一律走环境变量或系统密钥库。
2. 不得删除或覆盖用户的既有配置，除非已备份且经人工批准。
3. 不得修改或删除原始数据与结果目录（在下方 §7 "本仓受保护目录"中登记），除非任务明确要求且经批准。
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
- 沉淀流程与本仓受保护目录清单在首次整理任务时确定并登记到 STATE.md。

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
- 提交门禁：pre-commit 运行 `aiops/checks/run_all_checks.py`（安装时自动挂载）；
- 会话注入与破坏性命令守卫：支持 hooks 的工具（如 ZCode）参考 kit 内
  `adapters_templates/` 按需启用；不支持的靠 AGENTS.md 指针兜底。

## 6. 需要人工批准的清单

- 删除/覆盖大量文件、结果目录、归档区内容；
- 远程 push、部署、对外发消息；
- 权限变更、数据库迁移；
- 超预算的昂贵计算。

## 7. 本仓受保护目录（安装时填写）

<!-- bootstrap 后由人工补充：本仓库的结果/数据/大产物目录清单。
     示例：results/, outputs/, runs/, model_parameters/, data/ -->

## 8. 运行时不加载 Master Spec

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
- 解释器: （填写本机解释器路径）
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

## D-001 — 采用 AI-REOS v1.2 作为本仓治理系统

Date: {today}
Status: active
Decision: 安装 AI-REOS v1.2 kit；真源在 `aiops/`，工具侧只做薄适配；
产物区按 scratch（活跃）/archive（冻结）双区制管理。
Why: 跨工具规则漂移与仓库垃圾山是该系统针对的两个核心失败模式；
Spec 全文见 AI_REOS_MASTER_SPEC_v1.2.md 与 CHANGELOG。
Alternatives rejected: 各工具独立维护规则（漂移）；无治理裸奔（重复踩坑）。
Revisit when: 出现 kit 未覆盖的工作模式且经实测验证后，按 CHANGELOG 流程回流 Spec。
"""

FAILURES_TEMPLATE = """# FAILURES — 已证伪或被否决的方法（`F-xxx`）

本文件用于**防止 AI 复活旧错误**。每条必须写清：被推翻的是什么、依据哪条证据、什么条件下才允许重新使用。

---

## F-001 — "活跃工作线的临时产物可以直接写进归档区"

Status: rejected
Reason: 归档区的定义是 write-once 冻结封存。活跃产出混入后生长态与封存态不可分，
归档区会重新膨胀成垃圾山（首次发现于 RCWA_metamaterial 项目，两日两起）。
Evidence: AI_REOS_MASTER_SPEC_v1.2.md §4；D-001。
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

README_TEMPLATE = """# 安装报告（由 bootstrap 生成骨架，人工补全）

- 安装日期：{today}
- 目标仓库：（填写）
- 解释器：（填写）
- 已安装：aiops/ 机制层 + 账本骨架 + pre-commit 门禁 + AGENTS.md generated 区块
- 未安装（按需手工）：
  - 工具适配层：Trae（.trae/rules + skills 薄包装）、ZCode（.zcode/config.json）、
    Cursor/Copilot/Gemini（参考 Spec §23）；模板见 kit 的 adapters_templates/
  - 受保护目录清单（CHARTER §7）
  - 本仓首个 Task Spec
- 验证：python aiops/checks/run_all_checks.py → 应全部 PASS
"""


def _copy_mechanism(repo: Path, dry: bool) -> list:
    installed = []
    for d in MECHANISM_DIRS:
        src = KIT_ROOT / d
        if not src.is_dir():
            continue
        for f in src.rglob("*"):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(src)
            dst = repo / "aiops" / d / rel
            if dst.exists():
                continue
            installed.append(dst)
            if not dry:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
    return installed


def _write_if_missing(path: Path, text: str, dry: bool) -> bool:
    if path.exists():
        return False
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return True


def _patch_pre_commit(repo: Path, py: str, dry: bool) -> str:
    """替换 kit 版 pre-commit 里的解释器占位符，并挂载到 .git/hooks。"""
    src = repo / "aiops" / "checks" / "hooks" / "pre-commit"
    if not src.exists():
        return "skip（kit 未含 pre-commit）"
    text = src.read_text(encoding="utf-8")
    if "__AIOS_PYTHON__" in text:
        if not dry:
            src.write_text(text.replace("__AIOS_PYTHON__", py), encoding="utf-8")
    hook = repo / ".git" / "hooks" / "pre-commit"
    if dry:
        return "dry-run"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, hook)
    return "installed"


def _patch_agents(repo: Path, py: str, dry: bool) -> str:
    agents = repo / "AGENTS.md"
    block = AGENTS_BLOCK.format(begin=AGENTS_BEGIN, end=AGENTS_END)
    overrides = OVERRIDES_BLOCK.format(ob=OVERRIDES_BEGIN, oe=OVERRIDES_END, py=py or "PATH python")
    if not agents.exists():
        if not dry:
            agents.write_text(block + "\n" + overrides + "\n", encoding="utf-8")
        return "created"
    text = agents.read_text(encoding="utf-8")
    changed = False
    if AGENTS_BEGIN in text and AGENTS_END in text:
        pre = text[: text.index(AGENTS_BEGIN)]
        post = text[text.index(AGENTS_END) + len(AGENTS_END):]
        text = pre + block + post
        changed = True
        action = "updated（仅替换 generated 区块，其余保留）"
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
        changed = True
        action = "appended"
    if OVERRIDES_BEGIN not in text:
        text = text.rstrip("\n") + "\n" + overrides + "\n"
        action += "；已补 LOCAL OVERRIDES 骨架"
    if changed and not dry:
        agents.write_text(text, encoding="utf-8")
    return action


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-REOS kit bootstrap")
    ap.add_argument("--repo", default=".", help="目标仓库根目录（默认当前目录）")
    ap.add_argument("--python", default="", help="本机解释器路径（不填则交互询问）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"[warn] {repo} 不是 git 仓库——pre-commit 门禁将无法挂载")
    py = args.python or input("本机 Python 解释器路径（直接回车则用 PATH 中的 python）: ").strip()

    today = date.today().isoformat()
    aiops = repo / "aiops"

    mech = _copy_mechanism(repo, args.dry_run)
    print(f"[1] 机制层：{'将安装' if args.dry_run else '已安装'} {len(mech)} 个文件")

    ledgers = {
        "CHARTER.md": CHARTER_TEMPLATE.format(today=today),
        "STATE.md": STATE_TEMPLATE.format(today=today),
        "EVIDENCE.yaml": EVIDENCE_TEMPLATE.format(today=today),
        "DECISIONS.md": DECISIONS_TEMPLATE.format(today=today),
        "FAILURES.md": FAILURES_TEMPLATE.format(today=today),
        "ASSUMPTIONS.md": ASSUMPTIONS_TEMPLATE.format(today=today),
        "TASK_QUEUE.yaml": TASK_QUEUE_TEMPLATE.format(today=today),
        "README.md": "# aiops/\n\n跨工具治理真源。入口：CHARTER.md（规则）→ STATE.md（现状）→ TASK_QUEUE.yaml（待办）。\n",
        "AIOS_INSTALL_REPORT.md": README_TEMPLATE.format(today=today),
    }
    n = 0
    for name, tpl in ledgers.items():
        if _write_if_missing(aiops / name, tpl, args.dry_run):
            n += 1
    # specs 模板
    for f in (KIT_ROOT / "specs_template").glob("*"):
        if _write_if_missing(aiops / "specs" / f.name, f.read_text(encoding="utf-8"), args.dry_run):
            n += 1
    # experiments/ 骨架（校验脚本要求该目录存在）
    if _write_if_missing(aiops / "experiments" / "registry.yaml",
                         "schema_version: 1\nexperiments: []\n", args.dry_run):
        n += 1
    print(f"[2] 账本骨架：{'将生成' if args.dry_run else '已生成'} {n} 个（已存在的一律跳过，不覆盖项目记忆）")

    status = _patch_pre_commit(repo, py, args.dry_run)
    print(f"[3] pre-commit 门禁：{status}（解释器：{py or 'PATH python'}；跳过用 --no-verify）")

    status = _patch_agents(repo, py, args.dry_run)
    print(f"[4] AGENTS.md generated 区块：{status}")

    print("[5] 后续手工步骤：")
    print("    - 补 CHARTER.md §7 受保护目录清单")
    print("    - 按所用工具从 adapters_templates/ 生成适配层（Trae/ZCode/...）")
    print("    - 跑 python aiops/checks/run_all_checks.py 确认 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
