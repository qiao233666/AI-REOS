# -*- coding: utf-8 -*-
"""ledger_write 事务追加器验收测试（T-079 改动三；v2.1 验收矩阵）。

覆盖（第三轮终审 GO 的验收矩阵）：
- 双进程并发 append 不撞号（同 kind 多进程真实并发）；
- 幂等：相同 request-id 重试返回原编号且账本单条；同 request-id 不同内容拒绝；
- 崩溃语义：os.replace 前中断 → 原账完整（锁释放由进程退出保证）；
- 元数据往返：追加后注释保留，safe_load 不可见但 _read_meta 可读回；
- update：版本哈希不匹配拒绝；目标不存在拒绝。
"""
import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))

import ledger_write as lw  # noqa: E402
import yaml  # noqa: E402


def _make_root(tmp: Path, n_existing: int = 2) -> Path:
    rows = []
    for i in range(1, n_existing + 1):
        rows.append(
            {
                "schema_version": 1,
                "id": f"E-{i:03d}",
                "type": "observation",
                "statement": f"占位证据 {i}，供并发测试使用，长度充足。",
                "source": {"file": "t", "script": "t", "git_commit": "t"},
                "scope": {"cases": ["t"]},
                "limitations": ["t"],
                "status": "active",
                "recorded_at": "2026-10-15",
            }
        )
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "EVIDENCE.yaml").write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (tmp / "DECISIONS.md").write_text("# Decisions\n\n- id: D-001\n  内容。\n", encoding="utf-8")
    return tmp


def _entry(i: int) -> dict:
    return {
        "schema_version": 1,
        "type": "observation",
        "statement": f"并发写入测试条目 {i}，语句足够长以满足 schema。",
        "source": {"file": "t", "script": "t", "git_commit": "t"},
        "scope": {"cases": ["t"]},
        "limitations": ["t"],
        "status": "active",
        "recorded_at": "2026-10-15",
    }


def _worker(root_str: str, idx: int, q):
    """子进程 worker：并发追加，把结果编号放回队列。"""
    try:
        new_id, replayed = lw.append_ledger(Path(root_str), "E", _entry(idx))
        q.put(("ok", new_id))
    except Exception as exc:  # pragma: no cover
        q.put(("err", str(exc)))


class TestLedgerWrite(unittest.TestCase):
    def test_single_append_allocates_next(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_root(Path(d))
            new_id, replayed = lw.append_ledger(root, "E", _entry(1))
            self.assertEqual(new_id, "E-003")
            self.assertFalse(replayed)
            rows = yaml.safe_load((root / "EVIDENCE.yaml").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1]["id"], "E-003")

    def test_idempotent_replay_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_root(Path(d))
            rid = "11111111-1111-1111-1111-111111111111"
            first, r1 = lw.append_ledger(root, "E", _entry(1), rid)
            again, r2 = lw.append_ledger(root, "E", _entry(1), rid)
            self.assertEqual(first, again)
            self.assertTrue(r2)
            rows = yaml.safe_load((root / "EVIDENCE.yaml").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 3)  # 未重复追加
            with self.assertRaises(lw.LedgerWriteError):
                lw.append_ledger(root, "E", _entry(99), rid)  # 同请求号不同内容

    def test_meta_roundtrip_invisible_to_safe_load(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_root(Path(d))
            rid = "22222222-2222-2222-2222-222222222222"
            new_id, _ = lw.append_ledger(root, "E", _entry(1), rid)
            raw = (root / "EVIDENCE.yaml").read_text(encoding="utf-8")
            self.assertIn(lw.META_PREFIX, raw)  # 注释在
            parsed = yaml.safe_load(raw)
            blob = json.dumps(parsed, ensure_ascii=False)
            self.assertNotIn("payload_sha256", blob)  # safe_load 不可见
            meta = lw._read_meta(raw)
            self.assertEqual(meta[rid]["id"], new_id)

    def test_crash_before_replace_keeps_original(self):
        """模拟 replace 前中断：直接调 _atomic_replace 抛错路径。"""
        with tempfile.TemporaryDirectory() as d:
            root = _make_root(Path(d))
            target = root / "EVIDENCE.yaml"
            before = target.read_text(encoding="utf-8")
            import unittest.mock as mock

            with mock.patch.object(lw.os, "replace", side_effect=OSError("占用")):
                with self.assertRaises(OSError):
                    lw._atomic_replace(target, before + "junk\n")
            self.assertEqual(target.read_text(encoding="utf-8"), before)
            self.assertEqual(
                len(list(root.glob(".EVIDENCE.yaml.*tmp"))), 0
            )  # 临时文件已清理

    def test_concurrent_appends_no_collision(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_root(Path(d))
            q = multiprocessing.Queue()
            procs = [
                multiprocessing.Process(target=_worker, args=(str(root), i, q))
                for i in range(6)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=60)
            results = []
            while not q.empty():
                results.append(q.get())
            oks = [r[1] for r in results if r[0] == "ok"]
            errs = [r[1] for r in results if r[0] == "err"]
            self.assertEqual(len(errs), 0, f"并发出现错误: {errs}")
            self.assertEqual(len(oks), 6)
            self.assertEqual(len(set(oks)), 6)  # 六个编号互不相同
            rows = yaml.safe_load((root / "EVIDENCE.yaml").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 8)  # 2 既有 + 6 新增，零丢失

    def test_update_rejects_stale_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_root(Path(d))
            raw = (root / "EVIDENCE.yaml").read_text(encoding="utf-8")
            import hashlib

            stale_sha = hashlib.sha256((raw + "x").encode()).hexdigest()
            with self.assertRaises(lw.LedgerWriteError):
                lw.update_ledger(root, "E", "E-001", _entry(1), expect_old_sha256=stale_sha)
            with self.assertRaises(lw.LedgerWriteError):
                lw.update_ledger(root, "E", "E-999", _entry(1))


if __name__ == "__main__":
    unittest.main()
