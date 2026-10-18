# -*- coding: utf-8 -*-
"""科研路线复盘检查的**观察性包装**（观察期准入）。

为什么需要包装
--------------
`verify_research_reviews.py` 在发现坏文件时返回 1。若直接把它的退出码
并入 `run_all_checks.py` 的汇总，一个还没写好的 RD 文件就会阻断提交，
而现阶段机制仍在观察期——这会把"记录不完整"误当"科研决策错误"。

本模块因此把**报告结果**与**阻断策略**分开：

- `report_only = True`（默认）：发现 FAIL 时打印 `[report-only]`，
  汇总记 WARN，返回 0，不阻断。
- `report_only = False`：切换为强制门，FAIL 直接传播。

切换只在观察期验收通过后由人显式执行；不通过环境变量偷偷切换，
避免"某次提交临时放宽、之后再也没收紧"。

另：无 RD 文件时**必须报 SKIP 而不是 PASS**。`verify_claims` 曾经
在零记录时打印"校验通过"，属于同一族假安全感问题（F-031 教训）。
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import List, Tuple

import verify_research_reviews as vrr

# 观察期开关：改为 False 之前，必须先用本仓自己的真实复盘记录完成一次回放验收。
# 未完成验收就强制阻断，会把"记录不完整"误判为"科研决策错误"。
REPORT_ONLY = True


def _run_audit(root: Path) -> Tuple[int, List[str]]:
    """运行 audit 并捕获输出，返回 (退出码, 输出行)。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = vrr.check(root, "audit", None)
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    return code, lines


def main(root: Path) -> int:
    """观察性入口，签名为 run_all_checks 期望的 Callable[[Path], int]。

    `run_all_checks` 传入的是 **aiops 目录**，而 `verify_research_reviews`
    以**仓库根**为基准（它要找 `aiops/EVIDENCE.yaml`、`aiops/DECISIONS.md`）。
    这里统一换算成仓库根，避免两者基准不一致导致"永远找不到 RD 文件"。
    """
    root = Path(root)
    repo = root.parent if root.name == "aiops" else root

    try:
        code, lines = _run_audit(repo)
    except Exception as exc:  # noqa: BLE001 - 环境异常不应静默变成通过
        print(f"[FAIL] 科研路线检查自身异常：{exc}")
        return 0 if REPORT_ONLY else 1

    for line in lines:
        print(line)

    if code == 0:
        # audit 在无文件时返回 0 且打印 SKIP：如实转述，不升级为 PASS。
        return 0

    prefix = "[report-only] FAIL 不阻断（观察期）" if REPORT_ONLY else "[FAIL] 阻断"
    print(f"{prefix}：科研路线复盘结构检查未通过（见上）")
    return 0 if REPORT_ONLY else 1


if __name__ == "__main__":
    sys.exit(main(Path.cwd()))
