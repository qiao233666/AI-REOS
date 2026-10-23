"""可交付给命令行和调用者的明确错误。"""


class FrameworkError(Exception):
    """输入或工作流不满足约定。"""


class ConflictError(FrameworkError):
    """版本、请求身份或所有权冲突。"""


class PermissionDenied(FrameworkError):
    """受管入口没有相应能力。"""


class StaleInput(FrameworkError):
    """输入或依据已经变化，需要重新准备。"""
