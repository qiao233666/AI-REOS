# -*- coding: utf-8 -*-
"""ZCode PreToolUse 守卫 hook：程序化拦截对结果目录的破坏性删除。

把 AGENTS.md 的"不得未经批准删除/覆盖结果目录"从提示词约束变成程序约束：
- 命中保护规则 → exit 2（ZCode 对 PreToolUse 的 2 视为 deny），stderr 给出理由与豁免途径；
- 未命中 / 任何内部错误 → exit 0 放行（fail-open：守卫只拦明确危险，绝不误伤正常工作）。
- 内置 canary 字符串 ZCODE_HOOK_CANARY：构造任意含该字符串的命令应被拦截，
  用于活体验证 hook 链路是否真的在工作（自测锚点，勿删）。

保护对象（与 AGENTS.md / aiops 协议一致）：results、outputs、data、archive、runs、logs、
model_parameters、mechanical/outputs、graph_hao/datasets*、docs/homogenization_verification、.git。

验证方式（手工单测）：
    echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf results"}}' | python aiops/checks/hooks/guard_destructive.py
    # 期望：exit 2；把命令换成 "git status" → 期望 exit 0
"""
import json
import re
import sys
from pathlib import Path

CANARY = "ZCODE_HOOK_CANARY"

# 受保护目录：以路径组件匹配（前后必须是字符串边界或 / \ 空白 引号 等）
_PROTECTED_COMPONENTS = [
    "results",
    "outputs",
    "data",
    "archive",
    "runs",
    "logs",
    "model_parameters",
    ".git",
]
# 受保护子路径：带父目录前缀，避免误伤同名组件的其他用途
# 受保护子路径：带父目录前缀，避免误伤同名组件的其他用途。
# docs/homogenization_verification 是权威验证记录链（D-016：整目录只读保护，
# 内容更新须经用户明确批准后由 AI 提议、用户确认或人工执行）。
_PROTECTED_SUBPATHS = [
    "mechanical/outputs",
    "graph_hao/datasets",
    "docs/homogenization_verification",
]

# 活体探针判定：仅当命令主体就是在打印 canary（echo/printf/Write-Output）时才拦截。
# 文档、提交说明、脚本参数中"提到"该字符串不算探针（D-015：修复对文档写入的误拦）。
_RE_CANARY_PROBE = re.compile(
    r"^\s*(?:echo|printf|Write-Output)\s+['\"]?ZCODE_HOOK_CANARY['\"]?\s*(?:[>|;&]|$)",
    re.IGNORECASE,
)

# 破坏性动词：rm（任意形式，含删单文件）、Windows del/rd/rmdir、PowerShell Remove-Item
_RE_RM = re.compile(r"(^|[\s;&|(])rm(\s|$)", re.IGNORECASE)
_RE_WIN_DELETE = re.compile(
    r"(^|[\s;&|])((del|rd|rmdir)(\s|$)|remove-item(\s|$))", re.IGNORECASE
)
# git clean 带任何 -f/-x 组合 = 删除未跟踪（含全部被 gitignore 的结果目录）
_RE_GIT_CLEAN_FORCE = re.compile(r"git\s+clean\b[^|;&]*-(?:[a-z]*f|x)", re.IGNORECASE)

# 路径组件切分：把命令拆成 token 后再按 / \ 拆组件
_SPLIT_PARTS = re.compile(r"[/\\\s\"'()&|;]+")

# fail-open 事件的落地日志（scratch 双区制的 scratch 侧，不进版本库）
_FAIL_LOG = Path(__file__).resolve().parents[3] / "scratch" / "guard_failopen.log"


def _norm(token: str) -> str:
    t = token.strip().strip("\"'")
    t = re.sub(r"^(\./)+", "", t)
    return t.lower().replace("\\", "/")


def _touches_protected(command: str) -> bool:
    for raw in command.split():
        parts = [p.lower() for p in _SPLIT_PARTS.split(_norm(raw)) if p]
        for i, part in enumerate(parts):
            if part in _PROTECTED_COMPONENTS:
                return True
            joined = "/".join(parts[max(0, i) : i + 2])
            if any(joined.startswith(sub) for sub in _PROTECTED_SUBPATHS):
                return True
            # 子路径也可能横跨 >2 个 token 的切片（如 docs/homogenization_verification/x）
            if any(
                "/".join(parts[i : i + 2]).startswith(sub)
                or sub.startswith("/".join(parts[i : i + 2]))
                for sub in _PROTECTED_SUBPATHS
            ):
                return True
            # graph_hao/datasets* 通配（datasets、datasets_xxx、datasets19hole 等）
            if (
                i + 1 < len(parts)
                and parts[i] == "graph_hao"
                and parts[i + 1].startswith("datasets")
            ):
                return True
    return False


def main() -> int:
    # D-017：整脚本 catch-all fail-open。守卫自身的任何异常（缺依赖、stdin 异常、
    # 正则错误等）一律放行——hook 死锁过整个会话（cd 都被拦）比漏拦一次删除
    # 严重得多；git 可回滚是最终防线，守卫只是纵深防御的一层。
    try:
        return _main_inner()
    except Exception as exc:  # noqa: BLE001
        try:
            with open(_FAIL_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"[guard] fail-open: {exc!r}\n")
        except OSError:
            pass
        return 0


def _main_inner() -> int:
    try:
        payload = json.load(sys.stdin)
        command = str(
            (payload.get("tool_input") or {}).get("command")
            or (payload.get("tool_input") or {}).get("Command")
            or ""
        )
    except Exception:
        return 0  # fail-open：解析不了就不拦

    if not command:
        return 0

    if _RE_CANARY_PROBE.search(command):
        sys.stderr.write(
            "[AI-REOS guard] canary 命中：守卫 hook 链路确认在工作（本拦截为自测设计）。"
        )
        return 2

    destructive = bool(
        _RE_RM.search(command)
        or _RE_WIN_DELETE.search(command)
        or _RE_GIT_CLEAN_FORCE.search(command)
    )
    if destructive and _touches_protected(command):
        sys.stderr.write(
            "[AI-REOS guard] 已拦截：该命令会对受保护的结果目录（results/outputs/data/"
            "archive/runs/logs/model_parameters/mechanical/outputs/graph_hao/datasets*/"
            "docs/homogenization_verification/.git）做删除操作。\n"
            "[AI-REOS guard] 如确需执行，先取得用户明确批准；临时绕过请移除命令中的"
            "保护路径或由用户在终端自行执行。守卫脚本：aiops/checks/hooks/guard_destructive.py"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
