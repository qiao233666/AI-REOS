"""复核在开始时占用次数；失败、超时及未知终态均不自动退还。"""
from .errors import ConflictError, FrameworkError, PermissionDenied, StaleInput
from .recall import reference


def reserve(tx, case_id):
    from .project import Project, typed
    case = typed(tx, case_id, "case")
    target = typed(tx, case["data"]["target"]["id"], "item")
    Project._scope_task(tx, target["data"]["task"])
    if target["data"]["producer"] == tx.principal["actor"]:
        raise PermissionDenied("执行者不能预留对自己产物的独立复核")
    round_used = case["data"].get("round_used", case["data"]["used"])
    if case["data"]["status"] != "open" or round_used >= case["data"]["budget"]:
        raise ConflictError("争议未打开或本轮复核次数已耗尽")
    if reference(target) != case["data"]["target"]:
        raise StaleInput("争议目标已变化，须先明确新的复核对象")
    tx.replace(case_id, case["rev"], {**case["data"], "used": case["data"]["used"] + 1, "round_used": round_used + 1})
    return tx.create("case_attempt", dict(case=case_id, target=reference(target), reviewer=tx.principal["actor"],
        status="reserved", external_run=None, round=case["data"].get("round", 1), note="已占用一次；未知、失败或中断不自动退还"))


def validate(tx, attempt_id, case_id=None, target=None, actor=False):
    from .project import typed
    attempt = typed(tx, attempt_id, "case_attempt")
    data = attempt["data"]
    case = typed(tx, data["case"], "case")
    if data["status"] != "reserved" or case["data"]["status"] != "open":
        raise ConflictError("复核预留已用完或案件已结束")
    if case_id is not None and data["case"] != case_id:
        raise FrameworkError("复核预留不属于本案件")
    if data.get("round", 1) != case["data"].get("round", 1):
        raise StaleInput("旧轮次预留不能用于新一轮复核")
    current = typed(tx, data["target"]["id"], "item")
    if reference(current) != data["target"] or (target is not None and reference(target) != data["target"]):
        raise StaleInput("预留的复核目标版本变化")
    if actor and data["reviewer"] != tx.principal["actor"]:
        raise PermissionDenied("只能登记自己预留的实际复核意见")
    return attempt
