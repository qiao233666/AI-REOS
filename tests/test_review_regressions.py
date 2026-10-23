"""独立审查发现的实际绕过路径回归。"""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_workflow as workflow
from aireos.project import Project
from aireos.storage import Store, Transaction, normalize_scope
from aireos.coordination import Coordinator
from aireos.errors import FrameworkError, PermissionDenied, ConflictError
from aireos.views import Views


class ReviewRegressions(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    add_fact = workflow.WorkflowTests.add_fact
    deliver = workflow.WorkflowTests.deliver
    pass_review = workflow.WorkflowTests.pass_review
    adopt = workflow.WorkflowTests.adopt

    def test_unknown_case_never_promotes_candidate_or_restores_adoption(self):
        artifact, fact = self.add_fact()
        self.deliver([fact])
        self.adopt(fact, self.pass_review(fact))
        case = self.worker.challenge(fact["id"], "合成争议", [], "reviewer", 1, "保留未知后停止投入", request_id="case")
        self.reviewer.assess_case(case["id"], True, "需复查", request_id="assess")
        review = self.reviewer.review(fact["id"], "unknown", "独立复查", ["资料不能区分"], expected_revision=fact["rev"], request_id="unknown-review")
        self.owner.resolve_case(case["id"], review["id"], "unknown", "缺乏区分证据，本轮停止", request_id="resolve")
        self.assertEqual("needs_review", self.owner.status(fact["id"])["status"])
        with self.assertRaises(ConflictError):
            self.worker.challenge(fact["id"], "没有新增信息的重开", [], "reviewer", 1, "停止", request_id="reopen")

    def test_unadopted_artifact_dispute_propagates_to_adopted_fact(self):
        artifact, fact = self.add_fact()
        self.deliver([fact])
        self.adopt(fact, self.pass_review(fact))
        self.assertEqual("candidate", self.owner.status(artifact["id"])["status"])
        case = self.worker.challenge(artifact["id"], "测量方法可能无效，文件字节未变", [], "reviewer", 1, "核对方法", request_id="artifact-case")
        self.reviewer.assess_case(case["id"], True, "构成方法争议", request_id="artifact-assess")
        self.assertEqual("needs_review", self.owner.status(artifact["id"])["status"])
        self.assertEqual("needs_review", self.owner.status(fact["id"])["status"])

    def test_release_after_takeover_cannot_allow_late_submit_or_adopt(self):
        lease = Coordinator(self.worker.store).claim(self.task["id"], "outputs", request_id="lease")
        artifact, fact = self.add_fact()
        packet = self.worker.prepare(self.task["id"], "示例", request_id="leased-context")
        self.worker.submit(self.task["id"], [fact["id"]], packet["id"], leases=[dict(id=lease["id"], epoch=1)], request_id="leased-submit")
        review = self.pass_review(fact)
        grant = self.owner.store.grant("other", ["execute"], ["outputs"], "other-grant")
        other = Project(self.root, Path(grant["credential_file"]).read_text().strip())
        newer = Coordinator(self.owner.store).takeover(lease["id"], "other", True, "旧执行已停止", request_id="takeover")
        Coordinator(other.store).release(lease["id"], newer["data"]["epoch"], request_id="release")
        packet = self.worker.prepare(self.task["id"], "示例", request_id="late-context")
        with self.assertRaises(PermissionDenied):
            self.worker.submit(self.task["id"], [fact["id"]], packet["id"], request_id="late")
        with self.assertRaises(PermissionDenied):
            self.adopt(fact, review)

    def test_narrow_scope_cannot_review_decide_or_takeover_elsewhere(self):
        _, fact = self.add_fact()
        self.deliver([fact])
        review = self.pass_review(fact)
        grant = self.owner.store.grant("narrow", ["review", "decide", "coordinate"], ["elsewhere"], "narrow")
        narrow = Project(self.root, Path(grant["credential_file"]).read_text().strip())
        with self.assertRaises(PermissionDenied):
            narrow.review(fact["id"], "pass", "no scope", ["x"], expected_revision=fact["rev"], request_id="narrow-review")
        packet = narrow.prepare(self.task["id"], "示例", request_id="narrow-context")
        with self.assertRaises(PermissionDenied):
            narrow.adopt(fact["id"], review["id"], packet["id"], request_id="narrow-adopt")
        lease = Coordinator(self.worker.store).claim(self.task["id"], "outputs", request_id="lease")
        with self.assertRaises(PermissionDenied):
            Coordinator(narrow.store).takeover(lease["id"], "worker", True, "x", request_id="narrow-takeover")

    def test_old_version_exact_text_and_identifier_recalled_across_tasks(self):
        item = self.worker.add_item("旧失败", "unique_old_failure_token", "unknown", "合成", [], self.task["id"], request_id="old")
        self.worker.revise_item(item["id"], 1, "后来补充了另一解释", request_id="new")
        task = self.owner.create_task("另一任务", "新问题", ["可追溯"], ["other"], request_id="other-task")
        packet = self.owner.prepare(task["id"], "unique_old_failure_token", request_id="old-recall")
        self.assertEqual(1, packet["historical"][0]["rev"])
        packet = self.owner.prepare(task["id"], item["id"], request_id="id-recall")
        self.assertIn(item["id"], [obj["id"] for obj in packet["context"]])

    def test_overlapping_task_start_rejected_even_without_manual_lease(self):
        task = self.owner.create_task("并行任务", "测试输出冲突", ["可检查"], ["outputs/sub"], request_id="overlap-task")
        packet = self.owner.prepare(task["id"], "合成", request_id="overlap-context")
        with self.assertRaises(ConflictError):
            self.owner.start_task(task["id"], packet["id"], request_id="overlap-start")

    def test_windows_path_aliases_and_drive_relative_rejected(self):
        for path in ("C:relative", "C:/absolute", "\\rooted", "../outside"):
            with self.assertRaises(FrameworkError):
                normalize_scope(path)
        if os.name == "nt":
            Coordinator(self.worker.store).claim(self.task["id"], "outputs/Foo", request_id="upper")
            with self.assertRaises(ConflictError):
                Coordinator(self.worker.store).claim(self.task["id"], "outputs/foo", request_id="lower")

    def test_failed_grant_can_retry_without_deleting_orphan_key(self):
        original = Transaction.create
        with patch("aireos.storage.os.chmod", side_effect=OSError("injected after file write")):
            with self.assertRaises(OSError):
                self.owner.store.grant("recoverable", ["execute"], ["outputs"], "recover-grant")
        grant = self.owner.store.grant("recoverable", ["execute"], ["outputs"], "recover-grant")
        store = Store(self.root, Path(grant["credential_file"]).read_text().strip())
        with store.connection() as connection:
            self.assertEqual("recoverable", store._principal(connection, "execute")["actor"])

    def test_publish_retry_after_pointer_failure_and_old_retry_does_not_rewind(self):
        views = Views(self.owner.store)
        first = views.publish(request_id="first")
        with patch("aireos.views.switch_pointer", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                views.publish(request_id="second")
        second = views.publish(request_id="second")
        self.assertTrue(second["_replayed"])
        views.publish(request_id="first")
        self.assertEqual(second["generation"], (self.root / ".aireos/views/CURRENT").read_text().strip())
        self.assertTrue(views.status()["current"])

    def test_clock_changes_do_not_reorder_committed_decisions(self):
        _, fact = self.add_fact()
        self.deliver([fact])
        self.adopt(fact, self.pass_review(fact))
        from aireos.recall import reference
        with patch("aireos.storage.now", return_value="1900-01-01T00:00:00+00:00"):
            self.owner.store.mutate("test.withdraw", {}, "clock-withdraw", "decide", lambda tx:
                tx.create("decision", dict(target=reference(tx.get(fact["id"])), action="withdraw", reason="合成时间回拨回归")), corpus=True)
        self.assertEqual("withdrawn", self.owner.status(fact["id"])["status"])

    def test_later_failed_quality_opinion_cannot_be_bypassed_with_old_pass(self):
        _, fact = self.add_fact()
        self.deliver([fact])
        review = self.pass_review(fact)
        self.adopt(fact, review)
        self.reviewer.review(fact["id"], "fail", "新检查发现缺口", ["不能支持原有范围"], expected_revision=fact["rev"], request_id="new-fail")
        self.assertEqual("needs_review", self.owner.status(fact["id"])["status"])
        with self.assertRaises(FrameworkError):
            self.adopt(fact, review, "old-pass-retry")

    def test_context_display_budget_and_historical_navigation_are_explicit(self):
        self.worker.add_item("很长的合成材料", "内容" * 10000, "unknown", "合成", [], self.task["id"], request_id="long")
        packet = self.worker.prepare(self.task["id"], "很长的合成材料", max_chars=1000, request_id="budget")
        self.assertTrue(packet["display_truncated"])
        self.assertLessEqual(packet["display_chars"], 1000)
        self.assertEqual(1, len(packet["data"]["refs"]))

    def test_long_relation_chain_reports_truncation_and_blocks_unqualified_reuse(self):
        from aireos.recall import reference
        def build(tx):
            previous = None
            for index in range(205):
                previous = tx.create("item", dict(type="claim", title="deep_terminal" if index == 204 else "node",
                    text="合成关系", task="synthetic-other-task", supports=[reference(previous)] if previous else []))
            return previous
        self.owner.store.mutate("test.chain", {}, "build-chain", "write", build, corpus=True)
        packet = self.owner.prepare(self.task["id"], "deep_terminal", request_id="deep-recall")
        self.assertTrue(packet["data"]["truncated"])
        self.assertFalse(self.owner.check_receipt(packet["id"])["valid"])

    def test_stage_correction_appends_without_rewriting_old_and_stop_is_not_acceptance(self):
        item = self.worker.add_item("未完成工作", "尚不清楚", "unknown", "合成", [], self.task["id"], request_id="unfinished")
        stopped = self.owner.stop_task(self.task["id"], "这轮工作停止，尚无足够依据。", request_id="stop")
        first = stopped["stage"]
        second = self.owner.annotate_stage(first["id"], "补充：停止原因是缺少必要材料，尚未判断方法对错。", request_id="annotation")
        self.assertEqual("stopped", stopped["task"]["data"]["status"])
        self.assertEqual("candidate", self.owner.status(item["id"])["status"])
        self.assertEqual(first["data"]["summary"], self.owner.store.get(first["id"])["data"]["summary"])
        self.assertEqual(first["id"], second["data"]["corrects"])


if __name__ == "__main__":
    unittest.main()
