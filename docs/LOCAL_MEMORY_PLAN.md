# 本地记忆实施计划

Goal: 用现成检索组件及确定性维护减少模型记账负担，保持来源、历史与核查边界。

Architecture: SQLite正式版本 + FTS5派生索引 + 可选本地FastEmbed/Qdrant语义通道；组合业务入口复用现有动作和持久请求去重。

Spec: `LOCAL_MEMORY_DESIGN.md`。Python>=3.10；无真实模型服务、远端计算、既有项目写入或默认模型下载。

- [x] `tests/test_local_memory.py`先验证索引存在、旧版与更正回带、重复同步无重复编码、配置错误不静默降级、并发可恢复。实现`aireos/memory_index.py`与`aireos/semantic.py`；`recall.search`接入。
- [x] `tests/test_input_receipts.py`先复现声明输入变化仍有效、无关更新错误失效；实现`aireos/inputs.py`及`Project`的版本绑定与增量/strict收据。
- [x] `tests/test_workflow_batches.py`先验证一次交付自动登记、重试恢复、审核与采纳不混淆；实现`aireos/workflow.py`及固定CLI入口，不替代科学判断。
- [x] `tests/test_memory_evaluation.py`验证题目来源摘要、Hit@k/MRR及非法引用；实现离线评测脚本。真实语料只存独立私有目录。
- [x] 单独环境安装可选成熟组件，运行少量CPU编码与真实历史小规模比较；无模型可用时明确报告未验证，禁止用假向量冒充语义效果。
- [x] 审查最终diff，更新文档、版本与例子，运行Python3.10/3.12回归。逐文件摘要核对后同步独立框架，保存本地Git版本及可恢复的旧版本；不推送。
