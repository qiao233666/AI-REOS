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

    def test_hit_does_not_imply_all_required_evidence_found(self):
        self.questions.write_text(json.dumps([dict(question_id="q", query="网格改变", relevant_doc_ids=["doc-a", "doc-b"], required_doc_ids=["doc-a", "doc-b"], type="literal")]), encoding="utf-8")
        result = evaluate(self.corpus, self.questions, self.root / "evaluation")
        row = result["results"][0]
        self.assertTrue(row["hit_at_5"])
        self.assertEqual(0.5, row["required_recall_at_5"])
        self.assertFalse(row["required_complete_at_5"])
        self.assertEqual(["doc-b"], row["missing_required_at_5"])
        self.assertEqual(0.5, result["by_type"]["literal"]["required_recall_at_5"])

    def test_old_questions_default_to_relevant_as_required(self):
        result = evaluate(self.corpus, self.questions, self.root / "evaluation")
        self.assertEqual(["doc-a"], result["results"][0]["required"])
        self.assertEqual(1.0, result["metrics"]["required_complete_at_5"])

    def test_invalid_required_ids_are_rejected(self):
        for index, required in enumerate((["missing"], [], ["doc-a", "doc-a"])):
            self.questions.write_text(json.dumps([dict(question_id="q", query="网格", relevant_doc_ids=["doc-a"], required_doc_ids=required)]), encoding="utf-8")
            with self.subTest(required=required), self.assertRaises(FrameworkError):
                evaluate(self.corpus, self.questions, self.root / ("out" + str(index)))

    def test_absence_question_is_reported_separately_without_negative_inference(self):
        questions = [dict(question_id="q", query="网格改变", relevant_doc_ids=[], expected_absence=True, type="absence")]
        self.questions.write_text(json.dumps(questions), encoding="utf-8")
        result = evaluate(self.corpus, self.questions, self.root / "evaluation")
        self.assertIsNone(result["metrics"]["required_complete_at_5"])
        self.assertIsNone(result["results"][0]["required_recall_at_5"])
        self.assertEqual(1, result["metrics"]["no_evidence_questions"])
        self.assertEqual(["doc-a"], result["results"][0]["returned_candidates"])
        questions[0]["expected_absence"] = False
        self.questions.write_text(json.dumps(questions), encoding="utf-8")
        with self.assertRaises(FrameworkError):
            evaluate(self.corpus, self.questions, self.root / "invalid")
