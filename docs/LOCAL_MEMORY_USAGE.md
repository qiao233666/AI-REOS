# 本地历史检索说明

框架默认使用 FTS 全文检索，索引保存在项目本地；查询不调用对话模型，也不要求网络或语义模型。检索结果按词语和中文片段匹配并附带已登记关系，召回表示文本相似或存在显式关联，不表示内容正确、适用或获采纳。

可选 hybrid 模式会在 FTS 之外使用本地语义向量。配置时必须指向已经准备好的本地模型目录；框架不会自动下载模型，也不会联网寻找替代模型。缺少可选依赖或本地模型不匹配时，调用应明确失败，不把语义结果冒充 FTS，也不静默下载。

记忆检索只帮助找到可能相关的历史资料。未命中不说明历史上从未做过；命中也不证明适用性。开始或恢复任务时，应核对原始来源、版本、依赖和当前状态，再决定是否复用。私有历史语料只用于离线评测，不随独立框架发布。

一次小规模私有评测包含 22 条材料和 14 个问题：prototype 与 FTS 的 Hit@1、Hit@3 均为 1；hybrid 的 Hit@1 为 11/14、Hit@3 为 1。样本很小，不能证明总体检索性能；未标注材料不能自动视为不相关。因此默认继续使用 FTS，不据此宣称语义检索更可靠。

命令示例只使用占位路径，不指向具体研究项目：

```text
python -m aireos --project <ROOT> memory-query --input <QUERY_JSON>
python -m aireos --project <ROOT> --credential-file <KEY> memory-configure --request-id <REQUEST_ID> --input <CONFIG_JSON>
```

查询输入示例：`{"query":"需要查找的问题","limit":12,"max_chars":12000}`。显示预算包含正文与当前复用状态；未显示项按返回的id/rev补取。保持默认全文检索可配置 `{"mode":"fts"}`。可选语义配置需要明确本地目录与模型名，例如 `{"mode":"hybrid","model_name":"<LOCAL_MODEL_NAME>","model_directory":"<LOCAL_MODEL_DIRECTORY>","max_new_chunks":32}`；占位值必须由调用者替换为真实的本地配置。模型配置是本机项目设置，不应写入共享模板或公开评测语料。

0.3默认返回brief片段：body是原文子串，source是来源字段及字符位置，partial表示有省略。view=full给完整正文，get按准确id/rev给完整原始对象。小型UTF-8文本在登记文件时自动进入全文及可选向量索引；超出256KiB或不支持的格式只查元信息，并明确返回search_coverage。人的手账不承担机器正文索引职责。

可选组件建议装在专用虚拟环境；在框架目录执行：

```text
python -m venv .venv-memory
<VENV_PYTHON> -m pip install ".[memory]"
<VENV_PYTHON> scripts/verify_semantic.py --model-name <MODEL_NAME> --model-directory <LOCAL_MODEL_DIRECTORY> --output <NEW_REPORT_JSON>
```

模型需由使用者提前准备。本次测试的模型是`BAAI/bge-small-zh-v1.5`（512维、CPU单线程），依赖版本为Qdrant Client 1.20.0、FastEmbed 0.9.0、ONNX Runtime 1.23.2。模型全目录摘要及其余组件版本写入每个项目的配置；模型或组件变化须重新配置。验证脚本实际禁止网络，检查第一次编码、重复查询无重复编码及新增一项只编码新增项，不用假向量代替。

默认检索按需更新新增的历史版本，不运行后台模型。语义通道按max_new_chunks限制每次新增编码量；尚未完成会明确返回pending_chunks，不能将部分索引称为完整。大量历史首次编码应由使用者先确定范围，不要求工作AI持续处理。

`scripts/evaluate_memory.py`接受独立JSONL语料及JSON题目，模式为prototype、fts或hybrid；必须输出到新目录。语料每条保留doc_id/text/source_path/source_start_line/source_end_line/source_sha256，题目保留question_id/query/relevant_doc_ids/type。整理者在冻结时核对原文、行段及原文件摘要；脚本核对摘要格式和目标ID，保存语料整体摘要并输出逐题排名、Hit@k与MRR。它不会追到原项目将冻结材料替换为新版本。真实语料和结果留在私有目录，不放进共享框架。

机械登记可直接运行`examples/batch_demo.py`了解；实际项目采用三个入口：执行方一次交付、核查方一次录入意见、统筹一次结案。内部多步操作由程序负责并支持同请求恢复。它们仍是不同凭据下的独立动作，不能把一轮探索自动升级为正式成果。
