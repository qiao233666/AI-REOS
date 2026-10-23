"""工作完成、逐项核查、采纳和争议不能互相冒充。"""
from pathlib import Path
import tempfile
import unittest

from aireos.storage import Store
from aireos.errors import FrameworkError, PermissionDenied, StaleInput
try:
    from aireos.project import Project
except ImportError:
    Project = None


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Project, "工作流尚未实现")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        init = Store.initialize(self.root)
        self.owner_token = Path(init["credential_file"]).read_text().strip()
        self.owner = Project(self.root, self.owner_token)
        grant = self.owner.store.grant("worker", ["execute"], ["outputs"], "grant-worker")
        self.worker = Project(self.root, Path(grant["credential_file"]).read_text().strip())
        grant = self.owner.store.grant("reviewer", ["review"], ["."], "grant-reviewer")
        self.reviewer = Project(self.root, Path(grant["credential_file"]).read_text().strip())
        self.task = self.owner.create_task("检查示例方法", "核对合成结果的适用范围", ["结果和限制可复查"], ["outputs"], request_id="task")
        context = self.worker.prepare(self.task["id"], "示例方法", request_id="context")
        self.worker.start_task(self.task["id"], context["id"], request_id="start")

    def add_fact(self):
        (self.root / "outputs").mkdir(exist_ok=True)
        (self.root / "outputs/data.txt").write_text("synthetic value = 2\n", encoding="utf-8")
        artifact = self.worker.add_artifact("outputs/data.txt", "合成测量", self.task["id"], request_id="artifact")
        self.reviewer.review(artifact["id"], "pass", "独立核对合成文件", ["核对当前文件字节"],
                             expected_revision=artifact["rev"], request_id="artifact-review-" + artifact["id"])
        item = self.worker.add_item("示例方法的测量结果", "合成案例中测量值为2", "fact", "仅合成案例", [artifact["id"]], self.task["id"], request_id="fact")
        return artifact, item

    def deliver(self, items):
        context = self.worker.prepare(self.task["id"], "示例方法", request_id="context-deliver")
        return self.worker.submit(self.task["id"], [item["id"] for item in items], context["id"], request_id="deliver")

    def pass_review(self, item, key="review"):
        return self.reviewer.review(item["id"], "pass", "独立对照合成数据", ["核对实际产物和适用条件"],
                                    expected_revision=item["rev"], request_id=key)

    def adopt(self, item, review, key="adopt"):
        context = self.owner.prepare(self.task["id"], "示例方法", request_id=key + "-context")
        return self.owner.adopt(item["id"], review["id"], context["id"], request_id=key)

    def test_fact_requires_support_and_unknown_is_allowed(self):
        with self.assertRaises(FrameworkError):
            self.worker.add_item("无依据事实", "缺少依据", "fact", "合成", [], self.task["id"], request_id="bad-fact")
        obj = self.worker.add_item("尚未确定", "现有材料不足以判断", "unknown", "合成", [], self.task["id"], request_id="unknown")
        self.assertEqual("unknown", obj["data"]["classification"])

    def test_cannot_skip_delivery_or_force_status(self):
        with self.assertRaises(FrameworkError):
            self.owner.close_task(self.task["id"], "尚未交付", request_id="skip")
        self.assertEqual("running", self.owner.store.get(self.task["id"])["data"]["status"])

    def test_review_is_not_adoption_and_partial_acceptance_is_precise(self):
        artifact, fact = self.add_fact()
        explanation = self.worker.add_item("原因解释", "原因可能是实现选择", "hypothesis", "合成", [], self.task["id"], request_id="explanation")
        self.deliver([fact, explanation])
        review = self.pass_review(fact)
        self.assertEqual("candidate", self.owner.status(fact["id"])["status"])
        self.adopt(fact, review)
        self.assertEqual("adopted", self.owner.status(fact["id"])["status"])
        self.assertEqual("candidate", self.owner.status(explanation["id"])["status"])
        with self.assertRaises(FrameworkError):
            self.adopt(explanation, review, "wrong-item")

    def test_source_change_blocks_old_receipt_and_adoption(self):
        artifact, fact = self.add_fact()
        self.deliver([fact])
        review = self.pass_review(fact)
        context = self.owner.prepare(self.task["id"], "示例方法", request_id="before-change")
        (self.root / "outputs/data.txt").write_text("changed", encoding="utf-8")
        self.assertFalse(self.owner.check_receipt(context["id"])["valid"])
        with self.assertRaises(StaleInput):
            self.owner.adopt(fact["id"], review["id"], context["id"], request_id="stale-adopt")
        self.assertIn(b"synthetic", self.owner.store.blob(artifact["data"]["blob"]))

    def test_new_knowledge_invalidates_previous_receipt(self):
        context = self.owner.prepare(self.task["id"], "示例方法", request_id="before-correction")
        self.worker.add_item("新发现的更正", "旧解释缺少依据", "unknown", "合成", [], self.task["id"], request_id="correction")
        self.assertFalse(self.owner.check_receipt(context["id"])["valid"])

    def test_execution_role_cannot_adopt_or_review_itself(self):
        artifact, fact = self.add_fact()
        self.deliver([fact])
        with self.assertRaises(PermissionDenied):
            self.worker.review(fact["id"], "pass", "自称审核", ["自己同意"], expected_revision=fact["rev"], request_id="self-review")
        review = self.pass_review(fact)
        with self.assertRaises(PermissionDenied):
            self.worker.adopt(fact["id"], review["id"], "anything", request_id="self-adopt")

    def test_revised_item_does_not_inherit_old_review(self):
        artifact, fact = self.add_fact()
        self.deliver([fact])
        review = self.pass_review(fact)
        self.worker.revise_item(fact["id"], 1, text="new statement", request_id="revise")
        with self.assertRaises(FrameworkError):
            self.adopt(fact, review)

    def test_substantiated_case_marks_dependents_for_review_not_false(self):
        artifact, fact = self.add_fact()
        derived = self.worker.add_item("派生解释", "依赖测量的推断", "inference", "合成", [fact["id"]], self.task["id"], request_id="derived")
        self.deliver([fact, derived])
        first = self.pass_review(fact, "first-review")
        self.adopt(fact, first, "first-adopt")
        second = self.pass_review(derived, "second-review")
        self.adopt(derived, second, "second-adopt")
        case = self.worker.challenge(fact["id"], "需要核对测量过程", [], "reviewer", 1, "一次有界复核后保留未知或结案", request_id="challenge")
        self.assertEqual("adopted", self.owner.status(fact["id"])["status"])
        self.reviewer.assess_case(case["id"], True, "存在可核查冲突", request_id="substantiate")
        self.assertEqual("needs_review", self.owner.status(fact["id"])["status"])
        self.assertEqual("needs_review", self.owner.status(derived["id"])["status"])

    def test_negative_work_can_close_without_becoming_scientific_success(self):
        item = self.worker.add_item("没有得到肯定答案", "本次合成检查不足以判断", "unknown", "仅当前材料", [], self.task["id"], request_id="negative")
        self.deliver([item])
        self.reviewer.review(item["id"], "unknown", "核查了材料缺口", ["未得到可区分结果"], expected_revision=item["rev"], request_id="negative-review")
        result = self.owner.close_task(self.task["id"], "完成了本轮检查，但尚不能判断方法是否可靠。后续是否投入仍需决定。", request_id="close-negative")
        self.assertEqual("closed", result["task"]["data"]["status"])
        self.assertEqual("candidate", self.owner.status(item["id"])["status"])
        self.assertEqual(1, len(self.owner.store.list("stage")))

    def test_private_authority_file_cannot_be_registered_as_artifact(self):
        with self.assertRaises(FrameworkError):
            self.worker.add_artifact(".aireos/owner.key", "not allowed", self.task["id"], request_id="private")


if __name__ == "__main__":
    unittest.main()
