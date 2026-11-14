# 分段执行控制

逐项独立核查材料与完整格式见 [EXECUTION_REVIEW.md](EXECUTION_REVIEW.md)。

此模块把原任务验收、原件字节、阶段报告、独立审核与有界工具执行串接起来。正式事实仍在既有 `Store` 的版本化对象、事件和请求中；不迁移数据库、不新增依赖。工具计数与撤销只进入 `.aireos/cache/execution-control.sqlite3`，不能代替科研证据或任务事实。

本能力是显式接入：旧任务不会自动生成执行策略。模型可读取状态，但工具放行必须由受信宿主在真正执行工具之前调用；模型自行调用一次 `execution-admit` 不能证明宿主已强制接入。

## 原任务与证据规则

`ExecutionControl(store).configure(task_id, original_item_id, evidence_rules, max_tools, max_seconds, request_id)` 需要 `plan` 能力，输出 `execution_policy`。任务须已开始且输出范围获准，原件须是已经登记的文件产物。策略固定原合同摘要、原件的 `id/rev/digest` 和文件快照、每项验收所需证据类别、每段工具与时间预算。

原合同第1条验收为 `A1`，依次编号。`evidence_rules` 必须完整、不重复覆盖全部原验收项，例如：

```json
[
  {"criterion": "A1", "required_classes": ["implementation"]},
  {"criterion": "A2", "required_classes": ["physical"]}
]
```

一个任务只能建立一个策略；相同请求重放仍由 Store 去重，其他请求不能静默改写原件、验收或预算。任务合同变化后原策略失效；需要在原有治理流程下明确新的合同与授权，不能通过改报告或答复待决请求偷偷扩权。

## 段、报告与独立裁定

`begin(task_id, session_id, criteria, purpose, expected, request_id)` 需要 `execute`。`criteria` 是非空、不重复的原 `A` 编号子集，`purpose/expected/session_id` 均须为有效文本。返回 `execution_segment`，其中 `id` 是宿主后续放行必须携带的准确段ID，`data` 包含执行者、起止时刻、固定预算和原件引用。

首段可直接开始；后续段必须上一段已由独立明确审核及 `decide(..., "continue", ...)` 放行。同一任务不能有两个活动段，同一会话不能并行绑定另一个活动任务。换会话、重放 `begin` 或重建缓存都不能续期或重发预算。任务结束、合同/输入/原件变化或存在 `pending` 待决请求时不能开始。

先用既有 `Project.add_artifact` 登记UTF-8 JSON报告，再调用 `report(segment_id, artifact_id, request_id)`。报告必须由本段执行者登记在本任务中，内容如下：

```json
{
  "schema": "aireos.execution-report/1",
  "segment_id": "EXECUTION_SEGMENT-实际ID",
  "contract_hash": "begin返回的原合同摘要",
  "criteria": [
    {
      "criterion": "A1",
      "state": "satisfied",
      "evidence": [{"id": "ITEM-实现文件", "rev": 1, "class": "implementation"}],
      "remaining": ""
    },
    {
      "criterion": "A2",
      "state": "unknown",
      "evidence": [],
      "remaining": "尚缺物理验证，当前不能判断"
    }
  ]
}
```

阶段只研究 `A1` 时，报告仍须列出全部原验收项。`state` 允许 `progress/satisfied/unknown/pending`；每项必须有 `evidence` 数组及 `remaining` 字符串。只有 `satisfied` 要求证据类别覆盖策略。所有引用均须为可定位的文件产物，且对象当前准确版本、快照摘要和现场字节都一致。单纯claim及自称“通过”不能充当文件证据。记录保存完整准确引用，在裁定和后续放行时重新核对。

`class` 是待核查的类别声明。标签叫 `physical` 并不证明文件包含真实物理计算，也不证明计算正确。独立审核者必须核对文件内容、科学量、输入、方法、实际测量和适用边界；机械校验不能代替这些判断。

报告提交后段变为 `submitted`，工具立即停止。预算已经耗尽、会话已撤销或存在未决请求仍可提交报告，让停机原因及缺口进入正式记录。

随后由独立执行者先用 `execution-review-packet` 读取完整原件、合同、报告和可提供的实际证据，再用 `execution-review` 录入逐项核查；其内部仍调用既有 `Project.review`，明确报告 `expected_revision`，不新建审核库。通用的一句话pass不能续行。核查须准确绑定本段原件、合同、报告及全部原验收，逐项给出判断、准确证据和依据，明确范围保持情况及裁定。`decide(segment_id, review_id, decision, reason, request_id)` 需要 `decide`：

- `continue`：需要针对当前报告准确版本的最新、独立、明确 `pass` 意见。允许诚实局部进展和未知项。当前段进入 `continued`，之后才能创建新段。
- `hold`：允许 `pass/limit/fail/unknown` 的独立明确意见，保存拒绝/停止理由，进入 `hold`。新会话不能绕过。
- `completed`：需要同样的独立 `pass`，并且全部原验收项均为 `satisfied`，文件及类别齐全。当前段进入 `completed`，不能再创建下一段。

执行者不能审核自己生产的报告。审核目标必须是准确报告引用；别的报告、旧版本、后续已出现拒绝意见的旧pass都不能借用。前段审核本身的准确版本变化也会阻断后续放行。

continue的逐项核查还必须批准唯一 `next_step={criteria,purpose,expected}`。下一次begin三个字段必须与其完全一致，不能拿一个宽泛pass另开偏离原目标的阶段。判断为夸大、未知或范围缩减时只允许hold；继续和完成要求范围保持且每项当前陈述准确。报告本身仍可诚实写unknown，不把“判断未知项的陈述准确”误作“科学问题已解决”。

`hold` 恢复复用同一段：执行者登记字节确实修订的新报告，再 `report`，再由独立审核者对新报告准确版本审核，最后 `decide(..., "continue", ...)`。换一个item ID登记相同报告字节不算修订。修订不会重置原段预算，只有独立裁定后可开始下一段。无需平行的resume授权记录。

`completed` 只是执行控制段的判定，不自动调用 `Project.adopt`、`Workflow.finalize` 或 `Project.close_task`。交付、采纳和关闭继续走原有工作流；局部单测通过不表示母任务完成或无限范围科学正确。

## 受信宿主放行

`admit(session_id, tool_name, tool_input, event_id, segment_id=...)` 无需正式写凭据。宿主须在工具实际运行前提交原工具名称、完整原输入、唯一事件ID、当前会话及 `begin` 返回的准确段ID。缺少段ID、旧段迟到事件、跨会话、未决、过期、耗尽、submitted/hold/continued/completed、原件/证据/审核变化和缓存不可用均拒绝。所有工具计数，例外只能由适配器明确列出并在宿主边界处理。

缓存使用SQLite事务原子计数，允许跨进程并发。相同事件ID和完整payload重放不重复计数；相同ID不同payload拒绝。返回 `replayed=true` 的事件已处理，宿主不得再次执行工具。重放仍重新检查撤销、到期、当前段和正式证据；旧放行结果不能使已失效段重新执行。

计数槽仅由首次正式 `begin` 建立。缓存或槽丢失时，既有段拒绝执行；重放 `begin` 不会补发预算。修复缓存后仍须报告、独立核查和裁定，才能创建新段。缓存属于操作状态，虽然可移除，移除不能用作“重置进度”的正常恢复手段。

`invalidate(session_id)` 用于新用户输入或会话重置，撤销该会话已有段的放行。它不删除正式段或报告，不撤销已经运行的工具，也不自动批准下一段。宿主的工具请求处理须与本会话新输入/重置顺序一致；本核心不声称能取消已经发出的外部动作。

`status(task_id=None, session_id=None)` 为只读。`session_id` 始终必填；缺少任务ID时必须恰有一个任务绑定该会话，否则明确报错，不从多任务中猜。返回策略、当前/最后段、原件准确引用、全部原验收文字以及 `allowed/reasons/used/max_tools/deadline`。状态不创建磁盘缓存，不写正式事件。

状态另给出 `completion_ready` 和 `completion_reasons`，供宿主判断完成裁定是否仍有效。只有最新段属于当前会话、确有completed裁定、全A项satisfied、原合同/输入/原件及最新报告证据现场当前、已批准前段的证据准确快照完整、各段独立审核仍为准确版本的最新pass、无未决且未因新输入/重置撤销，`completion_ready` 才为true。不能只检查 `segment.data.status`。

已批准的新段可以正常迭代同一输出文件。前段报告和审核对象仍核对当前准确版本，前段证据仅按当时准确rev/digest及Store快照校验；它们不能自动作为最新完成证据。原任务输入、原件、开始下一段前的上一段、当前报告与最新完成证据继续核对现场字节；旧快照损坏仍阻断。

诚实UNKNOWN、阶段失败或需人决定可以通过正式hold交接：状态另给出 `handoff_ready/reasons`。最新段须属于当前会话，有准确报告、逐项独立hold审核和正式hold裁定，来源链与缓存可确认，且未被新输入撤销；允许保留待决请求。客户端此时可结束本轮，同时明确“原任务尚未完成”，`completion_ready` 仍为false，不能发放下一段或声称科学成功。仅有stopped标签或旧hold不能替代该链；正式stopped还须准确停止记录和原合同停止条件成立。

完成有效性忽略已完成段的时间和工具预算，不因预算在完成后耗尽或到期失效。正常经 `Project.close_task` 关闭后仍可确认完成；`stopped_without_acceptance` 不成为完成。缓存缺失或无法确认撤销状态时拒绝完成。新用户输入后的 `invalidate` 即使不改变正式completed记录，也会使旧完成不能自动用于新请求的Stop。

## CLI

执行段未开始、到期、耗尽或hold时，客户端可从零生成有限恢复文件，无需先获得普通科研工具预算：

- 治理输入只写 `.aireos/cache/execution-input-<session_sha256>-<command>.json`。`execution-status` 的 `recovery.input_paths` 给出全部准确路径；未绑定时阻断诊断也给出当前会话路径模板。参数必须符合对应既有CLI方法的有限字段，并精确定位已有正式任务或最新执行段。不会创建新合同、角色或授权。
- 报告只在原任务输出目录下新建 `execution-reports/<session_sha256>/<segment_id>/report-r<segment_revision>-<0..63>.json`；状态的 `recovery.report_path` 给出首个草稿路径，无需运行散列脚本。每段版本最多64个草稿，只可新建，不能覆写任何已有报告。原任务只有精确科学文件输出、没有可用报告目录时明确拒绝，须按原治理边界处理输出范围。
- 报告Write在放行前复用正式核心同一份全验收/证据校验，并限制为64KiB、固定字段、有限条目；不得藏任意脚本字段、删验收项、伪造满足状态或使用过期证据。普通科研输出、跨会话文件、旧段、链接和越界路径均不获得恢复豁免。
- 报告落地后仍须 `add-artifact → execution-report → execution-review → execution-decide`，审核者及裁定者仍持各自原有能力。cache中的CLI参数不能被登记为artifact。恢复文件只是参数和候选报告，不是新的许可或事实真源，也不重发预算。

恢复Bash命令必须使用当前守卫的绝对Python解释器、固定项目入口和纯字面成对参数。拒绝相对解释器、PATH别名、同名程序、变量、`$()`、反引号、通配、管道、重定向、命令串等shell语法。执行段治理豁免还会重读当前会话的准确派生输入文件，核对schema与正式关联，不能用任意预置JSON取得豁免。检索恢复仍使用本会话固定client-query文件。

现有 `--project / --input / --credential-file / --request-id` 语法保持一致：

```text
python -m aireos --project PROJECT execution-status --input status.json
python -m aireos --project PROJECT --credential-file PLAN_KEY execution-configure --input policy.json --request-id configure-1
python -m aireos --project PROJECT --credential-file EXECUTE_KEY execution-begin --input begin.json --request-id begin-1
python -m aireos --project PROJECT --credential-file EXECUTE_KEY execution-report --input report.json --request-id report-1
python -m aireos --project PROJECT --credential-file DECIDE_KEY execution-decide --input decision.json --request-id decision-1
python -m aireos --project PROJECT execution-review-packet --input review-packet.json
python -m aireos --project PROJECT --credential-file REVIEW_KEY execution-review --input assessment.json --request-id review-1
python -m aireos --project PROJECT execution-admit --input admission.json
python -m aireos --project PROJECT execution-invalidate --input invalidate.json
```

配置/开始/报告/裁定参数对应同名方法，`request_id` 仅由CLI参数传入。`execution-status` 在既有 `READ_ONLY` 中，不接受请求ID。`execution-admit` 和 `execution-invalidate` 仅改变操作缓存，拒绝凭据及请求ID，不能获得或使用owner密钥。适配器仍须把这些命令纳入自己的明确白名单；核心CLI注册不等于宿主强制执行已经成立。

## 验证范围

`tests/test_execution_control.py` 使用真实Store、Project、文件快照及不同执行/审核/裁定身份，覆盖完整生命周期、未知进展、删验收项、缺证据类别、自审、报告生产者、精确版本、报告和证据篡改、旧pass、hold修订恢复、预算、缓存丢失/损坏、并发、会话隔离、迟到事件、重放、撤销、只读状态和CLI路由。受信宿主及真实项目包装器的强制接入由宿主集成测试单独验证。

`tests/test_execution_client.py` 从未预置治理输入及报告开始，用真实固定入口子进程执行configure、begin、报告登记、逐项独立审核、hold修订、continue及新段。覆盖预算耗尽/到期后恢复、shell展开/同名解释器拒绝、跨会话及任意科学输出拒绝；这些本地CLI证据不等于真实模型工具及所有客户端都已接入。
