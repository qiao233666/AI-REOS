"""真实输入版本及并行上下文的失效边界。"""
from pathlib import Path
import unittest
import test_workflow as workflow
from aireos.errors import FrameworkError, StaleInput


class InputReceiptTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    add_fact = workflow.WorkflowTests.add_fact

    def input_task(self, inputs, key="input", **options):
        return self.owner.create_task("检查输入版本", "输入内容固定", ["依据可追溯"], [key], inputs=inputs, request_id=key, **options)

    def test_declared_file_change_invalidates_receipt_and_start(self):
        source = self.root / "parameters.json"
        source.write_text('{"version":"A"}', encoding="utf-8")
        task = self.input_task(["parameters.json"])
        packet = self.owner.prepare(task["id"], "尚未命中任何产物", request_id="pin-context")
        self.assertTrue(self.owner.check_receipt(packet["id"])["valid"])
        source.write_text('{"version":"B"}', encoding="utf-8")
        self.assertFalse(self.owner.check_receipt(packet["id"])["valid"])
        with self.assertRaises(StaleInput):
            self.owner.start_task(task["id"], packet["id"], request_id="changed-start")

    def test_refresh_does_not_silently_accept_new_input_and_old_bytes_preserved(self):
        source = self.root / "input.txt"
        source.write_text("old input", encoding="utf-8")
        task = self.input_task([{"type": "file", "path": "input.txt"}])
        source.write_text("new input", encoding="utf-8")
        packet = self.owner.prepare(task["id"], "检查输入", request_id="after-change")
        self.assertFalse(self.owner.check_receipt(packet["id"])["valid"])
        binding = task["data"]["contract"]["inputs"][0]
        self.assertEqual(b"old input", self.owner.store.blob(binding["sha256"]))

    def test_missing_declared_file_rejected_and_note_explicit(self):
        with self.assertRaises(FrameworkError):
            self.input_task(["does-not-exist.txt"])
        task = self.input_task([{"type": "note", "text": "这是说明而非文件"}], key="noted")
        self.assertEqual("note", task["data"]["contract"]["inputs"][0]["type"])

    def test_unrelated_update_does_not_block_other_task(self):
        other = self.input_task([], key="unrelated")
        packet = self.owner.prepare(other["id"], "其他工作", request_id="other-context")
        self.owner.start_task(other["id"], packet["id"], request_id="other-start")
        before = self.owner.prepare(self.task["id"], "示例方法", request_id="before-unrelated")
        self.owner.add_item("另一工作结果", "独立事项", "unknown", "另一个范围", [], other["id"], request_id="unrelated-item")
        checked = self.owner.check_receipt(before["id"])
        self.assertTrue(checked["valid"], checked)
        self.assertGreater(checked["delta_checked"], 0)

    def test_strict_mode_keeps_global_invalidation(self):
        task = self.input_task([], key="strict", recall_mode="strict")
        before = self.owner.prepare(task["id"], "输入", request_id="strict-before")
        self.owner.store.create("item", {"type": "claim", "title": "无关新增", "task": "other", "supports": []}, "unrelated-strict")
        self.assertFalse(self.owner.check_receipt(before["id"])["valid"])

    def test_declared_object_brings_upstream_even_when_query_misses(self):
        artifact, fact = self.add_fact()
        task = self.input_task([{"type": "item", "id": fact["id"]}])
        packet = self.owner.prepare(task["id"], "zz_no_keyword_hit", request_id="object-context")
        self.assertIn(artifact["id"], [ref["id"] for ref in packet["data"]["refs"]])
        (self.root / "outputs/data.txt").write_text("changed", encoding="utf-8")
        self.assertFalse(self.owner.check_receipt(packet["id"])["valid"])

    def test_file_input_also_retrieves_registered_source_dispute(self):
        artifact, _ = self.add_fact()
        case = self.worker.challenge(artifact["id"], "来源待核对", [], "reviewer", 1, "一次核查", request_id="source-case")
        self.reviewer.assess_case(case["id"], True, "存在实际矛盾", request_id="source-assess")
        task = self.input_task(["outputs/data.txt"])
        packet = self.owner.prepare(task["id"], "zz_no_keyword_hit", request_id="file-context")
        self.assertIn(case["id"], [ref["id"] for ref in packet["data"]["refs"]])

    def test_input_change_marks_derived_adopted_result_for_review(self):
        source = self.root / "input.txt"
        source.write_text("original", encoding="utf-8")
        task = self.input_task(["input.txt"])
        packet = self.owner.prepare(task["id"], "input", request_id="derive-context")
        self.owner.start_task(task["id"], packet["id"], request_id="derive-start")
        item = self.owner.add_item("本轮观察", "依赖原输入", "unknown", "合成", [], task["id"], request_id="derived")
        packet = self.owner.prepare(task["id"], "input", request_id="derive-submit-context")
        self.owner.submit(task["id"], [item["id"]], packet["id"], request_id="derive-submit")
        review = self.reviewer.review(item["id"], "pass", "合成核查", ["核对版本"], expected_revision=item["rev"], request_id="derive-review")
        packet = self.owner.prepare(task["id"], "input", request_id="derive-adopt-context")
        self.owner.adopt(item["id"], review["id"], packet["id"], request_id="derive-adopt")
        self.assertEqual("adopted", self.owner.status(item["id"])["status"])
        source.write_text("changed", encoding="utf-8")
        self.assertEqual("needs_review", self.owner.status(item["id"])["status"])

    def test_source_dispute_after_prepare_invalidates_declared_input_receipt(self):
        artifact, fact = self.add_fact()
        task = self.input_task([{"type": "item", "id": fact["id"]}])
        packet = self.owner.prepare(task["id"], "zz_no_keyword_hit", request_id="before-case")
        case = self.worker.challenge(artifact["id"], "来源待核对", [], "reviewer", 1, "一次核查", request_id="late-case")
        self.reviewer.assess_case(case["id"], True, "存在矛盾", request_id="late-assess")
        self.assertFalse(self.owner.check_receipt(packet["id"])["valid"])

    def test_reference_material_does_not_assert_upstream_truth(self):
        artifact, _ = self.add_fact()
        case = self.worker.challenge(artifact["id"], "待复算", [], "reviewer", 1, "一次核查", request_id="case")
        self.reviewer.assess_case(case["id"], True, "需重新检查", request_id="assess")
        for usage in ("basis", "reference"):
            task = self.input_task([{"type": "file", "path": "outputs/data.txt", "usage": usage}], key=usage)
            packet = self.owner.prepare(task["id"], "zz_no_keyword_hit", request_id=usage + "-context")
            self.owner.start_task(task["id"], packet["id"], request_id=usage + "-start")
            item = self.owner.add_item("研究记录", "仍需判断", "unknown", "合成", [], task["id"], request_id=usage + "-item")
            self.assertEqual("needs_review" if usage == "basis" else "candidate", self.owner.status(item["id"])["status"])
            self.assertIn(case["id"], [ref["id"] for ref in packet["data"]["refs"]])

    def test_registering_input_snapshot_in_own_task_is_not_self_dependency(self):
        (self.root / "input.txt").write_text("material", encoding="utf-8")
        task = self.owner.create_task("输入快照", "登记原材料", ["可追溯"], ["input.txt"], "own-task", inputs=["input.txt"])
        packet = self.owner.prepare(task["id"], "原材料", "own-context")
        self.owner.start_task(task["id"], packet["id"], "own-start")
        artifact = self.owner.add_artifact("input.txt", "原材料快照", task["id"], "own-artifact")
        self.assertEqual("candidate", self.owner.status(artifact["id"])["status"])

    def test_newly_registered_identity_of_file_input_invalidates_old_receipt(self):
        (self.root / "outputs").mkdir(exist_ok=True)
        (self.root / "outputs/data.txt").write_text("material", encoding="utf-8")
        task = self.input_task(["outputs/data.txt"])
        packet = self.owner.prepare(task["id"], "zz_no_keyword_hit", "before-identity")
        artifact = self.worker.add_artifact("outputs/data.txt", "later material", self.task["id"], "new-identity")
        case = self.worker.challenge(artifact["id"], "需核对", [], "reviewer", 1, "一次检查", request_id="identity-case")
        self.reviewer.assess_case(case["id"], True, "存在矛盾", "identity-assess")
        self.assertFalse(self.owner.check_receipt(packet["id"])["valid"])
