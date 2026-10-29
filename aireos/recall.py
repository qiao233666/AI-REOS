"""可重建的检索与带来源校验的上下文收据。"""
import hashlib
import re

from .errors import FrameworkError
from .storage import digest


def terms(text):
    text = str(text).lower()
    result = set(re.findall(r"[a-z0-9_]+", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        result.add(run)
        result.update(run[i:i + 2] for i in range(len(run) - 1))
    return result


def reference(obj):
    return {"id": obj["id"], "rev": obj["rev"], "digest": digest(obj["data"])}


def contract_hash(task):
    return digest(task["data"]["contract"])


def source_state(store, item):
    data = item["data"]
    if data.get("type") != "artifact":
        return None
    try:
        path = (store.root / data["origin"]).resolve()
        path.relative_to(store.root)
        if data.get("resolved_origin") and path.relative_to(store.root).as_posix() != data["resolved_origin"]:
            return False
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        store.blob(data["blob"])
        return actual == data["blob"]
    except (OSError, ValueError, FrameworkError):
        return False


def expand(tx, seeds, maximum=200):
    from .memory_index import MemoryIndex
    index = MemoryIndex(tx)
    index.refresh()
    selected = {}
    truncated = False
    for obj in seeds:
        if obj["id"] not in selected and len(selected) >= maximum:
            truncated = True
            continue
        selected[obj["id"]] = obj
    pending = list(selected.values())
    while pending:
        obj = pending.pop()
        for identifier in sorted(index.related(obj["id"])):
            if identifier in selected:
                continue
            if len(selected) >= maximum:
                truncated = True
                continue
            other = tx.get(identifier)
            selected[identifier] = other
            pending.append(other)
    return list(selected.values()), truncated


def search(tx, query, limit=12, seeds=()):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise FrameworkError("检索条数必须为1至100")
    from .memory_index import MemoryIndex
    index = MemoryIndex(tx)
    result = index.search(query, limit)
    selected = {obj["id"]: tx.get(obj["id"]) for obj in result["hits"]}
    historical = [obj for obj in result["hits"] if obj["rev"] != selected[obj["id"]]["rev"]]
    matches, truncated = expand(tx, [*seeds, *selected.values()])
    return {"matches": matches, "historical": historical, "truncated": truncated,
            "retrieval": {**result["metadata"], "candidate_limit_reached": result["truncated"]},
            "coverage": "有限候选检索及显式关系补取，不是完备历史证明；未命中不代表从未做过", "query": query}


def check(tx, receipt_id, task_id=None):
    receipt = tx.get(receipt_id)
    if receipt["kind"] != "receipt":
        raise FrameworkError("需要上下文收据")
    data = receipt["data"]
    reasons = []
    warnings = []
    if data["project"] != tx.store.project_id(tx.connection):
        reasons.append("项目身份不符")
    if task_id is not None and data["task"] != task_id:
        reasons.append("收据不属于当前任务")
    task = tx.get(data["task"])
    if contract_hash(task) != data["contract"]:
        reasons.append("任务合同已变化")
    from .inputs import validate
    reasons.extend(validate(tx, task["data"]["contract"].get("inputs", [])))
    from . import prework
    history_reasons, history_warnings = prework.validate(tx, data.get('history_check'), data['query'])
    reasons.extend(history_reasons)
    warnings.extend(history_warnings)
    delta_checked = 0
    if data["epoch"] != tx.store.epoch(tx.connection):
        if data.get("receipt_version", 1) < 2 or task["data"]["contract"].get("recall_mode") == "strict":
            reasons.append("严格模式下全库知识变化，需要重新准备")
        else:
            from .memory_index import KINDS, relations
            relevant = {ref["id"] for ref in data["refs"]}
            relevant.update(entry["id"] for entry in task["data"]["contract"].get("inputs", []) if isinstance(entry, dict) and entry.get("type") == "item")
            from .inputs import objects as input_objects
            relevant.update(obj["id"] for obj in input_objects(tx, task["data"]["contract"].get("inputs", [])))
            changes = tx.connection.execute("SELECT * FROM versions WHERE rowid>? ORDER BY rowid", (data["watermark"],)).fetchall()
            for row in changes:
                if row["kind"] not in KINDS:
                    continue
                delta_checked += 1
                payload = __import__('json').loads(row["payload"])
                if row["id"] in relevant or payload.get("task") == task["id"] or any(identifier in relevant for identifier, _ in relations(payload)):
                    reasons.append("相关任务、来源或明确关联有新增变化: " + row["id"])
                elif terms(data["query"]) & terms(str(payload)):
                    warnings.append({"id": row["id"], "rev": row["rev"], "note": "新增内容可能相关；尚未证明与当前判断存在冲突，可按需补取"})
    if data["truncated"]:
        reasons.append("检索结果超出本次范围，须细化查询")
    scoped = data.get('receipt_version',1)>=3
    if scoped and not isinstance(data.get('live_refs'),list):
        reasons.append('新收据缺少实际来源目录，需要重新prepare')
    live = data.get('live_refs',[]) if scoped else data['refs']
    live_keys={(ref['id'],ref['rev']) for ref in live}
    combined={(ref['id'],ref['rev']):ref for ref in data['refs']+live}
    for key,ref in combined.items():
        obj = tx.get(ref["id"])
        if reference(obj) != ref:
            reasons.append("所用记录版本变化: " + ref["id"])
        frozen=tx.get(ref['id'],ref['rev'])
        if frozen['data'].get('type')=='artifact':
            try:
                tx.store.blob(frozen['data']['blob'])
            except FrameworkError:
                reasons.append('历史文件快照损坏或缺失: '+ref['id'])
                continue
        if source_state(tx.store, obj) is False:
            if key in live_keys:
                reasons.append("实际文件变化或快照损坏: " + ref["id"])
            else:
                warnings.append(dict(id=ref['id'],rev=ref['rev'],type='historical_source_changed',
                    note='历史快照完整，现场文件已变化；仅作历史参考，不自动作为本次依据。'))
    for ref in data.get("historical", []):
        frozen=tx.get(ref["id"], ref["rev"])
        if reference(frozen) != ref:
            reasons.append("历史版本损坏: " + ref["id"])
        if frozen['data'].get('type')=='artifact':
            try:
                tx.store.blob(frozen['data']['blob'])
            except FrameworkError:
                reasons.append('历史文件快照损坏或缺失: '+ref['id'])
    return {"valid": not reasons, "reasons": list(dict.fromkeys(reasons)), "receipt": receipt_id,
            "delta_checked": delta_checked, "warnings": warnings[:12], "warnings_truncated": len(warnings) > 12,
            "coverage": data["coverage"]}
