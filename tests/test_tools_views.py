"""工具实际运行、恢复未知终态、视图发布与历史追溯。"""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_workflow as workflow
from aireos.errors import FrameworkError, ConflictError, PermissionDenied
try:
    from aireos.tools import ToolRegistry
    from aireos.views import Views
except ImportError:
    ToolRegistry = Views = None


class ToolViewTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    add_fact = workflow.WorkflowTests.add_fact
    deliver = workflow.WorkflowTests.deliver
    pass_review = workflow.WorkflowTests.pass_review
    adopt = workflow.WorkflowTests.adopt

    def register_tool(self, code=None, name="summary", version="1"):
        self.assertIsNotNone(ToolRegistry, "工具目录尚未实现")
        folder = self.root / "tools"
        folder.mkdir(exist_ok=True)
        path = folder / (name + ".py")
        path.write_text(code or "from pathlib import Path\nPath('result.txt').write_text('synthetic')\nprint('done')\n", encoding="utf-8")
        manifest = dict(name=name, version=version, api=1, entry=path.relative_to(self.root).as_posix(),
            purpose="合成汇总", aliases=["合成统计"], inputs="命令行字符串数组", outputs="独立运行目录", owner="owner",
            limitations="合成示例，不是可信进程隔离", verification="使用本测试中的固定合成数据", dependencies=[])
        return ToolRegistry(self.owner.store).register(manifest, request_id="tool-" + name + version)

    def test_tool_retry_never_runs_twice_and_restart_reads_record(self):
        tool = self.register_tool()
        packet = self.worker.prepare(self.task["id"], "合成统计", request_id="tool-context")
        registry = ToolRegistry(self.worker.store)
        result = registry.run(tool["id"], self.task["id"], packet["id"], [], request_id="run")
        self.assertEqual("completed", result["data"]["status"])
        self.assertEqual("synthetic", (self.root / result["data"]["directory"] / "result.txt").read_text())
        replay = registry.run(tool["id"], self.task["id"], packet["id"], [], request_id="run")
        self.assertTrue(replay["_replayed"])
        self.assertEqual(result["id"], replay["id"])
        self.assertEqual(1, len(self.owner.store.list("run")))

    def test_changed_entry_and_disabled_tool_block_new_run(self):
        tool = self.register_tool()
        packet = self.worker.prepare(self.task["id"], "合成统计", request_id="tool-context")
        (self.root / "tools/summary.py").write_text("print('changed')", encoding="utf-8")
        with self.assertRaises(FrameworkError):
            ToolRegistry(self.worker.store).run(tool["id"], self.task["id"], packet["id"], [], request_id="changed")
        ToolRegistry(self.owner.store).set_enabled(tool["id"], False, request_id="disable")
        packet = self.worker.prepare(self.task["id"], "合成统计", request_id="disabled-context")
        with self.assertRaises(FrameworkError):
            ToolRegistry(self.worker.store).run(tool["id"], self.task["id"], packet["id"], [], request_id="disabled")

    def test_import_never_happens_during_registration_and_api_rejected(self):
        tool = self.register_tool("raise RuntimeError('must not import')\n")
        self.assertTrue(tool["data"]["enabled"])
        bad = dict(tool["data"]["manifest"], name="other", api=99)
        with self.assertRaises(FrameworkError):
            ToolRegistry(self.owner.store).register(bad, request_id="bad-api")

    def test_failed_process_is_failed_and_missing_dependency_rejected(self):
        tool = self.register_tool("raise SystemExit(7)\n")
        packet = self.worker.prepare(self.task["id"], "合成统计", request_id="failed-context")
        result = ToolRegistry(self.worker.store).run(tool["id"], self.task["id"], packet["id"], [], request_id="failed-run")
        self.assertEqual("failed", result["data"]["status"])
        self.assertEqual(7, result["data"]["exit_code"])
        manifest = dict(tool["data"]["manifest"], name="missing", dependencies=["TOOL-missing"])
        with self.assertRaises(FrameworkError):
            ToolRegistry(self.owner.store).register(manifest, request_id="missing-dep")

    def test_timeout_is_unknown_and_same_request_does_not_restart(self):
        tool = self.register_tool("import time\ntime.sleep(2)\n")
        packet = self.worker.prepare(self.task["id"], "合成统计", request_id="tool-context")
        result = ToolRegistry(self.worker.store).run(tool["id"], self.task["id"], packet["id"], [], request_id="timeout", timeout=0.1)
        self.assertEqual("unknown", result["data"]["status"])
        again = ToolRegistry(self.worker.store).run(tool["id"], self.task["id"], packet["id"], [], request_id="timeout", timeout=0.1)
        self.assertEqual(result["id"], again["id"])

    def test_view_failure_retains_previous_complete_generation(self):
        self.assertIsNotNone(Views, "视图尚未实现")
        views = Views(self.owner.store)
        first = views.publish(request_id="publish-first", max_chars=2000)
        pointer = (self.root / ".aireos/views/CURRENT").read_text()
        with patch("aireos.views.switch_pointer", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                views.publish(request_id="publish-failed", max_chars=2000)
        self.assertEqual(pointer, (self.root / ".aireos/views/CURRENT").read_text())
        self.assertTrue((self.root / first["directory"] / "STATE.md").is_file())

    def test_human_stage_trace_preserves_old_bytes_and_shows_changes(self):
        self.assertIsNotNone(Views, "视图尚未实现")
        artifact, fact = self.add_fact()
        self.deliver([fact])
        self.adopt(fact, self.pass_review(fact))
        result = self.owner.close_task(self.task["id"], "核对了合成结果，当前范围内可作为后续工作的依据。", request_id="close")
        stage = result["stage"]
        (self.root / "outputs/data.txt").write_text("new content", encoding="utf-8")
        trace = Views(self.owner.store).trace(stage["id"])
        self.assertEqual({(artifact["id"], artifact["rev"]), (fact["id"], fact["rev"])},
            {(opinion["data"]["target"]["id"], opinion["data"]["target"]["rev"]) for opinion in trace["reviews_then"]})
        self.assertTrue(all(opinion["data"]["verdict"] == "pass" for opinion in trace["reviews_then"]))
        self.assertEqual(1, len(trace["decisions_then"]))
        self.assertEqual(stage["data"]["summary"], trace["stage"]["data"]["summary"])
        self.assertIn(artifact["data"]["blob"], trace["snapshots"])
        self.assertEqual("needs_review", trace["current"][fact["id"]]["status"])
        published = Views(self.owner.store).publish(request_id="publish", max_chars=1500)
        state = (self.root / published["directory"] / "STATE.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(state), 1500)
        self.assertIn("继续检索", state)
        self.assertTrue(Views(self.owner.store).status()["current"])


if __name__ == "__main__":
    unittest.main()
