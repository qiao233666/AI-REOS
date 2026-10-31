"""采纳前诊断说明可确定阻塞，不能写入记录或代替正式门禁。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_workflow as workflow
from aireos.errors import FrameworkError
from aireos.workflow import Workflow


class FinalizeReadinessTests(unittest.TestCase):
    def setUp(self):
        workflow.WorkflowTests.setUp(self)
        self.assertTrue(callable(getattr(self.owner, "check_finalize", None)), "尚未提供只读采纳就绪检查")

    def delivery(self, task=None, folder="outputs"):
        task = task or self.task
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        (directory / "result.txt").write_text("synthetic = 2", encoding="utf-8")
        delivered = Workflow(self.owner).deliver(task["id"],
            [{"key": "data", "path": folder + "/result.txt", "title": "合成文件"}],
            [{"key": "claim", "title": "合成观察", "text": "合成值为2", "classification": "inference",
              "scope": "合成", "supports": ["data"]}], "delivery-" + task["id"])
        values = delivered["data"]["result"]["items"]
        artifact = next(i for i in values if i["key"] == "data")
        claim = next(i for i in values if i["key"] == "claim")
        opinion = self.reviewer.review(claim["id"], "pass", "独立对照合成记录", ["只核对合成观察"],
                                       "claim-review-" + task["id"], expected_revision=claim["rev"])
        return artifact, claim, [{"item_id": claim["id"], "review_id": opinion["id"]}]

    def test_changed_input_and_missing_artifact_review_are_reported_together_without_writes(self):
        source = self.root / "SUMMARY.md"
        source.write_text("冻结说明", encoding="utf-8")
        task = self.owner.create_task("固定输入", "检查输入", ["可追溯"], ["fresh"], "input-task", inputs=["SUMMARY.md"])
        artifact, claim, selected = self.delivery(task, "fresh")
        source.write_text("冻结说明\n追加状态", encoding="utf-8")
        before = (len(self.owner.store.events()), len(self.owner.store.list()))
        result = self.owner.check_finalize(task["id"], selected)
        self.assertFalse(result["ready"])
        codes = {i["code"] for i in result["issues"]}
        self.assertTrue({"input_changed", "submission_review_missing", "support_review_missing"} <= codes, result)
        self.assertIn(artifact["id"], json.dumps(result))
        self.assertIn("SUMMARY.md", json.dumps(result))
        self.assertEqual(before, (len(self.owner.store.events()), len(self.owner.store.list())))

    def test_blocked_new_finalize_reports_recovery_without_creating_workflow(self):
        artifact, claim, selected = self.delivery()
        before = (len(self.owner.store.events()), len(self.owner.store.list()))
        with self.assertRaises(FrameworkError) as caught:
            Workflow(self.owner).finalize(self.task["id"], selected, "合成阶段", "blocked-finalize", publish=False)
        self.assertIn("check-finalize", str(caught.exception))
        self.assertIn(artifact["id"], str(caught.exception))
        self.assertEqual(before, (len(self.owner.store.events()), len(self.owner.store.list())))

    def test_reviewed_support_makes_precheck_ready_and_finalization_still_works(self):
        artifact, claim, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "合成字节核对", ["synthetic = 2"],
                             "artifact-review", expected_revision=artifact["rev"])
        result = self.owner.check_finalize(self.task["id"], selected)
        self.assertTrue(result["ready"], result)
        Workflow(self.owner).finalize(self.task["id"], selected, "合成已核查", "ready-finalize", publish=False)
        self.assertEqual("closed", self.owner.store.get(self.task["id"])["data"]["status"])

    def test_unadopted_sibling_file_change_does_not_block_partial_adoption(self):
        artifact, claim, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "合成字节核对", ["合成"], "artifact-review", expected_revision=1)
        bad = self.root / "outputs/bad.txt"
        bad.write_text("bad-v1", encoding="utf-8")
        obj = self.owner.add_artifact("outputs/bad.txt", "失败候选", self.task["id"], "bad-artifact")
        packet = self.owner.prepare(self.task["id"], "合成", "resubmit-context", working_items=[claim["id"], artifact["id"], obj["id"]])
        self.owner.submit(self.task["id"], [claim["id"], artifact["id"], obj["id"]], packet["id"], "resubmit")
        self.reviewer.review(obj["id"], "fail", "合成反例", ["失败"], "bad-review", expected_revision=1)
        bad.write_text("bad-v2", encoding="utf-8")
        result = self.owner.check_finalize(self.task["id"], selected)
        self.assertTrue(result["ready"], result)

    def test_whole_packet_budget_and_paging_preserve_total_blocker_count(self):
        artifact, claim, selected = self.delivery()
        identifiers = [artifact["id"], claim["id"]]
        for index in range(8):
            path = "outputs/extra-" + str(index) + ".txt"
            (self.root / path).write_text("合成附件", encoding="utf-8")
            identifiers.append(self.owner.add_artifact(path, "合成未审附件", self.task["id"], "extra-" + str(index))["id"])
        packet = self.owner.prepare(self.task["id"], "合成", "many-context", working_items=identifiers)
        self.owner.submit(self.task["id"], identifiers, packet["id"], "many-submit")
        result = self.owner.check_finalize(self.task["id"], selected, max_chars=1000)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 1000)
        self.assertFalse(result["ready"])
        self.assertIsNotNone(result["next_offset"])
        collected = list(result["issues"])
        while result["next_offset"] is not None:
            result = self.owner.check_finalize(self.task["id"], selected, max_chars=1000, offset=result["next_offset"])
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 1000)
            collected.extend(result["issues"])
        self.assertEqual(result["blockers"], len(collected))

    def test_cli_check_finalize_is_read_only_and_needs_no_write_credential(self):
        artifact, claim, selected = self.delivery()
        request = self.root / "check.json"
        request.write_text(json.dumps({"task_id": self.task["id"], "adoptions": selected}), encoding="utf-8")
        before = len(self.owner.store.events())
        run = subprocess.run([sys.executable, "-B", "-m", "aireos", "--project", str(self.root),
            "check-finalize", "--input", str(request)], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertFalse(json.loads(run.stdout)["ready"])
        self.assertEqual(before, len(self.owner.store.events()))

    def test_same_batch_adopts_support_claim_before_dependent_claim(self):
        artifact, first, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "合成字节", ["合成"], "artifact-review", expected_revision=1)
        second = self.owner.add_item("下游主张", "有限合成推断", "inference", "合成", [first["id"]], self.task["id"], "second")
        packet = self.owner.prepare(self.task["id"], "合成", "two-context", working_items=[artifact["id"], first["id"], second["id"]])
        self.owner.submit(self.task["id"], [artifact["id"], first["id"], second["id"]], packet["id"], "two-submit")
        opinion = self.reviewer.review(second["id"], "pass", "有限合成核查", ["合成"], "second-review", expected_revision=1)
        selected.append({"item_id": second["id"], "review_id": opinion["id"]})
        reverse = self.owner.check_finalize(self.task["id"], list(reversed(selected)))
        self.assertFalse(reverse["ready"])
        self.assertIn("support_not_adopted", {i["code"] for i in reverse["issues"]})
        result = self.owner.check_finalize(self.task["id"], selected)
        self.assertTrue(result["ready"], result)
        Workflow(self.owner).finalize(self.task["id"], selected, "合成依赖按顺序采纳", "ordered-finalize", publish=False)

    def test_later_failed_support_review_remains_a_blocker(self):
        artifact, claim, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "初次合成核查", ["合成"], "first-pass", expected_revision=1)
        self.reviewer.review(artifact["id"], "fail", "新反例", ["合成反例"], "later-fail", expected_revision=1)
        result = self.owner.check_finalize(self.task["id"], selected)
        self.assertFalse(result["ready"])
        self.assertIn("support_review_not_pass", {i["code"] for i in result["issues"]})

    def test_successful_preview_does_not_allow_changed_file_at_finalization(self):
        artifact, claim, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "合成字节", ["合成"], "artifact-review", expected_revision=1)
        self.assertTrue(self.owner.check_finalize(self.task["id"], selected)["ready"])
        (self.root / "outputs/result.txt").write_text("changed", encoding="utf-8")
        with self.assertRaises(FrameworkError):
            Workflow(self.owner).finalize(self.task["id"], selected, "合成", "changed-after-preview", publish=False)
        self.assertEqual([], self.owner.store.list("decision"))

    def test_output_path_binding_mismatch_is_reported(self):
        artifact, claim, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "合成字节", ["合成"], "artifact-review", expected_revision=1)
        task = self.owner.store.get(self.task["id"])
        contract = {**task["data"]["contract"], "outputs": ["different-resolved-output"]}
        self.owner.store.replace(task["id"], task["rev"], {**task["data"], "contract": contract}, "synthetic-path-rebinding")
        result = self.owner.check_finalize(task["id"], selected)
        self.assertFalse(result["ready"])
        self.assertIn("output_path_changed", {i["code"] for i in result["issues"]})

    def test_selected_target_superseded_by_adopted_claim_is_reported(self):
        artifact, claim, selected = self.delivery()
        self.reviewer.review(artifact["id"], "pass", "合成字节", ["合成"], "artifact-review", expected_revision=1)
        newer = self.owner.add_item("替代主张", "有限新解释", "unknown", "合成", [], self.task["id"],
                                    "superseding-claim", supersedes=[claim["id"]])
        packet = self.owner.prepare(self.task["id"], "合成", "superseding-context", working_items=[artifact["id"], claim["id"], newer["id"]])
        self.owner.submit(self.task["id"], [artifact["id"], claim["id"], newer["id"]], packet["id"], "superseding-submit")
        opinion = self.reviewer.review(newer["id"], "pass", "合成替代范围", ["合成"], "superseding-review", expected_revision=1)
        packet = self.owner.prepare(self.task["id"], "合成", "adopt-newer-context", working_items=[newer["id"]])
        self.owner.adopt(newer["id"], opinion["id"], packet["id"], "adopt-newer")
        result = self.owner.check_finalize(self.task["id"], selected)
        self.assertFalse(result["ready"])
        self.assertIn("target_superseded", {i["code"] for i in result["issues"]})


if __name__ == "__main__":
    unittest.main()
