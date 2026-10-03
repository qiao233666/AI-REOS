# AI-REOS v1.3 安装包（kit）

**AI-REOS** 是一套给"AI 编程助手 + 科研代码仓库"用的治理系统。它解决的问题只有一个：
**让 AI 长期在同一个项目里工作时，不重复踩坑、不乱改东西、结论有据可查。**

- **只想快速用起来** → 看本文件 §三（5 分钟安装）
- **想搞懂原理、能自己排障** → 看 `TUTORIAL.md`（四层架构逐层讲解 + 排障索引）
- **完整设计规范** → `AI_REOS_MASTER_SPEC_v1.2.md`（30+ 章节，参考书）

---

## 一、它在解决什么问题？（为什么需要）

没有治理系统时，AI 助手在科研项目里的典型事故：

| 事故 | 后果 | AI-REOS 的对策 |
|---|---|---|
| AI 忘了之前踩过的坑，同一种数值 bug 犯三遍 | 浪费算力与时间 | `FAILURES.md`：所有被证伪的方法记录在案，AI 开工前必读 |
| AI 说"测试应该能过"，实际没跑 | 假进度 | 校验脚本 + 提交门禁：不跑完检查提交不了 |
| AI 把"我猜是这样"写成"已确认" | 错误结论进论文 | `EVIDENCE.yaml`：结论必须挂可复算证据，禁止口说无凭 |
| 仓库被临时脚本、日志、大文件堆成垃圾山 | 谁都不敢动 | scratch（活跃）/ archive（冻结）双区制 |
| 换一个 AI 工具（Trae/Cursor/ZCode...），规矩全失效 | 治理断裂 | 真源一份（`aiops/`），各工具只挂薄适配 |

## 二、目录里有什么

```text
AI_REOS_kit_v1.3.2/
├── bootstrap.py                  ← 安装器（唯一需要执行的文件）
├── README.md                     ← 本文件（快速上手）
├── TUTORIAL.md                   ← 原理教程（四层架构/每层为什么/排障索引）
├── requirements.txt              ← 可选依赖（PyYAML 必需；jsonschema 启用世界层 schema 校验）
├── AI_REOS_MASTER_SPEC_v1.2.md   ← 完整设计规范（参考书；Spec 版本与 kit 版本独立）
├── AI_REOS_MASTER_SPEC_CHANGELOG.md  ← 版本变更记录
├── AI_REOS_MASTER_SPEC_v1.1.original.md ← v1.1 原文（存档）
│
├── protocols/                    ← 7 个工作流程规范（怎么改代码/跑实验/下结论…）
├── skills/                       ← 6 个技能（AI 按任务类型路由到对应流程）
├── schemas/                      ← 5 个数据格式定义（机器校验用；v1.3.2 增世界层对象 schema）
├── templates/                    ← 决策/证据/交接等模板
├── checks/                       ← 校验脚本 + git 门禁 + 守卫 hook（强制层）
├── tests/                        ← 治理代码回归测试（含世界层 builder/verifier 测试）
├── world/tools/                  ← 世界观层工具（构建器/校验器/账本分层视图，v1.3.2）
├── specs_template/               ← 任务合同模板
├── templates_ledger/             ← 实验登记模板
├── adapters_templates/           ← 各 AI 工具的适配层模板（Trae/ZCode 等）
└── aiops_root_examples/          ← CHARTER/README 示例（bootstrap 会自动生成骨架）
```

**你不需要逐个读这些文件。** 装好后，AI 会在需要时自己去读对应文件。

## 三、5 分钟安装

前提：目标仓库是个 git 仓库；本机有 Python 3.10+（标准库 + PyYAML；可选 jsonschema）。

```bash
cd /path/to/your-repo
python /path/to/AI_REOS_kit_v1.3.2/bootstrap.py --dry-run   # 先预览装什么
python /path/to/AI_REOS_kit_v1.3.2/bootstrap.py             # 实际安装
pip install -r /path/to/AI_REOS_kit_v1.3.2/requirements.txt # 可选依赖（建议）
```

bootstrap 会：
1. 把机制层拷进 `aiops/`（按 manifest 哈希安全升级——账本永不覆盖；托管机制文件未被你修改则自动升级，你改过则生成 `.reos-new` 冲突副本供人工合并）；
2. 生成账本骨架（STATE/EVIDENCE/DECISIONS/FAILURES/ASSUMPTIONS/TASK_QUEUE/CHARTER）；
3. 生成世界层骨架（`aiops/world/coverage.yaml` + `ledger-policy.yaml`，只补缺失）并生成一次初始视图（v1.3.2）；
4. 问你要本机 Python 路径，装好 **pre-commit 提交门禁**（以后每次 git commit 自动跑 6 项校验）；
5. 在根 `AGENTS.md` 追加 AI-REOS 区块（已有区块则只更新该区块，其余不动）。

装完验证：

```bash
python aiops/checks/run_all_checks.py    # 应显示 6 项全 PASS（世界层骨架为 report-only）
```

**世界层（v1.3.2）是什么**：给项目建一份机器可校验的"世界观索引"——环境（哪个求解器装在哪、许可证是否验证过）、设计文档（哪份任务书在管哪个参数）。AI 判断"某东西存不存在"前先查 `aiops/world/CURRENT.generated.md`，查不到只能记 UNKNOWN，不许推出"不存在"。骨架装好即用（空索引，report-only）；往 `aiops/world/objects/` 登记对象后跑 `python aiops/world/tools/build_world_registry.py` 重建视图即可生长。

## 四、装完之后，日常怎么用（给人和给 AI 的约定）

**你（人）只需要记住三件事：**

1. **开工时**对 AI 说一句："先读 aiops/STATE.md，按治理规则做 `<任务>`"；
2. **收工时**提交代码——门禁会自动校验，失败了按提示修（急事可 `git commit --no-verify` 绕过）；
3. **有新认知时**（踩了坑/做了决定/验证了事实），让 AI "登记进 aiops"——它会写进对应账本。

**AI 侧的自动行为**（装好后它会这样做，你不用管细节）：
- 任务开始 → 读 `STATE.md` 了解现状；下结论前 → 查 `EVIDENCE.yaml`、避开 `FAILURES.md` 里的坑；
- 改代码 → 走 `skills/code-change` 流程；跑实验 → 走 `skills/experiment-run` 流程（强制记账十项参数）；
- 发现无关问题 → 登记进 `TASK_QUEUE.yaml`，不顺手乱改。

## 五、账本文件速查（装完后在 aiops/ 里）

| 文件 | 一句话 | 谁在写 |
|---|---|---|
| `STATE.md` | 项目现在在哪、下一步干嘛 | 每次任务的收尾 |
| `EVIDENCE.yaml` | "什么是被证实的"——每条挂可复算来源 | 验证完成后 |
| `DECISIONS.md` | "为什么这么选"——含被否决的方案 | 每次决策后 |
| `FAILURES.md` | "什么路走不通"——防止复活旧错误 | 每次证伪后 |
| `ASSUMPTIONS.md` | "还没验证但正在依赖的前提" | 发现隐患时 |
| `TASK_QUEUE.yaml` | 待办与维护任务队列 | 随时登记 |

## 六、可选：接上各 AI 工具的适配层

真源永远在 `aiops/`，适配层只是"指路牌"，按你用的工具挑着装（模板在 `adapters_templates/`）：

- **ZCode**：`.zcode/config.json` → 每次会话自动注入状态指针 + 拦截危险删除命令；
- **Trae**：`.trae/rules/aiops.md`（常驻规则）+ 6 个技能薄包装；
- **Cursor / Copilot / Gemini**：见 Spec §23 的对应小节。

不装适配层系统也能用（AGENTS.md 兜底），装了则从"AI 大概率守规矩"升级为"程序保证守规矩"。

## 七、常见问题

**Q: 会动我已有的配置/代码吗？**
账本（STATE/EVIDENCE/DECISIONS/FAILURES/ASSUMPTIONS/TASK_QUEUE/CHARTER/policy.yaml）永不覆盖；托管机制文件（checks/schemas/protocols/skills/templates/tests）按 manifest 安全升级，你手工改过的会生成 `.reos-new` 冲突副本而不是被覆盖。卸载 = 删 `aiops/` + 删 pre-commit hook + 删 AGENTS.md 里的 generated 区块。

**Q: 我的小项目值得装吗？**
只要满足：① 用 git；② AI 会反复参与；③ 有数值/实验结果需要可信——就值得。装完体积约 400 KB。

**Q: 账本会不会越写越乱？**
内置了五档维护任务（每任务/每周/每月/里程碑/Spec 演进），安装时已写进 TASK_QUEUE，到期让 AI 执行即可。

**Q: 系统出问题（AI 不守规矩、门禁误拦、账本混乱）怎么排查？**
看 `TUTORIAL.md`：§1 每层都有"出问题时的特征"，§5 有症状 → 根因速查表。

**Q: 如何升级到新版本 kit？**
下载新版 → 重跑 bootstrap（机制层增量更新，账本不动）→ 按 CHANGELOG 核对变更 → 在本仓 DECISIONS 登记。
