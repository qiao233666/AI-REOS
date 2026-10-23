# -*- coding: utf-8 -*-
"""verify_ai_state 升级后的正负向测试（T-079；第二轮评审必改3验收矩阵）。

覆盖场景：
正向：150 行边界 PASS；
负向：151 行 FAIL、缺标题 FAIL、完全重复标题 FAIL、后缀竞争章节 FAIL、
     正文提及标题（非行首）不误判、代码围栏内伪标题不误判、
     不存在的 Evidence 引用 FAIL、超字符数 FAIL（20001 边界由超长单行构造）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))

import verify_ai_state as vs  # noqa: E402

MINIMAL_STATE = """# Project State

## Current objective

目标一段。

## Confirmed facts

- E-001：冒烟通过。

## Active hypotheses

- H-001：示例。

## Next 3 actions

1. 示例。
"""

SCHEMAS_DIR = Path(vs.__file__).resolve().parent.parent / "schemas"


def _make_root(tmp: Path, state_text: str) -> Path:
    """构造只含 STATE.md 的临时 aiops 根（其余校验项按需最小化）。"""
    (tmp / "schemas").mkdir(exist_ok=True)
    for src in SCHEMAS_DIR.glob("*.json"):
        (tmp / "schemas" / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("CHARTER.md", "DECISIONS.md", "FAILURES.md"):
        (tmp / name).write_text(f"# {name}\n", encoding="utf-8")
    (tmp / "EVIDENCE.yaml").write_text(
        "- schema_version: 1\n"
        "  id: E-001\n"
        "  type: observation\n"
        "  statement: 临时根内的最小占位证据，供引用检查使用。\n"
        "  source:\n"
        "    file: tmp\n"
        "    script: tmp\n"
        "    git_commit: tmp\n"
        "  scope:\n"
        "    cases: [tmp]\n"
        "  limitations:\n"
        "    - tmp\n"
        "  status: active\n"
        "  recorded_at: \"2026-10-15\"\n",
        encoding="utf-8",
    )
    (tmp / "STATE.md").write_text(state_text, encoding="utf-8")
    return tmp


class TestStateGate(unittest.TestCase):
    def _run(self, state_text: str) -> int:
        with tempfile.TemporaryDirectory() as d:
            code = vs.main(Path(d) if False else _make_root(Path(d), state_text))
        return code

    def test_minimal_pass(self):
        self.assertEqual(self._run(MINIMAL_STATE), 0)

    def test_150_lines_pass_151_fail(self):
        padded = MINIMAL_STATE + "\n" * (150 - len(MINIMAL_STATE.splitlines()))
        self.assertEqual(self._run(padded), 0)
        padded151 = padded + "\n"
        self.assertNotEqual(self._run(padded151), 0)

    def test_missing_heading_fail(self):
        self.assertNotEqual(self._run(MINIMAL_STATE.replace("## Active hypotheses\n", "")), 0)

    def test_duplicate_heading_fail(self):
        dup = MINIMAL_STATE.replace(
            "## Next 3 actions", "## Next 3 actions\n\n1. 重复。\n\n## Current objective"
        )
        self.assertNotEqual(self._run(dup), 0)

    def test_suffixed_competing_heading_fail(self):
        suffixed = MINIMAL_STATE.replace(
            "## Next 3 actions",
            "## Next 3 actions\n\n1. 新。\n\n## Confirmed facts（本轮新增索引）\n\n- E-002：x。",
        )
        self.assertNotEqual(self._run(suffixed), 0)

    def test_inline_mention_not_flagged(self):
        mentioned = MINIMAL_STATE.replace(
            "目标一段。", "正文提到 ## Confirmed facts 字样不应误判。"
        )
        self.assertEqual(self._run(mentioned), 0)

    def test_fence_heading_not_flagged(self):
        fenced = MINIMAL_STATE.replace(
            "1. 示例。", "1. 示例。\n\n```\n## Current objective\n```\n"
        )
        self.assertEqual(self._run(fenced), 0)

    def test_unknown_evidence_ref_fail(self):
        bad = MINIMAL_STATE.replace("- E-001：冒烟通过。", "- E-999：不存在。")
        self.assertNotEqual(self._run(bad), 0)

    def test_char_overrun_fail(self):
        long_line = "x" * 21000
        bloated = MINIMAL_STATE.replace("目标一段。", "目标一段。" + long_line)
        self.assertGreater(len(bloated), 20000)
        self.assertNotEqual(self._run(bloated), 0)


if __name__ == "__main__":
    unittest.main()
