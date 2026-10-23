"""成熟全文索引、版本关系和可重建缓存。"""
import unittest
import test_workflow as workflow
from aireos.storage import Transaction
try:
    from aireos.memory_index import MemoryIndex
except ImportError:
    MemoryIndex = None


class LocalMemoryTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def test_short_terms_not_lost_alone_or_with_long_terms(self):
        short = self.worker.add_item("AB 边界", "短术语材料", "unknown", "合成", [], self.task["id"], request_id="short")
        long = self.worker.add_item("RCWA", "另一材料", "unknown", "合成", [], self.task["id"], request_id="long")
        with self.owner.store.connection(write=True) as connection:
            index = MemoryIndex(Transaction(self.owner.store, connection, {}))
            self.assertIn(short["id"], [obj["id"] for obj in index.search("AB")["hits"]])
            combined = [obj["id"] for obj in index.search("边界 RCWA")["hits"]]
            self.assertIn(short["id"], combined)
            self.assertIn(long["id"], combined)

    def test_real_fts_index_incremental_and_rebuildable(self):
        self.assertIsNotNone(MemoryIndex, "全文索引尚未接入")
        item = self.worker.add_item("预测偏差", "曾发现系统性高估", "unknown", "合成", [], self.task["id"], request_id="indexed")
        with self.owner.store.connection(write=True) as connection:
            tx = Transaction(self.owner.store, connection, {})
            index = MemoryIndex(tx)
            first = index.refresh()
            self.assertGreater(first["indexed"], 0)
            self.assertEqual(0, index.refresh()["indexed"])
            self.assertIn("fts5", connection.execute("SELECT sql FROM sqlite_master WHERE name='memory_fts'").fetchone()[0].lower())
            self.assertIn(item["id"], [obj["id"] for obj in index.search("系统性高估", 5)["hits"]])

    def test_old_version_and_reverse_correction_retrieved_with_source(self):
        old = self.worker.add_item("旧方法", "unique_old_attempt", "unknown", "合成", [], self.task["id"], request_id="old")
        self.worker.revise_item(old["id"], 1, "补充条件", request_id="revision")
        new = self.worker.add_item("后来更正", "旧解释已更正", "unknown", "合成", [], self.task["id"], request_id="new", supersedes=[old["id"]])
        packet = self.owner.prepare(self.task["id"], "unique_old_attempt", request_id="history-recall")
        self.assertTrue(packet["historical"])
        self.assertIn(new["id"], [obj["id"] for obj in packet["context"]])
        self.assertEqual("sqlite-fts5", packet["retrieval"]["backend"])

    def test_correction_from_another_task_is_not_lost(self):
        old = self.worker.add_item("旧解释", "old_explanation", "unknown", "合成", [], self.task["id"], request_id="old")
        other = self.owner.create_task("新条件", "保留更正", ["可追溯"], ["other"], request_id="other")
        packet = self.owner.prepare(other["id"], "新条件", request_id="other-context")
        self.owner.start_task(other["id"], packet["id"], request_id="other-start")
        before = self.owner.prepare(self.task["id"], "old_explanation", request_id="before")
        correction = self.owner.add_item("后来的更正", "correction", "unknown", "合成", [], other["id"], request_id="correction", supersedes=[old["id"]])
        self.assertFalse(self.owner.check_receipt(before["id"])["valid"])
        after = self.owner.prepare(self.task["id"], "old_explanation", request_id="after")
        self.assertIn(correction["id"], [obj["id"] for obj in after["context"]])
