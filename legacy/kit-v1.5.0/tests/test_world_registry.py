# -*- coding: utf-8 -*-
"""世界层 unittest：builder/ledger/verify 的回归网（E-085 步骤 6；v1.3.2 随 kit 分发）。

自适应语义（kit 与主仓同一份文件）：
- 本仓已部署 world（objects 非空且 generated 视图存在）→ 全量断言（探针/替代链/视图一致）；
- 新装骨架项目（objects 为空、视图由 bootstrap 初始化）→ 探针与账本分层无数据可断言，
  相关用例 skip（不算失败）；builder/verify 的结构检查仍跑。

用法：python -m unittest aiops.tests.test_world_registry -v
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

AIOPS = Path(__file__).resolve().parents[1]
PY = sys.executable
BUILD = AIOPS / "world" / "tools" / "build_world_registry.py"
LEDGER = AIOPS / "world" / "tools" / "build_ledger_views.py"
VERIFY = AIOPS / "world" / "tools" / "verify_world_registry.py"
OBJECTS = AIOPS / "world" / "objects"
REGISTRY_OUT = AIOPS / "world" / "registry.generated.yaml"
RELEASES_INDEX = AIOPS / "world" / "releases" / "index.yaml"

WORLD_DEPLOYED = any(OBJECTS.glob("*.yaml")) and REGISTRY_OUT.exists()
LEDGER_DEPLOYED = (LEDGER.exists() and (AIOPS / "world" / "ledger-policy.yaml").exists()
                   and RELEASES_INDEX.exists())


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, *map(str, args)], capture_output=True, text=True, timeout=180)


@unittest.skipUnless(WORLD_DEPLOYED, "world registry 未部署（骨架项目）——仅跑结构检查")
class TestWorldRegistry(unittest.TestCase):
    """回归网：既有验收 + 负向突变（需已部署的世界层数据）。"""

    def test_01_build_check_consistent(self):
        # SOL57 审查：不得先跑写入式 builder（会把待检测的 drift 覆盖掉再 --check，
        # 测试自愈假绿）。只跑 --check；写入路径由人类/调用方显式触发。
        r = run(BUILD, "--check")
        self.assertEqual(r.returncode, 0, "generated 视图与重建不一致")

    def test_02_recall_probes(self):
        for term in ("Π_D", "N=1/2/3", "COMSOL"):
            r = run(BUILD, "--query", term)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("UNKNOWN", r.stdout, f"召回探针未命中: {term}")

    def test_03_unknown_query_returns_unknown_not_negative(self):
        r = run(BUILD, "--query", "zzz-never-registered-term-123")
        self.assertIn("UNKNOWN", r.stdout)
        self.assertNotIn("不存在", r.stdout)

    def test_07_verify_passes(self):
        r = run(VERIFY)
        self.assertEqual(r.returncode, 0, r.stdout)


@unittest.skipUnless(LEDGER_DEPLOYED, "ledger 发行链未部署（骨架项目）——skip")
class TestLedgerViews(unittest.TestCase):
    """账本三层视图 reducer：需 ledger-policy + releases/index 骨架存在。"""

    def test_04_ledger_layers_present(self):
        r = run(LEDGER)
        self.assertEqual(r.returncode, 0)
        self.assertIn("core=", r.stdout)

    def test_05_ledger_as_of_unknown_release(self):
        r = run(LEDGER, "--as-of", "GR-9999")
        self.assertIn("UNKNOWN", r.stdout)

    def test_06_ledger_id_query_with_supersession(self):
        r = run(LEDGER, "--id", "E-014")
        self.assertIn("active-cold", r.stdout)
        self.assertIn("supersession[$partial]", r.stdout)


class TestWorldStructure(unittest.TestCase):
    """结构检查（任何部署形态都应过）：视图一致 + 未命中 UNKNOWN 语义。"""

    def test_08_build_check_when_view_exists(self):
        if not REGISTRY_OUT.exists():
            self.skipTest("generated 视图未初始化（骨架项目由 bootstrap 生成）")
        r = run(BUILD, "--check")
        self.assertEqual(r.returncode, 0, "generated 视图与重建不一致")

    def test_09_unknown_query_semantics(self):
        if not BUILD.exists():
            self.skipTest("builder 未部署")
        r = run(BUILD, "--query", "zzz-never-registered-term-123")
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNKNOWN", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
