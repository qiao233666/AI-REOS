# alpha.2验证报告

Python 3.10和3.12在Windows本地各运行169项合成回归，均通过、无跳过。

- [Python 3.10详情](alpha2/python310/REPORT.md)
- [Python 3.12详情](alpha2/python312/REPORT.md)

alpha.1在GitHub Windows 3.12中的6个断言失败已用路径别名在本地复现。测试准备现在统一解析项目路径，新增别名反例；特殊文件拒读及文件读取后变化的原断言保留，运行证据实现未改变。旧版本标签、附件和原测试记录保留。

这些是合成检查；不能证明科研结论、长期召回或费用节省。GitHub跨平台执行状态以对应提交的Actions记录为准。
