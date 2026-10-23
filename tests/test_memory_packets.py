"""上下文显示应精简，但版本与复用状态必须可追溯。"""
from pathlib import Path
import tempfile
import unittest

from aireos.storage import Store
from aireos.project import Project
from aireos.semantic import MemoryService


class MemoryPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        init = Store.initialize(self.root)
        self.owner_token = Path(init["credential_file"]).read_text(encoding="utf-8").strip()
        self.owner = Project(self.root, self.owner_token)
        worker_grant = self.owner.store.grant("worker", ["execute"], ["outputs"], "grant-worker")
        self.worker = Project(self.root, Path(worker_grant["credential_file"]).read_text(encoding="utf-8").strip())
        reviewer_grant = self.owner.store.grant("reviewer", ["review"], ["outputs"], "grant-reviewer")
        self.reviewer = Project(self.root, Path(reviewer_grant["credential_file"]).read_text(encoding="utf-8").strip())
        self.task = self.owner.create_task(
            "Synthetic memory packet test", "Check context packet behavior with synthetic text",
            ["Relevant text and version references are retained"], ["outputs"], request_id="task",
        )
        first = self.worker.prepare(self.task["id"], "synthetic memory packet", request_id="initial-context")
        self.worker.start_task(self.task["id"], first["id"], request_id="start")

    def add_unknown(self, title, text, request_id):
        return self.worker.add_item(
            title, text, "unknown", "Synthetic test text only", [], self.task["id"], request_id=request_id,
        )

    @staticmethod
    def context_entry(packet, item_id):
        return next(entry for entry in packet["context"] if entry.get("id") == item_id)

    def test_default_brief_finds_query_in_long_text_and_receipt_binds_full_revision(self):
        query = "tailneedle-query-marker"
        original = ("padding text without the query. " * 500) + f"The relevant phrase is {query}."
        item = self.add_unknown("Long synthetic note", original, "long-note")

        packet = self.worker.prepare(self.task["id"], query, request_id="brief-default")

        entry = self.context_entry(packet, item["id"])
        self.assertIn(query, entry["body"])
        self.assertLess(len(entry["body"]), len(original))
        reference = next(ref for ref in packet["data"]["refs"] if ref["id"] == item["id"])
        self.assertEqual(item["rev"], reference["rev"])
        self.assertEqual(item["rev"], entry["rev"])
        self.assertEqual("brief", packet["view"], "brief must be the default view")

    def test_repeated_revisions_do_not_consume_all_memory_query_candidates(self):
        phrase = "shared retrieval needle"
        revised = self.add_unknown("Repeated synthetic record", phrase + " original", "repeat-record")
        for revision in range(1, 5):
            revised = self.worker.revise_item(
                revised["id"], revision, f"{phrase} revised version {revision}",
                request_id=f"repeat-revision-{revision}",
            )
        other = self.add_unknown("Other matching record", phrase + " separate record", "other-record")

        result = MemoryService(self.owner.store).query(phrase, limit=2)

        match_ids = [entry["id"] for entry in result["matches"]]
        self.assertEqual(len(match_ids), len(set(match_ids)))
        self.assertIn(revised["id"], match_ids)
        self.assertIn(other["id"], match_ids)

    def test_many_high_scoring_versions_cannot_hide_a_second_record(self):
        other = self.add_unknown("相关长记录", "commonneedle " + "填充 " * 1000, "other-long")
        repeated = self.add_unknown("commonneedle commonneedle", "commonneedle", "repeated")
        for revision in range(1, 8):
            repeated = self.worker.revise_item(repeated["id"], revision, "commonneedle " + str(revision), "repeated-" + str(revision))
        result = MemoryService(self.owner.store).query("commonneedle", limit=2)
        self.assertIn(other["id"], [entry["id"] for entry in result["matches"]])

    def test_brief_display_has_source_revision_current_status_and_budget(self):
        text = "brief-budget-marker " + ("padding. " * 800)
        item = self.add_unknown("Budgeted synthetic item", text, "budgeted-item")
        max_chars = 1000

        packet = self.worker.prepare(
            self.task["id"], "brief-budget-marker", request_id="brief-budget",
            max_chars=max_chars, view="brief",
        )

        entry = self.context_entry(packet, item["id"])
        self.assertEqual(item["id"], entry["id"])
        self.assertEqual(item["rev"], entry["rev"])
        self.assertIn(item["id"], packet["reuse_status"])
        self.assertIn("status", packet["reuse_status"][item["id"]])
        displayed = sum(len(row.get("body", "")) for row in packet["context"])
        displayed += sum(len(str(status)) for status in packet["reuse_status"].values())
        self.assertLessEqual(displayed, max_chars)

    def test_full_view_or_versioned_get_recovers_complete_original_text(self):
        original = "full-view-marker " + ("complete synthetic source text. " * 300)
        item = self.add_unknown("Full synthetic note", original, "full-note")

        packet = self.worker.prepare(
            self.task["id"], "full-view-marker", request_id="full-view",
            max_chars=50000, view="full",
        )

        entry = self.context_entry(packet, item["id"])
        self.assertIn("body", entry)
        self.assertEqual(original, entry["body"])
        recovered = self.owner.store.get(item["id"], item["rev"])
        self.assertEqual(original, recovered["data"]["text"])

    def test_registered_text_file_is_searchable_without_ai_rewriting_it(self):
        (self.root / "outputs").mkdir()
        path = self.root / "outputs/material.md"
        path.write_text("原始材料中的 unique_document_needle，不需要另写摘要。", encoding="utf-8")
        item = self.worker.add_artifact("outputs/material.md", "细节材料", self.task["id"], "document")
        result = MemoryService(self.owner.store).query("unique_document_needle")
        self.assertIn(item["id"], [entry["id"] for entry in result["matches"]])

    def test_binary_file_has_explicit_metadata_only_search_coverage(self):
        (self.root / "outputs").mkdir()
        (self.root / "outputs/binary.txt").write_bytes(b"\xff\xfe\x00\x00")
        item = self.worker.add_artifact("outputs/binary.txt", "二进制示例", self.task["id"], "binary")
        self.assertIn("search_coverage", item["data"])
        self.assertEqual("metadata_only", item["data"]["search_coverage"]["mode"])

    def test_exact_record_identity_does_not_need_semantic_encoder(self):
        item = self.add_unknown("精确定位", "已有正式身份", "exact")
        self.owner.store.create("memory_config", {"mode": "hybrid"}, "synthetic-hybrid-config")
        result = MemoryService(self.owner.store).query(item["id"])
        self.assertEqual(item["id"], result["matches"][0]["id"])
        self.assertEqual("exact_identity", result["retrieval"]["route"])
        self.assertFalse(result["retrieval"]["semantic"])

    def test_truncated_title_is_explicitly_partial(self):
        self.add_unknown("很长的标题" * 50, "title_budget_needle", "long-title")
        result = MemoryService(self.owner.store).query("title_budget_needle")
        card = result["matches"][0]
        self.assertTrue(card["partial"])
        self.assertIn("title", card["fields_omitted"])


if __name__ == "__main__":
    unittest.main()
