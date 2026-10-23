"""普通目录恢复及外部审核有界交付的真实文件回归。"""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_workflow as workflow
from aireos.errors import FrameworkError
from aireos.external_review import ExternalReview
from aireos.run_evidence import RunEvidence, capture
from aireos.storage import canonical


class Evidence04Tests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def directory(self):
        directory = self.root / ".aireos/runs/tree"
        directory.mkdir(parents=True)
        return directory

    def recorded(self, directory, evidence):
        return self.owner.store.create("run", dict(task=self.task["id"],
            directory=directory.relative_to(self.root).as_posix(), evidence=evidence), "fixture-run")

    def package(self):
        (self.root / "outputs").mkdir(exist_ok=True)
        (self.root / "outputs/material.txt").write_text("synthetic", encoding="utf-8")
        adapter = ExternalReview(self.owner.store)
        package = adapter.prepare(self.task["id"], ["outputs/material.txt"], "仅检查合成材料",
            "fake", "high", "仅授权本地假提供方", "package")
        return adapter, package

    @staticmethod
    def provider(report=b"synthetic report", events=b'{"type":"turn.completed"}\n', extra=None, calls=None):
        def fake(argv, prompt, work, stdout, stderr, timeout):
            if calls is not None:
                calls.append(1)
            Path(argv[argv.index("-o") + 1]).write_bytes(report)
            stdout.write(events)
            if extra:
                extra(work)
            return 0
        return fake

    def test_manifest_two_restores_empty_and_nested_directories(self):
        directory = self.directory()
        (directory / "empty/nested").mkdir(parents=True)
        (directory / "data").mkdir()
        (directory / "data/value.txt").write_bytes(b"ok")
        evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertEqual(2, evidence.get("manifest_version"))
        self.assertEqual({"empty", "empty/nested", "data"}, set(evidence["directories"]))
        run = self.recorded(directory, evidence)
        result = RunEvidence(self.owner.store).restore(run["id"], "outputs/restored", "restore")
        self.assertTrue((self.root / "outputs/restored/empty/nested").is_dir())
        self.assertEqual(b"ok", (self.root / "outputs/restored/data/value.txt").read_bytes())
        self.assertEqual("ordinary_tree", result["data"]["coverage"])

    def test_inspect_detects_only_directory_changes(self):
        directory = self.directory()
        empty = directory / "empty"
        empty.mkdir()
        run = self.recorded(directory, capture(self.owner.store, directory.relative_to(self.root), True))
        self.assertTrue(RunEvidence(self.owner.store).inspect(run["id"])["current_matches"])
        empty.rmdir()
        (directory / "different").mkdir()
        self.assertFalse(RunEvidence(self.owner.store).inspect(run["id"])["current_matches"])

    def test_final_inventory_detects_new_empty_directory(self):
        from aireos import run_evidence
        directory = self.directory()
        original = run_evidence.inventory
        calls = []
        def then_writer(*args, **kwargs):
            result = original(*args, **kwargs)
            if not calls:
                (directory / "late-empty").mkdir()
            calls.append(1)
            return result
        with patch("aireos.run_evidence.inventory", side_effect=then_writer):
            evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertFalse(evidence["complete"])

    def test_restore_continues_empty_directories_after_finish_failure(self):
        directory = self.directory()
        (directory / "empty/nested").mkdir(parents=True)
        run = self.recorded(directory, capture(self.owner.store, directory.relative_to(self.root), True))
        original = self.owner.store.mutate
        def interrupted(operation, *args, **kwargs):
            if operation == "run.restore.finish":
                raise RuntimeError("published before finish")
            return original(operation, *args, **kwargs)
        with patch.object(self.owner.store, "mutate", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                RunEvidence(self.owner.store).restore(run["id"], "outputs/restored", "restore")
        self.assertTrue((self.root / "outputs/restored/empty/nested").is_dir())
        result = RunEvidence(self.owner.store).restore(run["id"], "outputs/restored", "restore")
        self.assertEqual("completed", result["data"]["status"])
        self.assertEqual(1, len(self.owner.store.list("recovery")))

    def test_restore_resume_rejects_unlisted_directory(self):
        directory = self.directory()
        (directory / "empty").mkdir()
        run = self.recorded(directory, capture(self.owner.store, directory.relative_to(self.root), True))
        original = self.owner.store.mutate
        def interrupted(operation, *args, **kwargs):
            if operation == "run.restore.finish":
                raise RuntimeError("published before finish")
            return original(operation, *args, **kwargs)
        with patch.object(self.owner.store, "mutate", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                RunEvidence(self.owner.store).restore(run["id"], "outputs/restored", "restore")
        (self.root / "outputs/restored/unlisted").mkdir()
        with self.assertRaises(FrameworkError):
            RunEvidence(self.owner.store).restore(run["id"], "outputs/restored", "restore")

    def test_restoration_rejects_a_tree_over_current_entry_budget(self):
        directory = self.directory()
        (directory / "empty/nested").mkdir(parents=True)
        run = self.recorded(directory, capture(self.owner.store, directory.relative_to(self.root), True))
        with patch("aireos.run_evidence.MAX_ENTRIES", 1):
            with self.assertRaises(FrameworkError):
                RunEvidence(self.owner.store).restore(run["id"], "outputs/restored", "restore")
        self.assertFalse((self.root / "outputs/restored").exists())

    def test_old_manifest_stays_explicitly_files_only(self):
        directory = self.directory()
        (directory / "empty").mkdir()
        (directory / "value.txt").write_bytes(b"ok")
        current = capture(self.owner.store, directory.relative_to(self.root), True)
        legacy = {key: value for key, value in current.items()
            if key not in {"manifest_blob", "manifest_version", "directories", "coverage"}}
        legacy["manifest_blob"] = self.owner.store.snapshot(canonical(legacy).encode("utf-8"))
        run = self.recorded(directory, legacy)
        inspected = RunEvidence(self.owner.store).inspect(run["id"])
        self.assertEqual("files_only", inspected.get("coverage"))
        self.assertFalse(inspected["structure_complete"])
        restored = RunEvidence(self.owner.store).restore(run["id"], "outputs/legacy", "restore")
        self.assertEqual("files_only", restored["data"]["coverage"])
        self.assertFalse((self.root / "outputs/legacy/empty").exists())

    def test_report_file_budget_boundary_is_enforced_before_snapshot(self):
        for size in (4095, 4096, 4097):
            with self.subTest(size=size):
                adapter, package = self.package()
                # 每轮使用独立包及请求；生成的数据完全合成。
                if size != 4095:
                    package = adapter.prepare(self.task["id"], ["outputs/material.txt"], str(size),
                        "fake", "high", "合成授权", "package-" + str(size))
                snapshots = []
                original = self.owner.store.snapshot
                def record(data):
                    snapshots.append(data)
                    return original(data)
                with patch("aireos.run_evidence.MAX_FILE_BYTES", 4096), patch.object(self.owner.store, "snapshot", side_effect=record):
                    result = adapter.run(package["id"], "fake", "run-" + str(size),
                        runner=self.provider(report=b"R" * size))
                if size <= 4096:
                    self.assertEqual("completed", result["data"]["status"])
                    self.assertEqual(size, len(self.owner.store.blob(result["data"]["report_blob"])))
                else:
                    self.assertNotEqual("completed", result["data"]["status"])
                    self.assertIsNone(result["data"]["report_blob"])
                    self.assertNotIn(b"R" * size, snapshots)

    def test_events_are_parsed_only_from_captured_blob_with_line_limit(self):
        adapter, package = self.package()
        events = json.dumps({"type": "turn.completed", "padding": "X" * 200}).encode() + b"\n"
        with patch("aireos.external_review.MAX_EVENT_LINE_BYTES", 128, create=True):
            result = adapter.run(package["id"], "fake", "run", runner=self.provider(events=events))
        self.assertNotEqual("completed", result["data"]["status"])
        self.assertEqual("exited", result["data"]["process_status"])
        self.assertTrue(result["data"]["partial"])

    def test_global_budget_cannot_be_bypassed_by_report_or_events(self):
        adapter, package = self.package()
        events = b'{"type":"turn.completed"}\n'
        with patch("aireos.run_evidence.MAX_TOTAL_BYTES", 40):
            result = adapter.run(package["id"], "fake", "run", runner=self.provider(report=b"R" * 30, events=events))
        self.assertNotEqual("completed", result["data"]["status"])
        self.assertLessEqual(result["data"]["evidence"]["total_bytes"], 40)
        self.assertIsNone(result["data"]["report_blob"])

    def test_event_log_over_file_budget_cannot_claim_delivery_completed(self):
        adapter, package = self.package()
        events = b'{"type":"turn.completed"}\n' + b" " * 4096
        calls = []
        runner = self.provider(events=events, calls=calls)
        with patch("aireos.run_evidence.MAX_FILE_BYTES", 4096):
            result = adapter.run(package["id"], "fake", "run", runner=runner)
        adapter.run(package["id"], "fake", "run", runner=runner)
        self.assertEqual("exited", result["data"]["process_status"])
        self.assertEqual("incomplete", result["data"]["delivery_status"])
        self.assertFalse(result["data"]["delivery"]["events_complete"])
        self.assertEqual(1, len(calls))

    def test_critical_files_are_reserved_before_other_output_bytes(self):
        adapter, package = self.package()
        runner = self.provider(extra=lambda work: (work / "other.bin").write_bytes(b"x" * 200))
        # 完成事件25字节与报告16字节能容纳；其余元信息和产物超量。
        with patch("aireos.run_evidence.MAX_TOTAL_BYTES", 64):
            result = adapter.run(package["id"], "fake", "run", runner=runner)
        self.assertEqual("completed", result["data"]["status"])
        self.assertTrue(result["data"]["partial"])
        self.assertLessEqual(result["data"]["evidence"]["total_bytes"], 64)

    def test_report_and_events_are_not_reread_unbounded_after_capture(self):
        adapter, package = self.package()
        original_bytes, original_text = Path.read_bytes, Path.read_text
        def bytes_read(path):
            if path.name in {"events.jsonl", "report.md"}:
                raise AssertionError("不得无界重读原始报告或事件")
            return original_bytes(path)
        def text_read(path, *args, **kwargs):
            if path.name in {"events.jsonl", "report.md"}:
                raise AssertionError("不得无界重读原始报告或事件")
            return original_text(path, *args, **kwargs)
        with patch.object(Path, "read_bytes", bytes_read), patch.object(Path, "read_text", text_read):
            result = adapter.run(package["id"], "fake", "run", runner=self.provider())
        self.assertEqual("completed", result["data"]["status"])

    def test_report_growing_during_capture_is_not_a_complete_delivery(self):
        adapter, package = self.package()
        from aireos import run_evidence
        original = run_evidence.os.read
        calls = []
        report = self.root / package["data"]["directory"] / "report.md"
        def read_then_writer(descriptor, size):
            data = original(descriptor, size)
            if data == b"synthetic report" and not calls:
                with report.open("ab") as stream:
                    stream.write(b" grows while collecting")
                calls.append(1)
            return data
        with patch("aireos.run_evidence.os.read", side_effect=read_then_writer):
            result = adapter.run(package["id"], "fake", "run", runner=self.provider())
        self.assertNotEqual("completed", result["data"]["status"])
        self.assertTrue(result["data"]["partial"])
        self.assertIsNone(result["data"]["report_blob"])

    def test_other_oversize_output_keeps_complete_report_delivery_and_single_call(self):
        adapter, package = self.package()
        calls = []
        runner = self.provider(extra=lambda work: (work / "large.bin").write_bytes(b"x" * 8193), calls=calls)
        with patch("aireos.run_evidence.MAX_FILE_BYTES", 8192):
            result = adapter.run(package["id"], "fake", "run", runner=runner)
        replay = adapter.run(package["id"], "fake", "run", runner=runner)
        self.assertEqual("completed", result["data"]["status"])
        self.assertFalse(result["data"]["evidence"]["complete"])
        self.assertEqual("completed", result["data"].get("delivery_status"))
        self.assertTrue(result["data"]["partial"])
        self.assertEqual(1, len(calls))
        self.assertEqual(result["id"], replay["id"])

    def test_unknown_provider_keeps_unknown_process_and_partial_evidence(self):
        adapter, package = self.package()
        calls = []
        def timeout(argv, prompt, work, stdout, stderr, limit):
            calls.append(1)
            Path(argv[argv.index("-o") + 1]).write_bytes(b"unfinished")
            raise subprocess.TimeoutExpired(argv, limit)
        result = adapter.run(package["id"], "fake", "run", runner=timeout)
        adapter.run(package["id"], "fake", "run", runner=timeout)
        self.assertEqual("unknown", result["data"]["status"])
        self.assertEqual("unknown", result["data"].get("process_status"))
        self.assertTrue(result["data"]["partial"])
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
