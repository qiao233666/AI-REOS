# 接入旧账本与新任务

旧YAML及Markdown账本继续是历史原文真源。此接口按编号切分并建立可重建FTS5缓存，不要求AI逐条重写，也不将active/complete或旧审核自动升级成新版pass/adopt。新任务的正式执行、审核、采纳和阶段手账仍由同一项目的.aireos管理。

## 登记与查询

统筹用history-configure登记明确文件：

```json
{"sources":[{"path":"aiops/EVIDENCE.yaml","format":"yaml-records"},{"path":"aiops/FAILURES.md","format":"markdown"}]}
```

该写操作需要plan能力、来源范围和request-id。只支持YAML列表中明确id/schema_version边界及二/三级Markdown编号标题，保留原文、旧编号、原行段和状态原词；不是一般YAML解析器，不执行标签。重复编号、未知格式、二进制、链接、读取中变化及超预算均明确失败，不静默使用旧缓存。

Markdown中同编号相邻、明确标注“追加/修订/更正/补充/续”的标题保留在同一原条目内，展开时同时读取旧说法与更正，不自行推断谁已正式获采纳。没有这些明确标记的重复编号仍拒绝，不按位置猜测或覆盖。

history-query输入`{"query":"为什么旧方法被否定","limit":12,"max_chars":6000}`。准确旧编号优先，普通词用全文排序；候选和显示截断分别标记。每项source给原文件路径、整文件快照摘要、条目摘要、行段及片段位置。authority固定historical_reference，命中及旧状态不证明当前适用或科学正确。未命中为UNKNOWN。

history-get输入`{"path":"aiops/FAILURES.md","legacy_id":"F-001","source_sha256":"<查询返回摘要>"}`，展开当时快照中的完整条目；不提供摘要时读当前文件。原文件变化后仍可按已见快照追溯。相同原文重复查询不需要AI整理，快照按摘要复用。

最多16个文件，每文件8MiB，总32MiB，单条1MiB，总10000条。默认brief最多600字符，JSON显示预算1000至50000字符。大账本可以超过普通artifact自动索引的256KiB上限，但不能无限制读取。缓存放.aireos/cache/history.sqlite3，原文快照在.aireos/objects；缓存可重建，快照和正式记录不得删除。第一次处理历史与文件变化时需要确定性扫描，未承诺无限历史的固定查询延迟。

## 日常入口与责任

session-context输入`{"output_prefix":"pilot","query":"实际历史问题","max_chars":6000}`，给该输出范围的活跃任务；提供query时，同时返回memory（新版正式记录）和history（旧原文），避免已完成的新成果退出任务导航后被遗漏。memory保留当前审核、采纳、依据等复用状态，历史原词不升级为新版状态；整个JSON共同受max_chars限制，截断会标记，完整正文按新id/rev或旧编号及摘要展开。output_prefix只筛活跃任务导航，不是检索读取权限边界。没有query时只生成轻量导航，不触发两路检索。history-query与原memory-query仍可分别查旧原文和新正式记录，不建立第二套科学事实。

检索本身不产生正式收据，也不自动向新任务背书。实际复用旧材料时，展开准确版本、确认条件，在新任务inputs中声明reference；实际采用哪些依据需由核查者判断。新交付仍走prepare→submit→明确版本review→adopt/close-task。不得把“找到旧记录”当作新的独立审核。

真实接入先备份和界定一个任务的输出范围，在范围内使用小入口说明；旧账本只读，新任务只写新版正式入口。其他正在进行的研究任务继续原流程，避免同一成果双写。完整旧项目迁移、自动替换所有客户端hook及全部历史可信状态映射不在这个接口的保证内。
