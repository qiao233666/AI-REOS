# alpha.8 验证

Windows / Python 3.10.20 本地全量280项合成测试通过，无跳过。

详细报告：[Python 3.10](alpha8/python310/REPORT.md)。

本地受限进程对tempfile的Windows mode=0700临时目录不能访问；使用工作区专用临时目录和仅作用于tempfile目录创建的ACL继承测试启动器完成验证。生产权限、文件校验及测试断言均未更改。GitHub CI使用正常临时目录独立运行。

合成测试不证明真实科研正确性或ZCode当前会话已加载新钩子；未接入客户端不享有该程序保障。
