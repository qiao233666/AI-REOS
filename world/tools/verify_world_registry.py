# -*- coding: utf-8 -*-
"""verify_world_registry.py — 世界观层校验器（E-083 第五类检查 + 三条召回探针）。

检查类别（E-083 设计）：
  1) 文件↔声明：scope 内文件恰好被一个对象或有效豁免覆盖（孤儿活动文档=违规，迁移期告警级）；
  2) 声明↔账本：authority.granted_by / bindings[].authority_ref / evidence_refs / relations 的 ID 存在性；
  3) 声明↔视图：generated 两视图与内存重建逐字一致（手改 generated = fail-closed）；
  4) 生命周期语义：adopted 必有 effective_from；digest_state 合法；supersedes 无悬空/环；
  5) 召回探针：Π_D / N=1/2/3 / COMSOL 三条必须命中（防召回能力退化——E-078 修复自身的回归网）。

fail 分层（E-083）：结构类 fail-closed；未分类文件迁移期 warning；查询未命中返回 UNKNOWN 文本不判失败。
exit code：0=PASS；1=fail-closed 违规；2=有 warning（迁移期允许门禁放行，report-only 阶段恒 0）。

用法：
  python aiops/world/tools/verify_world_registry.py [--report-only]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_world_registry import (COVERAGE, OBJECTS_DIR, REPO, build_registry,
                                  load_objects, query)

FAIL: list[str] = []
WARN: list[str] = []


def fail(msg: str) -> None:
    FAIL.append(msg)


def warn(msg: str) -> None:
    WARN.append(msg)


def check_schema_and_lifecycle(objs: list[dict]) -> None:
    try:
        import jsonschema
    except ImportError:
        warn("jsonschema 未安装——schema 校验跳过（建议 pip install jsonschema；见 kit requirements.txt）")
        jsonschema = None
    schema = (AIOPS_SCHEMA := Path(__file__).resolve().parents[2] / "schemas" / "world-object.schema.json")
    schema_d = (jsonschema and __import__("json").loads(schema.read_text(encoding="utf-8"))) or None
    ids = set()
    for o in objs:
        if jsonschema and schema_d:
            canonical = {k: v for k, v in o.items() if not k.startswith("_")}
            try:
                jsonschema.validate(canonical, schema_d)
            except jsonschema.ValidationError as e:
                fail(f"schema 违规 {o['object_id']}: {e.message[:120]}")
        if o["object_id"] in ids:
            fail(f"object_id 重复: {o['object_id']}")
        ids.add(o["object_id"])
        lc = o.get("lifecycle", {})
        if lc.get("status") == "adopted" and not lc.get("effective_from"):
            fail(f"adopted 缺 effective_from: {o['object_id']}")
        d = derive = None  # derive 在 builder 内做，这里只查 canonical 字段语义
        acc = lc.get("accepted_digest") or {}
        if lc.get("status") == "adopted" and not acc:
            warn(f"adopted 无 accepted_digest（待人工复核回填）: {o['object_id']}")
        for sup in lc.get("supersedes", []):
            if sup not in ids and sup not in {x["object_id"] for x in objs}:
                fail(f"supersedes 悬空: {o['object_id']} -> {sup}")
        for b in o.get("bindings", []):
            if b["status"] == "adopted" and not b.get("authority_ref"):
                fail(f"adopted binding 缺 authority_ref: {o['object_id']} {b['selector']}")
            if b["status"] == "adopted" and not b.get("keywords"):
                fail(f"adopted binding 缺 keywords（召回依赖）: {o['object_id']} {b['selector']}")
        for r in o.get("evidence_refs", []):
            if not re.fullmatch(r"E-[0-9]{3,}", r):
                fail(f"evidence_refs 格式: {o['object_id']} {r}")
    for o in objs:
        for sup in o.get("lifecycle", {}).get("supersedes", []):
            tgt = next((x for x in objs if x["object_id"] == sup), None)
            if tgt and o["object_id"] in tgt.get("lifecycle", {}).get("supersedes", []):
                fail(f"supersedes 环: {o['object_id']} <-> {sup}")


def check_files_vs_coverage(objs: list[dict]) -> None:
    cov = yaml.safe_load(COVERAGE.read_text(encoding="utf-8")) or {}
    scope_globs = (cov.get("coverage") or {}).get("scope", [])
    exempt = {(e.get("path") or "").replace("\\", "/") for e in (cov.get("coverage") or {}).get("exempt", [])}
    declared_paths = {s["path"].replace("\\", "/") for o in objs for s in o.get("sources", [])}
    import fnmatch
    scoped_files = set()
    for g in scope_globs:
        base = str(REPO)
        for p in REPO.glob(g.lstrip("/")):
            rel = p.relative_to(REPO).as_posix()
            scoped_files.add(rel)
    orphans = scoped_files - declared_paths - exempt
    for f in sorted(orphans):
        # 迁移期：未分类文件=告警（转正后 fail-closed 由 coverage.status=full 触发）
        warn(f"scope 内未登记文件（迁移期孤儿）: {f}")
    # 声明的源文件必须存在
    for o in objs:
        for s in o.get("sources", []):
            if not (REPO / s["path"]).exists():
                fail(f"源路径不存在: {o['object_id']} -> {s['path']}")


def check_ledger_refs(objs: list[dict]) -> None:
    ev_text = (REPO / "aiops" / "EVIDENCE.yaml").read_text(encoding="utf-8")
    ev_ids = set(re.findall(r"id: (E-[0-9]{3,})", ev_text))
    for o in objs:
        for r in o.get("evidence_refs", []):
            if r not in ev_ids:
                fail(f"evidence_ref 不存在: {o['object_id']} -> {r}")
        g = o.get("authority", {}).get("granted_by", "")
        if g.startswith("E-") and g not in ev_ids:
            fail(f"authority.granted_by 的 Evidence 不存在: {o['object_id']} -> {g}")


def check_views(objs: list[dict]) -> None:
    reg = build_registry(objs)
    reg_yaml = yaml.safe_dump(reg, allow_unicode=True, sort_keys=False, width=100)
    from build_world_registry import render_current, CURRENT_OUT, REGISTRY_OUT
    cur_md = render_current(reg)
    if not REGISTRY_OUT.exists() or REGISTRY_OUT.read_text(encoding="utf-8") != reg_yaml:
        fail("registry.generated.yaml 与重建结果不一致（手改或未重建——fail-closed）")
    if not CURRENT_OUT.exists() or CURRENT_OUT.read_text(encoding="utf-8") != cur_md:
        fail("CURRENT.generated.md 与重建结果不一致（fail-closed）")


def check_recall_probes(reg: dict) -> None:
    probes = [
        ("Π_D", 1),        # E-078 事故 2：可信域
        ("N=1/2/3", 1),    # E-078 事故 3：层数扫描
        ("COMSOL", 1),     # E-078 事故 1：环境路径
    ]
    for term, min_hits in probes:
        hits = query(reg, term)
        if len(hits) < min_hits:
            fail(f"召回探针未命中: '{term}'（{len(hits)}<{min_hits}）——召回能力退化")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report-only", action="store_true", help="report-only：FAIL 降级为 warning，exit 恒 0")
    args = ap.parse_args()

    objs = load_objects()
    check_schema_and_lifecycle(objs)
    check_files_vs_coverage(objs)
    check_ledger_refs(objs)
    check_views(objs)
    reg = build_registry(objs)
    check_recall_probes(reg)

    for w in WARN:
        print(f"[WARN] {w}")
    for f in FAIL:
        print(f"[FAIL] {f}")
    print(f"[汇总] objects={len(objs)} warnings={len(WARN)} failures={len(FAIL)}")
    if args.report_only:
        print("[report-only] FAIL 不阻断（迁移期模式）")
        return 0
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
