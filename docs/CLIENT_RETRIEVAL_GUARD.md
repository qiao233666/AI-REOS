# 客户端每轮检索前置

此组件是alpha.8新增的显式接入能力。它解决“已经规定先检索，但客户端仍直接读旧文件或答状态”的执行缺口，不改变原有prepare收据、权限、审核、采纳和历史快照语义。

## 实际行为

项目在`aiops/aireos/retrieval-policy.json`启用策略。ZCode的SessionStart清除该会话旧完成记录；UserPromptSubmit先撤销上一轮状态，直接用本次请求联查新版正式记录和旧历史，并把有界结果注入上下文。成功返回两路状态后才记录本轮完成。PreToolUse没有完成记录就拒绝普通工具；Stop会要求继续检索。失败、空问题、仅导航、帮助或命令中提到session-context都不能解锁。

完成记录保存在本项目`.aireos/cache/client-guard.sqlite3`，按真实session_id及每轮随机标识隔离。较早轮次的迟到结果不能覆盖新一轮。缓存不是新的科研账本，不增加主库表，也不能作为正式执行或采纳授权。查询截断和未命中仍须如实处理：查询成功不等于完整召回，更不等于模型理解、原文核对或科学正确。

长请求只取前后各6000字符构造查询，并在注入结果中标记`prompt_truncated`。这不能证明覆盖长请求的全部问题，必要时仍需按具体问题追加治理检索。

## 接入

1. 备份项目适配、入口、版本锁和旧账本；新框架用独立固定目录，保留原版本及回滚记录。
2. 项目固定入口的`load_framework()`须先验证锁，再将锁定包加入sys.path；不能导入任意现场源码。
3. 将`template/retrieval_guard.py`复制至项目`aiops/checks/hooks/retrieval_guard.py`。
4. 写项目策略，例如：

```json
{"schema":1,"enabled":true,"entry":"aiops/aireos/run.py","hook":"aiops/checks/hooks/retrieval_guard.py","bootstrap_files":["AGENTS.md","aiops/aireos/PROJECT_RULES.md"],"readonly_files":{}}
```

5. 调用`aireos.client_guard.zcode_config(root, python, script, existing)`生成合并配置，核对后写入项目`.zcode/config.json`。保留原计算和危险命令守卫，不自动修改全局配置。四个事件须无matcher限制，使用固定绝对脚本路径；自动查询的超时为60秒。
6. 运行`client-adapter-check`核对挂载和可选只读文件哈希。重启客户端会话，观察真实事件、注入结果和拒绝记录；脚本测试及配置检查不能证明当前客户端已重新加载。

查询故障时允许Read读取明确bootstrap_files，以及仅含固定绝对入口的一条恢复命令：`python -X utf8 <固定run.py绝对路径> client-context --input query.json`。JSON含具体query和当前session_id；client-context重新执行真实查询，成功才解锁。echo、命令串和其他脚本不获此豁免。该操作只写运行缓存，不加载项目写凭据。

## 边界与验证

自动注入和拦截依赖宿主实际触发事件。当前提供ZCode适配；Codex、Trae或其他没有接入这些事件的客户端只有文字约定，不能宣称同等保障。宿主Stop最多请求有限次数的继续，不是可强制控制全部自然语言输出的安全边界。已启用守卫的异常给出诊断并拒绝普通工具，启动规则与恢复查询通路保留。策略可由负责人用备份恢复或明确关闭，不设计静默绕过开关。

结构化Write/Edit对readonly_files的直接写入会被拒绝；校验器还能发现哈希变化。任意shell、外部应用或未接入工具的文件写入不属于完整文件系统保护；不要把这些检查说成操作系统只读权限。

合成测试覆盖真实查询、失败恢复、跨会话/项目隔离、新轮撤销、迟到拒绝、Bootstrap通路、只读写入拒绝和漏挂检测。项目还须验证旧启动指令已替换、新教训通过正式入口可召回。公共包不得包含真实项目的提示、缓存、凭据、科研记录或适配备份。
