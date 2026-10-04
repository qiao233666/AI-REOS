# -*- coding: utf-8 -*-
"""verify_world_registry_entry — run_all_checks 的世界观层检查入口（适配层）。

模式语义（E-083 实施序列第 4 步 + E-085 步骤 6 收口 + SOL57 审查收紧，HANDOFF_WORLD_REGISTRY_20261003.md）：
- RUNNER_MODE="auto"：读 aiops/world/coverage.yaml 的 coverage.status——
  full → enforce（内部 FAIL 即门禁失败）；partial → report-only（--report-only，exit 恒 0）；
  与 E-085 步骤 6 一致：coverage partial 才允许 report-only，full 下异常即失败；
  模式唯一来源是 coverage.yaml。
- fail-open 只允许一种情况：**coverage.yaml 不存在**（world 未部署的骨架项目）——
  此时提示跳过。coverage 存在但损坏/解析失败、校验器缺失、执行超时（enforce 下）
  一律按违规处理（SOL57 审查：fail-open 与"full 下异常失败"承诺不符）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

AIOPS = Path(__file__).resolve().parents[1]
VERIFY = AIOPS / "world" / "tools" / "verify_world_registry.py"
COVERAGE = AIOPS / "world" / "coverage.yaml"
PY = sys.executable


def _mode() -> str:
    """返回 'enforce' / 'report-only' / 'undeployed'。"""
    if not COVERAGE.is_file():
        return "undeployed"
    try:
        import yaml
        cov = yaml.safe_load(COVERAGE.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[FAIL] coverage.yaml 存在但不可解析（按违规处理，不 fail-open）: {exc}")
        return "broken"
    status = (cov.get("coverage") or {}).get("status")
    if status == "full":
        return "enforce"
    if status == "partial":
        return "report-only"
    print(f"[FAIL] coverage.status 非法（{status!r}）——按违规处理")
    return "broken"


def main(root: Path) -> int:
    del root  # 校验器自含路径
    mode = _mode()
    if mode == "undeployed":
        if not VERIFY.exists():
            print("[WARN] 世界层未部署（无 coverage.yaml 与校验器）——跳过")
            return 0
        print("[FAIL] 存在校验器但 coverage.yaml 缺失——世界层部署不完整")
        return 1
    if not VERIFY.exists():
        print("[FAIL] coverage.yaml 存在但校验器缺失——世界层部署不完整")
        return 1
    args = [PY, str(VERIFY)]
    if mode == "report-only":
        args.append("--report-only")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        if mode == "enforce":
            print("[FAIL] 世界层校验超时——REGISTRY_UNAVAILABLE（enforce 下按违规处理）")
            return 1
        print("[WARN] 世界层校验超时——REGISTRY_UNAVAILABLE（report-only 不阻塞）")
        return 0
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-6:])
    print(f"[mode] {mode}（coverage.yaml 唯一模式来源）")
    print(tail)
    if r.returncode != 0:
        if mode == "enforce":
            return 1
        print("[report-only] 校验器异常不阻断（coverage=partial 迁移期）")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parents[2]))
