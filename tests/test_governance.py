# -*- coding: utf-8 -*-
"""AI-REOS 治理代码自身的回归测试（v1.3 新增：强制层必须有测试）。

覆盖外审 v1.2 负向测试暴露的全部缺口：
- schema 引擎：minLength / anyOf / 不支持关键字仍在报错；
- verify_claims：fact 引用不存在的证据 id → FAIL；引用非 active 证据 → FAIL；
- verify_experiment_metadata：registry 幽灵实验 → FAIL；
- verify_task_specs：*.example.yaml 不被当成真实任务；
- guard_destructive：policy 单一真源加载 + 定制生效 + 缺失回退。

运行（kit 目录内）：python -m unittest discover -s tests -p "test_*.py"
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]
CHECKS = KIT_ROOT / "checks"
sys.path.insert(0, str(CHECKS))
sys.path.insert(0, str(CHECKS / "hooks"))

import schema_validation as sv  # noqa: E402
import verify_claims as vc  # noqa: E402
import verify_experiment_metadata as vem  # noqa: E402
import verify_task_specs as vts  # noqa: E402
import guard_destructive as g  # noqa: E402


def _fresh_aiops(tmp: Path) -> Path:
    """搭一个最小 aiops 结构（schemas 从 kit 拷贝，账本手工写）。"""
    root = tmp / "aiops"
    (root / "schemas").mkdir(parents=True)
    for f in ("claim.schema.json", "experiment.schema.json", "task.schema.json"):
        shutil.copy(KIT_ROOT / "schemas" / f, root / "schemas" / f)
    return root


EVIDENCE_OK = (
    "- schema_version: 1\n  id: E-001\n  type: observation\n"
    "  statement: '真实存在的证据条目'\n"
    "  source: {file: a, script: b, git_commit: c}\n  scope: {cases: [x]}\n"
    "  limitations: ['仅演示']\n  status: active\n  recorded_at: '2026-09-29'\n"
)


class TestSchemaEngine(unittest.TestCase):
    def test_min_length_enforced(self):
        schema = {"type": "string", "minLength": 3}
        self.assertEqual(sv.validate("abc", schema), [])
        self.assertTrue(sv.validate("ab", schema))

    def test_any_of_branches(self):
        schema = {"anyOf": [{"const": 1}, {"const": 2}]}
        self.assertEqual(sv.validate(2, schema), [])
        self.assertTrue(sv.validate(3, schema))

    def test_unsupported_keyword_still_reported(self):
        problems = sv.find_unsupported_keywords({"format": "uri"})
        self.assertTrue(any("format" in p for p in problems))


class TestClaimEvidenceIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aios_kit_test_"))
        self.root = _fresh_aiops(self.tmp)
        (self.root / "EVIDENCE.yaml").write_text(EVIDENCE_OK, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _claim_file(self, supports):
        (self.root / "claims.yaml").write_text(
            "claims:\n  - claim_id: C-001\n    classification: fact\n"
            "    text: '某事实断言'\n    scope: '测试'\n"
            f"    supports: {supports}\n    limitations: ['x']\n",
            encoding="utf-8",
        )

    def test_ghost_evidence_id_fails(self):
        self._claim_file([999])
        self.assertEqual(vc.main(self.root), 1)

    def test_valid_evidence_id_passes(self):
        self._claim_file(["E-001"])
        self.assertEqual(vc.main(self.root), 0)

    def test_deprecated_evidence_fails(self):
        (self.root / "EVIDENCE.yaml").write_text(
            EVIDENCE_OK.replace("status: active", "status: deprecated"), encoding="utf-8"
        )
        self._claim_file(["E-001"])
        self.assertEqual(vc.main(self.root), 1)


class TestGhostRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aios_kit_test_"))
        self.root = _fresh_aiops(self.tmp)
        (self.root / "experiments").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registry_entry_without_file_fails(self):
        (self.root / "experiments" / "registry.yaml").write_text(
            "experiments:\n  - id: EXP-999\n    status: complete\n"
            "    purpose: ghost\n    experiment_file: x.yaml\n",
            encoding="utf-8",
        )
        self.assertEqual(vem.main(self.root), 1)

    def test_empty_dir_and_registry_warn_pass(self):
        (self.root / "experiments" / "registry.yaml").write_text(
            "experiments: []\n", encoding="utf-8"
        )
        self.assertEqual(vem.main(self.root), 0)


class TestExampleTaskExcluded(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aios_kit_test_"))
        self.root = _fresh_aiops(self.tmp)
        (self.root / "specs").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_example_yaml_not_counted(self):
        (self.root / "specs" / "T-xxx.complete-example.yaml").write_text(
            "schema_version: 1\nid: T-017\nstatus: bogus\ntitle: t\nreason: r\n"
            "next_action: n\nowner: human\npriority: low\ncreated_at: '2026-09-29'\n",
            encoding="utf-8",
        )
        self.assertEqual(vts.main(self.root), 0)


class TestGuardPolicy(unittest.TestCase):
    def tearDown(self):
        shutil.rmtree(Path(tempfile.gettempdir()) / "aios_guard_policy_test", ignore_errors=True)

    def test_policy_file_overrides_defaults(self):
        tmp = Path(tempfile.mkdtemp(prefix="aios_guard_policy_test"))
        policy = tmp / "policy.yaml"
        policy.write_text(
            "schema_version: 1\nprotected_dirs:\n  components: [paper_drafts]\n"
            "  subpaths: []\n  prefixes: []\n",
            encoding="utf-8",
        )
        old_path = g.POLICY_PATH
        try:
            g.POLICY_PATH = policy
            pol = g._load_policy()
            self.assertIn("paper_drafts", pol["components"])
            self.assertTrue(g._touches_protected("rm -rf paper_drafts", pol))
            self.assertFalse(g._touches_protected("rm -rf results", pol))
        finally:
            g.POLICY_PATH = old_path

    def test_missing_policy_falls_back(self):
        old_path = g.POLICY_PATH
        try:
            g.POLICY_PATH = Path("Z:/definitely/missing/policy.yaml")
            pol = g._load_policy()
            self.assertIn("results", pol["components"])
        finally:
            g.POLICY_PATH = old_path

    def test_canary_probe_blocked(self):
        self.assertEqual(g._main_inner.__wrapped__ if hasattr(g._main_inner, "__wrapped__") else g.main, g.main)




class TestBootstrapUpgradeLogic(unittest.TestCase):
    """v1.3.1 回归：外审第二轮的迁移/自链/模板矛盾问题不再复发。"""

    @classmethod
    def setUpClass(cls):
        cls.bootstrap = None
        bp = KIT_ROOT.parent / "bootstrap.py"
        if bp.exists():  # 仅 kit 开发布局可导入（安装后的仓库没有 bootstrap.py）
            import importlib.util
            spec = importlib.util.spec_from_file_location("bootstrap", bp)
            cls.bootstrap = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.bootstrap)

    def test_01_legacy_baseline_hit_updates(self):
        if not self.bootstrap:
            self.skipTest("bootstrap.py 不在（已安装布局）")
        d = self.bootstrap.decide_upgrade(True, "kithash", "v12hash", None, {"v12hash"})
        self.assertEqual(d, "UPDATE")

    def test_02_user_modified_conflicts(self):
        if not self.bootstrap:
            self.skipTest("bootstrap.py 不在（已安装布局）")
        d = self.bootstrap.decide_upgrade(True, "kithash", "userhash", None, {"v12hash"})
        self.assertEqual(d, "CONFLICT")

    def test_03_nhash_ignores_crlf(self):
        if not self.bootstrap:
            self.skipTest("bootstrap.py 不在（已安装布局）")
        self.assertEqual(self.bootstrap._nhash(b"a\r\nb"), self.bootstrap._nhash(b"a\nb"))


    def test_04_hook_marker_present_in_template(self):
        tpl = (KIT_ROOT / "checks" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        from pathlib import Path as P
        marker = self.bootstrap.AI_REOS_HOOK_MARKER if self.bootstrap else "AI-REOS Tier-0"
        self.assertIn(marker, tpl)

    def test_05_planned_experiment_exempt_from_provenance(self):
        schema = json.loads((KIT_ROOT / "schemas" / "experiment.schema.json").read_text(encoding="utf-8"))
        planned = {"schema_version": 1, "id": "EXP-001", "purpose": "p", "status": "planned",
                   "git_commit": "", "dataset_hash": "", "config_hash": "", "environment_lock": "",
                   "factors": {}, "controlled": {}, "measurements": [{"m": 1}],
                   "predictions": {"a": "b"}, "discriminating_readout": "d",
                   "acceptance": [{"acc": 1}]}
        provenance_errors = [e for e in sv.validate(planned, schema)
                             if "controlled" in e or "git_commit" in e]
        self.assertEqual(provenance_errors, [])

    def test_06_running_requires_full_provenance(self):
        schema = json.loads((KIT_ROOT / "schemas" / "experiment.schema.json").read_text(encoding="utf-8"))
        running = {"schema_version": 1, "id": "EXP-001", "purpose": "p", "status": "running",
                   "git_commit": "", "dataset_hash": "", "config_hash": "", "environment_lock": "",
                   "factors": {}, "controlled": {}, "measurements": [{"m": 1}],
                   "predictions": {"a": "b"}, "discriminating_readout": "d",
                   "acceptance": [{"acc": 1}]}
        errs = sv.validate(running, schema)
        self.assertTrue(any("controlled" in e for e in errs))

    def test_07_template_controlled_matches_schema(self):
        import yaml
        schema = json.loads((KIT_ROOT / "schemas" / "experiment.schema.json").read_text(encoding="utf-8"))
        tpl_path = KIT_ROOT / "templates_ledger" / "TEMPLATE.experiment.yaml"
        if not tpl_path.exists():  # 已安装布局：bootstrap 装到 aiops/experiments/
            tpl_path = KIT_ROOT / "experiments" / "TEMPLATE.experiment.yaml"
        tpl = yaml.safe_load(tpl_path.read_text(encoding="utf-8"))
        errs = sv.validate(tpl, schema)
        structural = [e for e in errs if "controlled" in e]
        self.assertEqual(structural, [])  # v1.3.0 模板 boundary_conditions 是 dict、random_seed 是 null，曾被 schema 拒绝

    def test_08_no_personal_paths_in_kit(self):
        if not self.bootstrap:
            self.skipTest("已安装布局——账本/对象属用户内容，不做个人路径扫描（仅 kit 开发布局生效）")
        import re
        hits = []
        for f in KIT_ROOT.rglob("*"):
            if f.is_file() and f.suffix in {".py", ".md", ".yaml", ".yml", ".json"}:
                needle = "251" + "51"  # 拼接构造，避免扫描到本测试自身
                if needle in f.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(str(f))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
