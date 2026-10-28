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
        absence = question.get("expected_absence", False)
        if not isinstance(absence, bool):
            raise FrameworkError("expected_absence须为布尔值")
        if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected) or (not expected and not absence) or (expected and absence) or not set(expected) <= identities or question.get("question_id") in seen:
            raise FrameworkError("每题需唯一身份和语料中已标注的相关材料")
        if "required_doc_ids" in question:
            required = question["required_doc_ids"]
            if absence or not isinstance(required, list) or not required or any(not isinstance(item, str) for item in required) or len(set(required)) != len(required) or not set(required) <= identities:
                raise FrameworkError("必需材料须为语料中非空且不重复的编号；无材料题不得指定")
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
        required = set(question.get("required_doc_ids", question["relevant_doc_ids"]))
        missing = required - set(retrieved[:5])
        ranks = [rank for rank, identifier in enumerate(retrieved, 1) if identifier in relevant]
        results.append(dict(question_id=question["question_id"], query=question["query"], type=question.get("type", "unspecified"),
            expected=sorted(relevant), retrieved=retrieved, first_relevant_rank=min(ranks) if ranks else None,
            hit_at_1=bool(relevant & set(retrieved[:1])), hit_at_3=bool(relevant & set(retrieved[:3])), hit_at_5=bool(relevant & set(retrieved[:5])),
            required=sorted(required), missing_required_at_5=sorted(missing),
            required_recall_at_5=(len(required)-len(missing))/len(required) if required else None,
            required_complete_at_5=not missing if required else None,
            expected_absence=question.get("expected_absence", False),
            returned_candidates=retrieved if question.get("expected_absence", False) else [],
            reciprocal_rank=1.0 / min(ranks) if ranks else 0.0, seconds=time.perf_counter() - tick, backend=backend))
    def metrics(entries):
        labeled = [entry for entry in entries if entry["required_recall_at_5"] is not None]
        absence_entries = [entry for entry in entries if entry["expected_absence"]]
        return {"questions": len(entries), **{name: sum(float(entry[name]) for entry in entries) / len(entries) for name in ("hit_at_1", "hit_at_3", "hit_at_5")},
            "mrr": sum(entry["reciprocal_rank"] for entry in entries) / len(entries), "mrr_cutoff": 10,
            "required_recall_at_5": sum(entry["required_recall_at_5"] for entry in labeled)/len(labeled) if labeled else None,
            "required_complete_at_5": sum(entry["required_complete_at_5"] for entry in labeled)/len(labeled) if labeled else None,
            "required_questions":len(labeled), "no_evidence_questions":len(absence_entries),
            "returned_candidates":sum(len(entry["returned_candidates"]) for entry in absence_entries)}
    report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(), mode=mode,
        corpus_sha256=hashlib.sha256(corpus_bytes).hexdigest(), questions_sha256=hashlib.sha256(question_bytes).hexdigest(),
        documents=len(documents), metrics=metrics(results),
        by_type={kind: metrics([entry for entry in results if entry["type"] == kind]) for kind in sorted({entry["type"] for entry in results})},
        seconds=time.perf_counter() - started, dialog_model_calls=0, results=results,
        limitations=["必需集合齐全仅指已标注集合，不代表总体证据完备性或科学正确性", "无标注材料题的返回候选不自动判错，必需集合指标不计入其分母；旧命中指标仍包含这些题", "未标注材料不作为负例", "自然问法由整理者给出，仍需使用者审核", "时长为本机本次观测，不是标准硬件基准"])
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text("# 本地记忆离线评测\n\n" +
        f"模式：{mode}；材料{len(documents)}条；问题{len(results)}道。\n\n" +
        f"Hit@1={report['metrics']['hit_at_1']:.3f}，Hit@3={report['metrics']['hit_at_3']:.3f}，Hit@5={report['metrics']['hit_at_5']:.3f}，MRR@10={report['metrics']['mrr']:.3f}。\n\n" +
        f"必需材料集合召回@5={report['metrics']['required_recall_at_5']}，完整率@5={report['metrics']['required_complete_at_5']}；无标注材料题={report['metrics']['no_evidence_questions']}，其返回候选数={report['metrics']['returned_candidates']}。\n\n" +
        "集合齐全仅指已标注集合，不代表总体证据完备性。无标注材料题不计入必需集合指标分母，候选返回不自动判错；旧命中指标仍包含这些题。未标注项不作为负例。语料、题目摘要和逐题结果见result.json。不能据此证明整体召回效果或科研正确性。没有调用对话模型。\n", encoding="utf-8")
    return report
