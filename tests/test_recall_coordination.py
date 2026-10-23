"""历史检索、并行占用和接管的实际失败边界。"""
from pathlib import Path
import unittest
import test_workflow as workflow
from aireos.coordination import Coordinator
from aireos.errors import FrameworkError, PermissionDenied, ConflictError


class RecallCoordinationTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    add_fact = workflow.WorkflowTests.add_fact
    deliver = workflow.WorkflowTests.deliver
    pass_review = workflow.WorkflowTests.pass_review
    adopt = workflow.WorkflowTests.adopt
    def test_chinese_query_and_explicit_alias_recall(self):
        obj = self.worker.add_item("历史试验", "尝试过一种边界处理，结果失败", "unknown", "合成", [], self.task["id"],
            request_id="alias", aliases=["以前试过的约束"])
        packet = self.worker.prepare(self.task["id"], "以前试过的约束为什么失败", request_id="recall-alias")
        self.assertIn(obj["id"], [entry["id"] for entry in packet["context"]])
        self.assertTrue(self.worker.check_receipt(packet["id"])["valid"])

    def test_overlapping_output_claim_and_fencing(self):
        coord = Coordinator(self.worker.store)
        lease = coord.claim(self.task["id"], "outputs", request_id="lease")
        with self.assertRaises(ConflictError):
            coord.claim(self.task["id"], "outputs/sub", request_id="overlap")
        grant = self.owner.store.grant("other", ["execute"], ["outputs"], "other")
        from aireos.project import Project
        other = Project(self.root, Path(grant["credential_file"]).read_text().strip())
        new = Coordinator(self.owner.store).takeover(lease["id"], "other", True, "旧任务已停止", request_id="takeover")
        self.assertEqual(2, new["data"]["epoch"])
        _, item = self.add_fact()
        packet = self.worker.prepare(self.task["id"], "示例", request_id="late-context")
        with self.assertRaises(PermissionDenied):
            self.worker.submit(self.task["id"], [item["id"]], packet["id"], request_id="late", leases=[{"id": lease["id"], "epoch": 1}])
        with self.assertRaises(PermissionDenied):
            coord.release(lease["id"], 1, request_id="late-release")
        Coordinator(other.store).release(lease["id"], 2, request_id="release-new")

    def test_grant_cannot_escalate_or_widen_scope(self):
        grant = self.owner.store.grant("planner", ["plan"], ["outputs"], "planner")
        from aireos.storage import Store
        planner = Store(self.root, Path(grant["credential_file"]).read_text().strip())
        with self.assertRaises(PermissionDenied):
            planner.grant("super", ["*"], ["outputs"], "bad-grant")
        with self.assertRaises(PermissionDenied):
            planner.grant("wide", ["plan"], ["."], "bad-scope")

    def test_correction_and_missing_source_visible(self):
        artifact, item = self.add_fact()
        self.deliver([item])
        self.adopt(item, self.pass_review(item))
        (self.root / "outputs/data.txt").unlink()
        self.assertEqual("needs_review", self.owner.status(item["id"])["status"])
        packet = self.worker.prepare(self.task["id"], "示例", request_id="missing-context")
        self.assertFalse(self.worker.check_receipt(packet["id"])["valid"])
        self.assertEqual("needs_review", packet["reuse_status"][item["id"]]["status"])


if __name__ == "__main__":
    unittest.main()
