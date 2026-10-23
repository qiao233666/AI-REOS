"""资源占用和外部审核适配器；全部使用假提供方，无真实付费调用。"""
from pathlib import Path
import unittest
import test_workflow as workflow
from aireos.coordination import Coordinator
from aireos.errors import FrameworkError, ConflictError, PermissionDenied
try:
    from aireos.external_review import ExternalReview
except ImportError:
    ExternalReview = None


class ResourceExternalTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def test_capacity_and_unavailable_and_unknown_reservation(self):
        coord = Coordinator(self.owner.store)
        self.assertTrue(hasattr(coord, "register_resource"), "资源入口尚未实现")
        node = coord.register_resource("test-node", 2, ["cpu"], True, "合成节点，不会启动计算", request_id="node")
        worker = Coordinator(self.worker.store)
        job = worker.reserve(node["id"], self.task["id"], 2, ["cpu"], request_id="reserve")
        with self.assertRaises(ConflictError):
            worker.reserve(node["id"], self.task["id"], 1, ["cpu"], request_id="overflow")
        with self.assertRaises(FrameworkError):
            worker.finish_reservation(job["id"], "completed", False, "不确定是否结束", request_id="false-terminal")
        worker.finish_reservation(job["id"], "failed", True, "已核实合成作业结束", request_id="finish")
        worker.reserve(node["id"], self.task["id"], 2, ["cpu"], request_id="next")
        unavailable = coord.register_resource("offline-node", 1, ["gpu"], False, "不可用", request_id="offline")
        with self.assertRaises(FrameworkError):
            worker.reserve(unavailable["id"], self.task["id"], 1, ["gpu"], request_id="bad-node")

    def test_external_request_requires_explicit_authority_and_write_access(self):
        self.assertIsNotNone(ExternalReview, "外部审核适配器尚未实现")
        (self.root / "outputs").mkdir()
        (self.root / "outputs/material.txt").write_text("synthetic example", encoding="utf-8")
        adapter = ExternalReview(self.owner.store)
        with self.assertRaises(FrameworkError):
            adapter.prepare(self.task["id"], ["outputs/material.txt"], "检查合成结果", "test-model", "high", "", request_id="no-authority")
        package = adapter.prepare(self.task["id"], ["outputs/material.txt"], "检查合成结果", "test-model", "high", "仅授权合成材料的测试", request_id="review-package")
        data = package["data"]
        self.assertEqual("workspace-write", data["sandbox"])
        work = self.root / data["directory"] / "work"
        (work / "probe.txt").write_text("can write", encoding="utf-8")
        self.assertEqual(1, len(data["materials"]))
        with self.assertRaises(PermissionDenied):
            ExternalReview(self.worker.store).prepare(self.task["id"], ["outputs/material.txt"], "检查", "test-model", "high", "text", request_id="worker-package")

    def test_external_fake_provider_logs_and_repeat_is_not_called_again(self):
        self.assertIsNotNone(ExternalReview, "外部审核适配器尚未实现")
        (self.root / "outputs").mkdir()
        (self.root / "outputs/material.txt").write_text("synthetic example", encoding="utf-8")
        adapter = ExternalReview(self.owner.store)
        package = adapter.prepare(self.task["id"], ["outputs/material.txt"], "检查", "test-model", "high", "合成测试授权", request_id="package")
        calls = []
        def fake(argv, prompt, work, stdout, stderr, timeout):
            calls.append(argv)
            (work / "review-check.txt").write_text("performed synthetic check", encoding="utf-8")
            report = Path(argv[argv.index("-o") + 1])
            report.write_text("合成审核：仍需判断实际适用性", encoding="utf-8")
            stdout.write(b'{"type":"turn.completed"}\n')
            return 0
        result = adapter.run(package["id"], "fake-codex", request_id="invoke", runner=fake)
        self.assertEqual("completed", result["data"]["status"])
        again = adapter.run(package["id"], "fake-codex", request_id="invoke", runner=fake)
        self.assertEqual(1, len(calls))
        self.assertEqual(result["id"], again["id"])
        self.assertIn("workspace-write", calls[0])
        self.assertNotIn("read-only", calls[0])
        self.assertEqual([], self.owner.store.list("review"))


if __name__ == "__main__":
    unittest.main()
