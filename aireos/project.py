"""受管项目动作；质量意见、协调裁定和历史发表各自留痕。"""
from pathlib import Path

from .errors import ConflictError, FrameworkError, PermissionDenied, StaleInput
from .storage import Store, Transaction, normalize_scope, within
from . import recall


def text_required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise FrameworkError(label + "不能为空")


def typed(tx, identifier, kind):
    obj = tx.get(identifier)
    if obj["kind"] != kind:
        raise FrameworkError("记录类型不符: " + kind)
    return obj


def explicit_review(opinion):
    """旧意见原样保留；缺少明确版本标记时不事后猜测实际检查对象。"""
    data = opinion["data"]
    revision = data.get("inspected_revision")
    return (data.get("review_schema") == 2 and isinstance(revision, int) and not isinstance(revision, bool)
        and revision >= 1 and revision == data["target"]["rev"])


class Project:
    def __init__(self, root, token=None):
        self.store = Store(root, token)

    def checkpoint(self, task_id, progress, next_step, expected_revision, request_id, pending='', references=None):
        from .checkpoints import save
        return save(self, task_id, progress, next_step, expected_revision, request_id, pending, references)

    def task_checkpoint(self, task_id):
        from .checkpoints import read
        return read(self, task_id)

    def create_task(self, title, objective, acceptance, outputs, request_id, inputs=None, budget=None, stop_conditions=None, recall_mode="incremental"):
        text_required(title, "任务标题")
        text_required(objective, "任务目标")
        if not isinstance(acceptance, list) or not isinstance(outputs, list) or not acceptance or not outputs:
            raise FrameworkError("任务必须有验收条件与输出范围")
        for entry in acceptance:
            text_required(entry, "验收条件")
        if inputs is not None and not isinstance(inputs, list):
            raise FrameworkError("任务输入必须为数组")
        if budget is not None and not isinstance(budget, dict):
            raise FrameworkError("预算必须为有字段的对象")
        if stop_conditions is not None and (not isinstance(stop_conditions, list) or not stop_conditions or not all(isinstance(x, str) and x.strip() for x in stop_conditions)):
            raise FrameworkError("停止条件必须为非空字符串数组")
        if recall_mode not in {"incremental", "strict"}:
            raise FrameworkError("召回模式须为incremental或strict")
        from .inputs import canonical_path, bind
        declared_outputs = [normalize_scope(path) for path in outputs]
        outputs = [canonical_path(self.store.root, path) for path in declared_outputs]
        contract = dict(title=title, objective=objective, acceptance=acceptance, outputs=outputs,
            declared_outputs=declared_outputs, recall_mode=recall_mode,
            inputs=inputs or [], budget=budget or {"mode": "manual", "limit": "没有额外昂贵资源授权"},
            stop_conditions=stop_conditions or ["目标、输入或适用条件变化时暂停并重建任务约定"])
        def apply(tx):
            for path in outputs:
                tx.require_scope(path)
            pinned = {**contract, "inputs": bind(tx, inputs or [])}
            return tx.create("task", {"contract": pinned, "status": "planned", "submitted": []})
        return self.store.mutate("task.create", contract, request_id, "plan", apply)

    def prepare(self, task_id, query, request_id, limit=12, max_chars=10000, view="brief"):
        text_required(query, "检索问题")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50000:
            raise FrameworkError("上下文显示预算必须为1000至50000字符")
        def apply(tx):
            task = typed(tx, task_id, "task")
            from . import prework
            from .inputs import objects as input_objects
            seeds = input_objects(tx, task["data"]["contract"].get("inputs", []))
            seeds.extend(obj for obj in tx.list("item") if obj["data"]["task"] == task_id)
            result = recall.search(tx, query, limit, seeds=seeds)
            objects = {obj["id"]: obj for obj in result["matches"]}
            legacy = prework.capture(self.store, tx, query, limit, task['data']['contract']['outputs'],
                [ref for obj in objects.values() if obj['kind']=='memory_note' for ref in obj['data']['sources']])
            receipt = tx.create("receipt", {"project": self.store.project_id(tx.connection),
                "receipt_version": 2, "watermark": tx.connection.execute("SELECT COALESCE(MAX(rowid),0) FROM versions").fetchone()[0],
                "task": task_id, "contract": recall.contract_hash(task), "epoch": self.store.epoch(tx.connection),
                "query": query, "refs": [recall.reference(obj) for obj in objects.values()],
                "historical": [recall.reference(obj) for obj in result["historical"]],
                "truncated": result["truncated"], "coverage": result["coverage"],
                "history_check": legacy['check']})
            from .memory_packet import present
            display = present(tx, list(objects.values()), result["historical"], query,
                max(1000,max_chars//2) if legacy['configured'] else max_chars, view)
            response = {**receipt, **{key: value for key, value in display.items() if key != "current"},
                "context": display["current"], "retrieval": result["retrieval"],
                "retrieval_note": "片段不改写原文，source给出正文字符位置。收据绑定完整版本；未显示内容按data.refs中的id/rev用get补取。状态表示当前复用条件，不证明已读完或科学正确。"}
            if legacy['configured'] or legacy['constraints']:
                response['history']=prework.packet(legacy,query,max(1000,max_chars//2))
                response['constraints']=legacy['constraints']
                return prework.fit_prepare(response,max(1000,max_chars-180))
            return response
        # 任一登记角色均可检索；不把读资料当作决策权限。
        response=self.store.mutate("context.prepare", dict(task=task_id, query=query, limit=limit, max_chars=max_chars, view=view), request_id, None, apply)
        if 'history' in response:
            from .reading import record
            response=record(self.store,'task:'+task_id,response)
        return response

    def check_receipt(self, receipt_id):
        with self.store.connection() as connection:
            return recall.check(Transaction(self.store, connection, {}), receipt_id)

    @staticmethod
    def _fresh(tx, receipt_id, task_id):
        result = recall.check(tx, receipt_id, task_id)
        if not result["valid"]:
            raise StaleInput("；".join(result["reasons"]))

    def start_task(self, task_id, receipt_id, request_id):
        def apply(tx):
            task = self._scope_task(tx, task_id)
            self._fresh(tx, receipt_id, task_id)
            if task["data"]["status"] != "planned":
                raise ConflictError("任务只能从待执行进入执行")
            for path in task["data"]["contract"]["outputs"]:
                tx.require_scope(path)
                from .storage import within
                for other in tx.list("task"):
                    if other["id"] != task_id and other["data"]["status"] in {"running", "delivered"} and any(
                            within(path, output) or within(output, path) for output in other["data"]["contract"]["outputs"]):
                        raise ConflictError("已有执行或待验收任务占用重叠输出；请使用独立候选目录")
            return tx.replace(task_id, task["rev"], {**task["data"], "status": "running"})
        return self.store.mutate("task.start", dict(task=task_id, receipt=receipt_id), request_id, "execute", apply)

    @staticmethod
    def _scope_task(tx, task_id):
        task = typed(tx, task_id, "task")
        from .inputs import canonical_path
        contract = task["data"]["contract"]
        if "declared_outputs" in contract and [canonical_path(tx.store.root, path) for path in contract["declared_outputs"]] != contract["outputs"]:
            raise StaleInput("任务输出的实际解析路径已经变化")
        for path in task["data"]["contract"]["outputs"]:
            tx.require_scope(path)
        return task

    @staticmethod
    def _task_open(tx, task_id):
        task = Project._scope_task(tx, task_id)
        if task["data"]["status"] not in {"running", "delivered"}:
            raise FrameworkError("任务不在可交付状态")
        for path in task["data"]["contract"]["outputs"]:
            tx.require_scope(path)
        return task

    def add_artifact(self, path, title, task_id, request_id):
        path = normalize_scope(path)
        text_required(title, "产物标题")
        resolved = (self.store.root / path).resolve()
        try:
            relative = resolved.relative_to(self.store.root)
        except ValueError as exc:
            raise FrameworkError("产物必须位于项目内") from exc
        if any(part.startswith(".") for part in relative.parts):
            raise FrameworkError("隐藏的治理文件或凭据不能作为项目产物")
        def apply(tx):
            task = self._task_open(tx, task_id)
            tx.require_scope(path)
            tx.require_scope(relative.as_posix())
            if not any(within(relative.as_posix(), output) for output in task["data"]["contract"]["outputs"]):
                raise PermissionDenied("产物不在任务输出范围")
            try:
                content = resolved.read_bytes()
                blob = self.store.snapshot(content)
            except OSError as exc:
                raise FrameworkError("产物文件不可读取") from exc
            from .documents import searchable
            return tx.create("item", dict(type="artifact", title=title, origin=path, resolved_origin=relative.as_posix(), blob=blob,
                task=task_id, producer=tx.principal["actor"], supports=[], aliases=[], **searchable(path, content)))
        return self.store.mutate("item.artifact", dict(path=path, title=title, task=task_id), request_id, "execute", apply, corpus=True)

    def add_item(self, title, text, classification, scope, supports, task_id, request_id, aliases=None, supersedes=None):
        for value, label in ((title, "标题"), (text, "内容"), (scope, "适用范围")):
            text_required(value, label)
        if classification not in {"fact", "inference", "hypothesis", "unknown"}:
            raise FrameworkError("知识分类不合法")
        for values in (supports, aliases or [], supersedes or []):
            if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
                raise FrameworkError("依据、别名和替代项必须为字符串数组")
        if classification == "fact" and not supports:
            raise FrameworkError("事实必须有可追溯依据")
        payload = dict(title=title, text=text, classification=classification, scope=scope, supports=supports,
                       task=task_id, aliases=aliases or [], supersedes=supersedes or [])
        def apply(tx):
            self._task_open(tx, task_id)
            refs = [recall.reference(typed(tx, identifier, "item")) for identifier in supports]
            previous = [recall.reference(typed(tx, identifier, "item")) for identifier in (supersedes or [])]
            return tx.create("item", {**payload, "supports": refs, "supersedes": previous,
                "type": "claim", "producer": tx.principal["actor"]})
        return self.store.mutate("item.claim", payload, request_id, "execute", apply, corpus=True)

    def revise_item(self, item_id, expected_revision, text, request_id):
        text_required(text, "修订内容")
        def apply(tx):
            item = typed(tx, item_id, "item")
            self._task_open(tx, item["data"]["task"])
            if item["data"]["producer"] != tx.principal["actor"]:
                raise PermissionDenied("只能修订自己提交的产物")
            if item["data"]["type"] != "claim":
                raise FrameworkError("文件产物变更应重新登记快照")
            return tx.replace(item_id, expected_revision, {**item["data"], "text": text})
        return self.store.mutate("item.revise", dict(item=item_id, rev=expected_revision, text=text), request_id, "execute", apply, corpus=True)

    def submit(self, task_id, items, receipt_id, request_id, leases=None):
        def apply(tx):
            task = self._task_open(tx, task_id)
            self._fresh(tx, receipt_id, task_id)
            if not items:
                raise FrameworkError("交付不能为空")
            refs = []
            for identifier in items:
                item = typed(tx, identifier, "item")
                if item["data"]["task"] != task_id:
                    raise FrameworkError("不能交付其他任务的产物")
                refs.append(recall.reference(item))
            from .coordination import check_leases
            check_leases(tx, task_id, leases or [])
            return tx.replace(task_id, task["rev"], {**task["data"], "status": "delivered", "submitted": refs,
                "submitted_by": tx.principal["actor"], "leases": leases or []})
        return self.store.mutate("task.submit", dict(task=task_id, items=items, receipt=receipt_id, leases=leases or []), request_id, "execute", apply)

    def begin_case_review(self, case_id, request_id):
        from .case_budget import reserve
        return self.store.mutate("case.review.begin", dict(case=case_id), request_id, "review", lambda tx: reserve(tx, case_id), corpus=True)

    def review(self, item_id, verdict, method, checks, request_id, expected_revision=None, attempt_id=None):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise FrameworkError("审核必须明确实际核查的正整数版本，不能省略或为空")
        if verdict not in {"pass", "limit", "fail", "unknown"} or not isinstance(checks, list) or not checks:
            raise FrameworkError("审核需要明确判定和检查记录")
        text_required(method, "审核方法")
        for entry in checks:
            text_required(entry, "检查记录")
        def apply(tx):
            item = typed(tx, item_id, "item")
            if item["rev"] != expected_revision:
                raise StaleInput("实际核查的版本与当前产物不符")
            self._scope_task(tx, item["data"]["task"])
            if item["data"]["producer"] == tx.principal["actor"]:
                raise PermissionDenied("产物执行者不能充当该产物的独立审核者")
            from .case_budget import reserve, validate
            active = [obj for obj in tx.list("case") if obj["data"]["target"]["id"] == item_id and obj["data"]["status"] in {"open", "unresolved"}]
            attempt = validate(tx, attempt_id, target=item, actor=True) if attempt_id else (reserve(tx, active[0]["id"]) if active else None)
            opinion = tx.create("review", dict(target=recall.reference(item), verdict=verdict,
                method=method, checks=checks, reviewer=tx.principal["actor"],
                review_schema=2, inspected_revision=expected_revision,
                case=attempt["data"]["case"] if attempt else None, attempt=attempt["id"] if attempt else None,
                case_round=attempt["data"].get("round", 1) if attempt else None))
            if attempt:
                tx.replace(attempt["id"], attempt["rev"], {**attempt["data"], "status": "reviewed", "review": opinion["id"]})
            return opinion
        return self.store.mutate("quality.review", dict(item=item_id, verdict=verdict, method=method, checks=checks, expected_revision=expected_revision, attempt=attempt_id), request_id, "review", apply, corpus=True)

    @staticmethod
    def _status(tx, item_id, visiting=None, skip_task_inputs=False):
        visiting = set() if visiting is None else set(visiting)
        if len(visiting) >= 200:
            return {"status": "needs_review", "reason": "显式依赖链超过自动核对范围，不能视作已验证"}
        if item_id in visiting:
            return {"status": "needs_review", "reason": "依赖存在循环"}
        visiting.add(item_id)
        item = typed(tx, item_id, "item")
        ref = recall.reference(item)
        decisions = [obj for obj in tx.list("decision") if obj["data"]["target"]["id"] == item_id]
        current = [obj for obj in decisions if obj["data"]["target"] == ref]
        lifecycle = [obj for obj in current if obj["data"]["action"] in {"adopt", "withdraw"}]
        last = lifecycle[-1]["data"] if lifecycle else {}
        reasons = []
        from .inputs import validate, objects as input_objects
        try:
            task_record = typed(tx, item["data"]["task"], "task")
            task_inputs = task_record["data"]["contract"].get("inputs", [])
        except (FrameworkError, KeyError):
            task_inputs = []
            task_record = None
            reasons.append("所属任务约定缺失，不能确认输入来源")
        reasons.extend(validate(tx, task_inputs))
        for obj in ([] if skip_task_inputs else input_objects(tx, task_inputs, basis_only=True)):
            # 为输入字节登记快照不是让原材料依赖自己；直接来源及争议仍由本次检查处理。
            if obj["id"] == item_id and obj["data"]["type"] == "artifact":
                continue
            same_task_artifact = obj["data"]["type"] == "artifact" and obj["data"]["task"] == item["data"]["task"]
            if Project._basis_issues(tx, obj, visiting, skip_task_inputs=same_task_artifact):
                reasons.append("任务声明的上游依据需要重新评估")
        if recall.source_state(tx.store, item) is False:
            reasons.append("实际文件已变化或历史快照损坏")
        for case in tx.list("case"):
            if case["data"]["target"]["id"] == item_id and case["data"]["status"] in {"open", "unresolved"}:
                reasons.append("存在已确认的争议")
        for support in item["data"].get("supports", []):
            obj = typed(tx, support["id"], "item")
            if recall.reference(obj) != support or recall.source_state(tx.store, obj) is False:
                reasons.append("依据版本或实际文件变化")
            else:
                if Project._basis_issues(tx, obj, visiting):
                    reasons.append("上游依据需要重新评估")
        if any(obj["data"].get("action") == "adopt" and any(old["id"] == item_id for old in tx.get(obj["data"]["target"]["id"])["data"].get("supersedes", [])) for obj in tx.list("decision")):
            reasons.append("已有采纳的新版本替代关系，需要核对当前适用性")
        adopted = any(obj["data"]["action"] == "adopt" for obj in current)
        opinions = [obj for obj in tx.list("review") if obj["data"]["target"] == ref]
        provenance_reasons = list(reasons)
        if opinions and (opinions[-1]["data"]["verdict"] == "fail" or (adopted and opinions[-1]["data"]["verdict"] != "pass")):
            reasons.append("当前版本最新质量意见未通过，需要统筹处理")
        if adopted and opinions and not explicit_review(opinions[-1]):
            reasons.append("旧核查记录缺少实际核查版本标记，不能事后推定")
        return {"status": "withdrawn" if last.get("action") == "withdraw" else ("needs_review" if reasons else ("adopted" if adopted else "candidate")),
            "reasons": reasons, "execution": task_record["data"]["status"] if task_record else "unknown",
            "quality": {"verdict": opinions[-1]["data"]["verdict"] if opinions else "not_reviewed",
                "version_binding": "explicit" if opinions and explicit_review(opinions[-1]) else "unknown",
                "review": opinions[-1]["id"] if opinions else None, "count": len(opinions)},
            "adoption": {"state": "withdrawn" if last.get("action") == "withdraw" else ("adopted" if adopted else "not_adopted"),
                "decision": lifecycle[-1]["id"] if lifecycle else None, "reason": last.get("reason")},
            "provenance": {"state": "needs_review" if provenance_reasons else "current", "reasons": provenance_reasons},
            "knowledge_type": item["data"].get("classification", item["data"]["type"])}

    @staticmethod
    def _basis_issues(tx, item, visiting=None, skip_task_inputs=False):
        """统一登记材料作为正向依据的资格；不禁止探索和失败归档。"""
        state = Project._status(tx, item["id"], visiting, skip_task_inputs=skip_task_inputs)
        reasons = []
        if state["status"] in {"needs_review", "withdrawn"}:
            reasons.append("依据目前需要重新核查或已撤回")
        if state.get("quality", {}).get("verdict") != "pass":
            reasons.append("依据准确版本尚无当前有效的通过核查")
        if state.get("quality", {}).get("version_binding") != "explicit":
            reasons.append("依据缺少明确实际核查版本的记录")
        if item["data"]["type"] == "claim" and state["status"] != "adopted":
            reasons.append("作为依据的主张尚未采纳")
        return reasons

    def status(self, item_id):
        with self.store.connection() as connection:
            return self._status(Transaction(self.store, connection, {}), item_id)

    def adopt(self, item_id, review_id, receipt_id, request_id):
        def apply(tx):
            item = typed(tx, item_id, "item")
            task = self._scope_task(tx, item["data"]["task"])
            self._fresh(tx, receipt_id, task["id"])
            ref = recall.reference(item)
            if task["data"]["status"] != "delivered" or ref not in task["data"]["submitted"]:
                raise FrameworkError("只能采纳已交付的具体版本")
            review = typed(tx, review_id, "review")
            if review["data"]["target"] != ref or review["data"]["verdict"] != "pass" or not explicit_review(review):
                raise FrameworkError("审核未明确通过当前产物版本")
            opinions = [obj for obj in tx.list("review") if obj["data"]["target"] == ref]
            if opinions and (opinions[-1]["data"]["verdict"] != "pass" or not explicit_review(opinions[-1])):
                raise FrameworkError("不能绕过当前版本更新后的未通过意见，继续使用旧通过记录")
            from .coordination import check_leases
            check_leases(tx, task["id"], task["data"].get("leases", []), task["data"].get("submitted_by"))
            if any(case["data"]["target"]["id"] == item_id and case["data"]["status"] in {"open", "unresolved"} for case in tx.list("case")):
                raise FrameworkError("必须先解决已确认的争议")
            if recall.source_state(self.store, item) is False:
                raise StaleInput("产物文件变化")
            if self._status(tx, item_id).get("provenance", {}).get("state") != "current":
                raise StaleInput("任务输入、来源或既有关系需要重新评估")
            for ref in item["data"].get("supports", []):
                support = typed(tx, ref["id"], "item")
                if recall.reference(support) != ref or recall.source_state(self.store, support) is False:
                    raise StaleInput("依据已变化")
                if self._basis_issues(tx, support):
                    raise FrameworkError("依赖结论尚未采纳或需要重新评估")
            return tx.create("decision", dict(target=recall.reference(item), action="adopt", review=review_id,
                receipt=receipt_id, authority=tx.principal["actor"], reason="按具体版本及适用范围采纳"))
        return self.store.mutate("coordination.adopt", dict(item=item_id, review=review_id, receipt=receipt_id), request_id, "decide", apply, corpus=True)

    def challenge(self, item_id, reason, evidence, owner_role, budget, exit_condition, request_id):
        for value, label in ((reason, "冲突理由"), (owner_role, "处理负责人"), (exit_condition, "退出条件")):
            text_required(value, label)
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            raise FrameworkError("复核预算必须为正整数")
        if not isinstance(evidence, list) or not all(isinstance(identifier, str) for identifier in evidence):
            raise FrameworkError("争议材料必须为记录身份数组")
        def apply(tx):
            from .case_lifecycle import evidence_keys, objection_fingerprint
            item = typed(tx, item_id, "item")
            self._scope_task(tx, item["data"]["task"])
            proof = [typed(tx, identifier, "item") for identifier in evidence]
            keys = evidence_keys(proof)
            fingerprint = objection_fingerprint(recall.reference(item), reason, keys)
            for existing in tx.list("case"):
                data = existing["data"]
                old_keys = data.get("evidence_keys")
                if old_keys is None:
                    old_keys = evidence_keys([tx.get(ref["id"], ref["rev"]) for ref in data.get("evidence", [])])
                old_fingerprint = data.get("objection_fingerprint") or objection_fingerprint(data["target"], data["reason"], old_keys)
                dynamic = objection_fingerprint(data["target"], data["reason"],
                    evidence_keys([tx.get(ref["id"], ref["rev"]) for ref in data.get("evidence", [])]))
                if fingerprint in {old_fingerprint, dynamic, *data.get("objection_fingerprints", [])}:
                    raise ConflictError("相同目标版本、理由和材料已有争议，请追溯原案件: " + existing["id"])
            if any(obj["data"]["target"]["id"] == item_id and obj["data"]["status"] in {"pending", "open", "unresolved"} for obj in tx.list("case")):
                raise ConflictError("同一结论已有待处理争议；补充现有案件")
            if tx.connection.execute("SELECT 1 FROM principals WHERE actor=?", (owner_role,)).fetchone() is None:
                raise FrameworkError("处理负责人必须已登记")
            return tx.create("case", dict(target=recall.reference(item), reason=reason,
                evidence=[recall.reference(obj) for obj in proof], evidence_keys=keys, objection_fingerprint=fingerprint,
                objection_fingerprints=[fingerprint],
                owner=owner_role, budget=budget, used=0, exit_condition=exit_condition,
                status="pending", proposer=tx.principal["actor"], case_schema=2, round=1, round_used=0, total_budget=budget))
        return self.store.mutate("case.propose", dict(item=item_id, reason=reason, evidence=evidence, owner=owner_role, budget=budget, exit=exit_condition), request_id, "execute", apply, corpus=True)

    def assess_case(self, case_id, substantial, reason, request_id):
        text_required(reason, "复核理由")
        if not isinstance(substantial, bool):
            raise FrameworkError("是否构成实质冲突必须为布尔值")
        def apply(tx):
            case = typed(tx, case_id, "case")
            self._scope_task(tx, tx.get(case["data"]["target"]["id"])["data"]["task"])
            if case["data"]["status"] != "pending":
                raise ConflictError("争议已经过初步核查")
            if tx.principal["actor"] == case["data"]["proposer"]:
                raise PermissionDenied("争议提出者不能独立核查自己的异议")
            return tx.replace(case_id, case["rev"], {**case["data"], "status": "open" if substantial else "dismissed",
                "assessment": reason, "assessor": tx.principal["actor"]})
        return self.store.mutate("case.assess", dict(case=case_id, substantial=substantial, reason=reason), request_id, "review", apply, corpus=True)

    def propose_case_continuation(self, case_id, evidence, reason, expected_revision, request_id):
        from .case_lifecycle import propose
        return propose(self.store, case_id, evidence, reason, expected_revision, request_id)

    def assess_case_continuation(self, continuation_id, substantial, reason, request_id):
        from .case_lifecycle import assess
        return assess(self.store, continuation_id, substantial, reason, request_id)

    def authorize_case_continuation(self, continuation_id, budget, owner_role, exit_condition, reason, request_id):
        from .case_lifecycle import authorize
        return authorize(self.store, continuation_id, budget, owner_role, exit_condition, reason, request_id)

    def pause_case(self, case_id, reason, request_id):
        text_required(reason, "暂停本轮并保留未知的理由")
        def apply(tx):
            case = typed(tx, case_id, "case")
            self._scope_task(tx, tx.get(case["data"]["target"]["id"])["data"]["task"])
            if case["data"]["status"] != "open":
                raise ConflictError("只能暂停正在进行的争议轮次")
            updated = tx.replace(case_id, case["rev"], {**case["data"], "status": "unresolved",
                "outcome": "unknown", "resolution": reason, "management_pause": True})
            decision = tx.create("decision", dict(target=case["data"]["target"], action="pause", review=None,
                case=case_id, reason=reason, authority=tx.principal["actor"]))
            return {"case": updated, "decision": decision}
        return self.store.mutate("case.pause", dict(case=case_id, reason=reason), request_id, "decide", apply, corpus=True)

    def resolve_case(self, case_id, review_id, outcome, reason, request_id):
        if outcome not in {"uphold", "withdraw", "unknown"}:
            raise FrameworkError("争议结案方式不合法")
        text_required(reason, "结案依据")
        def apply(tx):
            case = typed(tx, case_id, "case")
            review = typed(tx, review_id, "review")
            target = typed(tx, case["data"]["target"]["id"], "item")
            self._scope_task(tx, target["data"]["task"])
            if case["data"]["status"] != "open":
                raise ConflictError("争议未打开")
            if review["data"]["target"] != recall.reference(target):
                raise StaleInput("复核对象版本不符")
            if not explicit_review(review):
                raise FrameworkError("须用明确实际核查版本的新记录结案，旧意见仍保留供追溯")
            if review["data"].get("case") != case_id or not review["data"].get("attempt"):
                raise FrameworkError("结案须使用本次争议预留后完成的新复核")
            attempt = typed(tx, review["data"]["attempt"], "case_attempt")
            if (review["data"].get("case_round", 1) != case["data"].get("round", 1)
                    or attempt["data"].get("round", 1) != case["data"].get("round", 1)
                    or attempt["data"].get("case") != case_id or attempt["data"].get("review") != review_id
                    or case["data"]["target"] != recall.reference(target)):
                raise StaleInput("旧轮次或目标版本的意见不能结案当前轮次")
            if outcome == "uphold" and review["data"]["verdict"] != "pass":
                raise FrameworkError("保留结论必须有通过的复核")
            if review["id"] in [obj["data"].get("review") for obj in tx.list("decision")]:
                raise FrameworkError("须使用新的复核记录，不能重复计算旧审核")
            updated = tx.replace(case_id, case["rev"], {**case["data"], "status": "unresolved" if outcome == "unknown" else "resolved",
                "outcome": outcome, "resolution": reason, "review": review_id})
            decision = tx.create("decision", dict(target=recall.reference(target), action="withdraw" if outcome == "withdraw" else "recheck",
                reason=reason, review=review_id, case=case_id, authority=tx.principal["actor"]))
            return {"case": updated, "decision": decision}
        return self.store.mutate("case.resolve", dict(case=case_id, review=review_id, outcome=outcome, reason=reason), request_id, "decide", apply, corpus=True)

    def close_task(self, task_id, summary, request_id):
        text_required(summary, "阶段简述")
        if len(summary) > 500:
            raise FrameworkError("阶段简述限500字以内；详细信息放产物记录")
        def apply(tx):
            task = self._scope_task(tx, task_id)
            if task["data"]["status"] != "delivered":
                raise FrameworkError("必须先交付再结束阶段")
            from .coordination import check_leases
            check_leases(tx, task_id, task["data"].get("leases", []), task["data"].get("submitted_by"))
            for ref in task["data"]["submitted"]:
                if recall.reference(tx.get(ref["id"])) != ref:
                    raise StaleInput("交付版本变化，需要重新交付和核查")
                if not any(obj["data"]["target"] == ref for obj in tx.list("review")):
                    raise FrameworkError("每项交付都须有质量意见；未知和失败也可如实记录")
            stage = tx.create("stage", dict(summary=summary, task=recall.reference(task), items=task["data"]["submitted"],
                author=tx.principal["actor"]))
            closed = tx.replace(task_id, task["rev"], {**task["data"], "status": "closed", "stage": stage["id"]})
            return {"task": closed, "stage": stage}
        return self.store.mutate("task.close", dict(task=task_id, summary=summary), request_id, "decide", apply)

    def annotate_stage(self, stage_id, summary, request_id):
        text_required(summary, "补充说明")
        if len(summary) > 500:
            raise FrameworkError("阶段补充限500字")
        def apply(tx):
            stage = typed(tx, stage_id, "stage")
            self._scope_task(tx, stage["data"]["task"]["id"])
            return tx.create("stage", {**stage["data"], "summary": summary, "corrects": stage_id, "author": tx.principal["actor"]})
        return self.store.mutate("stage.annotate", dict(stage=stage_id, summary=summary), request_id, "decide", apply)

    def stop_task(self, task_id, summary, request_id):
        text_required(summary, "停止原因及未决事项")
        if len(summary) > 500:
            raise FrameworkError("阶段简述限500字")
        def apply(tx):
            task = self._scope_task(tx, task_id)
            if task["data"]["status"] in {"closed", "stopped"}:
                raise ConflictError("任务已结束")
            stage = tx.create("stage", dict(summary=summary, task=recall.reference(task),
                items=[recall.reference(obj) for obj in tx.list("item") if obj["data"]["task"] == task_id],
                author=tx.principal["actor"], outcome="stopped_without_acceptance"))
            stopped = tx.replace(task_id, task["rev"], {**task["data"], "status": "stopped", "stage": stage["id"]})
            return {"task": stopped, "stage": stage, "note": "停止不证明外部作业已终止；资源和输出占用须另行核实释放"}
        return self.store.mutate("task.stop", dict(task=task_id, summary=summary), request_id, "decide", apply)
