# -*- coding: utf-8 -*-
"""verify_world_registry_entry — run_all_checks 的世界观层检查入口（适配层）。

模式语义（E-083 实施序列第 4 步 + E-085 步骤 6 收口，HANDOFF_WORLD_REGISTRY_20261003.md）：
- RUNNER_MODE="auto"：读 aiops/world/coverage.yaml 的 coverage.status——
  full → enforce（内部 FAIL 即门禁失败）；partial/缺失/解析失败 → report-only
  （--report-only，exit 恒 0）。与 E-085 步骤 6 一致：coverage partial 才允许
  report-only，full 下异常即失败；模式唯一来源是 coverage.yaml。
- 未部署 world（校验器文件缺失）→ 提示后跳过，不阻塞未启用世界层的项目；
- 失败时绝不禁用 registry 本身——视图失效按 UNKNOWN 处理，不阻塞只读修复性工作。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

AIOPS = Path(__file__).resolve().parents[1]
VERIFY = AIOPS / "world" / "tools" / "verify_world_registry.py"
COVERAGE = AIOPS / "world" / "coverage.yaml"
PY = sys.executable


def _enforce_mode() -> bool:
    """coverage.status == full 才 enforce；其余一律 report-only（fail-safe 方向）。"""
    try:
        import yaml
        cov = yaml.safe_load(COVERAGE.read_text(encoding="utf-8")) or {}
        return (cov.get("coverage") or {}).get("status") == "full"
    except Exception:
        return False


def main(root: Path) -> int:
    del root  # 校验器自含路径
    if not VERIFY.exists():
        print("[WARN] 世界层校验器缺失（world registry 未部署？）——跳过")
        return 0
    enforce = _enforce_mode()
    args = [PY, str(VERIFY)]
    if not enforce:
        args.append("--report-only")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("[WARN] 世界层校验超时——REGISTRY_UNAVAILABLE（按 UNKNOWN 处理，不阻塞）")
        return 0
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-6:])
    print(f"[mode] {'enforce' if enforce else 'report-only'}（coverage.yaml 唯一模式来源）")
    print(tail)
    if enforce and r.returncode != 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parents[2]))
