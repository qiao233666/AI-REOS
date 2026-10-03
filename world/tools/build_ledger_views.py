# -*- coding: utf-8 -*-
"""build_ledger_views.py — 账本三层视图 reducer（E-085 缩减机制实现）。

canonical：EVIDENCE.yaml / TASK_QUEUE.yaml / DECISIONS.md / FAILURES.md（原文不删不改）。
本工具只读 canonical + ledger-policy.yaml + releases/，生成：
  LEDGER.generated.yaml        机器视图（每条目的层归属与派生状态）
  LEDGERS_CURRENT.generated.md active-core 人读视图
  LEDGERS_HISTORY.generated.md history 人读视图

层规则（ledger-policy.yaml）：active-core / active-cold / history。
查询：--as-of GR-xxxx（按发行年龄回放 Task 层归属）；--id 精确 ID（返回原记录+当前层+替代链）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

AIOPS = Path(__file__).resolve().parents[2]
POLICY = AIOPS / "world" / "ledger-policy.yaml"
RELEASES = AIOPS / "world" / "releases"


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_decisions() -> dict[str, dict]:
    out = {}
    t = (AIOPS / "DECISIONS.md").read_text(encoding="utf-8")
    for m in re.finditer(r"^## (D-\d+) — (.+)$", t, re.M):
        out[m.group(1)] = {"title": m.group(2).strip()}
    return out


def parse_failures() -> dict[str, dict]:
    out = {}
    t = (AIOPS / "FAILURES.md").read_text(encoding="utf-8")
    for m in re.finditer(r"^## (F-\d+) — (.+)$", t, re.M):
        out[m.group(1)] = {"title": m.group(2).strip()}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=None, help="按发行 seq 回放（如 GR-0000）")
    ap.add_argument("--id", default=None, help="精确 ID 查询（原记录+当前层+替代链）")
    args = ap.parse_args()

    policy = load_yaml(POLICY)
    n_recent = policy["task_retirement"]["complete_age_releases"]
    idx = load_yaml(RELEASES / "index.yaml")["releases"]
    seq_of = {r["release_id"]: r["release_seq"] for r in idx}
    if args.as_of:
        if args.as_of not in seq_of:
            print(f"UNKNOWN（发行不存在: {args.as_of}）")
            return 0
        as_of_seq = seq_of[args.as_of]
    else:
        as_of_seq = max(seq_of.values())

    tasks = load_yaml(AIOPS / "TASK_QUEUE.yaml")["tasks"]
    closed_in = {}
    gr0 = load_yaml(RELEASES / "GR-0000.yaml")
    for tid in gr0.get("closed_tasks_in_this_release", []):
        closed_in[tid] = 0
    # GR-0001+ 的 closed 任务：从 release 文件读取（存在则登记）
    for r in idx:
        f = RELEASES / f"{r['release_id']}.yaml"
        if r["release_id"] == "GR-0000" or not f.exists():
            continue
        rd = load_yaml(f)
        for tid in rd.get("closed_tasks_in_this_release", []):
            closed_in.setdefault(tid, rd["release_seq"])

    ev_text = (AIOPS / "EVIDENCE.yaml").read_text(encoding="utf-8")
    ev_ids = re.findall(r"id: (E-[0-9]{3,})", ev_text)
    # supersession 边（policy.known_supersessions 已声明五条；schema 尚未升级，读 policy）
    sup_edges = {}
    for e in policy.get("known_supersessions", []):
        sup_edges.setdefault(e["source"], []).append(e)

    def evidence_layer(eid: str) -> str:
        edges = sup_edges.get(eid, [])
        whole = [e for e in edges if e.get("mode") == "$whole"]
        if whole:
            succ = whole[0]["successor_ids"]
            if all(s in ev_ids for s in succ) and eid not in sup_edges:
                return "history"
            # 有 partial 边并存 → cold（替代映射不完整）
            return "active-cold"
        if edges:
            return "active-cold"
        return "active-core"

    def task_layer(t: dict) -> str:
        if t.get("status") not in ("complete", "aborted"):
            return "active-core"
        cseq = closed_in.get(t["id"])
        if cseq is None:
            return "active-cold"  # 完成时点未知（GR-0000 后漂移）→ 冷层待归位
        if as_of_seq - cseq >= n_recent:
            return "history"
        return "active-core"

    # 决策/失败首版不自动失效，全 active-core（policy.decisions_failures.auto_expiry=false）
    dec = parse_decisions()
    fl = parse_failures()

    layers = {"active-core": [], "active-cold": [], "history": []}
    for t in tasks:
        layers[task_layer(t)].append({"kind": "task", "id": t["id"],
                                      "title": t.get("title", "")[:60]})
    seen_ev = set()
    for eid in ev_ids:
        if eid in seen_ev:
            continue
        seen_ev.add(eid)
        layers[evidence_layer(eid)].append({"kind": "evidence", "id": eid})
    for did in dec:
        layers["active-core"].append({"kind": "decision", "id": did})
    for fid in fl:
        layers["active-core"].append({"kind": "failure", "id": fid})

    ledger = {"schema_version": 1, "as_of_release_seq": as_of_seq,
              "counts": {k: len(v) for k, v in layers.items()}, "layers": layers}

    if args.id:
        q = args.id
        loc = [(k, e) for k, v in layers.items() for e in v if e["id"] == q]
        if loc:
            k, e = loc[0]
            print(f"{q}: layer={k} ({e.get('title', '')})")
            if q in sup_edges:
                for e2 in sup_edges[q]:
                    print(f"  supersession[{e2['mode']}] -> {','.join(e2['successor_ids'])}: {e2['reason'][:80]}")
        else:
            print(f"UNKNOWN（未命中: {q}）")
        return 0

    (AIOPS / "world" / "LEDGER.generated.yaml").write_text(
        yaml.safe_dump(ledger, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cur = [e for e in layers["active-core"]]
    (AIOPS / "world" / "LEDGERS_CURRENT.generated.md").write_text(
        "# LEDGERS CURRENT（active-core — 生成视图禁手改）\n\n"
        + "\n".join(f"- {e['kind']}: {e['id']} {e.get('title', '')}" for e in cur) + "\n", encoding="utf-8")
    his = layers["history"]
    (AIOPS / "world" / "LEDGERS_HISTORY.generated.md").write_text(
        "# LEDGERS HISTORY（history 层 — 原文保留于 canonical，可按 ID 回溯）\n\n"
        + ("\n".join(f"- {e['kind']}: {e['id']} {e.get('title', '')}" for e in his) + "\n" if his
           else "（暂无条目进入 history 层）\n"), encoding="utf-8")
    print(f"[ledger] as_of_seq={as_of_seq} | core={len(layers['active-core'])} "
          f"cold={len(layers['active-cold'])} history={len(layers['history'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
