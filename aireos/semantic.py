"""本地FastEmbed编码与Qdrant嵌入式检索；不下载模型，不调用对话模型。"""
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import uuid

from .errors import FrameworkError
from .storage import Transaction, canonical, digest


MAX_SEMANTIC_POINT_SCAN = 4096


def chunks(text, size=360, overlap=60):
    if not 0 <= overlap < size:
        raise FrameworkError("切片重叠必须小于切片长度")
    if not text:
        return []
    return [(start, min(start + size, len(text)), text[start:start + size]) for start in range(0, len(text), size - overlap)]


def model_fingerprint(directory):
    path = Path(directory).resolve()
    if not path.is_dir() or not list(path.rglob("*.onnx")):
        raise FrameworkError("语义模型须已预置在本地目录；运行时不会下载")
    files = []
    for source in sorted(path.rglob("*")):
        if not source.is_file():
            continue
        checksum = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(block)
        files.append({"path": source.relative_to(path).as_posix(), "sha256": checksum.hexdigest()})
    return digest(files)


@lru_cache(maxsize=2)
def load_encoder(name, directory, fingerprint):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise FrameworkError("语义通道需要可选依赖：在独立环境安装qdrant-client[fastembed]") from exc
    try:
        return TextEmbedding(name, specific_model_path=directory, local_files_only=True,
            cache_dir=str(Path(directory).parent), threads=1, providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise FrameworkError("本地编码器加载失败；没有回退或联网下载: " + str(exc)) from exc


def semantic_text(data):
    values = []
    for key in ("title", "text", "search_text", "summary", "reason", "scope", "method", "checks", "aliases", "purpose", "limitations"):
        value = data.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(entry for entry in value if isinstance(entry, str))
    if isinstance(data.get("manifest"), dict):
        values.append(semantic_text(data["manifest"]))
    return "\n".join(values) or canonical(data)


class SemanticIndex:
    def __init__(self, tx, config):
        self.tx, self.config = tx, config
        path = config["model_directory"]
        if model_fingerprint(path) != config["model_fingerprint"]:
            raise FrameworkError("编码模型内容变化，须重新登记配置，不能混用旧向量")
        if any(importlib.metadata.version(name) != version for name, version in config["components"].items()):
            raise FrameworkError("语义组件版本变化，须重新登记配置，不能混用旧缓存")
        self.encoder = load_encoder(config["model_name"], path, config["model_fingerprint"])
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:
            raise FrameworkError("缺少Qdrant可选依赖") from exc
        self.models = models
        self.signature = digest({"project": tx.store.project_id(tx.connection), "config": config})
        self.collection = "aireos_memory"
        self.client = QdrantClient(path=str(tx.store.home / ("semantic-" + self.signature[:20])))
        try:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=config["dimension"], distance=models.Distance.COSINE))
        except Exception:
            self.client.close()
            raise
        tx.connection.execute("CREATE TABLE IF NOT EXISTS memory_vectors(signature TEXT,seq INTEGER,part INTEGER,point TEXT,checksum TEXT,PRIMARY KEY(signature,seq,part))")
        tx.connection.execute("CREATE TABLE IF NOT EXISTS memory_queries(signature TEXT,query_hash TEXT,vector TEXT,PRIMARY KEY(signature,query_hash))")
        self.query_encoded = False

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.client.close()

    def sync(self):
        desired = []
        for row in self.tx.connection.execute("SELECT * FROM memory_docs ORDER BY seq"):
            obj = self.tx.get(row["id"], row["rev"])
            for part, (start, end, fragment) in enumerate(chunks(semantic_text(obj["data"]))):
                checksum = digest({"document": row["body_hash"], "start": start, "end": end, "text": fragment})
                point = str(uuid.uuid5(uuid.NAMESPACE_URL, self.signature + ":" + str(row["seq"]) + ":" + str(part) + ":" + checksum))
                desired.append(dict(seq=row["seq"], part=part, point=point, checksum=checksum, text=fragment))
        known = {}
        for offset in range(0, len(desired), 128):
            points = self.client.retrieve(self.collection, ids=[entry["point"] for entry in desired[offset:offset + 128]], with_payload=True, with_vectors=False)
            known.update({str(point.id): point.payload for point in points})
        pending = [entry for entry in desired if known.get(entry["point"], {}).get("checksum") != entry["checksum"]]
        selected = pending[:self.config["max_new_chunks"]]
        if selected:
            vectors = list(self.encoder.embed([entry["text"] for entry in selected], batch_size=8))
            points = [self.models.PointStruct(id=entry["point"], vector=[float(value) for value in vector],
                payload={"seq": entry["seq"], "part": entry["part"], "checksum": entry["checksum"]}) for entry, vector in zip(selected, vectors)]
            self.client.upsert(self.collection, points=points, wait=True)
        for entry in desired:
            if entry in selected or entry["point"] in known:
                self.tx.connection.execute("INSERT OR REPLACE INTO memory_vectors VALUES(?,?,?,?,?)", (self.signature, entry["seq"], entry["part"], entry["point"], entry["checksum"]))
        return {"embedded_chunks": len(selected), "pending_chunks": len(pending) - len(selected),
            "embedding_complete": len(pending) == len(selected), "model": self.config["model_name"],
            "model_fingerprint": self.config["model_fingerprint"]}

    def search(self, query, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise FrameworkError("语义身份候选数必须为1至1000")
        key = digest(query)
        cached = self.tx.connection.execute("SELECT vector FROM memory_queries WHERE signature=? AND query_hash=?", (self.signature, key)).fetchone()
        if cached:
            try:
                vector = json.loads(cached[0])
                if (not isinstance(vector, list) or not vector or
                        any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                            for value in vector) or
                        (getattr(self, "config", {}).get("dimension") is not None and
                         len(vector) != self.config["dimension"])):
                    raise ValueError("向量结构或维度无效")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise FrameworkError("语义查询向量缓存损坏；请在副本重建缓存") from exc
        else:
            vector = [float(value) for value in next(self.encoder.query_embed(query))]
            if (not vector or any(not math.isfinite(value) for value in vector) or
                    (getattr(self, "config", {}).get("dimension") is not None and
                     len(vector) != self.config["dimension"])):
                raise FrameworkError("编码器返回无效查询向量")
            self.tx.connection.execute("INSERT INTO memory_queries VALUES(?,?,?)", (self.signature, key, canonical(vector)))
            self.query_encoded = True
        target_identities = limit + 1
        top_k = min(MAX_SEMANTIC_POINT_SCAN, max(32, target_identities * 4))
        latest_points, scan_limit_reached = [], False
        invalid_points = 0
        seen_points, best_by_seq = set(), {}
        returned_points = 0
        while True:
            result = self.client.query_points(self.collection, query=vector, limit=top_k, with_payload=True)
            latest_points = list(result.points)
            if len(latest_points) > top_k:
                raise FrameworkError("语义索引超过请求的点数预算")
            returned_points += len(latest_points)
            # Qdrant 对同分结果的内部顺序不作为契约；本地用点ID做稳定次序。
            try:
                def point_order(item):
                    score = item.score
                    valid = (not isinstance(score, bool) and isinstance(score, (int, float)) and
                             math.isfinite(score))
                    return (0, -float(score), str(item.id)) if valid else (1, 0.0, str(item.id))
                ordered = sorted(latest_points, key=point_order)
            except (TypeError, ValueError, AttributeError) as exc:
                raise FrameworkError("语义索引返回了无效分数或点") from exc

            for point in ordered:
                point_id = str(point.id)
                first_seen = point_id not in seen_points
                if first_seen:
                    if len(seen_points) >= MAX_SEMANTIC_POINT_SCAN:
                        scan_limit_reached = True
                        continue
                    seen_points.add(point_id)
                payload = point.payload
                if (not isinstance(payload, dict) or isinstance(payload.get("seq"), bool) or
                        not isinstance(payload.get("seq"), int) or payload["seq"] <= 0 or
                        not isinstance(payload.get("checksum"), str) or
                        isinstance(point.score, bool) or not isinstance(point.score, (int, float)) or
                        not math.isfinite(point.score)):
                    invalid_points += int(first_seen)
                    continue
                local = self.tx.connection.execute(
                    "SELECT seq,part,checksum FROM memory_vectors WHERE signature=? AND point=?",
                    (self.signature, point_id)).fetchone()
                if (not local or local["seq"] != payload["seq"] or
                        local["part"] != payload.get("part", 0) or
                        local["checksum"] != payload["checksum"]):
                    # 远端残留点或缓存映射不一致不进入候选；本地SQLite映射为准。
                    invalid_points += int(first_seen)
                    continue
                row = self.tx.connection.execute("SELECT * FROM memory_docs WHERE seq=?", (payload["seq"],)).fetchone()
                if not row:
                    invalid_points += int(first_seen)
                    continue
                previous = best_by_seq.get(payload["seq"])
                if previous is None or float(point.score) > previous["score"]:
                    best_by_seq[payload["seq"]] = {"row": row, "score": float(point.score), "point": point_id}

            # 多个切片先归并到版本，再在同一身份的版本中保留最佳匹配版本。
            best_by_id = {}
            for candidate in best_by_seq.values():
                row = candidate["row"]
                current = best_by_id.get(row["id"])
                rank = (candidate["score"], row["rev"], row["seq"])
                if current is None or rank > current["rank"]:
                    best_by_id[row["id"]] = {**candidate, "rank": rank}
            identities = sorted(best_by_id.values(), key=lambda item: (-item["score"], item["row"]["id"], -item["row"]["rev"]))
            if scan_limit_reached:
                break

            # 达到身份目标后，如果 top-k 边界同分，继续扩展以消除不稳定的截断。
            boundary_tie = False
            if len(latest_points) >= top_k and top_k < MAX_SEMANTIC_POINT_SCAN and latest_points:
                scores = [float(item.score) for item in latest_points
                    if not isinstance(item.score, bool) and isinstance(item.score, (int, float)) and math.isfinite(item.score)]
                try:
                    boundary = min(scores) if scores else None
                    boundary_tie = boundary is None or len(scores) != len(latest_points) or scores.count(boundary) > 1
                except (TypeError, ValueError, AttributeError):
                    boundary_tie = True
            if len(identities) >= target_identities and not boundary_tie:
                break
            if len(latest_points) < top_k:
                break
            if top_k >= MAX_SEMANTIC_POINT_SCAN:
                scan_limit_reached = True
                break
            top_k = min(MAX_SEMANTIC_POINT_SCAN, top_k * 2)

        selected = identities[:limit]
        self.search_metadata = {
            "point_scan_limit": MAX_SEMANTIC_POINT_SCAN,
            "points_scanned": len(seen_points),
            "points_returned": returned_points,
            "point_result_limit": 3 * MAX_SEMANTIC_POINT_SCAN,
            "scan_limit_reached": scan_limit_reached,
            "identity_pool_limit": limit,
            "identity_pool_size": len(selected),
            "identity_pool_truncated": len(identities) > limit or scan_limit_reached,
            "invalid_points_skipped": invalid_points,
        }
        return [item["row"] for item in selected]


class MemoryService:
    def __init__(self, store):
        self.store = store

    def configure(self, mode, request_id, model_name=None, model_directory=None, max_new_chunks=32):
        if mode not in {"fts", "hybrid"} or isinstance(max_new_chunks, bool) or not isinstance(max_new_chunks, int) or not 1 <= max_new_chunks <= 256:
            raise FrameworkError("记忆模式须为fts/hybrid，增量编码批次为1至256切片")
        request = dict(mode=mode, model_name=model_name, model_directory=model_directory, max_new_chunks=max_new_chunks)
        def apply(tx):
            config = {"mode": mode}
            if mode == "hybrid":
                if not isinstance(model_name, str) or not isinstance(model_directory, str):
                    raise FrameworkError("语义模式须明确已预置的本地模型")
                directory = str(Path(model_directory).resolve())
                fingerprint = model_fingerprint(directory)
                try:
                    from fastembed import TextEmbedding
                    spec = next((entry for entry in TextEmbedding.list_supported_models() if entry["model"] == model_name), None)
                    if spec is None:
                        raise FrameworkError("该模型不在已安装FastEmbed支持列表中")
                    versions = {name: importlib.metadata.version(name) for name in ("fastembed", "qdrant-client", "onnxruntime", "tokenizers", "numpy")}
                except ImportError as exc:
                    raise FrameworkError("语义依赖尚未安装") from exc
                load_encoder(model_name, directory, fingerprint)
                config.update(model_name=model_name, model_directory=directory, model_fingerprint=fingerprint,
                    dimension=spec["dim"], max_new_chunks=max_new_chunks, chunk_chars=360, overlap=60,
                    provider="CPUExecutionProvider", threads=1, components=versions)
            return tx.create("memory_config", config)
        return self.store.mutate("memory.configure", request, request_id, "plan", apply)

    def query(self, query, limit=12, max_chars=10000, view="brief"):
        if not isinstance(query, str) or not query.strip():
            raise FrameworkError("检索问题不能为空")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50000:
            raise FrameworkError("上下文显示预算必须为1000至50000字符")
        from .recall import search
        from .project import Project
        # 缓存更新受SQLite写事务串行化；读取不会生成新任务、收据或对话调用。
        with self.store.connection(write=True) as connection:
            tx = Transaction(self.store, connection, {})
            result = search(tx, query, limit)
            result["references"] = [{"id": obj["id"], "rev": obj["rev"]} for obj in result["matches"]]
            result["historical_references"] = [{"id": obj["id"], "rev": obj["rev"]} for obj in result["historical"]]
            from .memory_packet import present
            display = present(tx, result["matches"], result["historical"], query, max_chars, view)
            result.update({key: value for key, value in display.items() if key != "current"})
            result.update(matches=display["current"], retrieval_note="片段保留原文字节意义及字符位置；未显示内容按references中的id/rev用get补取。相似不证明正确。")
            return result
