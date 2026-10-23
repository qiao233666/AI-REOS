"""准确审核版本、依据资格与有历史的争议续查行为回归。"""
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import unittest
import test_workflow as workflow

from aireos.errors import ConflictError, FrameworkError, PermissionDenied
from aireos.project import Project
from aireos.workflow import Workflow


class Governance04Tests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def claim(self, key="target", supports=None):
        return self.worker.add_item(key, "synthetic " + key, "inference", "synthetic", supports or [], self.task["id"], key)

    def opinion(self, item, verdict="pass", key="review", **kwargs):
        return self.reviewer.review(item["id"], verdict, "检查准确版本", ["核对合成材料"], key,
            expected_revision=item["rev"], **kwargs)

    def deliver(self, items, key="deliver"):
        packet = self.worker.prepare(self.task["id"], "synthetic", key + "-context")
        return self.worker.submit(self.task["id"], [item["id"] for item in items], packet["id"], key)

    def adopt(self, item, review, key="adopt"):
        packet = self.owner.prepare(self.task["id"], "synthetic", key + "-context")
        return self.owner.adopt(item["id"], review["id"], packet["id"], key)

    def artifact(self):
        (self.root / "outputs").mkdir(exist_ok=True)
        (self.root / "outputs/data.txt").write_text("synthetic data", encoding="utf-8")
        return self.worker.add_artifact("outputs/data.txt", "原始材料", self.task["id"], "artifact")

    def unresolved_case(self, reserve_extra=False):
        item = self.claim()
        case = self.worker.challenge(item["id"], "需要检查的冲突", [], "reviewer", 2 if reserve_extra else 1, "本轮结束", "case")
        self.reviewer.assess_case(case["id"], True, "存在具体疑问", "assess")
        old = self.reviewer.begin_case_review(case["id"], "old-reservation") if reserve_extra else None
        review = self.opinion(item, "unknown")
        self.owner.resolve_case(case["id"], review["id"], "unknown", "暂缺信息", "resolve")
        return item, case, review, old

    def continuation(self, item, case, key="continue", evidence=None):
        proof = self.claim("new-proof") if evidence is None else None
        return self.worker.propose_case_continuation(case["id"], evidence if evidence is not None else [proof["id"]],
            "新增可检查的材料", item["rev"], key)

    def authorize(self, proposal, key="authorize"):
        self.reviewer.assess_case_continuation(proposal["id"], True, "确有新增信息", key + "-assessment")
        return self.owner.authorize_case_continuation(proposal["id"], 1, "reviewer", "一次复核后停止或裁定", "批准新增一轮", key)

    def test_invalid_or_missing_review_revision_creates_nothing_and_spends_no_attempt(self):
        item = self.claim()
        case = self.worker.challenge(item["id"], "冲突", [], "reviewer", 1, "一次", "case")
        self.reviewer.assess_case(case["id"], True, "需要检查", "assess")
        for index, value in enumerate((None, True, False, 0, -1, "1", 1.0)):
            with self.subTest(value=value), self.assertRaises(FrameworkError):
                self.reviewer.review(item["id"], "pass", "旧意见", ["实际检查第1版"], "bad-" + str(index), expected_revision=value)
        with self.assertRaises(FrameworkError):
            self.reviewer.review(item["id"], "pass", "省略版本", ["旧检查"], "missing")
        self.assertEqual([], self.owner.store.list("review"))
        self.assertEqual(0, self.owner.store.get(case["id"])["data"]["used"])

    def test_batch_null_revision_is_rejected(self):
        item = self.claim()
        with self.assertRaises(FrameworkError):
            Workflow(self.reviewer).record_reviews(self.task["id"], [dict(item_id=item["id"], revision=None,
                verdict="pass", method="旧意见", checks=["第1版"])], "batch-null")
        self.assertEqual([], self.owner.store.list("review"))

    def test_old_review_retry_remains_old_and_cannot_adopt_new_revision(self):
        item = self.claim()
        self.deliver([item])
        review = self.opinion(item)
        revised = self.worker.revise_item(item["id"], item["rev"], "unchecked new text", "revise")
        self.deliver([revised], "resubmit")
        replay = self.opinion(item)
        self.assertEqual(review["id"], replay["id"])
        self.assertEqual(item["rev"], replay["data"]["target"]["rev"])
        with self.assertRaises(FrameworkError):
            self.adopt(revised, review)
        with self.assertRaises(FrameworkError):
            self.opinion(item, key="late-old-opinion")

    def test_legacy_implicit_revision_opinion_remains_readable_but_cannot_certify_current_work(self):
        from aireos.recall import reference
        item = self.claim()
        revised = self.worker.revise_item(item["id"], item["rev"], "未经核查的新版", "legacy-revise")
        self.deliver([revised])
        # 0.3.1的真实旧布局：省略实际核查版本后，入口绑定当时最新版本；不修改旧记录。
        legacy = self.reviewer.store.mutate("quality.review", dict(item=item["id"], verdict="pass",
            method="当时读过第一版", checks=["实际检查第一版"], expected_revision=None, attempt=None), "legacy-opinion", "review",
            lambda tx: tx.create("review", dict(target=reference(revised), verdict="pass", method="当时读过第一版",
                checks=["实际检查第一版"], reviewer="reviewer", case=None, attempt=None)), corpus=True)
        with self.assertRaises(FrameworkError):
            self.adopt(revised, legacy, "legacy-cannot-adopt")
        self.assertEqual(legacy["data"], self.owner.store.get(legacy["id"])["data"])
        current = self.opinion(revised, key="explicit-new-check")
        self.adopt(revised, current, "explicit-new-adopt")
        self.assertEqual("adopted", self.owner.status(item["id"])["status"])

    def test_registered_basis_needs_a_pass_but_can_still_be_explored_and_closed(self):
        material = self.artifact()
        fact = self.claim(supports=[material["id"]])
        self.deliver([material, fact])
        review = self.opinion(fact)
        for index, verdict in enumerate((None, "unknown", "limit", "fail")):
            if verdict:
                self.opinion(material, verdict, "source-" + verdict)
            with self.subTest(verdict=verdict), self.assertRaises(FrameworkError):
                self.adopt(fact, review, "blocked-" + str(index))
        self.assertFalse(any(obj["data"].get("action") == "adopt" for obj in self.owner.store.list("decision")))
        closed = self.owner.close_task(self.task["id"], "已核查并保存失败材料，暂不能采纳结果。", "close-negative")
        self.assertEqual("closed", closed["task"]["data"]["status"])
        self.assertEqual("not_adopted", self.owner.status(fact["id"])["adoption"]["state"])

    def test_later_failure_blocks_two_levels_without_erasing_adoption_history(self):
        material = self.artifact()
        self.opinion(material, key="source-pass")
        fact = self.claim(supports=[material["id"]])
        self.deliver([fact])
        self.adopt(fact, self.opinion(fact))
        derived = self.claim("derived", [fact["id"]])
        self.deliver([fact, derived], "resubmit")
        self.adopt(derived, self.opinion(derived, key="derived-pass"), "derived-adopt")
        self.opinion(material, "fail", "source-fail")
        for item in (fact, derived):
            status = self.owner.status(item["id"])
            self.assertEqual("needs_review", status["status"])
            self.assertEqual("adopted", status["adoption"]["state"])
        self.assertEqual("current", self.owner.status(material["id"])["provenance"]["state"])

    def test_failed_file_basis_input_blocks_adoption_while_reference_remains_researchable(self):
        material = self.artifact()
        self.opinion(material, "fail", "source-fail")
        for usage in ("basis", "reference"):
            task = self.owner.create_task(usage, "研究失败材料", ["检查用途"], [usage], usage + "-task",
                inputs=[dict(type="file", path="outputs/data.txt", usage=usage)])
            packet = self.owner.prepare(task["id"], "synthetic", usage + "-context")
            self.owner.start_task(task["id"], packet["id"], usage + "-start")
            item = self.owner.add_item(usage, "说明目前情况", "inference", "synthetic", [], task["id"], usage + "-item")
            packet = self.owner.prepare(task["id"], "synthetic", usage + "-deliver-context")
            self.owner.submit(task["id"], [item["id"]], packet["id"], usage + "-submit")
            review = self.opinion(item, key=usage + "-opinion")
            packet = self.owner.prepare(task["id"], "synthetic", usage + "-adopt-context")
            if usage == "basis":
                with self.assertRaises(FrameworkError):
                    self.owner.adopt(item["id"], review["id"], packet["id"], usage + "-adopt")
            else:
                self.owner.adopt(item["id"], review["id"], packet["id"], usage + "-adopt")
                self.assertEqual("adopted", self.owner.status(item["id"])["status"])

    def test_withdrawn_status_does_not_hide_failed_task_basis_on_readoption(self):
        material = self.artifact()
        self.opinion(material, key="material-pass")
        task = self.owner.create_task("声明文件依据", "检查重新采纳", ["来源有效"], ["derived"], "derived-task",
            inputs=[dict(type="file", path="outputs/data.txt", usage="basis")])
        context = self.owner.prepare(task["id"], "synthetic", "derived-context")
        self.owner.start_task(task["id"], context["id"], "derived-start")
        item = self.owner.add_item("结论", "声明输入作为依据", "inference", "synthetic", [], task["id"], "derived-item")
        context = self.owner.prepare(task["id"], "synthetic", "derived-submit-context")
        self.owner.submit(task["id"], [item["id"]], context["id"], "derived-submit")
        review = self.opinion(item, key="derived-pass")
        context = self.owner.prepare(task["id"], "synthetic", "derived-adopt-context")
        self.owner.adopt(item["id"], review["id"], context["id"], "derived-adopt")
        case = self.owner.challenge(item["id"], "撤回后检查依据", [], "reviewer", 1, "一次复核", "withdraw-case")
        self.reviewer.assess_case(case["id"], True, "存在冲突", "withdraw-assess")
        review = self.opinion(item, "fail", "withdraw-review")
        self.owner.resolve_case(case["id"], review["id"], "withdraw", "不再采纳", "withdraw-resolve")
        self.opinion(material, "fail", "basis-later-fail")
        review = self.opinion(item, key="later-pass")
        self.assertEqual("withdrawn", self.owner.status(item["id"])["status"])
        context = self.owner.prepare(task["id"], "synthetic", "readopt-context")
        with self.assertRaises(FrameworkError):
            self.owner.adopt(item["id"], review["id"], context["id"], "readopt-invalid-basis")
        self.assertEqual("withdrawn", self.owner.status(item["id"])["adoption"]["state"])

    def test_unknown_resumes_with_new_evidence_and_preserves_cumulative_usage(self):
        item, case, old_review, old_attempt = self.unresolved_case(reserve_extra=True)
        proposal = self.continuation(item, case)
        with self.assertRaises(PermissionDenied):
            self.worker.assess_case_continuation(proposal["id"], True, "自审", "self-assess")
        with self.assertRaises(FrameworkError):
            self.owner.authorize_case_continuation(proposal["id"], 1, "reviewer", "一次", "尚未独立核查", "too-early")
        opened = self.authorize(proposal)
        self.assertEqual(2, opened["data"]["round"])
        self.assertEqual(2, opened["data"]["used"])
        self.assertEqual(0, opened["data"]["round_used"])
        with self.assertRaises(FrameworkError):
            self.opinion(item, key="stale-attempt", attempt_id=old_attempt["id"])
        with self.assertRaises(FrameworkError):
            self.owner.resolve_case(case["id"], old_review["id"], "uphold", "旧轮次意见", "old-resolve")
        attempt = self.reviewer.begin_case_review(case["id"], "new-reservation")
        replay = self.reviewer.begin_case_review(case["id"], "new-reservation")
        self.assertEqual(attempt["id"], replay["id"])
        current = self.owner.store.get(case["id"])
        self.assertEqual(3, current["data"]["used"])
        self.assertEqual(1, current["data"]["round_used"])
        with self.assertRaises(ConflictError):
            self.reviewer.begin_case_review(case["id"], "over-budget")
        review = self.opinion(item, key="new-review", attempt_id=attempt["id"])
        self.owner.resolve_case(case["id"], review["id"], "uphold", "新材料已核对", "new-resolve")
        self.assertEqual("resolved", self.owner.store.get(case["id"])["data"]["status"])

    def test_continuation_requires_new_content_and_is_not_self_authorized(self):
        item, case, _, _ = self.unresolved_case()
        with self.assertRaises(FrameworkError):
            self.continuation(item, case, evidence=[])
        proposal = self.continuation(item, case)
        self.reviewer.assess_case_continuation(proposal["id"], True, "确有新材料", "continuation-assess")
        with self.assertRaises(PermissionDenied):
            self.worker.authorize_case_continuation(proposal["id"], 1, "reviewer", "一次", "无权限", "worker-authorize")
        duplicate = self.claim("duplicate-proof")
        self.worker.revise_item(duplicate["id"], duplicate["rev"], "synthetic new-proof", "copy-proof-content")
        with self.assertRaises(ConflictError):
            self.continuation(item, case, "same-evidence-new-id", [duplicate["id"]])

    def test_dismissed_identical_objection_cannot_reset_budget_with_a_new_request(self):
        item = self.claim()
        args = (item["id"], "相同的异议", [], "reviewer", 1, "一次")
        case = self.worker.challenge(*args, "first-case")
        self.reviewer.assess_case(case["id"], False, "没有新增信息", "dismiss")
        with self.assertRaises(ConflictError):
            self.worker.challenge(*args, "same-content-new-request")

    def test_stale_continuation_can_be_replaced_without_reviving_old_assessment(self):
        item, case, _, _ = self.unresolved_case()
        proposal = self.continuation(item, case)
        revised = self.worker.revise_item(item["id"], item["rev"], "new target", "revise-target")
        with self.assertRaises(FrameworkError):
            self.reviewer.assess_case_continuation(proposal["id"], True, "旧对象", "stale-assessment")
        proof = proposal["data"]["evidence"][0]["id"]
        replacement = self.worker.propose_case_continuation(case["id"], [proof], "同一新材料需针对新版重核",
            revised["rev"], "replacement")
        self.assertNotEqual(proposal["id"], replacement["id"])
        self.assertEqual("stale", self.owner.store.get(proposal["id"])["data"]["status"])
        opened = self.authorize(replacement)
        self.assertEqual(revised["rev"], opened["data"]["target"]["rev"])

    def test_continuation_cannot_authorize_material_that_changed_after_assessment(self):
        item, case, _, _ = self.unresolved_case()
        proposal = self.continuation(item, case)
        self.reviewer.assess_case_continuation(proposal["id"], True, "核对了材料", "assess-new")
        proof = self.owner.store.get(proposal["data"]["evidence"][0]["id"])
        self.worker.revise_item(proof["id"], proof["rev"], "material replaced", "revise-proof")
        with self.assertRaises(FrameworkError):
            self.owner.authorize_case_continuation(proposal["id"], 1, "reviewer", "一次", "旧初核", "stale-approval")
        self.assertEqual("unresolved", self.owner.store.get(case["id"])["data"]["status"])

    def test_completed_continuation_cannot_be_reopened_as_a_fresh_case(self):
        item, case, _, _ = self.unresolved_case()
        proposal = self.continuation(item, case)
        self.authorize(proposal)
        review = self.opinion(item, key="round-two-pass")
        self.owner.resolve_case(case["id"], review["id"], "uphold", "核查完成", "round-two-resolve")
        with self.assertRaises(ConflictError):
            self.worker.challenge(item["id"], proposal["data"]["reason"],
                [ref["id"] for ref in proposal["data"]["evidence"]], "reviewer", 1, "重开", "reopen-round-two")

    def test_open_case_target_revision_change_has_an_authorized_recovery_path(self):
        item = self.claim()
        case = self.worker.challenge(item["id"], "须复核", [], "reviewer", 2, "有界复核", "open-case")
        self.reviewer.assess_case(case["id"], True, "有实质冲突", "open-assess")
        attempt = self.reviewer.begin_case_review(case["id"], "old-open-attempt")
        revised = self.worker.revise_item(item["id"], item["rev"], "修订后的候选", "open-target-revised")
        with self.assertRaises(FrameworkError):
            self.reviewer.begin_case_review(case["id"], "stale-open-review")
        proposal = self.continuation(revised, case)
        # 提议和初核不能悄悄把原案件移动到新版本。
        self.assertEqual(item["rev"], self.owner.store.get(case["id"])["data"]["target"]["rev"])
        opened = self.authorize(proposal)
        self.assertEqual(revised["rev"], opened["data"]["target"]["rev"])
        self.assertEqual(1, opened["data"]["used"])
        with self.assertRaises(FrameworkError):
            self.opinion(revised, key="old-open-attempt-review", attempt_id=attempt["id"])
        review = self.opinion(revised, key="new-target-review")
        result = self.owner.resolve_case(case["id"], review["id"], "uphold", "新版本已核对", "new-target-resolve")
        self.assertEqual("resolved", result["case"]["data"]["status"])

    def test_legacy_exhausted_open_case_can_pause_without_credit_or_budget_reset(self):
        from aireos.case_budget import reserve
        from aireos.recall import reference
        item = self.claim()
        case = self.worker.challenge(item["id"], "旧轮次", [], "reviewer", 1, "一次", "legacy-case")
        self.reviewer.assess_case(case["id"], True, "存在实质问题", "legacy-assess")
        def old_review(tx):
            attempt = reserve(tx, case["id"])
            opinion = tx.create("review", dict(target=reference(item), verdict="pass", method="旧版核查",
                checks=["旧版格式"], reviewer="reviewer", case=case["id"], attempt=attempt["id"]))
            tx.replace(attempt["id"], attempt["rev"], {**attempt["data"], "status": "reviewed", "review": opinion["id"]})
            return opinion
        legacy = self.reviewer.store.mutate("quality.review", dict(item=item["id"], expected_revision=None),
            "legacy-budget-used", "review", old_review, corpus=True)
        with self.assertRaises(FrameworkError):
            self.owner.resolve_case(case["id"], legacy["id"], "uphold", "旧意见不可授信", "legacy-uphold")
        with self.assertRaises(FrameworkError):
            self.opinion(item, key="legacy-no-quota")
        with self.assertRaises(PermissionDenied):
            self.worker.pause_case(case["id"], "无权暂停", "worker-pause")
        paused = self.owner.pause_case(case["id"], "旧意见版本无法确认，本轮暂停，保留未知", "legacy-pause")
        replay = self.owner.pause_case(case["id"], "旧意见版本无法确认，本轮暂停，保留未知", "legacy-pause")
        self.assertTrue(replay["_replayed"])
        self.assertEqual("unresolved", paused["case"]["data"]["status"])
        self.assertEqual(1, paused["case"]["data"]["used"])
        self.assertEqual("needs_review", self.owner.status(item["id"])["status"])
        proposal = self.continuation(item, case)
        self.authorize(proposal)
        review = self.opinion(item, key="post-upgrade-review")
        result = self.owner.resolve_case(case["id"], review["id"], "uphold", "新版准确核查", "post-upgrade-resolve")
        self.assertEqual("resolved", result["case"]["data"]["status"])
        self.assertEqual(2, result["case"]["data"]["used"])

    def test_legacy_case_retains_initial_evidence_content_after_continuation(self):
        from aireos.recall import reference
        item, original_proof = self.claim(), self.claim("original-proof")
        case = self.worker.store.mutate("case.propose", dict(item=item["id"], reason="旧案件"), "old-case-layout", "execute",
            lambda tx: tx.create("case", dict(target=reference(item), reason="旧案件", evidence=[reference(original_proof)],
                owner="reviewer", budget=1, used=0, exit_condition="一次", status="pending", proposer="worker")), corpus=True)
        self.reviewer.assess_case(case["id"], True, "需检查", "old-layout-assess")
        review = self.opinion(item, "unknown", "old-layout-review")
        self.owner.resolve_case(case["id"], review["id"], "unknown", "保留未知", "old-layout-resolve")
        proposal = self.continuation(item, case)
        self.authorize(proposal)
        review = self.opinion(item, "unknown", "old-layout-round-two-review")
        self.owner.resolve_case(case["id"], review["id"], "unknown", "仍未知", "old-layout-round-two-resolve")
        with self.assertRaises(ConflictError):
            self.continuation(item, case, "recycled-old-proof", evidence=[original_proof["id"]])

    def test_cli_requires_revision_and_runs_continuation_with_separate_authority(self):
        from aireos.cli import main
        def invoke(principal, command, params, key):
            credential = self.root.parent / (key + ".key")
            credential.write_text(principal.store.token, encoding="utf-8")
            payload = self.root.parent / (key + ".json")
            payload.write_text(json.dumps(params), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--project", str(self.root), "--credential-file", str(credential), command,
                    "--input", str(payload), "--request-id", key])
            return code, json.loads(stdout.getvalue() if code == 0 else stderr.getvalue())
        item, case, _, _ = self.unresolved_case()
        code, result = invoke(self.reviewer, "review", dict(item_id=item["id"], verdict="pass",
            method="缺实际核查版本", checks=["合成"]), "cli-no-revision")
        self.assertEqual(2, code)
        self.assertIn("版本", result["error"])
        proof = self.claim("cli-proof")
        code, proposal = invoke(self.worker, "propose-case-continuation", dict(case_id=case["id"],
            evidence=[proof["id"]], reason="CLI新材料", expected_revision=item["rev"]), "cli-propose")
        self.assertEqual(0, code, proposal)
        code, result = invoke(self.reviewer, "assess-case-continuation", dict(continuation_id=proposal["id"],
            substantial=True, reason="独立核对新信息"), "cli-assess")
        self.assertEqual(0, code, result)
        code, result = invoke(self.worker, "authorize-case-continuation", dict(continuation_id=proposal["id"],
            budget=1, owner_role="reviewer", exit_condition="一次复核", reason="无权限"), "cli-no-authority")
        self.assertEqual(2, code)
        code, result = invoke(self.owner, "authorize-case-continuation", dict(continuation_id=proposal["id"],
            budget=1, owner_role="reviewer", exit_condition="一次复核", reason="批准新一轮"), "cli-authorize")
        self.assertEqual(0, code, result)
        self.assertEqual(2, result["data"]["round"])


if __name__ == "__main__":
    unittest.main()
