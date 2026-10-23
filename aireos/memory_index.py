"""可重建的SQLite FTS5索引；语义通道为可选的成熟本地组件。"""
import json
import re
import sqlite3

from .errors import FrameworkError
from .storage import canonical, digest

KINDS = {"item", "case", "stage", "tool", "decision", "review", "resource"}


def relations(data):
    result = []
    for field in ("supports", "supersedes", "evidence", "items"):
        for ref in data.get(field, []):
            if isinstance(ref, dict) and isinstance(ref.get("id"), str):
                result.append((ref["id"], field))
    if isinstance(data.get("target"), dict) and data["target"].get("id"):
        result.append((data["target"]["id"], "target"))
    return result


def phrases(query):
    latin = set(re.findall(r"[a-z0-9_]{2,}", query.lower()))
    tokens = {token for token in latin if len(token) >= 3}
    short = sorted(latin - tokens)
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        if len(run) >= 3:
            tokens.update(run[index:index + 3] for index in range(len(run) - 2))
        elif len(run) == 2:
            short.append(run)
    return sorted(tokens)[:64], short[:8]


class MemoryIndex:
    def __init__(self, tx):
        self.tx = tx
        self.connection = tx.connection
        try:
            self.connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(title,body,tokenize='trigram')")
            self.connection.execute("CREATE TABLE IF NOT EXISTS memory_docs(seq INTEGER PRIMARY KEY,id TEXT,rev INTEGER,kind TEXT,task TEXT,body_hash TEXT)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS memory_edges(source TEXT,rev INTEGER,target TEXT,relation TEXT,PRIMARY KEY(source,rev,target,relation))")
            self.connection.execute("CREATE INDEX IF NOT EXISTS memory_edges_target ON memory_edges(target)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS memory_cursor(singleton INTEGER PRIMARY KEY CHECK(singleton=1),seq INTEGER NOT NULL)")
        except sqlite3.OperationalError as exc:
            raise FrameworkError("需要支持FTS5 trigram的SQLite；不能静默回退成另一个检索器") from exc

    def refresh(self):
        cursor = self.connection.execute("SELECT seq FROM memory_cursor WHERE singleton=1").fetchone()
        watermark = cursor[0] if cursor else 0
        rows = self.connection.execute("SELECT *,rowid AS ordinal FROM versions WHERE rowid>? ORDER BY rowid", (watermark,)).fetchall()
        indexed, maximum = 0, watermark
        for row in rows:
            maximum = row["ordinal"]
            if row["kind"] not in KINDS:
                continue
            data = json.loads(row["payload"])
            title = str(data.get("title", data.get("summary", data.get("reason", ""))))
            body = row["id"] + "\n" + canonical(data)
            self.connection.execute("INSERT INTO memory_docs VALUES(?,?,?,?,?,?)", (row["ordinal"], row["id"], row["rev"], row["kind"], str(data.get("task", "")), digest(body)))
            self.connection.execute("INSERT INTO memory_fts(rowid,title,body) VALUES(?,?,?)", (row["ordinal"], title, body))
            for target, relation in relations(data):
                self.connection.execute("INSERT OR IGNORE INTO memory_edges VALUES(?,?,?,?)", (row["id"], row["rev"], target, relation))
            indexed += 1
        self.connection.execute("INSERT INTO memory_cursor VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET seq=excluded.seq", (maximum,))
        return {"indexed": indexed, "watermark": maximum, "backend": "sqlite-fts5"}

    def search(self, query, limit=12):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise FrameworkError("检索条数必须为1至1000")
        state = self.refresh()
        candidate_limit = min(8192, max(256, limit * 32))
        def distinct(entries):
            chosen, seen = [], set()
            for row in entries:
                if row["id"] not in seen:
                    chosen.append(row)
                    seen.add(row["id"])
            return chosen
        tokens, short = phrases(query)
        rows = []
        if tokens:
            expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            # rank是FTS5的BM25列；先在每个身份内选最佳版本，再限制身份数量。
            # 不能先截取版本，也不能在窗口排序里调用bm25()辅助函数。
            rows = self.connection.execute("""SELECT * FROM (
                SELECT d.*,memory_fts.rank AS score,
                    ROW_NUMBER() OVER (PARTITION BY d.id ORDER BY memory_fts.rank,d.seq DESC) AS position
                FROM memory_fts JOIN memory_docs d ON d.seq=memory_fts.rowid WHERE memory_fts MATCH ?
                ) WHERE position=1 ORDER BY score,seq DESC LIMIT ?""", (expression, candidate_limit + 1)).fetchall()
        pool_reached = len(rows) > candidate_limit
        rows = rows[:candidate_limit]
        if short:
            clause = " OR ".join("body LIKE ? ESCAPE '\\'" for _ in short)
            escaped = [part.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') for part in short]
            more = self.connection.execute("""SELECT * FROM (
                SELECT d.*,0 AS score,ROW_NUMBER() OVER (PARTITION BY d.id ORDER BY d.seq DESC) AS position
                FROM memory_fts JOIN memory_docs d ON d.seq=memory_fts.rowid WHERE """ + clause +
                " ) WHERE position=1 ORDER BY seq DESC LIMIT ?", [*('%' + part + '%' for part in escaped), candidate_limit + 1]).fetchall()
            pool_reached = pool_reached or len(more) > candidate_limit
            more = more[:candidate_limit]
            seen = {row["seq"] for row in rows}
            rows += [row for row in more if row["seq"] not in seen]
        exact = self.connection.execute("SELECT *,0 AS score FROM memory_docs WHERE id=? ORDER BY rev DESC LIMIT 1", (query.strip(),)).fetchall()
        ranked = distinct(list(exact) + rows)
        metadata = {**state, "semantic": False, "embedding_complete": True, "route": "exact_identity" if exact else "fts",
            "ranking_unit": "object", "version_scan_limit": None, "version_scan_limit_reached": False,
            "candidate_pool_limit": candidate_limit, "candidate_pool_limit_reached": pool_reached}
        configs = self.tx.list("memory_config")
        hybrid = bool(configs and configs[-1]["data"].get("mode") == "hybrid")
        semantic_scan = {}
        if hybrid and exact:
            metadata["embedding_complete"] = None
        if hybrid and not exact:
            from .semantic import SemanticIndex
            try:
                with SemanticIndex(self.tx, configs[-1]["data"]) as semantic:
                    synced = semantic.sync()
                    vector_rows = distinct(semantic.search(query, min(256, max(32, limit * 4))))
                    synced["query_encoded"] = semantic.query_encoded
                    semantic_scan = semantic.search_metadata
            except FrameworkError:
                raise
            except Exception as exc:
                raise FrameworkError("本地语义缓存不可用；请核查配置或在副本重建缓存，没有静默降级: " + str(exc)) from exc
            scores = {}
            by_id = {row["id"]: row for row in reversed(ranked + vector_rows)}
            for channel in (ranked, vector_rows):
                for rank, row in enumerate(channel):
                    scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (60 + rank + 1)
            ranked = [by_id[identifier] for identifier in sorted(scores, key=lambda identifier: (-scores[identifier], -by_id[identifier]["seq"]))]
            metadata.update(backend="sqlite-fts5+qdrant", semantic=True, route="hybrid", **synced)
        # 明确身份查询不应被语义近似结果挤到后面。
        ranked = distinct(list(exact) + ranked)
        hits = [self.tx.get(row["id"], row["rev"]) for row in ranked[:limit]]
        metadata.update(final_output_limit=limit, final_output_size=len(hits),
            final_output_truncated=len(ranked) > limit)
        if semantic_scan:
            metadata.update(semantic_point_scan_limit=semantic_scan["point_scan_limit"],
                semantic_points_scanned=semantic_scan["points_scanned"],
                semantic_points_returned=semantic_scan["points_returned"],
                semantic_point_result_limit=semantic_scan["point_result_limit"],
                semantic_scan_limit_reached=semantic_scan["scan_limit_reached"],
                semantic_identity_pool_limit=semantic_scan["identity_pool_limit"],
                semantic_identity_pool_size=semantic_scan["identity_pool_size"],
                semantic_identity_pool_truncated=semantic_scan["identity_pool_truncated"],
                semantic_invalid_points_skipped=semantic_scan["invalid_points_skipped"])
        return {"hits": hits, "truncated": len(ranked) > limit, "metadata": metadata}

    def related(self, identifier):
        # 当前关系用于提示现在的纠错；旧版的依据仍可按其ref精确读取。
        rows = self.connection.execute("""SELECT e.* FROM memory_edges e JOIN objects o
            ON e.source=o.id AND e.rev=o.rev
            WHERE e.source=? OR (e.target=? AND e.relation IN ('supersedes','target'))""", (identifier, identifier)).fetchall()
        return {row["target"] if row["source"] == identifier else row["source"] for row in rows}
