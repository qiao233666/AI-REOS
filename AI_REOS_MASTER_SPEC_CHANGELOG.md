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

---

## v1.3 — 2026-09-29（外部实测审计驱动的强制层加固）

来源：用户携 v1.2 zip 交由另一 AI 做负向实测，17+1 项问题逐条核实**全部属实**，
本版本逐项修复（决策 D-020，证据 E-033）。

### 强制层真实性（P0，6 项）
1. **升级机制**：bootstrap 从"存在即跳过"改为 manifest 哈希升级——ADD/UPDATE
   （未被用户修改→自动升级）/KEEP/CONFLICT（用户改过→写 *.reos-new 不覆盖）+
   REMOVED 提示；账本文件永不覆盖。
2. **pre-commit**：不再覆盖用户 hook——既有 hook 备份为 pre-commit.user.bak 并
   被链式调用；渲染源永远是 kit 模板，版本化 aiops 副本的占位符不再被回写
   （v1.2 会销毁占位符导致换解释器后无法再更新）。
3. **worktree 兼容**：hooks 目录用 `git rev-parse --git-path hooks` 解析。
4. **claim→Evidence 引用完整性**：verify_claims 真正读取 EVIDENCE.yaml——
   supports 里编造 id / 非 active 状态均 FAIL。
5. **Evidence 强 schema**：scope/limitations/recorded_at 必填，statement 非空，
   limitations 非空列表。
6. **Experiment 条件必填**：status=complete 时十项 provenance + 四个溯源字段
   非空（anyOf 条件分支）；registry 幽灵实验（登记无文件）FAIL。

### 结构与工程（P1/P2）
7. 受保护目录单一真源 `aiops/policy.yaml`（guard 运行时读取，代码去项目硬编码）；
8. CI 模板 ci-template/ai-reos-ci.yml（可选启用）；
9. 示例 Task 不再装进 specs/（examples/ 目录 + 校验器排除 *-example.yaml）；
10. ZCode 模板与校验器矛盾修复：绝对路径占位符（D-017 禁止变量依赖）；
    `_check_zcode_adapter` 死代码接入 main，缺 ZCode 配置降级为 WARN（可选组件）；
11. kit 去项目痕迹（守卫硬编码/示例/模板）；
12. kit 自带 tests/（12 个治理代码回归测试）；
13. schema 引擎补 minLength/maxLength/anyOf；
14. --dry-run：免询问 + 逐文件 ADD/UPDATE/KEEP/CONFLICT 明细；
15. aiops/VERSION + .reos-manifest.json（kit 版本与托管文件哈希）。

---

## v1.3.1 — 2026-09-30（第二轮外部实测审计：升级生命周期闭环）

来源：同一外部 AI 对 v1.3 zip 的二轮实测（v1.2→v1.3 真实升级路径等 7 项，
逐条核实全部属实）。核心结论：v1.3 修好了"检查真实性"，但"v1.2→v1.3 升级"
这条真实用户最先走的路恰好不能自动升级。

1. **P0 旧版迁移**：新增 `migration_baselines/1.2.json`（官方 v1.2 机制文件
   归一化哈希，提取自实际发行的 v1.2 内容）。无 manifest 时命中基线 → UPDATE
   （v1.2 官方文件自动升级），不命中 → CONFLICT（用户改动受保护）。
   实测 v1.2→v1.3.1：9 个变化文件全部 UPDATE，CONFLICT=0。
2. **哈希归一化**：升级决策哈希统一 CRLF→LF 归一化（zip/git 检出/工作区
   行尾形态差异不再误判 CONFLICT）。
3. **hook 防自链**：AI-REOS 自装 hook 带 marker，重装时直接替换，不再把自家
   旧 hook 当用户 hook 备份并链式执行两遍。
4. **VERSION 托管覆盖**：每次安装写当前 kit 版本（v1.3.0 永远停在首次安装值）。
5. **治理测试随装**：tests/ 进托管机制目录，安装到 aiops/tests/；CI 模板改为
   `unittest discover -s aiops/tests`——目标仓库从此能长期回归 AI-REOS 本身。
6. **Experiment schema 生命周期语义**：planned 豁免 provenance（允许占位），
   running/complete/failed/aborted 必须十项 + 四个溯源字段非空；模板全部改为
   字符串值（v1.3.0 模板的 boundary_conditions dict / random_seed null 与
   schema 直接矛盾）。
7. **清理**：examples 个人路径、README"一律跳过"旧措辞、install_hooks.py 移除
   （统一 bootstrap 单一实现）、本仓 tests 全量回归遗留问题另行登记。

---

## v1.3.2 — 2026-10-04（世界观层 world registry 随 kit 分发）

来源：RCWA_metamaterial 项目三轮召回失效诊断与四/五轮结构设计（E-078 → E-083 结构
→ E-085 收口），最小内核+足够好版已在源头项目实测（三次提交 062d102/fa84725/2b905cb，
门禁 8 项全绿，恢复演练 PASS）。核心问题：AI 把"索引里没找到"推出"不存在"（开放世界
被当闭世界），三次实际事故（COMSOL 路径/可信域 Π_D/层数扫描）均为此失效模式。

### 1. 世界观层三层结构（`aiops/world/`）

- **canonical**：`world/objects/<kind>--<slug>.yaml`，一对象一文件；字段含
  authority（binding/advisory/historical + granted_by）、lifecycle（含 accepted_digest
  只能显式复核更新）、applies_to、bindings（同一文档不同章节可分别处于不同状态）、
  capabilities（环境对象：host/capability/expires_at，过期即 stale——"安装路径存在"
  ≠"许可证可用"）、relations；
- **机器视图**：`world/registry.generated.yaml`（builder 整文件生成，禁手改）；
- **读侧视图**：`world/CURRENT.generated.md`（Agent 检索入口；判断"某东西是否存在"
  先查这里，未命中只能记 UNKNOWN，禁止生成否定结论）。

### 2. kit 分发内容与安装语义（本版核心）

- `world/tools/` 三个纯机制工具按托管机制安装（manifest 哈希升级）：
  `build_world_registry.py`（构建器+`--query` 召回+`--check` 一致性）、
  `verify_world_registry.py`（五类检查+三条召回探针，fail-closed 分层）、
  `build_ledger_views.py`（账本三层视图 reducer：active-core/active-cold/history）；
- `world/coverage.yaml` 与 `world/ledger-policy.yaml` 属**项目骨架**（只补缺失，
  永不覆盖）；objects/、releases/、*.generated.* 由项目自行生长/生成，不随 kit 分发；
- 安装后自动用当前解释器生成一次初始空视图（空 registry 也生成，保证 `--check`
  一致）；生成失败不阻塞安装，世界层按 UNKNOWN 语义降级；
- `schemas/world-object.schema.json` 随 kit 分发；`requirements.txt` 新增
  （PyYAML 必需 + jsonschema 可选——缺省时 schema 校验跳过并告警，不阻塞）。

### 3. 门禁第六项与自动模式

- `run_all_checks.py` 增至 6 项：新增 `verify_world_registry`（入口适配层
  `checks/verify_world_registry_entry.py`）；
- 入口为 **auto 模式**：运行模式由 `aiops/world/coverage.yaml` 的 `coverage.status`
  唯一决定——partial（含缺失/解析失败）→ report-only（exit 恒 0），full → enforce
  （内部 FAIL 即门禁失败）。与 E-085 步骤 6 一致：只有 coverage partial 才允许
  report-only，full 下异常即失败；world 未部署自动跳过，不阻塞未启用项目；
- bootstrap 的 AGENTS.md generated 区块增世界层触发行（"未命中只能记 UNKNOWN"）；
- SessionStart 注入文案不再写死校验项数（以 run_all_checks 的 CHECKS 清单为准）。

### 4. 升级与测试

- `tests/test_world_registry.py` 随 kit 分发且**环境自适应**：已部署世界层（objects
  非空+视图存在）跑全量断言（召回探针/替代链/视图一致）；骨架项目相关用例自动
  skip，结构检查仍跑——同一份文件在源头仓（21 对象）与新装仓（空骨架）都成立；
- `tests/test_governance.py` 同步源头仓统一版：test_07 模板校验兼容安装后布局
  （`experiments/TEMPLATE.experiment.yaml`）；test_08 个人路径扫描仅 kit 开发布局
  生效（已安装仓的账本属用户内容）；
- v1.3.1 → v1.3.2 升级：world/tools 三文件+schema+entry+tests 走 ADD/UPDATE，
  账本与世界层骨架不动（实测见源头项目 dist/ 验收记录）。
- **v1.3.1 缺陷修复**：`tests/` 缺 `__init__.py`，`unittest discover -s aiops/tests`
  在已安装仓报 "Start directory is not importable"（CI 模板命令同样必挂）；v1.3.2 补齐。
- **文档修订（2026-10-04）**：TUTORIAL 增 §5 世界观层章节（是什么/怎么运行/与门禁联动/
  UNKNOWN 语义/排障表），排障索引与学习路径顺延为 §6/§7 并补世界层条目；kit VERSION
  不变（纯文档修订）。
- **发布通道（2026-10-04）**：GitHub 仓库 qiao233666/AI-REOS + 一键镜像发布脚本
  （源头项目 dist/publish_to_github.py，见 HOW_TO_UPLOAD_GITHUB.md）。
