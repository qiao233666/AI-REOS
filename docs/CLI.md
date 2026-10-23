# JSON 命令行

2.0预览版另提供history-configure、history-query、history-get、session-context，参数与边界见[旧账本接入](HISTORY_BRIDGE.md)。

入口形式：

```text
python -m aireos --project ROOT [--credential-file KEY] COMMAND [--input FILE] [--request-id ID]
```

`--input` 指向 UTF-8 JSON 参数对象；未提供时使用空对象。写操作必须显式提供 `--request-id`，该字段不得放进输入 JSON。重复请求由框架按请求身份处理。`--credential-file` 只在明确指定时读取；CLI 不会自动读取项目中的 owner 凭据，也不会输出凭据内容。没有凭据的只读查询可运行；受保护操作会由框架拒绝。

`init` 只调用项目存储初始化，不需要输入对象或请求 ID。初始化结果会指出凭据文件位置，调用者应妥善保管该凭据。

只读命令：

- `get`：参数如 `{"identifier":"item-...","revision":1}`。
- `list`：可选 `{"kind":"item"}`。
- `events`：不需要输入参数。
- `status`：`{"item_id":"item-..."}`。
- `check-receipt`：`{"receipt_id":"receipt-..."}`。
- `trace`：`{"stage_id":"stage-..."}`。
- `views-status`：不需要输入参数。
- `memory-query`：`{"query":"以前为什么放弃这条方法","limit":12,"max_chars":12000}`。不创建任务或收据，只增量更新派生索引。正文及复用状态受显示预算约束，未显示内容按返回的id/rev用get补取。
- `run-evidence`：`{"run_id":"<运行ID>"}`，校验工具或外部审核的清单、当前文件及历史字节快照。

写命令通过显式白名单分发到框架公开方法：`grant`、`create-task`、`prepare`、`start-task`、`add-artifact`、`add-item`、`revise-item`、`submit`、`review`、`adopt`、`challenge`、`assess-case`、`resolve-case`、`close-task`、`annotate-stage`、`claim-output`、`takeover-output`、`release-output`、`register-resource`、`reserve-resource`、`finish-reservation`、`prepare-external-review`、`run-external-review`、`register-tool`、`set-tool-enabled`、`run-tool` 和 `publish`。参数名须符合相应公开方法的签名；未知参数会产生 JSON 错误，不会动态调用任意对象方法。

外部审核的 `run-external-review` 只通过显式指定的 CLI 命令运行；不会因为准备审核包而自动执行外部命令。调用参数及批准范围由对应框架方法核验。

`stop-task` 记录停止原因与阶段说明，不把未完成工作标成验收成功。外部审核的内部测试钩子 `runner` 不接受CLI输入。

错误写入标准错误并以状态码2退出，成功时 JSON 写入标准输出并以状态码0退出。CLI 只调用固定白名单 API；工具和外部审核运行本身可以执行获授权的程序，因此仍需信任被登记的脚本和宿主隔离。

一个任务输入文件可以写成：

```json
{"title":"检查候选方法","objective":"核对适用范围","acceptance":["依据和限制可复查"],"outputs":["candidates/example"],"inputs":[],"budget":{"mode":"manual","limit":"仅便宜检查"},"stop_conditions":["输入变化时停止"]}
```

把它传给 `create-task --input task.json --request-id <本次请求身份>`，并显式指定统筹凭据。返回任务编号后，`prepare` 输入 `{"task_id":"<任务编号>","query":"以前做过哪些相近尝试"}`；`start-task` 输入任务及返回的 `receipt_id`。完整可执行顺序见 `examples/demo.py`，包含实际脚本运行和后续核查、采纳、结案、追溯。

输出占用证明为 `{"id":"<占用编号>","epoch":1}`，作为 `submit` 的 `leases` 数组提供。默认没有显式占用时仍由任务输出约定阻止运行中任务路径重叠；涉及接管时须另外登记占用代次。资源及外部审核详细参数参见对应公开方法和 `docs/PROTOCOLS.md`、`docs/EXTERNAL_REVIEW.md`。

## 0.2的固定组合入口

- `deliver-batch`（execute）：`task_id, artifacts, claims, query?, leases?`。每项需唯一key；文件项含path/title，主张项含title/text/classification/scope/supports。supports可引用本批前面项的key。程序准备上下文、启动待执行任务、登记快照、登记主张并提交全部主张和文件；不采纳。一次最多64项。
- `record-reviews`（review）：`task_id,reviews`，每条含`item_id,revision,verdict,method,checks`。revision必须是实际核查的版本。程序只登记意见，不产生审核判断。
- `finalize-task`（decide；publish为true时还需publish能力）：`task_id,adoptions,summary,publish?`。adoptions逐项指定item_id/review_id；可空表示没有成果获采纳。程序逐项验证采纳、结案和发表手账。每项交付必须先有质量意见；unknown/fail也能如实结案。
- `memory-configure`（plan）：`{"mode":"fts"}`或显式hybrid本地配置，见[记忆说明](LOCAL_MEMORY_USAGE.md)。配置变更不会自动重解释旧结论。

以上写入口都要求明确凭据与request-id。中断时用相同请求身份、相同内容重试；业务步骤已提交但流程日志未写完，也按固定子请求找回。不会因此重复采纳或重建阶段手账。已开始的组合请求不接受换材料，输入变化需重新约定任务。

`create-task.inputs`可用文件字符串简写，或有类型引用：

```json
[{"type":"file","path":"inputs/parameters.json","usage":"basis"},
 {"type":"item","id":"<交付项ID>","rev":1,"usage":"reference"},
 {"type":"note","text":"仅文字说明，不代表文件或已验证依据"}]
```

默认usage=basis：作为依据，之后的来源失效会传到本任务成果；reference用于研究、反驳和重审材料，不背书其原结论。两者都固定版本及字节，并带回已登记争议。文件最多32MiB，大型数据绑定有版本的清单。默认recall_mode=incremental，可设strict。目标、验收或固定输入需要改变时新建任务，不能刷新收据自动接受变化。

可运行的完整组合示例见`examples/batch_demo.py`；不要求模型逐条维护内部步骤。

## 0.3新增及兼容说明

`prepare`和`memory-query`新增view参数，默认brief。context/matches中的项是紧凑片段：id/rev/kind/title/body/source/partial及必要条件；reuse_status是当前状态。full模式给完整正文；get仍返回完整原始对象。旧调用者若依赖context项的data字段，应改用get；无项目被自动迁移。字符预算包含片段元信息及状态，引用清单另列。

`status`另返回execution、quality、adoption、provenance、knowledge_type，原status继续作为复用摘要。审核通过、尚未采纳、依据当前有效可以同时成立，不压成单一真实性分数。

`begin-case-review`（review能力）输入`{"case_id":"<案件ID>"}`，先占次数；`review`可另传attempt_id；record-reviews中的每条意见也可传该字段。prepare-external-review可传case_id，绑定案件时run-external-review必须传一次有效attempt_id。重试同请求不重复扣次数，结案也不再扣。

`restore-run`（execute能力）输入`{"run_id":"<运行ID>","destination":"<任务输出下的新目录>"}`，恢复完整运行快照。已有目录拒绝覆盖；同一请求自身已创建的目录以持久恢复身份校验后续接。部分证据不可冒称完整恢复。恢复目录的`.aireos-recovery`是请求身份标记，不含凭据。

## 0.4.0审核与续查

`review`必须提供`expected_revision`，为实际检查的正整数版本；省略、null、布尔值和旧版都会拒绝。例：`{"item_id":"<交付项ID>","expected_revision":1,"verdict":"pass","method":"独立运行并对照参考","checks":["实际运行的输入和结果"]}`。`record-reviews`中的`revision`同样必填。程序不自动将审核绑定到最新版本；原请求重试只返回原意见。

新意见保存`review_schema=2`及`inspected_revision`。旧意见保持可读，但缺少该版本标记的意见不再作为当前正向采纳或依据资格；须重新实际核查并登记新意见，不猜补旧记录。`status.quality.version_binding`区分explicit和unknown；历史采纳决定仍保留。

案件续查分三个写入口，每步需要各自请求身份及对应能力：

- `propose-case-continuation`（execute）：`{"case_id":"<案件ID>","evidence":["<新增材料ID>"],"reason":"新增了什么信息","expected_revision":1}`。
- `assess-case-continuation`（review）：`{"continuation_id":"<申请ID>","substantial":true,"reason":"独立判断新增信息的依据"}`。提出者或目标产物的执行者不能自审。
- `authorize-case-continuation`（decide）：`{"continuation_id":"<申请ID>","budget":1,"owner_role":"<已登记核查者身份>","exit_condition":"这一轮怎样停止","reason":"批准新增投入的理由"}`。

适用于unresolved、resolved、dismissed案件，或open但冻结目标已被修订的案件。同版本仍正常进行的open案件不能续查。新材料需要有新内容；改身份或换请求不会刷新预算。仅在最终授权时增加round、绑定当前目标并批准本轮预算，累计used保留；旧预留和旧意见不能跨轮次使用。申请后案件、目标或材料变化会拒绝旧初核；重新提议时将过期申请标stale，旧申请仍保留。

完整适用范围见[操作协议](PROTOCOLS.md)与[本轮兼容说明](REVIEW_0_4_0.md)。

`pause-case`（decide）：`{"case_id":"<案件ID>","reason":"本轮为何保留未知并暂停"}`。这是明确停止本轮投入的管理动作，保持unresolved、原目标及已用次数；不等同核查或通过，也不补额度。升级前已用完额度但只留下旧格式意见的open案件也可这样停下，再有新信息时申请续查。
