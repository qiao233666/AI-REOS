"""冻结语料上的离线检索评测；研究材料只能写到明确指定的私有目录。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from .errors import FrameworkError
from .storage import Store, Transaction
from .memory_index import MemoryIndex


def evaluate(corpus_path, questions_path, output, mode="fts", model_name=None, model_directory=None):
    corpus_path, questions_path, output = Path(corpus_path), Path(questions_path), Path(output)
    if mode not in {"fts", "hybrid", "prototype"}:
        raise FrameworkError("评测模式为fts/hybrid/prototype")
    corpus_bytes, question_bytes = corpus_path.read_bytes(), questions_path.read_bytes()
    documents = [json.loads(line) for line in corpus_bytes.decode("utf-8").splitlines() if line.strip()]
    questions = json.loads(question_bytes.decode("utf-8"))
    identities = {entry.get("doc_id") for entry in documents}
    if not documents or len(identities) != len(documents) or None in identities or not questions:
        raise FrameworkError("语料身份必须唯一、题目不能为空")
    for entry in documents:
        if not isinstance(entry.get("text"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry.get("source_sha256", "")):
            raise FrameworkError("每条评测材料需要原文与来源摘要")
    seen = set()
    for question in questions:
        expected = question.get("relevant_doc_ids")
        if not isinstance(expected, list) or not expected or not set(expected) <= identities or question.get("question_id") in seen:
            raise FrameworkError("每题需唯一身份和语料中已标注的相关材料")
        if not isinstance(question.get("query"), str) or not question["query"].strip():
            raise FrameworkError("评测问题不能为空")
        seen.add(question["question_id"])
    if output.exists():
        raise FrameworkError("评测输出已存在；保留旧结果并使用新目录")
    output.mkdir(parents=True)
    initialized = Store.initialize(output / "authority")
    store = Store(output / "authority", Path(initialized["credential_file"]).read_text().strip())
    def ingest(tx):
        for entry in documents:
            tx.create("item", dict(type="claim", title=entry["doc_id"], text=entry["text"],
                source_doc_id=entry["doc_id"], provenance={key: value for key, value in entry.items() if key not in {"text", "doc_id"}},
                classification="unknown", scope="仅冻结评测材料，不是新增科研结论", task="evaluation-source", producer="evaluation", supports=[]))
        return {"count": len(documents)}
    store.mutate("evaluation.ingest", {"corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest()}, "ingest", "write", ingest, corpus=True)
    if mode == "hybrid":
        from .semantic import MemoryService
        MemoryService(store).configure("hybrid", "configure", model_name=model_name, model_directory=model_directory, max_new_chunks=256)
    # 初始化索引与语义缓存，不把首题冷启动不完整与后面题目混为一个比较条件。
    with store.connection(write=True) as connection:
        tx = Transaction(store, connection, {})
        index = MemoryIndex(tx)
        index.refresh()
        if mode == "hybrid":
            from .semantic import SemanticIndex
            with SemanticIndex(tx, tx.list("memory_config")[-1]["data"]) as semantic:
                progress = semantic.sync()
                if not progress["embedding_complete"]:
                    raise FrameworkError("评测语料超过本次256切片预算；请分批预建索引后评测")
    results = []
    started = time.perf_counter()
    for question in questions:
        tick = time.perf_counter()
        with store.connection(write=True) as connection:
            tx = Transaction(store, connection, {})
            if mode == "prototype":
                from .recall import terms
                query_terms = terms(question["query"])
                pairs = [(len(query_terms & terms(str(obj["data"]))), obj) for obj in tx.list("item")]
                pairs = sorted((pair for pair in pairs if pair[0]), key=lambda pair: (-pair[0], pair[1]["data"]["source_doc_id"]))
                hits = [obj for _, obj in pairs[:10]]
                backend = {"backend": "prototype-bigram-scan"}
            else:
                found = MemoryIndex(tx).search(question["query"], 10)
                hits, backend = found["hits"], found["metadata"]
            retrieved = list(dict.fromkeys(obj["data"]["source_doc_id"] for obj in hits))
        relevant = set(question["relevant_doc_ids"])
        ranks = [rank for rank, identifier in enumerate(retrieved, 1) if identifier in relevant]
        results.append(dict(question_id=question["question_id"], query=question["query"], type=question.get("type", "unspecified"),
            expected=sorted(relevant), retrieved=retrieved, first_relevant_rank=min(ranks) if ranks else None,
            hit_at_1=bool(relevant & set(retrieved[:1])), hit_at_3=bool(relevant & set(retrieved[:3])), hit_at_5=bool(relevant & set(retrieved[:5])),
            reciprocal_rank=1.0 / min(ranks) if ranks else 0.0, seconds=time.perf_counter() - tick, backend=backend))
    def metrics(entries):
        return {"questions": len(entries), **{name: sum(float(entry[name]) for entry in entries) / len(entries) for name in ("hit_at_1", "hit_at_3", "hit_at_5")},
            "mrr": sum(entry["reciprocal_rank"] for entry in entries) / len(entries), "mrr_cutoff": 10}
    report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(), mode=mode,
        corpus_sha256=hashlib.sha256(corpus_bytes).hexdigest(), questions_sha256=hashlib.sha256(question_bytes).hexdigest(),
        documents=len(documents), metrics=metrics(results),
        by_type={kind: metrics([entry for entry in results if entry["type"] == kind]) for kind in sorted({entry["type"] for entry in results})},
        seconds=time.perf_counter() - started, dialog_model_calls=0, results=results,
        limitations=["小规模已标注材料定位，不是总体召回率或科学正确性", "未标注材料不作为负例", "自然问法由整理者给出，仍需使用者审核", "时长为本机本次观测，不是标准硬件基准"])
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text("# 本地记忆离线评测\n\n" +
        f"模式：{mode}；材料{len(documents)}条；问题{len(results)}道。\n\n" +
        f"Hit@1={report['metrics']['hit_at_1']:.3f}，Hit@3={report['metrics']['hit_at_3']:.3f}，Hit@5={report['metrics']['hit_at_5']:.3f}，MRR@10={report['metrics']['mrr']:.3f}。\n\n" +
        "只核对已标注材料能否找回；未标注项不作为负例。语料、题目摘要和逐题结果见result.json。不能据此证明整体召回效果或科研正确性。没有调用对话模型。\n", encoding="utf-8")
    return report
