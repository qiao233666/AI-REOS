"""离线评测检查来源、已标注必需项和真实排名，不把未标注项算负例。"""
import json
from pathlib import Path
import tempfile
import unittest
try:
    from aireos.memory_eval import evaluate
except ImportError:
    evaluate = None
from aireos.errors import FrameworkError


class MemoryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evaluate)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus.jsonl"
        self.questions = self.root / "questions.json"
        self.corpus.write_text("\n".join(json.dumps(obj, ensure_ascii=False) for obj in [
            {"doc_id": "doc-a", "text": "网格改变后原结果无法复现", "source_sha256": "a" * 64, "recorded_status": "unknown"},
            {"doc_id": "doc-b", "text": "另一项独立的合成记录", "source_sha256": "b" * 64, "recorded_status": "unknown"}]), encoding="utf-8")
        self.questions.write_text(json.dumps([dict(question_id="q1", query="网格改变", relevant_doc_ids=["doc-a"], type="literal")]), encoding="utf-8")

    def test_metrics_and_dataset_hashes_and_no_overwrite(self):
        result = evaluate(self.corpus, self.questions, self.root / "evaluation")
        self.assertEqual(1.0, result["metrics"]["hit_at_3"])
        self.assertEqual(1.0, result["metrics"]["mrr"])
        self.assertEqual(0, result["dialog_model_calls"])
        self.assertEqual(64, len(result["corpus_sha256"]))
        with self.assertRaises(FrameworkError):
            evaluate(self.corpus, self.questions, self.root / "evaluation")

    def test_missing_expected_document_is_an_error(self):
        self.questions.write_text(json.dumps([dict(question_id="q", query="x", relevant_doc_ids=["not-in-corpus"], type="literal")]), encoding="utf-8")
        with self.assertRaises(FrameworkError):
            evaluate(self.corpus, self.questions, self.root / "evaluation")
