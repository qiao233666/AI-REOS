"""语义身份检索回归；只使用内存SQLite与确定性假客户端。"""
import sqlite3
import unittest
from types import SimpleNamespace

from aireos.errors import FrameworkError
from aireos.semantic import SemanticIndex
from aireos.storage import canonical, digest


def make_index(rows, points, encoder=None):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE memory_queries(signature TEXT,query_hash TEXT,vector TEXT);"
        "CREATE TABLE memory_vectors(signature TEXT,seq INTEGER,part INTEGER,point TEXT,checksum TEXT);"
        "CREATE TABLE memory_docs(seq INTEGER,id TEXT,rev INTEGER,kind TEXT,task TEXT,body_hash TEXT);"
    )
    connection.execute("INSERT INTO memory_queries VALUES(?,?,?)", ("stub", digest("query"), canonical([1.0])))
    for seq, identifier, revision in rows:
        connection.execute("INSERT INTO memory_docs VALUES(?,?,?,?,?,?)", (seq, identifier, revision, "item", "", "hash"))
    for vector_point in points:
        payload = vector_point.payload
        connection.execute("INSERT INTO memory_vectors VALUES(?,?,?,?,?)", ("stub", payload["seq"], payload.get("part", 0), vector_point.id, "checked"))

    class FakeClient:
        def __init__(self):
            self.requested = []

        def query_points(self, collection, query, limit, with_payload):
            self.requested.append(limit)
            return SimpleNamespace(points=points[:limit])

    index = SemanticIndex.__new__(SemanticIndex)
    index.tx = SimpleNamespace(connection=connection)
    index.signature = "stub"
    index.collection = "stub"
    index.client = FakeClient()
    index.encoder = encoder
    index.query_encoded = False
    return index, connection


def point(number, seq, score, part=0):
    return SimpleNamespace(id=str(number), score=score,
        payload={"seq": seq, "part": part, "checksum": "checked"})


class SemanticIdentityTests(unittest.TestCase):
    def test_history_versions_do_not_exclude_next_identity(self):
        rows = [(seq, "A" if seq <= 48 else "B", seq if seq <= 48 else 1) for seq in range(1, 50)]
        points = [point(seq, seq, 1.0 - seq / 1000) for seq in range(1, 50)]
        index, connection = make_index(rows, points)
        try:
            hits = index.search("query", 2)
            self.assertEqual([(row["id"], row["rev"]) for row in hits], [("A", 1), ("B", 1)])
            self.assertGreater(len(index.client.requested), 1)
            self.assertGreaterEqual(index.search_metadata["points_scanned"], 49)
            self.assertFalse(index.search_metadata["scan_limit_reached"])
        finally:
            connection.close()

    def test_identity_quota_selects_best_matching_version(self):
        rows = [(1, "A", 1), (2, "A", 2), (3, "B", 1)]
        points = [point(1, 1, 0.99), point(2, 2, 0.95), point(3, 3, 0.80)]
        index, connection = make_index(rows, points)
        try:
            hits = index.search("query", 2)
            self.assertEqual([(row["id"], row["rev"]) for row in hits], [("A", 1), ("B", 1)])
            self.assertEqual(index.search_metadata["identity_pool_size"], 2)
        finally:
            connection.close()

    def test_corrupt_cached_query_vector_fails_closed(self):
        index, connection = make_index([], [])
        try:
            connection.execute("UPDATE memory_queries SET vector=?", ('{"bad":true}',))
            with self.assertRaises(FrameworkError):
                index.search("query", 1)
            self.assertEqual(index.client.requested, [])
        finally:
            connection.close()

    def test_point_checksum_must_match_local_cache_mapping(self):
        stale = point(1, 1, 0.9)
        stale.payload["checksum"] = "stale"
        index, connection = make_index([(1, "B", 1)], [stale])
        try:
            self.assertEqual(index.search("query", 1), [])
            self.assertEqual(index.search_metadata["invalid_points_skipped"], 1)
        finally:
            connection.close()

    def test_scan_cap_is_reported_without_claiming_complete_identity_pool(self):
        from aireos.semantic import MAX_SEMANTIC_POINT_SCAN
        rows = [(seq, "A", seq) for seq in range(1, MAX_SEMANTIC_POINT_SCAN + 1)]
        points = [point(seq, seq, 0.9 - seq / 100000) for seq in range(1, MAX_SEMANTIC_POINT_SCAN + 1)]
        index, connection = make_index(rows, points)
        try:
            hits = index.search("query", 2)
            self.assertEqual(len(hits), 1)
            self.assertEqual(index.search_metadata["points_scanned"], MAX_SEMANTIC_POINT_SCAN)
            self.assertTrue(index.search_metadata["scan_limit_reached"])
            self.assertTrue(index.search_metadata["identity_pool_truncated"])
        finally:
            connection.close()

    def test_expansion_keeps_valid_candidates_when_results_are_not_prefix_stable(self):
        rows = [(seq, "A", seq) for seq in range(1, 65)] + [(65, "B", 1), (66, "C", 1)]
        a_points = [point(seq, seq, 0.999 - seq / 10000) for seq in range(1, 65)]
        b, c = point(65, 65, 0.98), point(66, 66, 0.7)
        index, connection = make_index(rows, a_points + [b, c])
        def non_prefix(collection, query, limit, with_payload):
            index.client.requested.append(limit)
            return SimpleNamespace(points=a_points[:31] + [b] if limit == 32 else
                a_points if limit == 64 else a_points + [c])
        index.client.query_points = non_prefix
        try:
            hits = index.search("query", 2)
            self.assertEqual(["A", "B"], [row["id"] for row in hits])
            self.assertTrue(index.search_metadata["identity_pool_truncated"])
            self.assertLessEqual(index.search_metadata["points_scanned"], index.search_metadata["point_scan_limit"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
