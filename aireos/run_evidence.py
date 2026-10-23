"""有界运行快照、校验和恢复；不覆盖原目录，不执行恢复的脚本。"""
import hashlib
import os
from pathlib import Path
import stat
import time
import uuid

from .errors import FrameworkError
from .storage import Transaction, canonical, digest, normalize_scope, within

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 128
MAX_ENTRIES = 1024
MAX_CAPTURE_SECONDS = 5.0
READ_CHUNK_BYTES = 64 * 1024


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("运行证据采集时间预算已用尽，未检查内容须另行归档")


def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool((getattr(info, "st_file_attributes", 0) or 0) & 0x400)


def inventory(directory, deadline=None, priority_paths=(), ignored=()):
    deadline = time.monotonic() + MAX_CAPTURE_SECONDS if deadline is None else deadline
    root_info = directory.lstat()
    if linked(root_info):
        raise OSError("运行根目录被替换为目录链接")
    if not stat.S_ISDIR(root_info.st_mode):
        raise OSError("运行目录缺失或不是目录")
    files, directories, skipped, pending, seen, entries = [], [], [], [directory], set(), 0
    def visit(path):
        nonlocal entries
        name = path.relative_to(directory).as_posix()
        if name in seen or name in ignored:
            return True
        if entries >= MAX_ENTRIES or len(files) + len(skipped) >= MAX_FILES:
            skipped.append(dict(path="*", reason="目录条目或文件数量超过快照预算"))
            return False
        seen.add(name)
        entries += 1
        try:
            info = path.lstat()
            if path.name.startswith(".") or linked(info):
                skipped.append(dict(path=name, reason="不跟随链接或隐藏内容"))
            elif stat.S_ISDIR(info.st_mode):
                directories.append(path)
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                files.append(path)
            else:
                skipped.append(dict(path=name, reason="特殊文件不读取，非普通文件"))
        except OSError as exc:
            skipped.append(dict(path=name, reason=str(exc)))
        return True
    # 关键日志仍消耗同一有限额度；先登记，避免普通输出挤掉报告和事件。
    for relative in priority_paths:
        check_deadline(deadline)
        if not visit(directory / normalize_scope(relative)):
            return files, directories, skipped
    while pending:
        folder = pending.pop()
        try:
            check_deadline(deadline)
            info = folder.lstat()
            if linked(info) or not stat.S_ISDIR(info.st_mode):
                raise OSError("目录在采集期间变为链接或非目录")
            # 逐项遍历并同时限制目录，避免先列出无界目录或扫描任意多空目录。
            with os.scandir(folder) as children:
                for child in children:
                    check_deadline(deadline)
                    path = folder / child.name
                    if not visit(path):
                        return files, directories, skipped
        except TimeoutError as exc:
            return files, directories, skipped + [dict(path="*", reason=str(exc))]
        except OSError as exc:
            skipped.append(dict(path=folder.relative_to(directory).as_posix(), reason=str(exc)))
    return files, directories, skipped


def file_signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def recheck_inventory(directory, paths, directories, signatures, deadline, ignored=()):
    """在同一预算内复查覆盖及已读文件；这不提供原子文件系统快照。"""
    try:
        final, final_directories, skipped = inventory(directory, deadline, ignored=ignored)
        if set(final) != set(paths) or set(final_directories) != set(directories):
            skipped.append(dict(path="*", reason="采集期间普通文件或目录集合变化，快照覆盖不完整"))
        for path, expected in signatures.items():
            check_deadline(deadline)
            if file_signature(path.lstat()) != expected:
                skipped.append(dict(path=path.relative_to(directory).as_posix(), reason="读取后文件仍有变化，快照不完整"))
        return skipped
    except OSError as exc:
        return [dict(path="*", reason="末次覆盖检查失败: " + str(exc))]


def read_regular(path, byte_limit, deadline):
    """只从经文件描述符再次确认的普通文件作有界读取。"""
    check_deadline(deadline)
    before = path.lstat()
    if linked(before) or not stat.S_ISREG(before.st_mode):
        raise OSError("文件已变为链接或特殊文件，不读取")
    if before.st_size > byte_limit:
        raise OSError("超过字节快照预算，需另行归档")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        # Windows部分Python版本的lstat/fstat对ctime定义不同；只在同类接口前后比较ctime。
        if not stat.S_ISREG(opened.st_mode) or file_signature(before)[:4] != file_signature(opened)[:4]:
            raise OSError("打开时文件类型或身份变化，不读取")
        content = bytearray()
        while True:
            check_deadline(deadline)
            block = os.read(descriptor, min(READ_CHUNK_BYTES, byte_limit - len(content) + 1))
            if not block:
                break
            content.extend(block)
            if len(content) > byte_limit:
                raise OSError("读取期间超过字节快照预算，不能宣称稳定快照")
        if file_signature(opened) != file_signature(os.fstat(descriptor)):
            raise OSError("读取期间仍在变化，不能宣称稳定快照")
    finally:
        os.close(descriptor)
    if file_signature(before) != file_signature(path.lstat()):
        raise OSError("读取期间文件路径或内容变化，不能宣称稳定快照")
    check_deadline(deadline)
    return bytes(content)


def capture(store, relative, terminal, priority_paths=()):
    directory = store.root / relative
    deadline = time.monotonic() + MAX_CAPTURE_SECONDS
    try:
        paths, directories, skipped = inventory(directory, deadline, priority_paths=priority_paths)
    except OSError as exc:
        paths, directories, skipped = [], [], [dict(path="*", reason=str(exc))]
    files, total, signatures = [], 0, {}
    for path in paths:
        name = path.relative_to(directory).as_posix()
        try:
            signatures[path] = file_signature(path.lstat())
            content = read_regular(path, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total), deadline)
            files.append(dict(path=name, blob=store.snapshot(content), bytes=len(content)))
            total += len(content)
            check_deadline(deadline)
        except (OSError, FrameworkError) as exc:
            skipped.append(dict(path=name, reason=str(exc)))
    skipped.extend(recheck_inventory(directory, paths, directories, signatures, deadline))
    manifest = dict(manifest_version=2, coverage="ordinary_tree", complete=terminal and not skipped,
        terminal_confirmed=terminal, files=files,
        directories=sorted(path.relative_to(directory).as_posix() for path in directories), skipped=skipped,
        total_bytes=total, limits=dict(file_bytes=MAX_FILE_BYTES, total_bytes=MAX_TOTAL_BYTES, files=MAX_FILES,
            entries=MAX_ENTRIES, seconds=MAX_CAPTURE_SECONDS))
    manifest["manifest_blob"] = store.snapshot(canonical(manifest).encode("utf-8"))
    return manifest


class RunEvidence:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def record(tx, run_id):
        run = tx.get(run_id)
        if run["kind"] not in {"run", "external_run"}:
            raise FrameworkError("需要工具或外部审核的运行身份")
        return run

    def inspect(self, run_id):
        deadline = time.monotonic() + MAX_CAPTURE_SECONDS
        with self.store.connection() as connection:
            run = self.record(Transaction(self.store, connection, {}), run_id)
        evidence = run["data"].get("evidence")
        if not evidence:
            return dict(run=run_id, snapshots_valid=False, current_matches=False, complete=False, changes=["旧运行没有字节快照清单"])
        changes, valid = [], True
        frozen = {key: value for key, value in evidence.items() if key != "manifest_blob"}
        try:
            if self.store.blob(evidence["manifest_blob"]) != canonical(frozen).encode("utf-8"):
                raise FrameworkError("运行清单与其快照不一致")
        except (FrameworkError, KeyError) as exc:
            valid = False
            changes.append(str(exc))
        directory = self.store.root / run["data"]["directory"]
        try:
            current, current_directories, skipped = inventory(directory, deadline)
        except OSError as exc:
            current, current_directories, skipped = [], [], [dict(path="*", reason=str(exc))]
        tree = evidence.get("manifest_version", 1) == 2
        if tree and {path.relative_to(directory).as_posix() for path in current_directories} != set(evidence.get("directories", [])):
            changes.append("普通目录集合变化")
        available = {path.relative_to(directory).as_posix() for path in current}
        signatures, checked_bytes = {}, 0
        for entry in evidence["files"]:
            try:
                self.store.blob(entry["blob"])
            except FrameworkError:
                valid = False
                changes.append("历史快照缺失或损坏: " + entry["path"])
            try:
                path = directory / entry["path"]
                if entry["path"] not in available:
                    changes.append("当前文件缺失或不再是普通文件: " + entry["path"])
                else:
                    signatures[path] = file_signature(path.lstat())
                    content = read_regular(path, min(MAX_FILE_BYTES, entry["bytes"], MAX_TOTAL_BYTES - checked_bytes), deadline)
                    checked_bytes += len(content)
                    if hashlib.sha256(content).hexdigest() != entry["blob"]:
                        changes.append("当前文件变化: " + entry["path"])
            except OSError as exc:
                changes.append("当前文件未通过有界检查: " + entry["path"] + ": " + str(exc))
        expected = {entry["path"] for entry in evidence["files"]}
        changes.extend("新增或未快照文件: " + path.relative_to(directory).as_posix() for path in current if path.relative_to(directory).as_posix() not in expected)
        final_skipped = recheck_inventory(directory, current, current_directories, signatures, deadline)
        changes.extend("末次目录检查: " + entry["path"] + ": " + entry["reason"] for entry in final_skipped)
        if skipped:
            changes.append("当前目录含未检查链接、隐藏或特殊文件，或超出采集预算")
        return dict(run=run_id, snapshots_valid=valid, current_matches=valid and evidence["complete"] and not changes,
            complete=evidence["complete"], structure_complete=tree and evidence["complete"],
            coverage="ordinary_tree" if tree else "files_only", changes=changes,
            files=evidence["files"], directories=evidence.get("directories", []) if tree else None, skipped=evidence["skipped"])

    def restore(self, run_id, destination, request_id):
        destination = normalize_scope(destination)
        def reserve(tx):
            from .inputs import canonical_path
            from .project import Project
            run = self.record(tx, run_id)
            task = Project._scope_task(tx, run["data"]["task"])
            relative = canonical_path(tx.store.root, destination)
            tx.require_scope(destination)
            tx.require_scope(relative)
            if any(part.startswith(".") for part in Path(relative).parts) or not any(within(relative, output) for output in task["data"]["contract"]["outputs"]):
                raise FrameworkError("恢复位置须为任务输出范围中的非隐藏新目录")
            evidence = run["data"].get("evidence", {})
            if not evidence.get("complete"):
                raise FrameworkError("运行快照不完整；不能宣称完整恢复，请按清单另行归档缺失材料")
            frozen = {key: value for key, value in evidence.items() if key != "manifest_blob"}
            if self.store.blob(evidence["manifest_blob"]) != canonical(frozen).encode("utf-8"):
                raise FrameworkError("运行证据清单损坏")
            target = self.store.root / relative
            if target.exists():
                raise FrameworkError("恢复目标已存在，拒绝覆盖；请使用新目录")
            tree = evidence.get("manifest_version", 1) == 2
            return tx.create("recovery", dict(run=run_id, destination=relative, declared_destination=destination,
                manifest_blob=evidence["manifest_blob"], files=len(evidence["files"]), status="reserved",
                coverage="ordinary_tree" if tree else "files_only", structure_complete=tree))
        record = self.store.mutate("run.restore.reserve", dict(run=run_id, destination=destination), request_id, "execute", reserve)
        current = self.store.get(record["id"])
        if current["data"]["status"] == "completed":
            return {**current, "_replayed": True}
        from .inputs import canonical_path
        relative = canonical_path(self.store.root, destination)
        if relative != current["data"]["destination"]:
            raise FrameworkError("恢复目标的实际解析位置已变化")
        frozen = json_manifest(self.store.blob(current["data"]["manifest_blob"]))
        deadline = time.monotonic() + MAX_CAPTURE_SECONDS
        expected_files = {entry["path"] for entry in frozen["files"]}
        if frozen.get("manifest_version", 1) == 2:
            expected_directories = set(frozen["directories"])
        else:
            # 旧清单只能恢复写入文件所必需的父目录；不猜测未记录的空目录。
            expected_directories = {parent.as_posix() for name in expected_files
                for parent in Path(name).parents if parent != Path(".")}
        target = self.store.root / relative
        stage = self.store.home / ("restore-" + record["id"])
        marker_name = ".aireos-recovery"
        marker = canonical(dict(recovery=record["id"], manifest_blob=current["data"]["manifest_blob"])).encode("utf-8")
        pending = self.store.home / "restore-pending"
        pending.mkdir(exist_ok=True)
        def atomic_file(path, content):
            check_deadline(deadline)
            if path.exists():
                if read_regular(path, len(content), deadline) != content:
                    raise FrameworkError("恢复中发现不一致的已有文件，拒绝覆盖")
                return
            temporary = pending / uuid.uuid4().hex
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                temporary.rename(path)
            except OSError:
                if not path.is_file() or read_regular(path, len(content), deadline) != content:
                    raise
        def verify(folder):
            if read_regular(folder / marker_name, len(marker), deadline) != marker:
                raise FrameworkError("已有目录不属于本次恢复请求，拒绝接管")
            paths, directories, skipped = inventory(folder, deadline, ignored={marker_name})
            if skipped or {path.relative_to(folder).as_posix() for path in paths} != expected_files or {
                    path.relative_to(folder).as_posix() for path in directories} != expected_directories:
                raise FrameworkError("恢复文件或目录集合不完整或有未声明内容，停止而不覆盖")
            signatures, total = {}, 0
            for entry in frozen["files"]:
                path = folder / normalize_scope(entry["path"])
                signatures[path] = file_signature(path.lstat())
                content = read_regular(path, min(MAX_FILE_BYTES, entry["bytes"], MAX_TOTAL_BYTES - total), deadline)
                total += len(content)
                if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["blob"]:
                    raise FrameworkError("恢复文件不完整或变化，停止而不覆盖")
            if recheck_inventory(folder, paths, directories, signatures, deadline, ignored={marker_name}):
                raise FrameworkError("恢复末次覆盖校验未通过，停止而不覆盖")
        if target.exists():
            verify(target)
        else:
            stage.mkdir(exist_ok=True)
            atomic_file(stage / marker_name, marker)
            for name in sorted(expected_directories, key=lambda value: (len(Path(value).parts), value)):
                check_deadline(deadline)
                (stage / normalize_scope(name)).mkdir(parents=True, exist_ok=True)
            for entry in frozen["files"]:
                output = stage / normalize_scope(entry["path"])
                output.parent.mkdir(parents=True, exist_ok=True)
                content = self.store.blob(entry["blob"])
                if len(content) != entry["bytes"]:
                    raise FrameworkError("恢复文件字节数与清单不符")
                atomic_file(output, content)
            verify(stage)
            target.parent.mkdir(parents=True, exist_ok=True)
            stage.resolve().relative_to(self.store.home.resolve())
            target.resolve().relative_to(self.store.root)
            try:
                stage.rename(target)
            except OSError:
                if not target.exists():
                    raise
                verify(target)
        def finish(tx):
            from .project import Project
            Project._scope_task(tx, self.record(tx, run_id)["data"]["task"])
            tx.require_scope(relative)
            obj = tx.get(record["id"])
            return tx.replace(obj["id"], obj["rev"], {**obj["data"], "status": "completed"})
        return self.store.mutate("run.restore.finish", dict(recovery=record["id"]),
            "restore-finish-" + digest([request_id, record["id"]]), "execute", finish)


def json_manifest(content):
    import json
    value = json.loads(content)
    if not value.get("complete") or not isinstance(value.get("files"), list):
        raise FrameworkError("恢复清单不是完整运行快照")
    version = value.get("manifest_version", 1)
    if version not in {1, 2} or (version == 2 and not isinstance(value.get("directories"), list)):
        raise FrameworkError("不支持的运行目录清单版本")
    files = value["files"]
    directories = value.get("directories", []) if version == 2 else []
    names = [entry["path"] for entry in files] + directories
    if len(files) > MAX_FILES or len(names) > MAX_ENTRIES or len(set(names)) != len(names):
        raise FrameworkError("恢复清单超过路径预算或路径重复")
    for name in names:
        if normalize_scope(name) != name or name == "." or any(part.startswith(".") for part in Path(name).parts):
            raise FrameworkError("恢复清单含不支持的相对路径")
    if any(isinstance(entry.get("bytes"), bool) or not isinstance(entry.get("bytes"), int)
            or not 0 <= entry["bytes"] <= MAX_FILE_BYTES for entry in files) or sum(entry["bytes"] for entry in files) > MAX_TOTAL_BYTES:
        raise FrameworkError("恢复清单超过字节预算")
    if version == 2 and any(parent.as_posix() not in directories for name in names
            for parent in Path(name).parents if parent != Path(".")):
        raise FrameworkError("恢复清单缺少普通父目录")
    return value
