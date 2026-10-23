"""同一读快照的当前视图与面向人的追加式阶段手账。"""
import json
import os
from pathlib import Path
import uuid

from .errors import FrameworkError
from .storage import Transaction, digest
from .project import Project, typed
from .recall import reference


def switch_pointer(home, generation):
    temporary = home / ("CURRENT." + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(generation + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, home / "CURRENT")


class Views:
    def __init__(self, store):
        self.store = store

    def publish(self, request_id, max_chars=6000):
        if not isinstance(max_chars, int) or not 500 <= max_chars <= 20000:
            raise FrameworkError("当前上下文字符上限必须为500至20000")
        def apply(tx):
            generation = uuid.uuid4().hex
            folder = self.store.home / "views" / generation
            folder.mkdir(exist_ok=False)
            objects = tx.list()
            tasks = [obj for obj in objects if obj["kind"] == "task" and obj["data"]["status"] not in {"closed", "stopped"}]
            stages = [obj for obj in objects if obj["kind"] == "stage"]
            cases = [obj for obj in objects if obj["kind"] == "case" and obj["data"]["status"] in {"pending", "open", "unresolved"}]
            lines = ["# 当前项目入口", "", "此页是有限长度的导航，不是全部历史，也不表示未显示的工作不存在。", "", "## 当前任务"]
            for obj in tasks:
                data = obj["data"]
                lines.append(f"- {data['contract']['title']}：{data['status']}；定位 {obj['id']}")
            lines += ["", "## 尚待处理的争议"]
            for obj in cases:
                lines.append(f"- {obj['data']['reason']}；定位 {obj['id']}")
            lines += ["", "## 最近阶段说明"]
            for obj in stages[-5:]:
                lines.append(f"- {obj['data']['summary']}；追溯 {obj['id']}" + (f"；补充/更正 {obj['data']['corrects']}" if obj['data'].get('corrects') else ""))
            footer = "\n\n继续检索：先用任务问题 prepare；阶段历史看 JOURNAL.md，再用 trace 恢复当时版本。未命中只能说明本次没有找到。\n"
            state = "\n".join(lines)
            if len(state) + len(footer) > max_chars:
                state = state[:max_chars - len(footer) - 20] + "\n（部分导航已省略）"
            state += footer
            journal = "# 阶段手账\n\n以下条目按阶段追加；后续变化通过关联记录查看，当时叙述保留。\n\n" + "\n\n".join(
                f"{obj['created']} — {obj['data']['summary']}\n\n追溯：{obj['id']}" + (f"；补充/更正：{obj['data']['corrects']}" if obj['data'].get('corrects') else "") for obj in stages)
            data = {"generation": generation, "project": self.store.project_id(tx.connection),
                "epoch": self.store.epoch(tx.connection), "events": tx.connection.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events").fetchone()[0],
                "objects": [reference(obj) for obj in objects], "state_chars": len(state),
                "request_id": request_id,
                "source": "权威数据库的同一事务快照；本目录禁止作为写入真源"}
            for filename, content in (("STATE.md", state), ("JOURNAL.md", journal), ("index.json", json.dumps(objects, ensure_ascii=False, indent=2))):
                (folder / filename).write_text(content, encoding="utf-8")
            data["files"] = {path.name: digest(path.read_text(encoding="utf-8")) for path in folder.iterdir()}
            (folder / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            publication = tx.create("publication", {"generation": generation})
            return {"generation": generation, "directory": folder.relative_to(self.store.root).as_posix(),
                "state_chars": len(state), "manifest_digest": digest(data), "publication": publication["id"]}
        result = self.store.mutate("views.publish", dict(max_chars=max_chars), request_id, "publish", apply)
        # 指针仅指向已提交的完整批次；指针失败可按同一请求恢复，重试旧请求不会回拨新批次。
        with self.store.connection(write=True) as connection:
            publications = self.store._list(connection, "publication")
            if publications and publications[-1]["id"] == result["publication"]:
                switch_pointer(self.store.home / "views", result["generation"])
        return result

    def status(self):
        try:
            generation = (self.store.home / "views/CURRENT").read_text(encoding="utf-8").strip()
            if len(generation) != 32 or any(char not in "0123456789abcdef" for char in generation):
                raise ValueError("当前指针格式异常")
            folder = self.store.home / "views" / generation
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            intact = all(digest((folder / filename).read_text(encoding="utf-8")) == checksum for filename, checksum in manifest["files"].items())
            with self.store.connection() as connection:
                seq = connection.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
                row = connection.execute("SELECT result FROM requests WHERE request_id=?", (manifest["request_id"],)).fetchone()
                publication = json.loads(row[0]) if row else {}
                committed = publication.get("generation") == generation and publication.get("manifest_digest") == digest(manifest)
                current = intact and committed and seq == manifest["events"] and manifest["epoch"] == self.store.epoch(connection)
            return {"current": current, "intact": intact, "generation": generation,
                "directory": folder.relative_to(self.store.root).as_posix(), "note": "滞后时重新发布；阅读历史不要求强行发布"}
        except (OSError, ValueError, KeyError) as exc:
            return {"current": False, "intact": False, "note": "暂无完整可核对视图: " + str(exc)}

    def trace(self, stage_id):
        with self.store.connection() as connection:
            tx = Transaction(self.store, connection, {})
            stage = typed(tx, stage_id, "stage")
            historical, snapshots, current = {}, set(), {}
            pending = list(stage["data"]["items"])
            while pending:
                ref = pending.pop()
                key = ref["id"] + ":" + str(ref["rev"])
                if key in historical:
                    continue
                item = tx.get(ref["id"], ref["rev"])
                historical[key] = item
                current[item["id"]] = Project._status(tx, item["id"])
                if item["data"].get("blob"):
                    self.store.blob(item["data"]["blob"])
                    snapshots.add(item["data"]["blob"])
                pending.extend(item["data"].get("supports", []))
            task_ref = stage["data"]["task"]
            cutoff = stage["sequence"]
            then = [self.store._row(row) for row in connection.execute("""
                SELECT v.*,v.rowid AS ordinal FROM versions v JOIN
                (SELECT id,MAX(rowid) AS latest FROM versions WHERE rowid<=? GROUP BY id) s
                ON v.id=s.id AND v.rowid=s.latest ORDER BY v.rowid
            """, (cutoff,))]
            identities = {obj["id"] for obj in historical.values()}
            reviews = [obj for obj in then if obj["kind"] == "review" and obj["data"]["target"]["id"] in identities]
            decisions = [obj for obj in then if obj["kind"] == "decision" and obj["data"]["target"]["id"] in identities]
            runs = [obj for obj in then if obj["kind"] in {"run", "external_run", "reservation"} and obj["data"].get("task") == task_ref["id"]]
            return {"stage": stage, "task_then": tx.get(task_ref["id"], task_ref["rev"]),
                "historical": historical, "snapshots": sorted(snapshots), "current": current,
                "reviews_then": reviews, "decisions_then": decisions, "runs_then": runs,
                "snapshot_directory": ".aireos/objects", "note": "historical是当时版本；current只表示现在的复用状态"}
