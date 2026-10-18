# -*- coding: utf-8 -*-
"""research-review.schema.json v2 的结构表达力测试。

本测试只验证"schema 能否表达 req 要求的各种情形"，不涉及检查器的语义门。
检查器语义由 test_research_review_gates.py 覆盖。

对应施工第一步通过条件：
- 多主张、多动作、候选保留、合法停路、维持原方案均可表达；
- 错配与缺字段被拒绝。
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import yaml

try:
    from jsonschema import Draft7Validator
except ImportError:  # 项目治理环境可能无 jsonschema
    Draft7Validator = None

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "aiops" / "schemas" / "research-review.schema.json"


def _base() -> dict:
    """一份最小的、合法的 v2 复盘。"""
    return {
        "schema_version": 2,
        "review_id": "RD-RL001-TEST",
        "line": {
            "line_id": "RL-001",
            "line_revision": 1,
            "question": "掠流下局部被动实现障碍能否被非局域连接解除？",
            "scope": "二维均匀流、冻结双活塞子空间、对角被动阻抗类",
            "answer_criterion": "给出受限实现类的被动可实现性判定及其物理适用范围",
            "owner": "root-agent",
            "task_refs": ["T-044"],
            "derived_from": None,
        },
        "supersedes": [],
        "trigger": {
            "kind": "decisive_negative",
            "evidence_refs": ["E-156"],
            "summary": "原被当作能量闭合的关系被证明是代数恒等式，不含独立信息。",
        },
        "question": {
            "original": "对指定掠流散射与边界运动学，如何建立可独立检验的能量分账？",
            "scientific_value": "区分数学归一约定与物理守恒，决定哪些结论可迁移到真实边界。",
        },
        "judgments": {
            "claim": [
                {
                    "target_ref": "C2@v1",
                    "verdict": "unsupported",
                    "evidence_refs": ["E-156"],
                    "reason": "支撑该主张的验证方法被证明不可识别，不能继续作为证据。",
                }
            ],
            "method": {
                "target_ref": "sum-w-identity@v1",
                "verdict": "unsuitable",
                "revisable_premises": ["改用独立 PDE 能量积分", "改用真实界面功共轭"],
                "reason": "该方法由定义代入必然成立，对物理分账是否正确不敏感。",
            },
            "line": {
                "value_verdict": "continue_bounded",
                "remaining_question": "P_mean 的严格定义与独立计算式仍未导出。",
                "anomaly_assessment": "未发现值得保留的异常，原闭合只是恒等式代入。",
                "knowledge_gain": "能判断现有物理定义在哪一步欠定，或给出可独立检验的分账。",
                "coverage_refs": [],
                "opportunity_cost": "纸面推导约 90 分钟；替代用途为继续写已支持的受限主张。",
                "reason": "障碍是验证方法不可识别，不是科学问题自动失效。",
            },
        },
        "opportunities": {
            "assessment": "open",
            "reason": "可从独立 PDE 能量恒等式与直接界面功积分建立交叉检验。",
            "questions": [],
        },
        "options": {
            "narrow_write": {
                "ruling": "do_not_recommend",
                "reason": "仅增加免责声明不能填补独立分账的科学缺口。",
                "evidence_refs": ["E-156"],
                "discriminator": None,
            },
            "repair_method": {
                "ruling": "recommend",
                "reason": "保留原始科学问题，改用独立推导与真实边界功共轭。",
                "evidence_refs": ["E-156"],
                "discriminator": "在刚壁与一个可动边界锚点上比较独立能量预算各项及残差。",
            },
            "pivot_question": {
                "ruling": "undetermined",
                "reason": "运动学差异提示另一问题，但不能替代当前能量验证。",
                "evidence_refs": ["E-156"],
                "discriminator": "固定外缘与功率定义后，检查运动学敏感性的参数规律。",
            },
            "stop_pause": {
                "ruling": "do_not_recommend",
                "reason": "核心问题尚有明确独立推导路径，无足够依据判为无价值。",
                "evidence_refs": ["E-156"],
                "discriminator": None,
            },
            "continue_existing": {
                "ruling": "do_not_recommend",
                "reason": "原方法已被证明不可识别，维持原样无法回答目标问题。",
                "evidence_refs": ["E-156"],
                "discriminator": None,
            },
        },
        "recommendation": {
            "primary": "repair_method",
            "why_over_alternatives": "独立推导能直接回答尚未解决的物理问题，且无需先做大规模 FE。",
            "next_action": "先完成纸面推导，再写不调用旧恒等式的脚本核对一个最小算例。",
            "expected_knowledge_gain": "判断能否以独立物理收支支撑端口功与平均流交换解释。",
            "change_my_mind_if": "若独立推导显示端口功与流体功共轭定义根本不闭合，则重新评估建模假设。",
            "resource_ceiling": {"cpu_hours": 0.0, "human_minutes": 90.0},
            "revisit_condition": "首轮推导若不能在限定时间内给出可检验分账，则暂停新增计算。",
            "manuscript_action": "hold",
        },
        "selected_actions": [
            {
                "action_id": "A1",
                "kind": "repair_method",
                "target_ref": "energy-budget@v2",
                "task_ref": "T-044",
                "mode": "execute",
            }
        ],
        "lifecycle": {"from": "active", "to": "active"},
        "approval": {
            "status": "proposed",
            "approval_ref": None,
            "approved_at": None,
            "input_digest": None,
        },
        "provenance": {
            "author": "test-suite",
            "created_at": "2026-10-18",
            "source_commit": None,
        },
    }


@unittest.skipIf(Draft7Validator is None, "jsonschema 不可用")
class SchemaExpressivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft7Validator.check_schema(cls.schema)
        cls.validator = Draft7Validator(cls.schema)

    def assert_valid(self, doc):
        errors = sorted(self.validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
        self.assertEqual([], [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors])

    def assert_invalid(self, doc, needle=None):
        errors = list(self.validator.iter_errors(doc))
        self.assertTrue(errors, "期望被拒绝，但 schema 通过了")
        if needle:
            joined = " | ".join(e.message for e in errors)
            self.assertIn(needle, joined, f"期望错误信息含 {needle!r}，实际: {joined}")

    # ---------- 表达力：必须能写出来 ----------

    def test_minimal_valid(self):
        self.assert_valid(_base())

    def test_multiple_claims_allowed(self):
        """多主张：同一次复盘可分别撤回、收窄、保留不同主张。"""
        doc = _base()
        doc["judgments"]["claim"] = [
            {"target_ref": "C1@v1", "verdict": "supported",
             "evidence_refs": ["E-156"], "reason": "该受限主张不依赖被撤回的验证方法，仍然成立。"},
            {"target_ref": "C2@v1", "verdict": "unsupported",
             "evidence_refs": ["E-156"], "reason": "支撑它的验证被证明不可识别，支持不足。"},
            {"target_ref": "C3@v1", "verdict": "narrowed",
             "evidence_refs": ["E-156"], "reason": "仅在给定端接条件下成立，需写入适用范围。"},
        ]
        self.assert_valid(doc)

    def test_multiple_actions_coexist(self):
        """多动作并存：收窄论文 + 独立候选，两者对象不同。"""
        doc = _base()
        doc["recommendation"]["primary"] = "narrow_write"
        doc["options"]["narrow_write"]["ruling"] = "recommend"
        doc["options"]["repair_method"]["ruling"] = "undetermined"
        doc["selected_actions"] = [
            {"action_id": "A1", "kind": "narrow_write", "target_ref": "manuscript@draft2",
             "task_ref": "T-044", "mode": "execute"},
            {"action_id": "A2", "kind": "pivot_question", "target_ref": "RL-002@1",
             "task_ref": None, "mode": "candidate_only"},
        ]
        self.assert_valid(doc)

    def test_candidate_only_without_task(self):
        doc = _base()
        doc["selected_actions"] = [
            {"action_id": "A1", "kind": "repair_method", "target_ref": "energy-budget@v2",
             "task_ref": "T-044", "mode": "execute"},
            {"action_id": "A2", "kind": "pivot_question", "target_ref": "RL-002@1",
             "task_ref": None, "mode": "candidate_only"},
        ]
        self.assert_valid(doc)

    def test_legitimate_stop(self):
        """合法停路：允许在存在未解问题的情况下停止投入。"""
        doc = _base()
        doc["recommendation"]["primary"] = "stop_pause"
        doc["options"]["stop_pause"]["ruling"] = "recommend"
        doc["options"]["repair_method"]["ruling"] = "do_not_recommend"
        doc["judgments"]["line"]["value_verdict"] = "retire"
        doc["judgments"]["line"]["remaining_question"] = "独立分账仍未完成，但已验证成本高于预期知识增量。"
        doc["judgments"]["line"]["reason"] = "剩余问题存在但成本收益不再合理，属于资源判断而非论断失败。"
        doc["selected_actions"] = [
            {"action_id": "A1", "kind": "stop_pause", "target_ref": "energy-budget@v2",
             "task_ref": None, "mode": "execute"},
        ]
        doc["lifecycle"] = {"from": "active", "to": "retired"}
        self.assert_valid(doc)

    def test_continue_existing_is_legal(self):
        """维持原方案：异常被排除后可以不改方法。"""
        doc = _base()
        doc["recommendation"]["primary"] = "continue_existing"
        doc["options"]["continue_existing"]["ruling"] = "recommend"
        doc["options"]["repair_method"]["ruling"] = "do_not_recommend"
        doc["selected_actions"] = [
            {"action_id": "A1", "kind": "continue_existing", "target_ref": "model_A@v1",
             "task_ref": "T-044", "mode": "execute"},
        ]
        self.assert_valid(doc)

    def test_derived_from_new_line(self):
        """换问题建新线：允许 derived_from 指回来源线。"""
        doc = _base()
        doc["line"]["line_id"] = "RL-002"
        doc["line"]["derived_from"] = {
            "line_id": "RL-001", "line_revision": 1, "decision_ref": "D-018",
        }
        self.assert_valid(doc)

    def test_supersedes_same_line(self):
        doc = _base()
        doc["supersedes"] = ["RD-RL001-OLD"]
        self.assert_valid(doc)

    def test_retired_with_proposed_is_legal(self):
        """建议 retired 但审批 proposed 合法——有效状态需沿采纳链推导。"""
        doc = _base()
        doc["lifecycle"] = {"from": "active", "to": "retired"}
        doc["judgments"]["line"]["value_verdict"] = "retire"
        doc["approval"] = {"status": "proposed", "approval_ref": None,
                           "approved_at": None, "input_digest": None}
        self.assert_valid(doc)

    def test_approved_with_digest(self):
        doc = _base()
        doc["approval"] = {
            "status": "approved",
            "approval_ref": "D-018",
            "approved_at": "2026-10-18",
            "input_digest": "sha256:" + "a" * 64,
        }
        self.assert_valid(doc)

    # ---------- 拒绝：错配与缺字段 ----------

    def test_line_id_must_be_rl_not_task(self):
        """禁止把 line_id 等同于任务号。"""
        doc = _base()
        doc["line"]["line_id"] = "T-044"
        self.assert_invalid(doc, "does not match")

    def test_missing_judgments_rejected(self):
        doc = _base()
        del doc["judgments"]
        self.assert_invalid(doc, "required")

    def test_missing_continue_existing_rejected(self):
        """五种处置必须全部表态，缺一不可。"""
        doc = _base()
        del doc["options"]["continue_existing"]
        self.assert_invalid(doc, "required")

    def test_missing_selected_actions_rejected(self):
        doc = _base()
        del doc["selected_actions"]
        self.assert_invalid(doc, "required")

    def test_empty_claim_list_rejected(self):
        doc = _base()
        doc["judgments"]["claim"] = []
        self.assert_invalid(doc)

    def test_short_explanation_rejected(self):
        """占位式短文本拒绝（explanation minLength 12）。"""
        doc = _base()
        doc["recommendation"]["why_over_alternatives"] = "进一步研究"
        self.assert_invalid(doc)

    def test_bad_line_revision(self):
        doc = _base()
        doc["line"]["line_revision"] = 0
        self.assert_invalid(doc)

    def test_bad_input_digest(self):
        doc = _base()
        doc["approval"]["input_digest"] = "sha256:zzz"
        self.assert_invalid(doc)

    def test_extra_field_rejected(self):
        doc = _base()
        doc["surprise"] = 1
        self.assert_invalid(doc, "Additional properties")

    def test_schema_version_must_be_2(self):
        doc = _base()
        doc["schema_version"] = 1
        self.assert_invalid(doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
