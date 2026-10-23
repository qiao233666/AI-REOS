"""输出占用与接管代号：并行执行者提交时必须仍持有当前代号。"""
from .errors import ConflictError, FrameworkError, PermissionDenied, StaleInput
from .storage import normalize_scope, within


def validate_lease_path(tx, data):
    from .inputs import canonical_path
    actual = canonical_path(tx.store.root, data.get("declared_path", data["path"]))
    if not (within(actual, data["path"]) and within(data["path"], actual)):
        raise StaleInput("输出占用的实际目录已变化；核实旧执行终态后释放旧占用")


def check_leases(tx, task_id, proofs, holder=None):
    held = [obj for obj in tx.list("lease") if obj["data"]["task"] == task_id]
    for obj in held:
        data = obj["data"]
        validate_lease_path(tx, data)
        if not data["active"] or data["holder"] != (holder or tx.principal["actor"]) or {"id": obj["id"], "epoch": data["epoch"]} not in proofs:
            raise PermissionDenied("提交者没有当前输出占用代号")


class Coordinator:
    def __init__(self, store):
        self.store = store

    def claim(self, task_id, path, request_id):
        path = normalize_scope(path)
        def apply(tx):
            from .project import Project
            from .inputs import canonical_path
            task = Project._task_open(tx, task_id)
            tx.require_scope(path)
            resolved = canonical_path(tx.store.root, path)
            tx.require_scope(resolved)
            if not any(within(resolved, output) for output in task["data"]["contract"]["outputs"]):
                raise PermissionDenied("占用路径超出任务范围")
            for obj in tx.list("lease"):
                data = obj["data"]
                if data["active"]:
                    validate_lease_path(tx, data)
                if data["active"] and (within(resolved, data["path"]) or within(data["path"], resolved)):
                    raise ConflictError("输出范围已有执行者占用")
            for obj in tx.list("lease"):
                if obj["data"]["task"] == task_id and obj["data"]["path"] == resolved:
                    return tx.replace(obj["id"], obj["rev"], {**obj["data"], "declared_path": path, "holder": tx.principal["actor"], "epoch": obj["data"]["epoch"] + 1, "active": True})
            return tx.create("lease", dict(task=task_id, path=resolved, declared_path=path, holder=tx.principal["actor"], epoch=1, active=True))
        return self.store.mutate("output.claim", dict(task=task_id, path=path), request_id, "execute", apply)

    def takeover(self, lease_id, holder, confirmed_idle, reason, request_id):
        if confirmed_idle is not True or not str(reason).strip():
            raise FrameworkError("接管需要已确认旧执行停止及理由，不能只靠超时猜测")
        def apply(tx):
            from .project import typed
            lease = typed(tx, lease_id, "lease")
            validate_lease_path(tx, lease["data"])
            tx.require_scope(lease["data"]["path"])
            row = tx.connection.execute("SELECT scopes,capabilities FROM principals WHERE actor=?", (holder,)).fetchone()
            if row is None:
                raise FrameworkError("新执行者未登记")
            import json
            scopes, caps = json.loads(row["scopes"]), json.loads(row["capabilities"])
            if not ({"*", "execute"} & set(caps)) or not any(within(lease["data"]["path"], scope) for scope in scopes):
                raise PermissionDenied("新执行者没有相应输出权限")
            return tx.replace(lease_id, lease["rev"], {**lease["data"], "holder": holder,
                "epoch": lease["data"]["epoch"] + 1, "active": True, "takeover_reason": reason})
        return self.store.mutate("output.takeover", dict(lease=lease_id, holder=holder, idle=confirmed_idle, reason=reason), request_id, "coordinate", apply)

    def release(self, lease_id, epoch, request_id):
        def apply(tx):
            from .project import typed
            lease = typed(tx, lease_id, "lease")
            if lease["data"]["holder"] != tx.principal["actor"] or lease["data"]["epoch"] != epoch or not lease["data"]["active"]:
                raise PermissionDenied("占用代号失效")
            return tx.replace(lease_id, lease["rev"], {**lease["data"], "active": False})
        return self.store.mutate("output.release", dict(lease=lease_id, epoch=epoch), request_id, "execute", apply)

    def register_resource(self, node_id, capacity, capabilities, available, notes, request_id):
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", node_id) or isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise FrameworkError("资源需稳定node_id和正整数容量单位")
        if not isinstance(available, bool) or not capabilities or not all(isinstance(tag, str) and tag.strip() for tag in capabilities):
            raise FrameworkError("资源能力和可用性必须明确")
        data = dict(node_id=node_id, capacity=capacity, capabilities=capabilities, available=available, notes=notes)
        def apply(tx):
            if any(obj["data"]["node_id"] == node_id for obj in tx.list("resource")):
                raise ConflictError("资源已登记")
            return tx.create("resource", data)
        return self.store.mutate("resource.register", data, request_id, "coordinate", apply, corpus=True)

    def reserve(self, resource_id, task_id, units, required, request_id):
        if isinstance(units, bool) or not isinstance(units, int) or units < 1:
            raise FrameworkError("占用单位必须为正整数")
        def apply(tx):
            from .project import typed, Project
            Project._task_open(tx, task_id)
            node = typed(tx, resource_id, "resource")
            if not node["data"]["available"] or not set(required) <= set(node["data"]["capabilities"]):
                raise FrameworkError("资源不可用或不具备所需能力")
            occupied = sum(obj["data"]["units"] for obj in tx.list("reservation") if obj["data"]["resource"] == resource_id and obj["data"]["status"] == "unknown")
            if occupied + units > node["data"]["capacity"]:
                raise ConflictError("资源容量已占用；未知作业不能自动释放")
            return tx.create("reservation", dict(resource=resource_id, task=task_id, units=units, required=required,
                holder=tx.principal["actor"], status="unknown", note="仅记录占用，不启动任何实际计算"))
        return self.store.mutate("resource.reserve", dict(resource=resource_id, task=task_id, units=units, required=required), request_id, "execute", apply)

    def finish_reservation(self, reservation_id, status, confirmed_terminal, note, request_id):
        if status not in {"completed", "failed", "cancelled"} or confirmed_terminal is not True or not str(note).strip():
            raise FrameworkError("须确认实际作业已经结束并给出依据，才可释放资源")
        def apply(tx):
            from .project import typed
            obj = typed(tx, reservation_id, "reservation")
            if obj["data"]["holder"] != tx.principal["actor"] or obj["data"]["status"] != "unknown":
                raise PermissionDenied("只能确认自己尚未结束的作业")
            return tx.replace(obj["id"], obj["rev"], {**obj["data"], "status": status, "note": note})
        return self.store.mutate("resource.finish", dict(reservation=reservation_id, status=status, confirmed=confirmed_terminal, note=note), request_id, "execute", apply)
