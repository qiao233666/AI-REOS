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
- **写路径约束（2026-10-04，F-016 事故修复/T-054）**：AGENTS.md generated 区块与
  SessionStart 注入各增一条——跨会话/跨工具需持久的项目事实/教训/裁定唯一真源是
  `aiops/` 账本（教训→FAILURES.md），AI 工具私有记忆只放工作偏好，不得存放项目事实。
  背景：实测发现某 AI 把 GPU 吞吐教训写进工具私有记忆，治理真源未收到（首例写失效）。
- **schema 扩展（2026-10-04，T-053）**：world-object.schema.json 增 kind=record
  （记录类：交接/留痕/简报/对话，参考非约束），源头项目 docs/notes 33 篇已全量登记。

## v1.4.0（2026-10-15）——对话纪律、STATE 硬门与账本事务追加器（源头项目 T-084/D-040）

用户投诉 AI 回复全是难读的"账本体"（浓缩编号串联）触发；方案经 gpt-5.6-sol 三轮
评审终审 GO 后在源头项目实施，本轮回流 kit。

### 1. 对话纪律（新章）

- CHARTER 模板新增"对话纪律（回复用户）"章（kit 布局 §8；源头仓 §10）：账本体只属
  账本文件，回复用户结论先行、编号只作定位注记（核查/调试/交接例外）、未知原因
  不编造、改变呈现形式不减少交付信息；
- bootstrap 的 AGENTS_BLOCK 与 SessionStart 注入各增一行对话纪律指针（kit 内引用
  §8，与 kit CHARTER 章节布局一致——源头仓为 §10，跨仓引用不得照搬章节号）。

### 2. 账本读法（上下文饮食）

- AGENTS_BLOCK 与 SessionStart 注入各增"账本读法"行：EVIDENCE.yaml/TASK_QUEUE.yaml
  等大账本禁止无任务需要地整文件加载进模型上下文（源头仓实测已超 29 万字符），
  先 grep 定位再按行段读取；校验脚本完整解析不受此限。

### 3. STATE.md 体积硬门（verify_ai_state 升级）

- 行数 ≤150（splitlines 含空行）与字符数 ≤20000 双硬门，超限 FAIL（软性"1-3 页"
  声明升为程序化门禁；源头仓 STATE 曾膨胀至 481 行）；
- 章节标题检查升级：统一 Markdown 二级标题解析（代码围栏免疫、正文提及不误判），
  四个必需标题各**恰好一次**，带说明后缀的竞争章节（历史分叉形态）判 FAIL；
- Confirmed facts 引用检查改用共享解析结果并覆盖全部同名/后缀章节（旧实现只取
  第一个精确匹配章节，后缀章节中的引用漏检——sol 第二轮评审抓出的缺口）；
- 新增 tests/test_verify_ai_state_gate.py：9 项正负向（150/151 行边界、缺标题、
  重复标题、后缀竞争、围栏/正文提及、坏引用、超字符）。

### 4. 账本事务追加器 ledger_write.py（撞号结构修复，T-060 阶段 1）

- 新增 `checks/ledger_write.py`：append 走 OS 句柄锁（Windows msvcrt/POSIX flock，
  进程退出自动释放）→ 锁内从账本实际条目取号 → 完整候选文件内存校验 → 同目录
  临时文件 fsync 后 os.replace 原子替换（替换失败原文件不动）；
- request-id 幂等：写成功但返回前崩溃的场景重试返回原编号；同请求号不同内容拒绝；
  request 元数据以受控注释行持久化（safe_load 不可见，专项可读回）；
- update 入口带版本哈希比较（拒绝基于旧整本的覆盖更新）；已发布编号永不回收；
- 边界声明：单工作树（canonical writer=主仓主工作树）内防撞；跨 worktree 只提交
  草稿不自行定号——本工具不声称解决跨工作树并发（诚实边界，sol 评审必改7）；
- `protocols/evidence-protocol.md` 增"写入入口"节：新条目必须走追加器，不得手工
  编辑账本取号；撞号按内容锚点+前向重编号+ID_ALIAS 处理，语义冲突另行核查；
- 新增 tests/test_ledger_write.py（6 项：分配/幂等重放与冲突/元数据往返/崩溃保护/
  6 进程并发/陈旧更新拒绝）与 tests/stress_ledger_write.py（12 进程压力盲测）；
  依赖仅标准库+PyYAML，Windows/Linux 均可运行；
- 背景：源头仓历史撞号七次在册（grep 取号与写入间隙被并行会话插入）；修复期另
  挖出历史账本结构损伤（E-150/E-151/E-152 连环，FAILURES F-025）——旧"手工编辑+
  子串检查"流程的温床，追加器为结构性修复。

### 5. 升级与兼容

- 新装：`bootstrap.py` 三模板更新（CHARTER §8/AGENTS_BLOCK 两行/STATE 模板不变）；
- 升级（1.3.2→1.4.0）：checks/ 与 tests/ 走 manifest ADD/UPDATE（新增 ledger_write.py、
  test_ledger_write.py、test_verify_ai_state_gate.py、stress_ledger_write.py；
  更新 verify_ai_state.py、session_start_context.py、evidence-protocol.md），
  账本与世界层骨架不动；已安装仓的 CHARTER/STATE 属用户账本，kit 模板升级不自动
  迁移（按 README 升级说明人工对照回流）；
- run_all_checks 的 CHECKS 清单不变（ledger_write 是写入工具不是门禁检查项；
  引用完整性仍由 verify_ai_state/check_id_uniqueness 承担）。

### 6. v3 修订（2026-10-16，源主仓同日）

用户反馈"汇报仍难读"（会话内黑话+读者在场假设两大病灶），对照公开的 Claude Code
官方沟通规范（system-prompt-communication-style / outcome-first / concise-output-style，
v2.1.x）逐条吸收修订对话纪律章（kit §8/主仓 §10）：

- 核心姿态：写给"中途离场、回来接盘的同事"，假定读者没跟过程；
- 新增条款：行话/任务号（T-xxx）首现必须一句话解释或换平实说法，不得让读者反查
  早前编号；可读优先于简洁（短靠选择性取舍，不靠碎片/缩写串/箭头链）；
- 结构匹配：简单问题平实散文直答，表格只放可枚举短事实；
- 保留：结论先行、交付信息不减少、错误报告完整、编号仅定位注记（核查场景例外）。

同步面：主仓 CHARTER v3 + SessionStart 注入行 + AGENTS.md；kit bootstrap 两模板；
三部署项目注入行同日跟升。

---

## v1.5.0（2026-10-19）——科研路线判断机制通用化回流（源：下游声涡耦合项目的实战机制）

### 1. 背景

下游项目在实战中提出并验证了一套"科研路线判断"机制：把失败事件的三种判断
（论断不成立 / 方法走不通 / 路线不值得投入）分开记录，避免系统唯一出口退化为
"缩小论断 + 加免责声明"。机制经四轮外部模型评审、三份真实复盘记录压测后定稿。
本次将其抽象为 kit 通用组件。

### 2. 新增托管文件

- `schemas/research-review.schema.json`（v2）：三类判断分离、五种处置
  （含 `continue_existing`，防止为拿预算被迫声称改了方法）、
  `selected_actions` 区分 `execute` 与 `candidate_only`、
  `lifecycle` 与 `approval` 双维度、`input_digest` 冻结输入；
- `protocols/research-review.md`：规则真源，明确**机器能查结构与引用、
  不能查理由充分性与科学价值**；含已接受的四种失败模式及拦截手段；
- `checks/verify_research_reviews.py`：结构门（D 编号存在性、approval 三字段
  一致、动作冲突、candidate_only 不得绑任务、retire 需 execute 的 stop 动作等）；
- `checks/verify_research_reviews_observe.py`：观察性包装（`REPORT_ONLY=True`），
  区分"报告结果"与"阻断提交"；无复盘文件时报 SKIP 而非 PASS，不制造假安全感；
- `checks/research_action_guard.py`：执行前守卫（拒绝无副作用、幂等防重复消费、
  超额拒绝、纠错豁免、superseded 决策拒绝）；
- `tests/`：schema 20 项 + 门禁 46 项 + 守卫 10 项，共 76 项（kit 合计 100 项）；
- `tests/fixtures/RD-FIXTURE-001.yaml`：**纯合成**的结构完备样例，
  不含任何下游项目科学内容，同时可作为新项目的填写骨架。

### 3. 通用化处理（与下游项目版的差异）

- 清除所有项目专属引用（任务号/证据号/研究线号/领域术语）；示例统一为 `RL-xxx`；
- 测试改为依赖 kit 自带 fixture，不再引用项目真实复盘记录；
- `run_all_checks.py` 改为**按需启用**：仅当仓库存在 `aiops/research_reviews/RD-*.y*ml`
  时才挂载该项检查。未启用该机制的项目完全零噪音（不出现 SKIP 行），
  避免"装了却永远跳过"的视觉负担。

### 4. 升级决策逻辑修复（影响所有项目）

原 bootstrap 在 CONFLICT 分支把 **kit 哈希**写入 manifest，而磁盘上仍是本地内容。
后果：同一文件每次升级都必然重复报 CONFLICT，**永不收敛**，且无法区分
"用户还没合并"与"用户已合并但 kit 又变了"。

v1.5.0 改为记录**本地实际哈希**：本地未改动时仍报 CONFLICT（如实提示未合并），
一旦用户把本地内容改成与 kit 一致，下次升级自动转 KEEP。收敛语义恢复正确。

> 注意：本修复只解决"冲突记录永不收敛"，不自动合并任何本地修改。
> 已有部署项目升级后 manifest 漂移会归零，但未合并的文件仍会如实报 CONFLICT。

### 5. 升级与兼容

- 新装：全部文件 ADD；机制默认未启用（无 `research_reviews` 目录即不检查）；
- 1.4.0 → 1.5.0：research 相关 6 文件 ADD、其余 KEEP；下游实战项目因已有本地
  版本会报 CONFLICT，采纳 kit 版即可获得条件挂载与通用化措辞；
- 启用方式：创建 `aiops/research_reviews/` 并放入 `RD-*.yaml` 记录，
  检查项自动挂载；观察期 `REPORT_ONLY=True`，验收通过后由人改为强制。
