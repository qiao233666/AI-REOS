# -*- coding: utf-8 -*-
"""ledger_write 12 进程压力盲测（一次性验证脚本，验证后可删）。"""
import multiprocessing
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))

import ledger_write as lw  # noqa: E402
import yaml  # noqa: E402


def _worker(root_str: str, i: int, q):
    try:
        new_id, replayed = lw.append_ledger(
            Path(root_str), "E",
            {
                "schema_version": 1,
                "type": "observation",
                "statement": f"压力测试条目 {i}，语句足够长以满足 schema 校验。",
                "source": {"file": "t", "script": "t", "git_commit": "t"},
                "scope": {"cases": ["t"]},
                "limitations": ["t"],
                "status": "active",
                "recorded_at": "2026-10-15",
            },
        )
        q.put(("ok", new_id))
    except Exception as exc:
        q.put(("err", str(exc)))


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        rows = [
            {"schema_version": 1, "id": f"E-{i:03d}", "type": "observation",
             "statement": f"既有条目 {i}。", "source": {"file": "t", "script": "t", "git_commit": "t"},
             "scope": {"cases": ["t"]}, "limitations": ["t"], "status": "active",
             "recorded_at": "2026-10-15"}
            for i in range(1, 6)
        ]
        (root / "EVIDENCE.yaml").write_text(
            yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        q = multiprocessing.Queue()
        ps = [multiprocessing.Process(target=_worker, args=(str(root), i, q)) for i in range(12)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(timeout=120)
        res = []
        while not q.empty():
            res.append(q.get())
        oks = [r[1] for r in res if r[0] == "ok"]
        errs = [r[1] for r in res if r[0] == "err"]
        rows2 = yaml.safe_load((root / "EVIDENCE.yaml").read_text(encoding="utf-8"))
        print(f"12 进程: ok={len(oks)} err={len(errs)} 唯一号={len(set(oks))} 账本条数={len(rows2)} (期望 17)")
        if len(errs) == 0 and len(set(oks)) == 12 and len(rows2) == 17:
            print("压力盲测 PASS")
            return 0
        print("压力盲测 FAIL", errs[:3])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
