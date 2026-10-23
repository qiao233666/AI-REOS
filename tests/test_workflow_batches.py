"""一次交付、核查录入和有权结案由程序串联，失败可恢复。"""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_workflow as workflow
from aireos.errors import FrameworkError, PermissionDenied
try:
    from aireos.workflow import Workflow
except ImportError:
    Workflow = None


class WorkflowBatchTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def specs(self):
        self.assertIsNotNone(Workflow)
        (self.root / "outputs").mkdir()
        (self.root / "outputs/data.txt").write_text("synthetic value = 2", encoding="utf-8")
        return [dict(key="data", path="outputs/data.txt", title="合成输出")], [dict(key="fact", title="合成观察", text="当前合成值为2", classification="fact", scope="仅合成", supports=["data"])]

    def review_artifacts(self, delivered):
        for obj in delivered["data"]["result"]["artifacts"]:
            self.reviewer.review(obj["id"], "pass", "合成文件对照", ["已核查文件字节"], expected_revision=obj["rev"], request_id="file-review-" + obj["id"])

    def test_one_delivery_and_retry_do_not_duplicate_items(self):
        files, claims = self.specs()
        result = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.assertEqual("completed", result["data"]["status"])
        self.assertEqual("delivered", self.owner.store.get(self.task["id"])["data"]["status"])
        again = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.assertEqual(result["id"], again["id"])
        self.assertTrue(again["_replayed"])
        self.assertEqual(2, len(self.owner.store.list("item")))
        self.assertEqual([], self.owner.store.list("decision"))
        self.assertEqual(2, len(self.owner.store.get(self.task["id"])["data"]["submitted"]))

    def test_interrupted_delivery_resumes_exact_steps(self):
        files, claims = self.specs()
        with patch.object(self.worker, "add_item", side_effect=RuntimeError("injected interruption")):
            with self.assertRaises(RuntimeError):
                Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.assertEqual(1, len(self.owner.store.list("item")))
        result = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.assertEqual("completed", result["data"]["status"])
        self.assertEqual(2, len(self.owner.store.list("item")))

    def test_review_then_adoption_keep_role_boundaries(self):
        files, claims = self.specs()
        delivered = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.review_artifacts(delivered)
        item = delivered["data"]["result"]["items"][0]
        opinion = dict(item_id=item["id"], revision=item["rev"], verdict="pass", method="实际合成对照", checks=["数值和范围相符"])
        with self.assertRaises(PermissionDenied):
            Workflow(self.worker).record_reviews(self.task["id"], [opinion], request_id="self-review")
        reviewed = Workflow(self.reviewer).record_reviews(self.task["id"], [opinion], request_id="reviews")
        self.assertEqual("candidate", self.owner.status(item["id"])["status"])
        review = reviewed["data"]["result"]["reviews"][0]
        result = Workflow(self.owner).finalize(self.task["id"], [dict(item_id=item["id"], review_id=review["id"])], "合成检查已经完成，可追溯其适用范围。", request_id="finish")
        self.assertEqual("completed", result["data"]["status"])
        self.assertEqual("adopted", self.owner.status(item["id"])["status"])
        self.assertEqual(1, len(self.owner.store.list("stage")))
        again = Workflow(self.owner).finalize(self.task["id"], [dict(item_id=item["id"], review_id=review["id"])], "合成检查已经完成，可追溯其适用范围。", request_id="finish")
        self.assertEqual(result["id"], again["id"])
        self.assertEqual(1, len(self.owner.store.list("stage")))

    def test_old_review_payload_does_not_bind_to_new_revision(self):
        files, claims = self.specs()
        delivered = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        item = delivered["data"]["result"]["items"][0]
        self.worker.revise_item(item["id"], item["rev"], "修订后内容", request_id="revision")
        with self.assertRaises(FrameworkError):
            Workflow(self.reviewer).record_reviews(self.task["id"], [dict(item_id=item["id"], revision=item["rev"], verdict="pass", method="旧版意见", checks=["只检查过旧版"])], request_id="stale-review")

    def test_publication_failure_after_completed_business_actions_can_resume(self):
        files, claims = self.specs()
        delivered = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.review_artifacts(delivered)
        item = delivered["data"]["result"]["items"][0]
        review = self.reviewer.review(item["id"], "pass", "合成核对", ["符合范围"], expected_revision=item["rev"], request_id="review")
        selected = [dict(item_id=item["id"], review_id=review["id"])]
        with patch("aireos.workflow.Views.publish", side_effect=OSError("injected publication failure")):
            with self.assertRaises(OSError):
                Workflow(self.owner).finalize(self.task["id"], selected, "已完成合成检查。", request_id="finalize")
        self.assertEqual(1, len(self.owner.store.list("decision")))
        result = Workflow(self.owner).finalize(self.task["id"], selected, "已完成合成检查。", request_id="finalize")
        self.assertEqual("completed", result["data"]["status"])
        self.assertEqual(1, len(self.owner.store.list("stage")))
        from aireos.views import Views
        self.assertTrue(Views(self.owner.store).status()["current"])

    def test_commit_before_step_log_does_not_duplicate_adoption_on_resume(self):
        files, claims = self.specs()
        delivered = Workflow(self.worker).deliver(self.task["id"], files, claims, request_id="batch")
        self.review_artifacts(delivered)
        item = delivered["data"]["result"]["items"][0]
        review = self.reviewer.review(item["id"], "pass", "合成核对", ["符合范围"], expected_revision=item["rev"], request_id="review")
        selected = [dict(item_id=item["id"], review_id=review["id"])]
        wrapper = Workflow(self.owner)
        original = wrapper._step
        def interrupted(flow_id, name, *args):
            if name == "adopt:0":
                raise RuntimeError("committed action before step log")
            return original(flow_id, name, *args)
        with patch.object(wrapper, "_step", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                wrapper.finalize(self.task["id"], selected, "已完成合成检查。", request_id="finalize")
        self.owner.store.create("item", {"title": "无关新增", "task": "another-task", "supports": []}, "unrelated")
        wrapper.finalize(self.task["id"], selected, "已完成合成检查。", request_id="finalize")
        self.assertEqual(1, len(self.owner.store.list("decision")))
