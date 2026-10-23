"""复核次数、运行证据、路径身份及多维状态的反例。"""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import test_workflow as workflow
import test_tools_views as tool_tests
from aireos.errors import ConflictError, FrameworkError, StaleInput
from aireos.coordination import Coordinator
from aireos.external_review import ExternalReview
from aireos.tools import ToolRegistry


class MaintenanceRepairs(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    add_fact = workflow.WorkflowTests.add_fact
    deliver = workflow.WorkflowTests.deliver
    register_tool = tool_tests.ToolViewTests.register_tool

    def open_case(self):
        _, item = self.add_fact()
        case = self.worker.challenge(item["id"], "有待检查的矛盾", [], "reviewer", 1, "一次实际复核", "case")
        self.reviewer.assess_case(case["id"], True, "有具体依据", "assess")
        return item, case

    def test_review_count_is_consumed_before_resolution(self):
        item, case = self.open_case()
        self.reviewer.review(item["id"], "fail", "实际合成检查", ["发现缺口"], "first", expected_revision=item["rev"])
        self.assertEqual(1, self.owner.store.get(case["id"])["data"]["used"])
        with self.assertRaises(ConflictError):
            self.reviewer.review(item["id"], "fail", "重复调用", ["没有新增预算"], "second", expected_revision=item["rev"])

    def test_explicit_reservation_retry_does_not_spend_again_and_can_resolve(self):
        self.assertTrue(hasattr(self.reviewer, "begin_case_review"), "缺少调用前预算预留入口")
        item, case = self.open_case()
        attempt = self.reviewer.begin_case_review(case["id"], "reserve")
        self.assertEqual(attempt["id"], self.reviewer.begin_case_review(case["id"], "reserve")["id"])
        with self.assertRaises(ConflictError):
            self.reviewer.begin_case_review(case["id"], "another")
        opinion = self.reviewer.review(item["id"], "pass", "已完成复核", ["范围一致"], "opinion", attempt_id=attempt["id"], expected_revision=item["rev"])
        result = self.owner.resolve_case(case["id"], opinion["id"], "uphold", "依据已核对", "resolve")
        self.assertEqual(1, result["case"]["data"]["used"])

    def test_external_case_call_needs_one_reserved_attempt_before_invocation(self):
        self.assertTrue(hasattr(self.reviewer, "begin_case_review"), "缺少预算预留")
        item, case = self.open_case()
        adapter = ExternalReview(self.owner.store)
        package = adapter.prepare(self.task["id"], ["outputs/data.txt"], "核对矛盾", "fake", "high", "仅合成提供方", "package", case_id=case["id"])
        calls = []
        def fake(argv, prompt, work, stdout, stderr, timeout):
            calls.append(1)
            Path(argv[argv.index("-o") + 1]).write_text("合成复核", encoding="utf-8")
            stdout.write(b'{"type":"turn.completed"}\n')
            return 0
        with self.assertRaises(FrameworkError):
            adapter.run(package["id"], "fake", "without-reservation", runner=fake)
        self.assertEqual([], calls)
        attempt = self.reviewer.begin_case_review(case["id"], "reserve")
        adapter.run(package["id"], "fake", "invoke", runner=fake, attempt_id=attempt["id"])
        adapter.run(package["id"], "fake", "invoke", runner=fake, attempt_id=attempt["id"])
        self.assertEqual(1, len(calls))
        another = adapter.prepare(self.task["id"], ["outputs/data.txt"], "重复核对", "fake", "high", "仅合成提供方", "another-package", case_id=case["id"])
        with self.assertRaises(FrameworkError):
            adapter.run(another["id"], "fake", "again", runner=fake, attempt_id=attempt["id"])
        self.assertEqual(1, len(calls))

    def test_run_logs_and_outputs_have_recoverable_snapshots(self):
        tool = self.register_tool()
        packet = self.worker.prepare(self.task["id"], "合成统计", "tool-context")
        registry = ToolRegistry(self.worker.store)
        run = registry.run(tool["id"], self.task["id"], packet["id"], [], "run")
        self.assertIn("evidence", run["data"], "运行文件尚未形成正式证据清单")
        self.assertTrue(run["data"]["evidence"]["complete"])
        files = {entry["path"]: entry for entry in run["data"]["evidence"]["files"]}
        self.assertIn("stdout.log", files)
        self.assertIn("result.txt", files)
        directory = self.root / run["data"]["directory"]
        (directory / "stdout.log").write_text("tampered", encoding="utf-8")
        self.assertFalse(registry.inspect_evidence(run["id"])["current_matches"])
        self.assertEqual(b"synthetic", self.owner.store.blob(files["result.txt"]["blob"]))
        registry.restore(run["id"], "outputs/recovered", "restore")
        self.assertEqual(b"synthetic", (self.root / "outputs/recovered/result.txt").read_bytes())
        self.assertNotIn(b"tampered", (self.root / "outputs/recovered/stdout.log").read_bytes())
        with self.assertRaises(FrameworkError):
            registry.restore(run["id"], "outputs/recovered", "different-restore")

    def test_missing_run_directory_is_not_a_complete_empty_snapshot(self):
        from aireos.run_evidence import capture
        captured = capture(self.owner.store, ".aireos/runs/missing", True)
        self.assertFalse(captured["complete"])
        self.assertTrue(captured["skipped"])

    def test_restore_resumes_after_files_published_before_finish(self):
        tool = self.register_tool()
        packet = self.worker.prepare(self.task["id"], "合成统计", "tool-context")
        registry = ToolRegistry(self.worker.store)
        run = registry.run(tool["id"], self.task["id"], packet["id"], [], "run")
        mutate = registry.store.mutate
        def interrupted(action, *args, **kwargs):
            if action == "run.restore.finish":
                raise RuntimeError("files published before final record")
            return mutate(action, *args, **kwargs)
        with patch.object(registry.store, "mutate", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                registry.restore(run["id"], "outputs/recovered", "restore")
        self.assertTrue((self.root / "outputs/recovered/result.txt").is_file())
        restored = registry.restore(run["id"], "outputs/recovered", "restore")
        self.assertEqual("completed", restored["data"]["status"])
        self.assertEqual(1, len(self.owner.store.list("recovery")))

    def alias(self, relative, target):
        path = self.root / relative
        if os.name == "nt":
            completed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path '" + str(path) + "' -Target '" + str(self.root / target) + "' | Out-Null"], capture_output=True, timeout=15)
            self.assertEqual(0, completed.returncode, completed.stderr)
        else:
            path.symlink_to(self.root / target, target_is_directory=True)
        return path

    def test_oversize_output_is_explicitly_partial_and_not_falsely_restored(self):
        tool = self.register_tool("from pathlib import Path\nPath('large.txt').write_text('x' * 100)\n")
        packet = self.worker.prepare(self.task["id"], "合成统计", "tool-context")
        registry = ToolRegistry(self.worker.store)
        with patch("aireos.run_evidence.MAX_FILE_BYTES", 16):
            run = registry.run(tool["id"], self.task["id"], packet["id"], [], "bounded-run")
        self.assertEqual("completed", run["data"]["status"])
        self.assertFalse(run["data"]["evidence"]["complete"])
        self.assertIn("large.txt", [entry["path"] for entry in run["data"]["evidence"]["skipped"]])
        with self.assertRaises(FrameworkError):
            registry.restore(run["id"], "outputs/partial", "restore-partial")

    def test_lease_identity_resolves_directory_alias(self):
        (self.root / "outputs").mkdir()
        alias = self.alias("alias", "outputs")
        self.addCleanup(alias.rmdir if os.name == "nt" else alias.unlink)
        coord = Coordinator(self.owner.store)
        lease = coord.claim(self.task["id"], "alias", "alias-claim")
        self.assertEqual("outputs", lease["data"]["path"])
        (alias / "result.txt").write_text("synthetic", encoding="utf-8")
        artifact = self.owner.add_artifact("alias/result.txt", "别名中的产物", self.task["id"], "alias-artifact")
        self.assertEqual("outputs/result.txt", artifact["data"]["resolved_origin"])
        with self.assertRaises(ConflictError):
            coord.claim(self.task["id"], "outputs", "canonical-claim")

    def test_retargeted_alias_cannot_submit_using_old_lease(self):
        (self.root / "outputs/a").mkdir(parents=True)
        (self.root / "outputs/b").mkdir()
        alias = self.alias("outputs/view", "outputs/a")
        self.addCleanup(alias.rmdir if os.name == "nt" else alias.unlink)
        lease = Coordinator(self.worker.store).claim(self.task["id"], "outputs/view", "lease")
        alias.rmdir() if os.name == "nt" else alias.unlink()
        self.alias("outputs/view", "outputs/b")
        item = self.worker.add_item("候选", "合成", "unknown", "合成", [], self.task["id"], "item")
        packet = self.worker.prepare(self.task["id"], "候选", "after-retarget")
        with self.assertRaises(StaleInput):
            self.worker.submit(self.task["id"], [item["id"]], packet["id"], "submit", leases=[dict(id=lease["id"], epoch=lease["data"]["epoch"])])

    def test_quality_adoption_and_provenance_are_separate_dimensions(self):
        _, item = self.add_fact()
        self.deliver([item])
        review = self.reviewer.review(item["id"], "pass", "实际合成检查", ["范围符合"], "review", expected_revision=item["rev"])
        status = self.owner.status(item["id"])
        self.assertIn("quality", status)
        self.assertEqual("pass", status["quality"]["verdict"])
        self.assertEqual(review["id"], status["quality"]["review"])
        self.assertEqual("not_adopted", status["adoption"]["state"])
        self.assertEqual("current", status["provenance"]["state"])
        self.assertEqual("delivered", status["execution"])
        self.assertEqual("fact", status["knowledge_type"])
