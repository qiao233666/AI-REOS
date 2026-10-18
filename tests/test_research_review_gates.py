# -*- coding: utf-8 -*-
"""verify_research_reviews.py 的语义门测试（第二步验收）。

覆盖 6.1-sol 第四轮终审点名的反例：
  - 伪造 D 编号能通过预算门（修复前）；
  - approval=rejected 却通过冻结门（修复前）；
以及多动作冲突、candidate_only 绑定任务、占位词、引用悬空等。

每个反例都断言"必须被拒绝"，避免结构检查通过被误当执行许可。
"""

from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CHECKS = REPO / "aiops" / "checks"
sys.path.insert(0, str(CHECKS))

import verify_research_reviews as vrr  # noqa: E402


def _base() -> dict:
    return yaml.safe_load(
        (REPO / "aiops" / "tests" / "fixtures" / "RD-FIXTURE-001.yaml")
        .read_text(encoding="utf-8")
    )


class _TmpRepo:
    """把单份 RD 与必要账本放进临时目录。"""

    def __init__(self, doc: dict):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "aiops" / "research_reviews").mkdir(parents=True)
        (self.dir / "aiops" / "schemas").mkdir(parents=True)
        (self.dir / "aiops" / "schemas" / "research-review.schema.json").write_text(
            (REPO / "aiops" / "schemas" / "research-review.schema.json")
            .read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (self.dir / "aiops" / "EVIDENCE.yaml").write_text(
            yaml.safe_dump(
                [{"id": "E-001", "status": "active"},
                 {"id": "E-002", "status": "active"}],
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        # D 条目用真实格式，验证"存在性检查"确实生效
        (self.dir / "aiops" / "DECISIONS.md").write_text(
            "# DECISIONS\n\n## D-018 — 测试裁决\n\n正文。\n", encoding="utf-8"
        )
        self.write(doc)

    def write(self, doc: dict):
        (self.dir / "aiops" / "research_reviews" / "RD-TEST.yaml").write_text(
            yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8"
        )

    def run(self, gate: str, line: str = "RL-900") -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vrr.check(self.dir, gate, line)
        return code, buffer.getvalue()


class GateTests(unittest.TestCase):
    def setUp(self):
        self.doc = _base()

    def assert_rejected(self, gate: str, needle: str, line: str = "RL-900"):
        repo = _TmpRepo(self.doc)
        code, out = repo.run(gate, line)
        self.assertNotEqual(0, code, f"期望拒绝，实际通过。输出：{out}")
        self.assertIn(needle, out, f"期望提示含 {needle!r}，实际：{out}")

    def assert_accepted(self, gate: str, line: str = "RL-900"):
        repo = _TmpRepo(self.doc)
        code, out = repo.run(gate, line)
        self.assertEqual(0, code, f"期望通过，实际失败：{out}")
        return out

    # ---------- 基线 ----------

    def test_baseline_proposal_passes_decision(self):
        out = self.assert_accepted("decision")
        self.assertIn("不等于科学价值审核", out)

    def test_evidence_update_never_blocked(self):
        out = self.assert_accepted("evidence_update")
        self.assertIn("不要求路线复盘", out)

    # ---------- 反例 1：伪造 D 编号 ----------

    def test_forged_decision_ref_rejected(self):
        self.doc["approval"] = {
            "status": "approved",
            "approval_ref": "D-999999",
            "approved_at": "2026-10-18",
            "input_digest": "sha256:" + "a" * 64,
        }
        self.assert_rejected("extra_budget", "指向不存在的决策条目")

    # ---------- 反例 2：被拒绝的复盘冻结稿件 ----------

    def test_rejected_cannot_freeze_scope(self):
        self.doc["approval"] = {
            "status": "rejected",
            "approval_ref": None,
            "approved_at": None,
            "input_digest": None,
        }
        self.doc["recommendation"]["manuscript_action"] = "narrow"
        self.assert_rejected("scope_freeze", "不能用于冻结稿件范围")

    def test_rejected_can_withdraw_invalid_claims(self):
        """撤下错误主张是纠错，不因审批被拒而阻断。"""
        self.doc["approval"] = {
            "status": "rejected",
            "approval_ref": None,
            "approved_at": None,
            "input_digest": None,
        }
        self.doc["recommendation"]["manuscript_action"] = "withdraw_invalid_claims"
        self.assert_accepted("scope_freeze")

    # ---------- 反例 3：approved 但缺快照 ----------

    def test_approved_without_digest_rejected(self):
        self.doc["approval"] = {
            "status": "approved",
            "approval_ref": "D-018",
            "approved_at": "2026-10-18",
            "input_digest": None,
        }
        self.assert_rejected("decision", "input_digest 为空")

    def test_proposed_with_ref_rejected(self):
        self.doc["approval"]["approval_ref"] = "D-018"
        self.assert_rejected("decision", "未获批准却提前填写")

    # ---------- 反例 4：动作冲突 ----------

    def test_same_target_stop_and_continue_rejected(self):
        """同一对象不得同时退役与继续投入。"""
        self.doc["selected_actions"] = [
            {"action_id": "A1", "kind": "repair_method",
             "target_ref": "energy-budget@v2", "task_ref": "T-044", "mode": "execute"},
            {"action_id": "A2", "kind": "stop_pause",
             "target_ref": "energy-budget@v2", "task_ref": "T-044", "mode": "execute"},
        ]
        self.assert_rejected("decision", "同一对象同时被退役与继续投入")

    def test_different_targets_can_coexist(self):
        self.doc["selected_actions"] = [
            {"action_id": "A1", "kind": "repair_method",
             "target_ref": "energy-budget@v2", "task_ref": "T-044", "mode": "execute"},
            {"action_id": "A2", "kind": "stop_pause",
             "target_ref": "model-B@v1", "task_ref": "T-044", "mode": "execute"},
        ]
        self.assert_accepted("decision")

    def test_candidate_only_with_task_rejected(self):
        self.doc["selected_actions"].append(
            {"action_id": "A2", "kind": "pivot_question", "target_ref": "RL-002@1",
             "task_ref": "T-044", "mode": "candidate_only"}
        )
        self.assert_rejected("decision", "candidate_only，不应绑定执行任务")

    def test_execute_without_task_rejected(self):
        self.doc["selected_actions"][0]["task_ref"] = None
        self.assert_rejected("decision", "必须绑定执行任务")

    # ---------- 反例 5：推荐与动作不一致 ----------

    def test_primary_without_execute_action_rejected(self):
        self.doc["recommendation"]["primary"] = "continue_existing"
        self.doc["options"]["continue_existing"]["ruling"] = "recommend"
        self.assert_rejected("decision", "没有对应的 mode=execute 动作")

    def test_primary_ruling_mismatch_rejected(self):
        self.doc["options"]["repair_method"]["ruling"] = "undetermined"
        self.assert_rejected("decision", "ruling 不是 recommend")

    # ---------- 反例 6：retire 与 lifecycle 不一致 ----------

    def test_retire_requires_retired_lifecycle(self):
        self.doc["judgments"]["line"]["value_verdict"] = "retire"
        self.assert_rejected("decision", "lifecycle.to 不是 retired")

    def test_retired_requires_stop_action(self):
        self.doc["judgments"]["line"]["value_verdict"] = "retire"
        self.doc["lifecycle"] = {"from": "active", "to": "retired"}
        self.doc["options"]["stop_pause"]["ruling"] = "recommend"
        self.assert_rejected("decision", "没有 mode=execute 的 stop_pause 动作")

    def test_completed_rejected_as_unverifiable(self):
        self.doc["lifecycle"] = {"from": "active", "to": "completed"}
        self.assert_rejected("decision", "answer_criterion")

    # ---------- 反例 7：占位词与判别设计 ----------

    def test_placeholder_rejected(self):
        # 用长度过 schema 门槛、但仍是占位词的字符串，验证语义检查这一层。
        self.doc["recommendation"]["change_my_mind_if"] = "TODO TODO TODO TODO"
        self.assert_rejected("decision", "模板占位词")

    def test_repair_without_discriminator_rejected(self):
        self.doc["options"]["repair_method"]["discriminator"] = None
        self.assert_rejected("decision", "未给出最小有区分力的观测")

    # ---------- 反例 8：引用悬空 ----------

    def test_invented_evidence_rejected(self):
        self.doc["trigger"]["evidence_refs"] = ["E-999"]
        self.assert_rejected("decision", "引用不存在的 EVIDENCE ID")

    def test_line_id_must_not_be_task(self):
        repo = _TmpRepo(self.doc)
        code, out = repo.run("decision", line="T-044")
        self.assertNotEqual(0, code)
        self.assertIn("RL-xxx", out)

    # ---------- gate 语义 ----------

    def test_close_line_needs_stop_pause(self):
        self.assert_rejected("close_line", "主推荐为 stop_pause")

    def test_extra_budget_needs_ceiling(self):
        self.doc["approval"] = {
            "status": "approved", "approval_ref": "D-018",
            "approved_at": "2026-10-18",
            "input_digest": "sha256:" + "b" * 64,
        }
        self.doc["recommendation"]["resource_ceiling"] = {
            "cpu_hours": None, "human_minutes": None,
        }
        self.assert_rejected("extra_budget", "未提出任何资源上限")

    def test_extra_budget_approved_passes(self):
        self.doc["approval"] = {
            "status": "approved", "approval_ref": "D-018",
            "approved_at": "2026-10-18",
            "input_digest": "sha256:" + "b" * 64,
        }
        self.assert_accepted("extra_budget")

    def test_scope_freeze_hold_rejected(self):
        self.doc["recommendation"]["manuscript_action"] = "hold"
        self.assert_rejected("scope_freeze", "需要明确的 manuscript_action")


class EmptyDirTests(unittest.TestCase):
    def test_no_reviews_audit_is_skip_not_pass(self):
        import tempfile
        empty = Path(tempfile.mkdtemp())
        (empty / "aiops" / "schemas").mkdir(parents=True)
        (empty / "aiops" / "schemas" / "research-review.schema.json").write_text(
            (REPO / "aiops" / "schemas" / "research-review.schema.json")
            .read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (empty / "aiops" / "EVIDENCE.yaml").write_text("[]\n", encoding="utf-8")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vrr.check(empty, "audit", None)
        out = buffer.getvalue()
        self.assertEqual(0, code)
        self.assertIn("[SKIP]", out)
        self.assertNotIn("科研决策校验通过", out.replace("没有声称科研决策校验通过", ""))

    def test_missing_review_fails_decision(self):
        import tempfile
        empty = Path(tempfile.mkdtemp())
        (empty / "aiops" / "schemas").mkdir(parents=True)
        (empty / "aiops" / "schemas" / "research-review.schema.json").write_text(
            (REPO / "aiops" / "schemas" / "research-review.schema.json")
            .read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (empty / "aiops" / "EVIDENCE.yaml").write_text("[]\n", encoding="utf-8")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vrr.check(empty, "decision", "RL-001")
        self.assertNotEqual(0, code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
