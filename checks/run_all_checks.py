"""
AI-REOS 校验统一入口（Tier 0）。

用途
----
一次运行全部检查，供人工执行或 pre-commit hook 调用，避免"要记住多个脚本名分别手动跑"。
本身不重复实现任何校验逻辑，只负责调度同目录下的各检查模块并汇总结果。

检查清单
--------
- verify_ai_state            治理文件齐备、STATE 章节、EVIDENCE 按 schema 校验、引用一致性
- verify_task_specs          Task Spec 按 schema 校验 + convergence/plan 一致性
- verify_experiment_metadata 实验元数据按 schema 校验 + registry 一致性
- verify_claims              claim 按 schema 校验 + "fact 必须有 Evidence"规则
- check_adapter_drift        .trae 薄包装与 AGENTS.md generated 区块是否与真源漂移
- verify_world_registry      世界观层一致性 + 三条召回探针（v1.3.2；world 未部署自动跳过，
                              模式由 aiops/world/coverage.yaml 的 coverage.status 决定：
                              partial=report-only，full=enforce）

设计说明
--------
只依赖标准库与 PyYAML，不使用命令行参数解析。对外暴露 main(root: Path) -> int。
单个检查失败不会中断其余检查，全部跑完后统一汇总并以非 0 退出码表示存在失败项。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Tuple

import check_adapter_drift
import verify_ai_state
import verify_claims
import verify_experiment_metadata
import verify_task_specs
import verify_world_registry_entry

# 模块级配置：检查项清单集中定义，新增检查在此登记即可
REPO_ROOT = Path(__file__).resolve().parents[2]
AIOPS_ROOT = REPO_ROOT / "aiops"

CHECKS: Tuple[Tuple[str, Callable[[Path], int]], ...] = (
    ("verify_ai_state", verify_ai_state.main),
    ("verify_task_specs", verify_task_specs.main),
    ("verify_experiment_metadata", verify_experiment_metadata.main),
    ("verify_claims", verify_claims.main),
    ("check_adapter_drift", check_adapter_drift.main),
    ("verify_world_registry", verify_world_registry_entry.main),
)


def main(root: Path) -> int:
    """
    依次运行全部检查并汇总。

    Parameters
    ----------
    root : Path
        aiops 目录路径（测试中可传入临时目录）。

    Returns
    -------
    exit_code : int
        0 表示全部通过，非 0 表示存在失败项。
    """
    root = Path(root)
    print(f"== run_all_checks: {root} ==")

    results: List[Tuple[str, int, float]] = []
    for name, runner in CHECKS:
        started = time.perf_counter()
        code = runner(root)
        elapsed = time.perf_counter() - started
        results.append((name, code, elapsed))
        print()

    print("== 汇总 ==")
    failed = 0
    for name, code, elapsed in results:
        status = "PASS" if code == 0 else "FAIL"
        if code != 0:
            failed += 1
        print(f"[{status}] {name}  ({elapsed:.2f}s)")

    total = sum(elapsed for _, _, elapsed in results)
    if failed == 0:
        print(f"[OK] 全部 {len(results)} 项检查通过，总耗时 {total:.2f}s")
        return 0
    print(f"[FAIL] {failed}/{len(results)} 项检查失败，总耗时 {total:.2f}s")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(AIOPS_ROOT))
