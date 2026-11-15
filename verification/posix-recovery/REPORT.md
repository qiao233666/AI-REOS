# 带版本解释器的恢复入口

GitHub Linux 3.10/3.12的两个既有恢复用例失败：真实sys.executable解析后basename为python3.10/python3.12，旧额外名称白名单拒绝它们。移除冗余basename过滤，仍要求绝对路径解析后与守卫实际sys.executable相同；参数、固定入口和shell展开拦截均保留。

本地14项client_guard回归通过，零失败/错误/跳过，源码稳定。CI在最新提交上重新执行跨平台全量；不追加模型调用或外审。
