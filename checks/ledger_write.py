# -*- coding: utf-8 -*-
"""ledger_write：AI-REOS 账本统一事务追加器（T-079 改动三 = T-060 阶段 1）。

解决什么问题
------------
并发会话/同项目多问题并行时的账本撞号（历史七次在册，FAILURES/ID_ALIAS）：
旧流程"grep 最大号→写入"在取号与写入之间存在间隙，并行线插入即撞。

机制（v2.1 定稿，三轮 sol 评审终审 GO）
--------------------------------------
1. append 与 update 共用同一把 canonical 写锁（Windows 独占文件句柄，
   进程退出自动释放；不使用"删锁文件+超时"判持有人失效）。
2. 持锁后读取最新账本 → 结构+唯一性校验 → 从实际条目计算下一编号
   （草稿不携带最终编号；已发布编号永不回收）。
3. 追加后完整候选文件先内存校验 → 同目录唯一临时文件 → flush/fsync/close
   → os.replace 原子替换；替换失败不得截断/破坏原文件。
4. request-id 与规范化 payload hash 以受控注释行随条目原子写入（不破坏
   YAML schema，safe_load 不可见）；相同 request-id 重试返回原编号；
   相同 request-id 不同内容拒绝（覆盖"写成功但返回前崩溃"场景）。
5. update 入口对既有条目做版本哈希比较，拒绝基于旧整本的覆盖更新。

边界声明（诚实边界，第二轮评审必改7）
--------------------------------------
本锁为单工作树（canonical writer = 主仓主工作树）内防撞；跨 worktree
须提交草稿、不在本地副本自行定号。本工具不声称解决跨工作树并发。

用法
----
    python ledger_write.py append --kind E --entry <草稿.yaml> --root <aiops目录>
    python ledger_write.py show   --kind E --request-id <uuid> --root <aiops目录>
对外暴露 append_ledger(root, kind, entry, request_id) 供测试导入。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

LEDGER_FILES: Dict[str, str] = {
    "E": "EVIDENCE.yaml",
    "D": "DECISIONS.md",
    "T": "TASK_QUEUE.yaml",
    "F": "FAILURES.md",
}
ID_PATTERNS: Dict[str, re.Pattern] = {
    # 账本内 id 行形态：YAML 键（`  id: E-001`）与 markdown 列表项（`- id: D-001`）；
    # 行尾允许受控注释（request-meta 注释在 id 行后一行，不与本行冲突）
    kind: re.compile(rf"^.*\bid:\s*{kind}-([0-9]+)\s*(?:#.*)?$", re.MULTILINE)
    for kind in LEDGER_FILES
}
META_PREFIX = "# request-meta: "
# YAML 顶层必须是列表的账本（E）；D/T/F 为 markdown/yaml 混合，走 ID 扫描
YAML_LIST_LEDGERS = {"E"}


class LedgerWriteError(RuntimeError):
    """事务追加失败（原文件未被破坏）。"""


def _payload_hash(entry: dict) -> str:
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _next_id(ids: List[int]) -> int:
    return (max(ids) + 1) if ids else 1


def _scan_ids(kind: str, text: str) -> List[int]:
    return [int(m) for m in ID_PATTERNS[kind].findall(text)]


def _id_line(kind: str, number: int) -> str:
    if kind == "E":
        return f"- schema_version: 1\n  id: E-{number:03d}"
    return f"  - id: {kind}-{number:03d}"


def _meta_comment(request_id: str, phash: str) -> str:
    return f"{META_PREFIX}{{\"request_id\": \"{request_id}\", \"payload_sha256\": \"{phash}\"}}"


def _read_meta(text: str) -> Dict[str, Dict[str, str]]:
    """从受控注释行读回 request 元数据：{request_id: {payload_sha256, id}}。"""
    out: Dict[str, Dict[str, str]] = {}
    current_id = None
    for line in text.splitlines():
        m = re.match(rf"^\s*-?\s*(?:id:\s*)([A-Z]-\d+)", line)
        if m:
            current_id = m.group(1)
        if line.lstrip().startswith(META_PREFIX):
            try:
                data = json.loads(line.lstrip()[len(META_PREFIX):])
            except ValueError:
                continue
            rid = data.get("request_id")
            if rid:
                out[rid] = {
                    "payload_sha256": data.get("payload_sha256", ""),
                    "id": current_id or "",
                }
    return out


def _exclusive_lock(lock_path: Path):
    """OS 级独占句柄锁上下文（用法：`with _exclusive_lock(p):`）。

    进程退出自动释放；Windows 用 msvcrt.locking，POSIX 用 fcntl.flock。
    """
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        fh = open(lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    return _ctx


def _atomic_replace(target: Path, new_text: str) -> None:
    """同目录唯一临时文件 → flush/fsync/close → os.replace；替换失败原文件不动。"""
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def append_ledger(
    root: Path,
    kind: str,
    entry: dict,
    request_id: Optional[str] = None,
) -> Tuple[str, bool]:
    """事务追加一条账本条目。返回 (分配到的 id, 是否幂等重放)。

    幂等语义：相同 request-id + 相同 payload → 返回原编号（不重复追加）；
    相同 request-id + 不同 payload → 拒绝。
    """
    if kind not in LEDGER_FILES:
        raise LedgerWriteError(f"未知账本 kind: {kind}")
    root = Path(root)
    ledger_path = root / LEDGER_FILES[kind]
    if not ledger_path.is_file():
        raise LedgerWriteError(f"账本不存在: {ledger_path}")
    request_id = request_id or str(uuid.uuid4())
    phash = _payload_hash(entry)

    lock_path = root / f".ledger_write_{kind}.lock"
    with _exclusive_lock(lock_path)():
        text = ledger_path.read_text(encoding="utf-8")

        # 幂等查回：写成功但调用方未拿到返回（崩溃/断线）场景
        meta = _read_meta(text)
        prior = meta.get(request_id)
        if prior is not None:
            if prior["payload_sha256"] == phash:
                return prior["id"], True
            raise LedgerWriteError(
                f"request-id {request_id} 已存在但 payload 不同（拒绝）："
                f"已有条目 {prior['id']}"
            )

        # 取号：锁内从实际条目计算（已发布编号永不回收）
        number = _next_id(_scan_ids(kind, text))
        new_id = f"{kind}-{number:03d}"

        if kind in YAML_LIST_LEDGERS:
            # 完整候选文件先在内存中校验
            payload = yaml.safe_load(text) or []
            if not isinstance(payload, list):
                raise LedgerWriteError("EVIDENCE.yaml 顶层必须是列表")
            candidate = list(payload) + [dict(entry, id=new_id)]
            new_text = yaml.safe_dump(
                candidate, allow_unicode=True, sort_keys=False, width=1000
            )
            # 受控注释插到新条目 id 行后（safe_dump 会丢注释，故整体重拼）
            new_text = _inject_meta_into_last_entry(
                new_text, new_id, _meta_comment(request_id, phash)
            )
        else:
            # D/F/T：文末按 "- id: X-nnn" 模板追加（markdown/yaml 混合账本最小干预）
            new_text = (
                text.rstrip("\n")
                + "\n\n"
                + f"- id: {new_id}\n"
                + _meta_comment(request_id, phash)
                + "\n"
            )

        # 追加后唯一性内检（与 check_id_uniqueness 同方向，锁内兜底）
        ids_after = _scan_ids(kind, new_text)
        if len(ids_after) != len(set(ids_after)):
            raise LedgerWriteError(
                f"追加 {new_id} 后出现重复编号（异常冲突，拒绝写入）"
            )
        if kind in YAML_LIST_LEDGERS and candidate is not None:
            try:
                reparsed = yaml.safe_load(new_text)
            except yaml.YAMLError as exc:
                raise LedgerWriteError(f"候选文件重解析失败: {exc}")
            if not isinstance(reparsed, list) or len(reparsed) != len(candidate):
                raise LedgerWriteError("候选文件结构校验失败（条数不符），拒绝写入")

        _atomic_replace(ledger_path, new_text)
    return new_id, False


def _inject_meta_into_last_entry(yaml_text: str, new_id: str, meta_line: str) -> str:
    """把受控注释行插到最后一个条目的 id 行之后（缩进对齐 id 行）。"""
    lines = yaml_text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == f"id: {new_id}":
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines.insert(i + 1, indent.rstrip() + meta_line.strip())
            return "\n".join(lines) + "\n"
    raise LedgerWriteError(f"候选文本中未找到 id 行 {new_id}（内部错误）")


def update_ledger(
    root: Path,
    kind: str,
    target_id: str,
    entry: dict,
    request_id: Optional[str] = None,
    expect_old_sha256: Optional[str] = None,
) -> str:
    """更新既有条目：锁内做版本比较，拒绝基于旧整本的覆盖更新。"""
    if kind not in LEDGER_FILES:
        raise LedgerWriteError(f"未知账本 kind: {kind}")
    root = Path(root)
    ledger_path = root / LEDGER_FILES[kind]
    request_id = request_id or str(uuid.uuid4())
    lock_path = root / f".ledger_write_{kind}.lock"
    with _exclusive_lock(lock_path)():
        raw = ledger_path.read_text(encoding="utf-8")
        current_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if expect_old_sha256 is not None and expect_old_sha256 != current_sha:
            raise LedgerWriteError(
                "账本已被其他会话修改（版本哈希不匹配，拒绝基于旧整本的覆盖更新）；"
                "请重新读取后重试"
            )
        # 最小实现：校验目标存在，由调用方在锁外生成新整本时改为走本函数。
        # 这里仅做保护性替换校验，避免误删目标。
        if not re.search(rf"^\s*-?\s*id:\s*{re.escape(target_id)}\b", raw, re.MULTILINE):
            raise LedgerWriteError(f"目标条目不存在: {target_id}")
        raise NotImplementedError(
            "update 的整本替换路径由调用方组合 _atomic_replace 实现本仓暂未启用；"
            "当前新增条目一律走 append。"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AI-REOS 账本事务追加器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_append = sub.add_parser("append", help="事务追加一条账本条目")
    p_append.add_argument("--kind", required=True, choices=sorted(LEDGER_FILES))
    p_append.add_argument("--entry", required=True, help="草稿 YAML 文件路径")
    p_append.add_argument("--request-id", default=None)
    p_append.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    p_show = sub.add_parser("show", help="按 request-id 查回已分配编号")
    p_show.add_argument("--kind", required=True, choices=sorted(LEDGER_FILES))
    p_show.add_argument("--request-id", required=True)
    p_show.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.cmd == "append":
        entry = yaml.safe_load(Path(args.entry).read_text(encoding="utf-8"))
        if not isinstance(entry, dict):
            print("FAIL 草稿顶层必须是映射", file=sys.stderr)
            return 2
        new_id, replayed = append_ledger(root, args.kind, entry, args.request_id)
        mode = "REPLAY(幂等)" if replayed else "APPENDED"
        print(f"OK {mode} id={new_id} request_id={args.request_id or '(auto)'}")
        return 0
    meta = _read_meta((root / LEDGER_FILES[args.kind]).read_text(encoding="utf-8"))
    prior = meta.get(args.request_id)
    if prior is None:
        print("FAIL request-id 不存在", file=sys.stderr)
        return 1
    print(f"OK id={prior['id']} payload_sha256={prior['payload_sha256'][:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
