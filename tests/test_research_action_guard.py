# -*- coding: utf-8 -*-
"""research_action_guard 的测试（T-045 第四步验收）。

必须验证：
  1. 拒绝时无副作用（不写消费账）；
  2. 重试不重复执行、不重复追加额度；
  3. 超额被拒；
  4. 证据纠错仍可登记（不受路线门阻断）；
  5. candidate_only 不产生执行许可。
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "checks"))

import research_action_guard as guard  # noqa: E402


def _review() -> dict:
    return yaml.safe_load(
        (REPO / "aiops" / "tests" / "fixtures" / "RD-FIXTURE-001.yaml")
        .read_text(encoding="utf-8")
    )


class _Repo:
    def __init__(self, doc: dict):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "aiops" / "research_reviews").mkdir(parents=True)
        (self.dir / "aiops" / "DECISIONS.md").write_text(
            "# DECISIONS\n\n## D-018 — 测试裁决\n\nstatus: active\n", encoding="utf-8"
        )
        self.write(doc)

    def write(self, doc: dict) -> Path:
        path = self.dir / "aiops" / "research_reviews" / "RD-TEST.yaml"
        path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
        return path

    @property
    def ledger(self) -> Path:
        return self.dir / "aiops" / "research_reviews" / guard.LEDGER_NAME

    def ledger_records(self) -> list:
        if not self.ledger.is_file():
            return []
        return yaml.safe_load(self.ledger.read_text(encoding="utf-8")) or []


def _approved(doc: dict) -> dict:
    doc = copy.deepcopy(doc)
    doc["approval"] = {
        "status": "approved",
        "approval_ref": "D-018",
        "approved_at": "2026-10-18",
        "input_digest": "sha256:" + "c" * 64,
    }
    return doc


class GuardTests(unittest.TestCase):
    def test_denied_when_not_approved(self):
        repo = _Repo(_review())
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A1", "ev-1")
        self.assertFalse(r.allowed)
        self.assertEqual([], repo.ledger_records(), "拒绝时不得写消费账")

    def test_denied_on_forged_decision(self):
        doc = _approved(_review())
        doc["approval"]["approval_ref"] = "D-999999"
        repo = _Repo(doc)
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A1", "ev-1")
        self.assertFalse(r.allowed)
        self.assertIn("不存在", r.reason)
        self.assertEqual([], repo.ledger_records())

    def test_denied_without_digest(self):
        doc = _approved(_review())
        doc["approval"]["input_digest"] = None
        repo = _Repo(doc)
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A1", "ev-1")
        self.assertFalse(r.allowed)
        self.assertIn("input_digest", r.reason)

    def test_denied_on_candidate_only(self):
        doc = _approved(_review())
        doc["selected_actions"].append(
            {"action_id": "A2", "kind": "pivot_question", "target_ref": "RL-002@1",
             "task_ref": None, "mode": "candidate_only"}
        )
        repo = _Repo(doc)
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A2", "ev-1")
        self.assertFalse(r.allowed)
        self.assertIn("candidate_only", r.reason)

    def test_allowed_records_consumption_once(self):
        repo = _Repo(_approved(_review()))
        r1 = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                                "A1", "ev-1")
        self.assertTrue(r1.allowed)
        self.assertTrue(r1.consumed)
        self.assertEqual(1, len(repo.ledger_records()))

        # 同一事件重试：放行但不再记账
        r2 = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                                "A1", "ev-1")
        self.assertTrue(r2.allowed)
        self.assertFalse(r2.consumed)
        self.assertEqual(1, len(repo.ledger_records()), "重试不得重复追加")

    def test_over_budget_rejected(self):
        doc = _approved(_review())
        doc["recommendation"]["resource_ceiling"] = {
            "cpu_hours": 1.0, "human_minutes": 90.0,
        }
        repo = _Repo(doc)
        # 先消费 0.8 核时
        r1 = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                                "A1", "ev-1", {"cpu_hours": 0.8, "human_minutes": 0})
        self.assertTrue(r1.allowed)
        # 再申请 0.5 核时 → 1.3 > 1.0
        r2 = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                                "A1", "ev-2", {"cpu_hours": 0.5, "human_minutes": 0})
        self.assertFalse(r2.allowed)
        self.assertIn("超出", r2.reason)
        self.assertEqual(1, len(repo.ledger_records()), "拒绝不得新增账目")

    def test_null_ceiling_is_not_infinite(self):
        doc = _approved(_review())
        doc["recommendation"]["resource_ceiling"] = {
            "cpu_hours": None, "human_minutes": None,
        }
        repo = _Repo(doc)
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A1", "ev-1", {"cpu_hours": 1.0, "human_minutes": 0})
        self.assertFalse(r.allowed)
        self.assertIn("不是无限", r.reason)

    def test_evidence_update_always_passes(self):
        repo = _Repo(_review())  # 未批准
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A1", "ev-1", None, action_kind="evidence_update")
        self.assertTrue(r.allowed)
        self.assertIn("纠错豁免", r.reason)
        self.assertEqual([], repo.ledger_records())

    def test_superseded_decision_rejected(self):
        repo = _Repo(_approved(_review()))
        (repo.dir / "aiops" / "DECISIONS.md").write_text(
            "# DECISIONS\n\n## D-018 — 测试裁决\n\nstatus: superseded\n",
            encoding="utf-8",
        )
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A1", "ev-1")
        self.assertFalse(r.allowed)
        self.assertIn("已被取代", r.reason)

    def test_missing_action_rejected(self):
        repo = _Repo(_approved(_review()))
        r = guard.guard_action(repo.dir, "aiops/research_reviews/RD-TEST.yaml",
                               "A9", "ev-1")
        self.assertFalse(r.allowed)
        self.assertIn("无动作", r.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
