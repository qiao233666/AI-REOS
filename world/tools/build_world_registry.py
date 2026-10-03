# -*- coding: utf-8 -*-
"""build_world_registry.py — AI-REOS 世界观层构建器（E-083 结构性方案最小内核）。

三层结构：
  canonical: aiops/world/objects/<kind>--<slug>.yaml（一对象一文件，人工/半自动维护）
  机器视图:  aiops/world/registry.generated.yaml（整文件生成，禁手改）
  读侧视图:  aiops/world/CURRENT.generated.md（Agent 与人的检索入口；STATE 不再维护设计地图）

纯函数保证：稳定排序、无墙钟字段、输入 fingerprint；不回写 canonical、不修改源账本。
generated 视图失效时（E-083 契约）调用方必须按 REGISTRY_UNAVAILABLE/UNKNOWN 处理，不得阻塞只读修复。

用法（RCWA_electronic 解释器）：
  python aiops/world/tools/build_world_registry.py            # 构建 + 一致性自检
  python aiops/world/tools/build_world_registry.py --check    # 只校验不写（供 verify 调用）
  python aiops/world/tools/build_world_registry.py --query "Π_D"   # 召回查询（探针用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

AIOPS = Path(__file__).resolve().parents[2]
REPO = AIOPS.parent
OBJECTS_DIR = AIOPS / "world" / "objects"
REGISTRY_OUT = AIOPS / "world" / "registry.generated.yaml"
CURRENT_OUT = AIOPS / "world" / "CURRENT.generated.md"
COVERAGE = AIOPS / "world" / "coverage.yaml"


def sha256_file(p: Path) -> str:
    if p.is_dir():
        return "DIR(目录源，digest 不适用——checker 仅校验存在性)"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_objects() -> list[dict]:
    objs = []
    for p in sorted(OBJECTS_DIR.glob("*.yaml")):
        obj = yaml.safe_load(p.read_text(encoding="utf-8"))
        obj["_file"] = p.name
        objs.append(obj)
    objs.sort(key=lambda o: o["object_id"])  # 稳定排序（与文件名序一致）
    return objs


def derive_status(obj: dict) -> dict:
    """派生字段：current_digest（当前源文件哈希）、digest_state、effective_status。不回写 canonical。"""
    out = {"current_digest": {}, "digest_state": "unknown", "effective_status": obj["lifecycle"]["status"]}
    for s in obj.get("sources", []):
        f = REPO / s["path"]
        out["current_digest"][s["path"]] = sha256_file(f) if f.exists() else "MISSING"
    acc = obj.get("lifecycle", {}).get("accepted_digest") or {}
    if obj["lifecycle"]["status"] == "adopted" and acc:
        if any(v == "PENDING_REHASH" for v in acc.values()):
            out["digest_state"] = "pending_manual_review"
        elif all(acc.get(k) == v for k, v in out["current_digest"].items() if k in acc):
            out["digest_state"] = "verified"
        else:
            out["digest_state"] = "stale_pending_review"  # 实质修改触发待复核（E-083 写入契约）
    # 环境对象：capabilities 过期 → stale/UNKNOWN（假设 D 契约）
    if obj.get("kind") == "environment":
        caps = obj.get("capabilities", [])
        if caps and all(c.get("status") == "stale" for c in caps):
            out["effective_status"] = "stale"
    return out


def build_registry(objs: list[dict]) -> dict:
    reg = {"schema_version": 1, "generator": "aiops/world/tools/build_world_registry.py",
           "objects": []}
    for o in objs:
        entry = {k: v for k, v in o.items() if k != "_file"}
        entry["_derived"] = derive_status(o)
        entry["_source_file"] = f"aiops/world/objects/{o['_file']}"
        reg["objects"].append(entry)
    return reg


def render_current(reg: dict) -> str:
    lines = ["# CURRENT（世界观读侧视图 — 由 build_world_registry.py 生成，禁手改）",
             "",
             f"coverage: 见 aiops/world/coverage.yaml（未命中一律 UNKNOWN，不产生否定结论）",
             f"objects: {len(reg['objects'])}（含 _derived 派生状态）", ""]
    for o in reg["objects"]:
        d = o["_derived"]
        lines.append(f"## {o['object_id']} — {o['title']}")
        lines.append(f"- status: {o['lifecycle']['status']} | digest: {d['digest_state']} | "
                     f"authority: {o['authority']['level']} ({o['authority']['granted_by']})")
        for s in o.get("sources", []):
            lines.append(f"- source: {s['path']} ({s['role']})")
        for b in o.get("bindings", []):
            kw = ", ".join(b.get("keywords", []))
            lines.append(f"- binding [{b['status']}] {b['selector']}: {b['purpose']}"
                         + (f"  ⟪keywords: {kw}⟫" if kw else ""))
        if o.get("kind") == "environment":
            for c in o.get("capabilities", []):
                lines.append(f"- cap [{c['status']}] {c['host_id']}: {c['capability']}"
                             + (f"（过期判定见 checker）" if c["status"] != "unverified" else "（未验证——不得当可用）"))
        lines.append("")
    return "\n".join(lines) + "\n"


def query(reg: dict, term: str) -> list[str]:
    """召回查询：命中 object/binding 关键词，返回人读行（探针与 verify 共用）。"""
    hits = []
    term_l = term.lower()
    for o in reg["objects"]:
        for b in o.get("bindings", []):
            hay = " ".join([b.get("selector", ""), b.get("purpose", ""), *b.get("keywords", [])]).lower()
            if term_l in hay:
                hits.append(f"{o['object_id']} §{b['selector']} [{b['status']}]: {b['purpose']}")
        if o.get("kind") == "environment":
            for c in o.get("capabilities", []):
                if term_l in c.get("capability", "").lower():
                    hits.append(f"{o['object_id']} cap[{c['status']}]: {c['capability']}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="只校验 generated 视图与重建结果逐字一致，不写")
    ap.add_argument("--query", default=None, help="召回查询（命中 binding keywords / capabilities）")
    args = ap.parse_args()

    objs = load_objects()
    reg = build_registry(objs)

    if args.query:
        hits = query(reg, args.query)
        print("\n".join(hits) if hits else f"UNKNOWN（registry 未命中: {args.query}——按 E-083 契约只返回 UNKNOWN 语义）")
        return 0

    reg_yaml = yaml.safe_dump(reg, allow_unicode=True, sort_keys=False, width=100)
    cur_md = render_current(reg)

    if args.check:
        ok_a = REGISTRY_OUT.read_text(encoding="utf-8") == reg_yaml
        ok_b = CURRENT_OUT.read_text(encoding="utf-8") == cur_md
        print(f"registry.generated.yaml: {'一致' if ok_a else '漂移'}；"
              f"CURRENT.generated.md: {'一致' if ok_b else '漂移'}")
        return 0 if (ok_a and ok_b) else 1

    REGISTRY_OUT.write_text(reg_yaml, encoding="utf-8")
    CURRENT_OUT.write_text(cur_md, encoding="utf-8")
    fp = {o["object_id"]: sha256_file(OBJECTS_DIR / o["_file"])[:12] for o in objs}
    (AIOPS / "world" / ".build_fingerprint.json").write_text(
        json.dumps(fp, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"[build] objects={len(objs)} -> registry.generated.yaml + CURRENT.generated.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
