"""只读采纳预检：说明当前可确定的阻塞，正式动作仍逐项重新校验。"""
import json

from .errors import FrameworkError
from .inputs import canonical_path, objects as input_objects, validate
from .recall import reference, source_state
from .source_scope import live_sources


def collect(tx, task_id, adoptions):
    from .project import Project, explicit_review, typed
    if (not isinstance(adoptions, list) or len(adoptions) > 64
            or any(not isinstance(e, dict) or not isinstance(e.get("item_id"), str)
                   or not isinstance(e.get("review_id"), str) for e in adoptions)):
        raise FrameworkError("采纳清单须逐项明确item_id与review_id，最多64项")
    task = typed(tx, task_id, "task")
    issues, keys = [], set()

    def add(code, message, item_id=None, path=None):
        issue = dict(code=code, message=message)
        if item_id:
            issue["item_id"] = item_id
        if path:
            issue["path"] = path
        key = json.dumps(issue, ensure_ascii=False, sort_keys=True)
        if key not in keys:
            keys.add(key)
            issues.append(issue)

    if task["data"]["status"] != "delivered":
        add("task_not_delivered", "任务须先交付；已完成请求应以原request-id恢复，不能新建采纳请求")
    contract = task["data"]["contract"]
    try:
        if ("declared_outputs" in contract
                and [canonical_path(tx.store.root, path) for path in contract["declared_outputs"]] != contract["outputs"]):
            add("output_path_changed", "任务输出的实际解析路径已经变化，须核对原范围而非刷新收据")
    except FrameworkError as exc:
        add("output_path_changed", str(exc))
    reviews = tx.list("review")
    decisions = tx.list("decision")
    submitted = task["data"].get("submitted", [])

    def opinions(ref):
        return [r for r in reviews if r["data"]["target"] == ref]

    if adoptions:
        for entry in task["data"]["contract"].get("inputs", []):
            for reason in validate(tx, [entry]):
                add("input_changed", reason, path=entry.get("path") if isinstance(entry, dict) else None)
    for ref in submitted:
        obj = tx.get(ref["id"])
        if reference(obj) != ref:
            add("submission_stale", "交付版本已变化，需要重新交付并核对实际版本", obj["id"])
        if not opinions(ref):
            add("submission_review_missing", "该交付版本没有质量意见；未知或失败也须如实登记", obj["id"])
    promoted = set()
    for selection in adoptions:
        before = len(issues)
        item = typed(tx, selection["item_id"], "item")
        ref = reference(item)
        if item["data"]["task"] != task_id or ref not in submitted:
            add("target_not_submitted", "采纳目标不是本任务当前已交付版本", item["id"])
            continue
        try:
            review = typed(tx, selection["review_id"], "review")
            if (review["data"]["target"] != ref or review["data"]["verdict"] != "pass"
                    or not explicit_review(review)):
                add("review_invalid", "所选审核未明确通过该准确版本", item["id"])
        except FrameworkError as exc:
            add("review_invalid", str(exc), item["id"])
        current = opinions(ref)
        if not current or current[-1]["data"]["verdict"] != "pass" or not explicit_review(current[-1]):
            add("latest_review_not_pass", "当前版本最新意见尚未明确通过", item["id"])
        try:
            live_sources(tx, task, [item["id"]])
        except FrameworkError as exc:
            add("source_graph_invalid", str(exc), item["id"])
            continue
        seen = set()

        def inspect(obj, positive=False):
            key = (obj["id"], obj["rev"], positive)
            if key in seen:
                return
            if len(seen) >= 400:
                add("source_graph_limit", "输入及依据诊断范围超限，须缩小任务", obj["id"])
                return
            seen.add(key)
            data = obj["data"]
            state = Project._status(tx, obj["id"])
            if any("替代关系" in reason for reason in state.get("provenance", {}).get("reasons", [])):
                add("support_superseded" if positive else "target_superseded",
                    "已有采纳的新版本替代该材料，须核对当前适用性", obj["id"])
            if data.get("type") == "artifact":
                try:
                    tx.store.blob(data["blob"])
                except FrameworkError:
                    add("snapshot_invalid", "历史字节快照缺失或摘要不符，须调查而非清除记录", obj["id"])
                if source_state(tx.store, obj) is False:
                    add("source_changed", "本次实际来源路径或字节不符；保留旧快照，核对恢复或新版交付", obj["id"], data["origin"])
            if positive:
                quality = opinions(reference(obj))
                if not quality:
                    add("support_review_missing", "正向支持材料的准确版本尚未核查，不能由结论的pass代替", obj["id"])
                elif quality[-1]["data"]["verdict"] != "pass" or not explicit_review(quality[-1]):
                    add("support_review_not_pass", "支持材料当前版本的最新审核尚未明确通过", obj["id"])
                if data.get("type") == "claim" and state["status"] != "adopted" and obj["id"] not in promoted:
                    add("support_not_adopted", "作为依据的主张须已采纳，或在本批中先于下游通过采纳", obj["id"])
                # 依赖未采纳等一般原因由递归检查展开；明确撤回或替代不能被批内顺序掩盖。
                if state["status"] == "withdrawn":
                    add("support_withdrawn", "正向支持材料已撤回，不能继续背书", obj["id"])
            for case in tx.list("case"):
                if case["data"]["target"]["id"] == obj["id"] and case["data"]["status"] in {"open", "unresolved"}:
                    add("case_unresolved", "实际来源存在已确认但未解决的争议", obj["id"])
            origin_task = typed(tx, data["task"], "task")
            if origin_task["id"] != task_id:
                for reason in validate(tx, origin_task["data"]["contract"].get("inputs", [])):
                    add("source_input_changed", reason, obj["id"])
            for source in input_objects(tx, origin_task["data"]["contract"].get("inputs", []), basis_only=True):
                if source["id"] != obj["id"]:
                    inspect(source, True)
            for expected in data.get("supports", []):
                source = typed(tx, expected["id"], "item")
                if reference(source) != expected:
                    add("support_version_changed", "正向支持材料版本已变化，需要准确的新依据约定", source["id"])
                else:
                    inspect(source, True)

        inspect(item)
        # 同批上游只有在其预检无新增阻塞时，才可供后续项目作顺序参考。
        if len(issues) == before:
            promoted.add(item["id"])
    return dict(task_id=task_id, task_revision=task["rev"], ready=not issues, blockers=len(issues), issues=issues,
                note="只读预检不授予采纳；正式动作仍核对权限、收据、占用及当前版本。未通过时先修指认条件，重复请求用原request-id恢复。")


def packet(result, max_chars=10000, offset=0):
    if (isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50000
            or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= result["blockers"]):
        raise FrameworkError("诊断预算须为1000至50000字符，offset须在阻塞条目范围内")
    response = {**result, "issues": [], "offset": offset, "next_offset": None, "display_truncated": False}
    for index, issue in enumerate(result["issues"][offset:], offset):
        response["issues"].append(issue)
        response["next_offset"] = index + 1 if index + 1 < result["blockers"] else None
        response["display_truncated"] = response["next_offset"] is not None
        if len(json.dumps(response, ensure_ascii=False)) > max_chars:
            response["issues"].pop()
            response["next_offset"] = index
            response["display_truncated"] = True
            if not response["issues"]:
                raise FrameworkError("单项诊断超出预算，请提高max_chars；不得省略后宣称无阻塞")
            break
    return response


def require_ready(result):
    if not result["ready"]:
        details = [i["code"] + (" " + i["item_id"] if "item_id" in i else "") + ": " + i["message"]
                   for i in result["issues"][:4]]
        raise FrameworkError("采纳前检查发现" + str(result["blockers"]) + "项阻塞；" + "；".join(details)
            + "。完整分页诊断用check-finalize；不要以新request-id反复登记或清除历史快照。")
