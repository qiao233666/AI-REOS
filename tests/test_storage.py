"""验证事务、交错重试、权限和跨进程共享权威。"""
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

try:
    from aireos.storage import Store
    from aireos.errors import ConflictError, PermissionDenied
except ImportError:
    Store = None


def concurrent_append(root, token, number):
    store = Store(root, token)
    store.create("note", {"number": number}, request_id=f"parallel-{number}")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Store, "事务存储尚未实现")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        result = Store.initialize(self.root)
        self.token = Path(result["credential_file"]).read_text().strip()
        self.store = Store(self.root, self.token)

    def test_initialize_refuses_overwrite(self):
        original = (self.root / ".aireos/owner.key").read_bytes()
        with self.assertRaises(ConflictError):
            Store.initialize(self.root)
        self.assertEqual(original, (self.root / ".aireos/owner.key").read_bytes())

    def test_interleaved_retries_survive_restart(self):
        first = self.store.create("note", {"text": "A"}, request_id="A")
        self.store.create("note", {"text": "B"}, request_id="B")
        restarted = Store(self.root, self.token)
        retry = restarted.create("note", {"text": "A"}, request_id="A")
        self.assertEqual(first["id"], retry["id"])
        self.assertEqual(2, len(restarted.list("note")))
        self.assertEqual(2, len(restarted.events()))

    def test_same_request_changed_body_rejected_after_other_request(self):
        self.store.create("note", {"text": "A"}, request_id="A")
        self.store.create("note", {"text": "B"}, request_id="B")
        with self.assertRaises(ConflictError):
            self.store.create("note", {"text": "changed"}, request_id="A")

    def test_failure_rolls_back_objects_versions_events_and_request(self):
        def fail(tx):
            tx.create("note", {"text": "must not appear"})
            raise RuntimeError("injected failure")
        with self.assertRaisesRegex(RuntimeError, "injected"):
            self.store.mutate("failure", {}, "request-failure", "write", fail)
        self.assertEqual([], self.store.list())
        self.assertEqual([], self.store.events())
        result = self.store.mutate("failure", {}, "request-failure", "write", lambda tx: tx.create("note", {"text": "committed"}))
        self.assertEqual("committed", result["data"]["text"])

    def test_versions_preserved_and_old_revision_rejected(self):
        obj = self.store.create("note", {"text": "old"}, request_id="old")
        current = self.store.replace(obj["id"], 1, {"text": "new"}, request_id="new")
        self.assertEqual(2, current["rev"])
        self.assertEqual("old", self.store.get(obj["id"], 1)["data"]["text"])
        with self.assertRaises(ConflictError):
            self.store.replace(obj["id"], 1, {"text": "late"}, request_id="late")

    def test_actor_name_cannot_grant_permission(self):
        grant = self.store.grant("reader", ["execute"], ["outputs"], request_id="grant-reader")
        token = Path(grant["credential_file"]).read_text().strip()
        worker = Store(self.root, token)
        with self.assertRaises(PermissionDenied):
            worker.create("decision", {"actor": "owner"}, request_id="forge-owner")
        with self.assertRaises(PermissionDenied):
            Store(self.root, "reader").create("note", {}, request_id="name-is-not-key")

    def test_snapshots_keep_actual_bytes(self):
        digest = self.store.snapshot(b"original material")
        self.assertEqual(b"original material", self.store.blob(digest))
        self.assertEqual(digest, self.store.snapshot(b"original material"))

    def test_multiple_processes_use_same_authority(self):
        ctx = multiprocessing.get_context("spawn")
        workers = [ctx.Process(target=concurrent_append, args=(str(self.root), self.token, n)) for n in range(8)]
        for proc in workers:
            proc.start()
        for proc in workers:
            proc.join(20)
            self.assertFalse(proc.is_alive())
            self.assertEqual(0, proc.exitcode)
        self.assertEqual(8, len(self.store.list("note")))
        self.assertEqual(8, len({x["id"] for x in self.store.list("note")}))


if __name__ == "__main__":
    unittest.main()
