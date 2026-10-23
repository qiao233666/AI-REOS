"""版本固定的数据式工具目录；本地脚本运行保留输出且不自动重试。"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from .errors import ConflictError, FrameworkError, StaleInput
from .storage import normalize_scope
from .project import Project, typed, text_required


class ToolRegistry:
    def __init__(self, store):
        self.store = store

    def register(self, manifest, request_id):
        required = {"name", "version", "api", "entry", "purpose", "aliases", "inputs", "outputs", "owner", "limitations", "verification", "dependencies"}
        if not isinstance(manifest, dict) or set(manifest) != required or manifest.get("api") != 1:
            raise FrameworkError("工具清单字段不完整或API版本不兼容")
        for key in required - {"api", "aliases", "dependencies"}:
            text_required(manifest[key], "工具字段 " + key)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", manifest["name"]):
            raise FrameworkError("工具名称不合法")
        if not isinstance(manifest["aliases"], list) or not all(isinstance(x, str) for x in manifest["aliases"]):
            raise FrameworkError("工具别名必须为字符串数组")
        if not isinstance(manifest["dependencies"], list):
            raise FrameworkError("工具依赖必须为工具身份数组")
        path = normalize_scope(manifest["entry"])
        resolved = (self.store.root / path).resolve()
        try:
            relative = resolved.relative_to(self.store.root)
        except ValueError as exc:
            raise FrameworkError("工具入口必须在项目内") from exc
        if any(part.startswith(".") for part in relative.parts) or resolved.suffix != ".py":
            raise FrameworkError("首版只登记非隐藏目录中的单文件Python入口")
        def apply(tx):
            tx.require_scope(path)
            if any(obj["data"]["manifest"]["name"] == manifest["name"] and obj["data"]["manifest"]["version"] == manifest["version"] for obj in tx.list("tool")):
                raise ConflictError("同名同版本工具已存在，内容变动须升版")
            for identifier in manifest["dependencies"]:
                dependency = typed(tx, identifier, "tool")
                if not dependency["data"]["enabled"]:
                    raise FrameworkError("依赖工具已停用")
            blob = self.store.snapshot(resolved.read_bytes())
            return tx.create("tool", dict(manifest={**manifest, "entry": path}, blob=blob, enabled=True))
        return self.store.mutate("tool.register", {"manifest": manifest}, request_id, "tools", apply, corpus=True)

    def set_enabled(self, tool_id, enabled, request_id):
        if not isinstance(enabled, bool):
            raise FrameworkError("启停状态必须为布尔值")
        def apply(tx):
            tool = typed(tx, tool_id, "tool")
            tx.require_scope(tool["data"]["manifest"]["entry"])
            return tx.replace(tool_id, tool["rev"], {**tool["data"], "enabled": enabled})
        return self.store.mutate("tool.enable", dict(tool=tool_id, enabled=enabled), request_id, "tools", apply, corpus=True)

    def _validate(self, tx, tool_id, visiting=None):
        visiting = set() if visiting is None else set(visiting)
        if tool_id in visiting:
            raise FrameworkError("工具依赖存在循环")
        visiting.add(tool_id)
        tool = typed(tx, tool_id, "tool")
        data = tool["data"]
        if not data["enabled"] or data["manifest"]["api"] != 1:
            raise FrameworkError("工具停用或API不兼容")
        try:
            source = (self.store.root / data["manifest"]["entry"]).resolve()
            source.relative_to(self.store.root)
            if hashlib.sha256(source.read_bytes()).hexdigest() != data["blob"]:
                raise StaleInput("工具入口内容变化，必须先登记新版本")
        except (OSError, ValueError) as exc:
            raise StaleInput("工具入口不可读取") from exc
        for identifier in data["manifest"]["dependencies"]:
            self._validate(tx, identifier, visiting)
        self.store.blob(data["blob"])
        return tool

    def run(self, tool_id, task_id, receipt_id, arguments, request_id, timeout=30):
        if not isinstance(arguments, list) or not all(isinstance(arg, str) and "\x00" not in arg for arg in arguments):
            raise FrameworkError("工具参数必须为字符串数组，不使用shell命令")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
            raise FrameworkError("本地工具超时范围为0至3600秒")
        from .recall import reference
        payload = dict(tool=tool_id, task=task_id, receipt=receipt_id, arguments=arguments, timeout=timeout)
        def reserve(tx):
            Project._task_open(tx, task_id)
            Project._fresh(tx, receipt_id, task_id)
            tool = self._validate(tx, tool_id)
            directory = ".aireos/runs/" + uuid.uuid4().hex
            return tx.create("run", dict(tool=reference(tool), task=task_id, arguments=arguments, timeout=timeout,
                directory=directory, status="unknown", actor=tx.principal["actor"],
                note="调用已登记；进程是否开始或结束须以结果记录为准，重复请求不会重跑"))
        run = self.store.mutate("tool.run.reserve", payload, request_id, "execute", reserve)
        if run["_replayed"]:
            return {**self.store.get(run["id"]), "_replayed": True}
        directory = self.store.root / run["data"]["directory"]
        # 先登记再启动；崩溃最多留下未知，不会因重试重新执行副作用。
        directory.mkdir(exist_ok=False)
        tool = self.store.get(tool_id, run["data"]["tool"]["rev"])
        entry = directory / "entry.py"
        entry.write_bytes(self.store.blob(tool["data"]["blob"]))
        (directory / "request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [sys.executable, "-I", str(entry), *arguments]
        status, code, note = "unknown", None, ""
        try:
            with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
                result = subprocess.run(command, cwd=directory, stdout=stdout, stderr=stderr, timeout=timeout, shell=False)
            code = result.returncode
            status = "completed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            note = "主进程超时已终止；子进程和外部副作用终态未知，须确认后另建请求"
        except OSError as exc:
            note = "进程启动或日志写入失败: " + str(exc)
        from .run_evidence import capture
        evidence = capture(self.store, run["data"]["directory"], status in {"completed", "failed"})
        def finish(tx):
            current = typed(tx, run["id"], "run")
            return tx.replace(run["id"], current["rev"], {**current["data"], "status": status, "exit_code": code, "note": note, "evidence": evidence})
        from .storage import digest
        result = self.store.mutate("tool.run.finish", dict(run=run["id"], status=status, exit_code=code, note=note, evidence=evidence),
            "tool-finish-" + digest([request_id, run["id"]]), "execute", finish)
        return result

    def inspect_evidence(self, run_id):
        from .run_evidence import RunEvidence
        return RunEvidence(self.store).inspect(run_id)

    def restore(self, run_id, destination, request_id):
        from .run_evidence import RunEvidence
        return RunEvidence(self.store).restore(run_id, destination, request_id)
