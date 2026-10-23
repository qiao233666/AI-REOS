"""确定性组合入口：执行者只提交一次说明，程序完成机械登记。"""
import re
import json

from .errors import FrameworkError
from .storage import digest
from .project import typed
from .views import Views


class Workflow:
    def __init__(self, project):
        self.project, self.store = project, project.store

    @staticmethod
    def _request(request_id, step):
        return "flow-" + digest([request_id, step])

    def _begin(self, kind, task_id, spec, request_id, capability, publish=False):
        def apply(tx):
            self.project._scope_task(tx, task_id)
            if publish:
                self.store._principal(tx.connection, "publish")
            return tx.create("workflow", dict(type=kind, task=task_id, status="running", steps={}, spec_digest=digest(spec), actor=tx.principal["actor"]))
        return self.store.mutate("workflow." + kind, dict(task=task_id, spec=spec), request_id, capability, apply)

    def _step(self, flow_id, name, result, request_id, capability):
        minimal = {key: result[key] for key in ("id", "rev", "generation", "directory") if key in result}
        def apply(tx):
            flow = typed(tx, flow_id, "workflow")
            return tx.replace(flow_id, flow["rev"], {**flow["data"], "steps": {**flow["data"]["steps"], name: minimal}})
        self.store.mutate("workflow.step", dict(flow=flow_id, step=name, result=minimal), self._request(request_id, "log:" + name), capability, apply)

    def _finish(self, flow, result, request_id, capability):
        def apply(tx):
            current = typed(tx, flow["id"], "workflow")
            return tx.replace(current["id"], current["rev"], {**current["data"], "status": "completed", "result": result})
        self.store.mutate("workflow.finish", dict(flow=flow["id"], result=result), self._request(request_id, "finish"), capability, apply)
        return {**self.store.get(flow["id"]), "_replayed": flow["_replayed"]}

    @staticmethod
    def _completed(store, flow):
        current = store.get(flow["id"])
        return {**current, "_replayed": True} if current["data"]["status"] == "completed" else None

    def _recorded(self, flow, request_id, step):
        with self.store.connection() as connection:
            row = connection.execute("SELECT r.result,e.actor FROM requests r JOIN events e ON e.seq=r.event_seq WHERE r.request_id=?", (self._request(request_id, step),)).fetchone()
            if not row:
                return None
            if row["actor"] != flow["data"]["actor"]:
                raise FrameworkError("组合请求的执行者不一致")
            return {**json.loads(row["result"]), "_replayed": True}

    def deliver(self, task_id, artifacts, claims, request_id, query=None, leases=None):
        if not isinstance(artifacts, list) or not isinstance(claims, list) or not 1 <= len(artifacts) + len(claims) <= 64:
            raise FrameworkError("一次交付须有1至64项，复杂阶段应拆任务")
        keys = [entry.get("key") for entry in artifacts + claims if isinstance(entry, dict)]
        if len(keys) != len(artifacts) + len(claims) or len(set(keys)) != len(keys) or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,40}", key) for key in keys):
            raise FrameworkError("交付项需要唯一的简短key，以便引用而不让模型手工维护编号")
        spec = dict(artifacts=artifacts, claims=claims, query=query, leases=leases or [])
        flow = self._begin("deliver", task_id, spec, request_id, "execute")
        completed = self._completed(self.store, flow)
        if completed:
            return completed
        task = self.store.get(task_id)
        query = query or task["data"]["contract"]["title"]
        if task["data"]["status"] == "planned":
            packet = self.project.prepare(task_id, query, self._request(request_id, "start-context"))
            started = self.project.start_task(task_id, packet["id"], self._request(request_id, "start"))
            self._step(flow["id"], "start", started, request_id, "execute")
        aliases, files, items = {}, [], []
        for entry in artifacts:
            obj = self.project.add_artifact(entry["path"], entry["title"], task_id, self._request(request_id, "artifact:" + entry["key"]))
            aliases[entry["key"]] = obj["id"]
            files.append({"key": entry["key"], "id": obj["id"], "rev": obj["rev"]})
            self._step(flow["id"], "artifact:" + entry["key"], obj, request_id, "execute")
        for entry in claims:
            supports = [aliases.get(value, value) for value in entry.get("supports", [])]
            obj = self.project.add_item(entry["title"], entry["text"], entry["classification"], entry["scope"], supports,
                task_id, self._request(request_id, "claim:" + entry["key"]), aliases=entry.get("aliases"), supersedes=entry.get("supersedes"))
            aliases[entry["key"]] = obj["id"]
            items.append({"key": entry["key"], "id": obj["id"], "rev": obj["rev"]})
            self._step(flow["id"], "claim:" + entry["key"], obj, request_id, "execute")
        submitted = items + files
        # 恢复时可以重新检索；同一交付的正式版本仍由子请求去重固定。
        task = self._recorded(flow, request_id, "submit")
        if task is None:
            packet_key = self._request(request_id, "submit-context:" + str(self.store.epoch()))
            packet = self.project.prepare(task_id, query, packet_key)
            task = self.project.submit(task_id, [obj["id"] for obj in submitted], packet["id"], self._request(request_id, "submit"), leases=leases)
        self._step(flow["id"], "submit", task, request_id, "execute")
        return self._finish(flow, dict(task=task_id, items=submitted, artifacts=files), request_id, "execute")

    def record_reviews(self, task_id, reviews, request_id):
        if not isinstance(reviews, list) or not 1 <= len(reviews) <= 64:
            raise FrameworkError("一次录入1至64项实际核查意见")
        for entry in reviews:
            if (not isinstance(entry, dict) or isinstance(entry.get("revision"), bool)
                    or not isinstance(entry.get("revision"), int) or entry["revision"] < 1):
                raise FrameworkError("每项核查须指明实际检查的正整数版本，不能省略或为空")
        flow = self._begin("reviews", task_id, {"reviews": reviews}, request_id, "review")
        completed = self._completed(self.store, flow)
        if completed:
            return completed
        results = []
        for index, entry in enumerate(reviews):
            item = self.store.get(entry["item_id"])
            if item["data"]["task"] != task_id or "revision" not in entry:
                raise FrameworkError("批量意见必须指明本任务产物及实际核查的准确版本")
            obj = self.project.review(entry["item_id"], entry["verdict"], entry["method"], entry["checks"],
                self._request(request_id, "review:" + str(index)), expected_revision=entry["revision"], attempt_id=entry.get("attempt_id"))
            results.append(dict(id=obj["id"], rev=obj["rev"], item_id=entry["item_id"]))
            self._step(flow["id"], "review:" + str(index), obj, request_id, "review")
        return self._finish(flow, {"reviews": results}, request_id, "review")

    def finalize(self, task_id, adoptions, summary, request_id, publish=True):
        if not isinstance(adoptions, list) or len(adoptions) > 64 or not isinstance(publish, bool):
            raise FrameworkError("采纳清单须明确，发表选项须为布尔值")
        flow = self._begin("finalize", task_id, dict(adoptions=adoptions, summary=summary, publish=publish), request_id, "decide", publish=publish)
        completed = self._completed(self.store, flow)
        if completed:
            if publish:
                Views(self.store).publish(completed["data"]["result"]["publication_request"])
            return completed
        for index, entry in enumerate(adoptions):
            item = self.store.get(entry["item_id"])
            if item["data"]["task"] != task_id:
                raise FrameworkError("采纳项不属于本任务")
            step = "adopt:" + str(index)
            obj = self._recorded(flow, request_id, step)
            if obj is None:
                task = self.store.get(task_id)
                packet = self.project.prepare(task_id, task["data"]["contract"]["title"], self._request(request_id, step + ":context:" + str(self.store.epoch())))
                obj = self.project.adopt(item["id"], entry["review_id"], packet["id"], self._request(request_id, step))
            self._step(flow["id"], step, obj, request_id, "decide")
        closed = self.project.close_task(task_id, summary, self._request(request_id, "close"))
        self._step(flow["id"], "close", closed["stage"], request_id, "decide")
        result = dict(task=task_id, stage=closed["stage"]["id"])
        # 先记录完成状态，再发布，避免刚发表的视图立即被本流程自己的收尾事件标为滞后。
        result["publication_request"] = self._request(request_id, "publish") if publish else None
        finished = self._finish(flow, result, request_id, "decide")
        if publish:
            Views(self.store).publish(result["publication_request"])
        return finished
