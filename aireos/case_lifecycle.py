"""有新增信息、独立初核和统筹授权的争议续查；不擦除旧轮次。"""
from .errors import ConflictError, FrameworkError, PermissionDenied, StaleInput
from .recall import reference
from .storage import digest


def evidence_keys(objects):
    """相同内容换身份不自动变成新信息；语义等价仍由独立初核判断。"""
    return sorted({digest({"artifact": obj["data"]["blob"]}) if obj["data"].get("type") == "artifact"
        else digest({"text": obj["data"].get("text"), "scope": obj["data"].get("scope")}) for obj in objects})


def objection_fingerprint(target, reason, keys):
    return digest(dict(target=target, reason=" ".join(reason.split()), evidence=sorted(keys)))


def pins_current(tx, proposal, case, target):
    from .recall import source_state
    if reference(case) != proposal["case_ref"] or reference(target) != proposal["target"]:
        return False
    return all(reference(tx.get(ref["id"])) == ref and source_state(tx.store, tx.get(ref["id"])) is not False
        for ref in proposal["evidence"])


def can_continue(case, target):
    # 新版目标使旧轮次无法再核查；仅经新材料、独立初核及统筹授权才重绑定。
    return case["data"]["status"] in {"unresolved", "resolved", "dismissed"} or (
        case["data"]["status"] == "open" and case["data"]["target"] != reference(target))


def propose(store, case_id, evidence, reason, expected_revision, request_id):
    from .project import Project, text_required, typed
    text_required(reason, "续查的新增信息及理由")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(value, str) for value in evidence):
        raise FrameworkError("续查须提供新增材料身份数组，不能无信息重开")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise FrameworkError("续查须明确当前目标的正整数版本")
    def apply(tx):
        case = typed(tx, case_id, "case")
        target = typed(tx, case["data"]["target"]["id"], "item")
        Project._scope_task(tx, target["data"]["task"])
        if not can_continue(case, target):
            raise ConflictError("须先结束或暂停上一轮，再申请续查")
        if target["rev"] != expected_revision:
            raise StaleInput("续查目标版本已变化")
        proof = [typed(tx, identifier, "item") for identifier in evidence]
        keys = evidence_keys(proof)
        previous = set(case["data"].get("evidence_keys", evidence_keys([tx.get(ref["id"], ref["rev"]) for ref in case["data"].get("evidence", [])])))
        for old in tx.list("case_continuation"):
            if old["data"]["case"] == case_id:
                if old["data"]["status"] in {"proposed", "substantial"}:
                    if pins_current(tx, old["data"], case, target):
                        raise ConflictError("本案已有待处理续查申请，不重复登记")
                    tx.replace(old["id"], old["rev"], {**old["data"], "status": "stale", "staleness": "案件、目标或材料版本变化"})
                elif old["data"]["status"] != "stale":
                    previous.update(old["data"]["evidence_keys"])
        if not set(keys) - previous:
            raise ConflictError("没有新的材料内容，不能换身份或请求刷新复核预算")
        if any(obj["id"] == target["id"] for obj in proof):
            raise FrameworkError("不能把争议目标本身当作新增证据")
        return tx.create("case_continuation", dict(case=case_id, case_ref=reference(case), target=reference(target),
            evidence=[reference(obj) for obj in proof], evidence_keys=keys, reason=reason,
            proposer=tx.principal["actor"], status="proposed", schema_version=1))
    return store.mutate("case.continue.propose", dict(case=case_id, evidence=evidence, reason=reason, expected_revision=expected_revision),
        request_id, "execute", apply, corpus=True)


def current(tx, continuation_id):
    from .project import Project, typed
    proposal = typed(tx, continuation_id, "case_continuation")
    case = typed(tx, proposal["data"]["case"], "case")
    target = typed(tx, proposal["data"]["target"]["id"], "item")
    Project._scope_task(tx, target["data"]["task"])
    if not pins_current(tx, proposal["data"], case, target):
        raise StaleInput("申请后案件或目标版本变化，须重新确认，不能沿用旧初核")
    return proposal, case, target


def assess(store, continuation_id, substantial, reason, request_id):
    from .project import text_required
    text_required(reason, "新增信息的独立初核依据")
    if not isinstance(substantial, bool):
        raise FrameworkError("是否有实质新增信息须为布尔值")
    def apply(tx):
        proposal, _, target = current(tx, continuation_id)
        if proposal["data"]["status"] != "proposed":
            raise ConflictError("续查申请已经过初核")
        if tx.principal["actor"] in {proposal["data"]["proposer"], target["data"]["producer"]}:
            raise PermissionDenied("提出者或产物执行者不能独立初核续查申请")
        return tx.replace(proposal["id"], proposal["rev"], {**proposal["data"], "status": "substantial" if substantial else "rejected",
            "assessment": reason, "assessor": tx.principal["actor"]})
    return store.mutate("case.continue.assess", dict(continuation=continuation_id, substantial=substantial, reason=reason),
        request_id, "review", apply, corpus=True)


def authorize(store, continuation_id, budget, owner_role, exit_condition, reason, request_id):
    from .project import text_required
    for value, label in ((owner_role, "续查负责人"), (exit_condition, "退出条件"), (reason, "统筹授权理由")):
        text_required(value, label)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise FrameworkError("新轮次预算须为正整数")
    def apply(tx):
        proposal, case, target = current(tx, continuation_id)
        if proposal["data"]["status"] != "substantial":
            raise FrameworkError("须先由独立核查者确认有实质新增信息")
        if not can_continue(case, target):
            raise ConflictError("案件尚未结束旧轮次")
        principal = tx.connection.execute("SELECT capabilities FROM principals WHERE actor=?", (owner_role,)).fetchone()
        if principal is None:
            raise FrameworkError("续查负责人须已登记")
        import json
        if not set(json.loads(principal[0])) & {"review", "*"}:
            raise FrameworkError("续查负责人须有核查能力")
        data = case["data"]
        round_number = data.get("round", 1) + 1
        prior_keys = data.get("evidence_keys")
        if prior_keys is None:
            prior_keys = evidence_keys([tx.get(ref["id"], ref["rev"]) for ref in data.get("evidence", [])])
        prior_fingerprint = data.get("objection_fingerprint") or objection_fingerprint(data["target"], data["reason"],
            prior_keys)
        fingerprints = sorted(set(data.get("objection_fingerprints", [prior_fingerprint])) |
            {objection_fingerprint(reference(target), proposal["data"]["reason"], proposal["data"]["evidence_keys"])})
        updated = tx.replace(case["id"], case["rev"], {**data, "case_schema": 2, "round": round_number,
            "round_used": 0, "used": data["used"], "budget": budget,
            "total_budget": data.get("total_budget", data["budget"]) + budget,
            "target": reference(target), "status": "open", "owner": owner_role, "exit_condition": exit_condition,
            "continuation": proposal["id"], "round_authority": tx.principal["actor"], "round_reason": reason,
            "objection_fingerprints": fingerprints,
            "evidence": proposal["data"]["evidence"], "evidence_keys": sorted(set(prior_keys) | set(proposal["data"]["evidence_keys"]))})
        tx.replace(proposal["id"], proposal["rev"], {**proposal["data"], "status": "approved", "round": round_number,
            "authorization": reason, "authority": tx.principal["actor"], "budget": budget})
        return updated
    return store.mutate("case.continue.authorize", dict(continuation=continuation_id, budget=budget, owner=owner_role,
        exit=exit_condition, reason=reason), request_id, "decide", apply, corpus=True)
