"""可选语义配置不下载模型，默认全文检索可在断网时工作。"""
import unittest
from unittest.mock import patch
import test_workflow as workflow
from aireos.errors import FrameworkError
from aireos.storage import canonical
try:
    from aireos.semantic import MemoryService, chunks
except ImportError:
    MemoryService = chunks = None


class MemoryServiceTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def test_default_search_is_offline_and_does_not_create_receipt(self):
        self.assertIsNotNone(MemoryService)
        item = self.worker.add_item("旧失败", "模型曾系统性高估", "unknown", "合成", [], self.task["id"], request_id="item")
        with patch("socket.create_connection", side_effect=AssertionError("禁止网络")):
            result = MemoryService(self.owner.store).query("系统性高估")
        self.assertIn(item["id"], [obj["id"] for obj in result["matches"]])
        self.assertEqual("sqlite-fts5", result["retrieval"]["backend"])

    def test_semantic_configuration_requires_existing_model_and_does_not_silently_fallback(self):
        self.assertIsNotNone(MemoryService)
        with self.assertRaises(FrameworkError):
            MemoryService(self.owner.store).configure("hybrid", request_id="bad", model_name="missing", model_directory=str(self.root / "not-present"))
        self.assertEqual([], self.owner.store.list("memory_config"))

    def test_chunk_offsets_preserve_all_characters(self):
        self.assertIsNotNone(chunks)
        text = "前期失败与后来更正。" * 100
        spans = chunks(text)
        recovered = [False] * len(text)
        for start, end, fragment in spans:
            self.assertEqual(text[start:end], fragment)
            self.assertLessEqual(len(fragment), 360)
            recovered[start:end] = [True] * (end - start)
        self.assertTrue(all(recovered))

    def test_fingerprint_includes_external_weights(self):
        from aireos.semantic import model_fingerprint
        directory = self.root / "model"
        directory.mkdir()
        (directory / "model.onnx").write_bytes(b"synthetic graph")
        weights = directory / "model.onnx_data"
        weights.write_bytes(b"v1")
        before = model_fingerprint(directory)
        weights.write_bytes(b"v2")
        self.assertNotEqual(before, model_fingerprint(directory))

    def test_display_budget_includes_reuse_state(self):
        for index in range(5):
            self.worker.add_item("历史材料", "检索正文" * 90, "unknown", "合成", [], self.task["id"], request_id="display-" + str(index))
        packet = self.owner.prepare(self.task["id"], "历史材料", request_id="budget", max_chars=1000)
        used = sum(len(canonical(obj)) + len(canonical({obj["id"]: packet["reuse_status"][obj["id"]]})) for obj in packet["context"])
        self.assertLessEqual(used, 1000)
        self.assertEqual(used, packet["display_chars"])
        result = MemoryService(self.owner.store).query("历史材料", max_chars=1000)
        used = sum(len(canonical(obj)) + len(canonical({obj["id"]: result["reuse_status"][obj["id"]]})) for obj in result["matches"])
        self.assertLessEqual(used, 1000)
        self.assertTrue(result["display_truncated"])
