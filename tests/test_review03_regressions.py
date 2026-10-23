"""独立审查反例：多版本召回与运行证据的有限采集。"""
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_memory_packets as packets
import test_workflow as workflow
import test_tools_views as tools
from aireos.errors import FrameworkError
from aireos.run_evidence import capture
from aireos.semantic import MemoryService
from aireos.tools import ToolRegistry
from aireos import run_evidence


def special_result(info, kind):
    fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    fields["st_mode"] = kind | 0o600
    return SimpleNamespace(**fields)


class RevisionRecallTests(unittest.TestCase):
    setUp = packets.MemoryPacketTests.setUp
    add_unknown = packets.MemoryPacketTests.add_unknown

    def test_421_revisions_cannot_hide_other_identities_in_any_text_channel(self):
        other = self.add_unknown("Other relevant record", "alpha xx accompanied by context", "other")
        repeated = self.add_unknown("alpha alpha alpha xx", "alpha xx " * 40, "repeated")
        for revision in range(420):
            repeated = self.worker.revise_item(repeated["id"], repeated["rev"],
                "alpha xx " * 40 + str(revision), "revision-" + str(revision))
        for query in ("alpha", "xx", "alpha xx"):
            with self.subTest(query=query):
                result = MemoryService(self.owner.store).query(query, limit=12)
                identifiers = [row["id"] for row in result["matches"]]
                self.assertIn(other["id"], identifiers)
                self.assertIn(repeated["id"], identifiers)
                self.assertEqual(2, len(identifiers))
                self.assertFalse(result["retrieval"]["candidate_limit_reached"])
                self.assertFalse(result["retrieval"]["version_scan_limit_reached"])

    def test_more_distinct_identities_than_requested_are_reported_as_truncated(self):
        for index in range(15):
            self.add_unknown("Independent record " + str(index), "distinctneedle", "item-" + str(index))
        result = MemoryService(self.owner.store).query("distinctneedle", limit=12)
        self.assertEqual(12, len(result["matches"]))
        self.assertEqual(12, len({row["id"] for row in result["matches"]}))
        self.assertTrue(result["retrieval"]["candidate_limit_reached"])


class EvidenceCollectionTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    register_tool = tools.ToolViewTests.register_tool

    def output_directory(self):
        directory = self.root / ".aireos/runs/synthetic"
        directory.mkdir(parents=True)
        return directory

    @staticmethod
    def special_mode(path, kind):
        original = Path.lstat
        def lstat(candidate, *args, **kwargs):
            info = original(candidate, *args, **kwargs)
            if candidate == path:
                return special_result(info, kind)
            return info
        return patch.object(Path, "lstat", lstat)

    def test_special_file_types_are_skipped_and_snapshot_is_partial(self):
        directory = self.output_directory()
        channel = directory / "channel"
        channel.write_bytes(b"")
        for kind in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK):
            with self.subTest(kind=kind), self.special_mode(channel, kind):
                evidence = capture(self.owner.store, directory.relative_to(self.root), True)
                self.assertFalse(evidence["complete"])
                self.assertNotIn("channel", [entry["path"] for entry in evidence["files"]])
                self.assertIn("channel", [entry["path"] for entry in evidence["skipped"]])

    def test_capture_deadline_is_independent_and_expiry_is_explicit(self):
        directory = self.output_directory()
        (directory / "result.txt").write_text("synthetic", encoding="utf-8")
        with patch("aireos.run_evidence.MAX_CAPTURE_SECONDS", 0, create=True):
            evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertFalse(evidence["complete"])
        self.assertTrue(evidence["skipped"])

    def test_different_ctime_conventions_between_path_and_descriptor_are_supported(self):
        directory = self.output_directory()
        (directory / "result.txt").write_text("synthetic", encoding="utf-8")
        original = os.fstat
        def descriptor_stat(descriptor):
            info = original(descriptor)
            fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            fields["st_ctime_ns"] += 1000000000
            return SimpleNamespace(**fields)
        with patch("aireos.run_evidence.os.fstat", descriptor_stat):
            evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertTrue(evidence["complete"], evidence["skipped"])
        self.assertEqual(b"synthetic", self.owner.store.blob(evidence["files"][0]["blob"]))

    def test_empty_directories_also_consume_inventory_budget(self):
        directory = self.output_directory()
        for index in range(12):
            (directory / str(index)).mkdir()
        with patch("aireos.run_evidence.MAX_ENTRIES", 8, create=True):
            evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertFalse(evidence["complete"])
        self.assertTrue(evidence["skipped"])

    def test_file_added_after_inventory_is_not_omitted_from_a_complete_snapshot(self):
        directory = self.output_directory()
        (directory / "a.txt").write_text("old", encoding="utf-8")
        original = run_evidence.inventory
        calls = []
        def inventory_then_writer(*args, **kwargs):
            found = original(*args, **kwargs)
            if not calls:
                (directory / "b.txt").write_text("new output", encoding="utf-8")
                (directory / "a.txt").write_text("new stable version", encoding="utf-8")
            calls.append(1)
            return found
        with patch("aireos.run_evidence.inventory", inventory_then_writer):
            evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertFalse(evidence["complete"])
        self.assertTrue(evidence["skipped"])

    def test_file_changed_after_its_read_is_not_a_complete_snapshot(self):
        directory = self.output_directory()
        output = directory / "a.txt"
        output.write_text("old", encoding="utf-8")
        original = run_evidence.read_regular
        def read_then_writer(path, *args, **kwargs):
            content = original(path, *args, **kwargs)
            if path == output:
                output.write_text("changed after read", encoding="utf-8")
            return content
        with patch("aireos.run_evidence.read_regular", read_then_writer):
            evidence = capture(self.owner.store, directory.relative_to(self.root), True)
        self.assertFalse(evidence["complete"])
        self.assertTrue(evidence["skipped"])

    def test_inspection_checks_for_new_files_at_completion(self):
        tool = self.register_tool()
        packet = self.worker.prepare(self.task["id"], "synthetic tool", "tool-context")
        registry = ToolRegistry(self.worker.store)
        result = registry.run(tool["id"], self.task["id"], packet["id"], [], "run")
        directory = self.root / result["data"]["directory"]
        original = run_evidence.inventory
        calls = []
        def inventory_then_writer(*args, **kwargs):
            found = original(*args, **kwargs)
            if not calls:
                (directory / "late.txt").write_text("late output", encoding="utf-8")
            calls.append(1)
            return found
        with patch("aireos.run_evidence.inventory", inventory_then_writer):
            inspected = registry.inspect_evidence(result["id"])
        self.assertTrue(inspected["snapshots_valid"])
        self.assertFalse(inspected["current_matches"])

    def test_terminal_tool_with_special_output_is_recorded_and_never_restarted(self):
        tool = self.register_tool("from pathlib import Path\nPath('channel').write_text('synthetic')\n")
        packet = self.worker.prepare(self.task["id"], "synthetic tool", "tool-context")
        registry = ToolRegistry(self.worker.store)
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path.name == "channel":
                return special_result(info, stat.S_IFIFO)
            return info
        with patch.object(Path, "lstat", lstat):
            result = registry.run(tool["id"], self.task["id"], packet["id"], [], "run", timeout=1)
        self.assertEqual("completed", result["data"]["status"])
        self.assertFalse(result["data"]["evidence"]["complete"])
        replay = registry.run(tool["id"], self.task["id"], packet["id"], [], "run", timeout=1)
        self.assertEqual(result["id"], replay["id"])
        self.assertEqual(1, len(self.owner.store.list("run")))
        with self.assertRaises(FrameworkError):
            registry.restore(result["id"], "outputs/incomplete", "restore")

    def test_inspection_does_not_read_output_replaced_by_special_file(self):
        tool = self.register_tool()
        packet = self.worker.prepare(self.task["id"], "synthetic tool", "tool-context")
        registry = ToolRegistry(self.worker.store)
        result = registry.run(tool["id"], self.task["id"], packet["id"], [], "run")
        output = self.root / result["data"]["directory"] / "result.txt"
        with self.special_mode(output, stat.S_IFIFO):
            inspected = registry.inspect_evidence(result["id"])
        self.assertTrue(inspected["snapshots_valid"])
        self.assertFalse(inspected["current_matches"])


if __name__ == "__main__":
    unittest.main()
