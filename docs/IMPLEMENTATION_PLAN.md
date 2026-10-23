# 独立项目框架实施计划

> This document records the implementation scope of prototype 0.1; it grants no authority for future migration, publication, or paid calls.

Goal: 建立无项目具体内容、可本地运行并验证核心工作闭环的独立框架。

Architecture: Python 标准库和本地 SQLite；三类工作职责共用版本化记录、请求去重、快照、召回、输出协调和派生视图。共享正式记录只有一个权威位置；所有正式状态转换通过受管接口。

Tech Stack: Python >=3.10、sqlite3、unittest；无必需外部包、网络服务或付费模型。

Spec: `docs/DESIGN.md`；审查处理：`docs/REVIEW_DECISIONS.md`。

## 共同约束

- 只写独立候选和目标新目录，不迁移现有项目。
- 默认配置无项目事实、节点或模型；示例及测试只用合成资料。
- 核查意见、采纳决定和执行权限分开；历史未知不补推为验收。
- 原文和旧版本保留，初始化及发布不得覆盖已有记录。
- 真实远端动作、付费模型和科学计算不在测试中启动。

## 任务 1：事务、身份和版本

Files: `aireos/storage.py`、`aireos/errors.py`、`tests/test_storage.py`。

Interfaces: `Store.initialize(root)`；`Store(root)`；读对象、历史及事件；带请求身份、作用域授权和回调的事务写入口。对象、版本、事件和请求结果在同一提交中保存。

- [x] 先写并运行失败测试：A、B、A 重试只有两项；相同请求换内容拒绝；回调异常完全回滚；多个进程同权威目录不撞号；不同权限不能互相冒充。
- [x] 实现 SQLite 事务与持久请求表、对象版本、作用域凭据和内容快照。
- [x] 运行该组测试，确认旧反例对应的新行为通过。

## 任务 2：工作、逐项审核与采纳

Files: `aireos/project.py`、`tests/test_workflow.py`。

Interfaces: 创建任务、准备输入、开始、逐项交付、独立核查、采纳、争议及阶段结案；当前任务不能由调用者随意设置状态。

- [x] 先写并运行失败测试：不能跳过交付直接完成；事实型项无依据拒绝；只通过代码不能采纳解释；来源或依赖变化阻止旧采纳；执行者不能自称独立核查。
- [x] 实现三类责任的独立动作，输入版本与核查覆盖绑定，争议局部影响和退出记录。
- [x] 用完整合成工作链验证负结果、部分采纳、争议、更正与下一次复用。

## 任务 3：历史检索和输出协调

Files: `aireos/recall.py`、`aireos/coordination.py`、`tests/test_recall_coordination.py`、`tests/test_review_regressions.py`。

Interfaces: 普通中文和别名检索；有覆盖信息的输入收据；来源变化核查；同一权威位置的输出认领、释放、接管代次。

- [x] 先写并运行失败测试：中文自然问法、后续更正、长关系链截断、原文件变动、压缩后恢复；并行输出范围重叠拒绝，接管后迟到代次拒绝。
- [x] 实现来源与知识版本核验、已登记关系补取、明确截断及所有权检查。
- [x] 验证新记录不能悄悄留下旧收据有效，输出范围按路径组件比较。

## 任务 4：脚本资产和本地执行

Files: `aireos/tools.py`、`tests/test_tools_views.py`、`examples/summarize.py`。

Interfaces: 数据式工具登记、查找、启停及运行；固定脚本摘要和接口版本，记录真实调用与终态。

- [x] 先写并运行失败测试：停用、变更脚本、缺依赖、不兼容接口拒绝；同请求只执行一次；失败不返回成功；超时保持未知。
- [x] 实现稳定的 Python 脚本入口、独立运行目录、日志和有版本的工具条目。
- [x] 验证目录元数据检查不会导入执行代码，历史运行能追到实际版本。

## 任务 5：手账、视图、CLI 和空项目模板

Files: `aireos/views.py`、`aireos/cli.py`、`aireos/__main__.py`、`template/`、`tests/test_tools_views.py`、`tests/test_cli.py`。

Interfaces: `python -m aireos --help`；初始化空项目；通过文件输入调用上述动作；一致快照生成当前状态和只追加阶段手账。

- [x] 先写并运行失败测试：初始化不覆盖；手账追到当时版本及更正；视图中断不会切换到半批次；CLI 返回真实错误及帮助。
- [x] 实现可复用命令与轻量模板，示例涵盖任务、产物、核查、采纳、召回和阶段说明。
- [x] 从全新临时目录运行示例及全套 unittest，检查无原项目标识、真实配置或凭据。

## 任务 6：独立目录交付与验证

Target: `D:/AI/AI-REOS-Framework`；此目录需保持与既有项目物理独立。

- [x] 完成文档、审查意见处理与功能覆盖表，核查没有未实现接口被写成已完成。
- [x] 在候选目录运行全部测试和 CLI 冒烟；必要的失败后才扩展或重复验证。
- [x] 逐文件复制到目标新目录，校验源和目标摘要，不覆盖用户已有内容。
- [x] 在独立目录重跑全套测试及空项目演示，生成验证报告和可读使用入口。
- [x] 新建本地 Git 仓库与 `codex/initial-framework` 分支保存交付；不 push、不迁移现有项目。

验证命令：`python -B -m unittest discover -s tests -v`，`python -B -m aireos --help`。开发中先观察缺失实现及审查反例失败；最终绿灯输出和独立目录验证分别保存，不能只用静态字段检查证明功能通过。
