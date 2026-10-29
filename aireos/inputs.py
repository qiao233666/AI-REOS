"""任务声明输入的固定版本；刷新检索不能自动改变输入约定。"""
import hashlib
from pathlib import Path

from .errors import FrameworkError
from .storage import normalize_scope, digest

MAX_INPUT_BYTES = 32 * 1024 * 1024


def canonical_path(root, path):
    relative = normalize_scope(path)
    try:
        return (root / relative).resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise FrameworkError("路径解析后必须仍在项目内") from exc


def file_bytes(root, path):
    relative = canonical_path(root, path)
    if any(part.startswith(".") for part in Path(relative).parts):
        raise FrameworkError("治理私有目录不能作为任务输入")
    source = root / relative
    try:
        if not source.is_file() or source.stat().st_size > MAX_INPUT_BYTES:
            raise FrameworkError("输入必须为可读文件且不超过32MiB；大型数据请绑定其版本清单")
        return relative, source.read_bytes()
    except OSError as exc:
        raise FrameworkError("任务输入文件不可读取") from exc


def bind(tx, entries, save_snapshots=True):
    result = []
    for entry in entries:
        entry = {"type": "file", "path": entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict):
            raise FrameworkError("输入须为文件、对象版本或note说明")
        kind = entry.get("type")
        usage = entry.get("usage", "basis")
        if usage not in {"basis", "reference"}:
            raise FrameworkError("输入用途须为basis依据或reference待研究材料")
        if kind == "file":
            relative, content = file_bytes(tx.store.root, entry.get("path"))
            if save_snapshots:
                checksum = tx.store.snapshot(content)
            else:
                checksum = hashlib.sha256(content).hexdigest()
            if entry.get("sha256", checksum) != checksum:
                raise FrameworkError("声明的输入摘要与实际文件不符")
            result.append(dict(type="file", path=relative, declared_path=entry["path"], sha256=checksum, bytes=len(content), usage=usage))
        elif kind == "item":
            obj = tx.get(entry.get("id"), entry.get("rev"))
            if obj["kind"] != "item":
                raise FrameworkError("对象输入必须为交付项")
            result.append(dict(type="item", id=obj["id"], rev=obj["rev"], digest=digest(obj["data"]), usage=usage))
        elif kind == "note" and isinstance(entry.get("text"), str) and entry["text"].strip():
            result.append(dict(type="note", text=entry["text"]))
        else:
            raise FrameworkError("未知输入类型或缺少说明")
    return result


def objects(tx, entries, basis_only=False):
    """文件的字节身份也绑定已登记来源；用途不影响取回争议与更正。"""
    found = {}
    for entry in entries:
        if not isinstance(entry, dict) or (basis_only and entry.get("usage", "basis") != "basis"):
            continue
        if entry.get("type") == "item":
            obj = tx.get(entry["id"])
            found[obj["id"]] = obj
        elif entry.get("type") == "file":
            for obj in tx.list("item"):
                data = obj["data"]
                if data.get("type") != "artifact" or data.get("blob") != entry["sha256"]:
                    continue
                if canonical_path(tx.store.root, data["origin"]) == entry["path"]:
                    found[obj["id"]] = obj
    return list(found.values())


def validate(tx, entries, verify_snapshots=True):
    reasons = []
    for entry in entries:
        if not isinstance(entry, dict):
            reasons.append("旧任务输入没有绑定版本，需要新建明确约定，不能事后补推")
            continue
        try:
            if entry["type"] == "file":
                path, content = file_bytes(tx.store.root, entry["declared_path"])
                if path != entry["path"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    reasons.append("声明输入的实际内容或路径变化: " + entry["path"])
                if verify_snapshots:
                    tx.store.blob(entry["sha256"])
            elif entry["type"] == "item":
                obj = tx.get(entry["id"])
                if obj["rev"] != entry["rev"] or digest(obj["data"]) != entry["digest"]:
                    reasons.append("声明对象输入的版本变化: " + entry["id"])
            elif entry["type"] != "note":
                reasons.append("未知的输入绑定类型")
        except (FrameworkError, KeyError, TypeError):
            reasons.append("声明输入缺失、损坏或尚未正确绑定")
    return reasons
