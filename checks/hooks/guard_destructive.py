# -*- coding: utf-8 -*-
"""ZCode PreToolUse 守卫 hook：程序化拦截对结果目录的破坏性删除。

把 AGENTS.md 的"不得未经批准删除/覆盖结果目录"从提示词约束变成程序约束：
- 命中保护规则 → exit 2（ZCode 对 PreToolUse 的 2 视为 deny），stderr 给出理由与豁免途径；
- 未命中 / 任何内部错误 → exit 0 放行（fail-open：守卫只拦明确危险，绝不误伤正常工作）。
- 内置 canary 字符串 ZCODE_HOOK_CANARY：构造任意含该字符串的 echo 命令应被拦截，
  用于活体验证 hook 链路是否真的在工作（自测锚点，勿删）。

保护清单单一真源（v1.3）：aiops/policy.yaml 的 protected_dirs。
本脚本内置的 DEFAULT_* 只是 policy.yaml 缺失/损坏时的兜底（通用最小集），
项目特定的子路径/前缀一律写进 policy.yaml，不要改本脚本。

验证方式（手工单测）：
    echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf results"}}' | python aiops/checks/hooks/guard_destructive.py
    # 期望：exit 2；把命令换成 "git status" → 期望 exit 0
"""
import json
import re
import sys
from pathlib import Path

import yaml

CANARY = "ZCODE_HOOK_CANARY"

# policy.yaml 位置：本脚本位于 aiops/checks/hooks/ 下
POLICY_PATH = Path(__file__).resolve().parents[2] / "policy.yaml"

# 兜底保护清单（通用最小集）：仅当 policy.yaml 缺失/损坏时使用
DEFAULT_COMPONENTS = ["results", "outputs", "data", "archive", "runs", "logs", "model_parameters", ".git"]
DEFAULT_SUBPATHS: list = []
DEFAULT_PREFIXES: list = []

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


def _load_policy() -> dict:
    """
    读取保护清单（aiops/policy.yaml）。

    Returns
    -------
    policy : dict
        含 components / subpaths / prefixes 三个字符串列表；文件缺失、损坏或
        字段类型不对时逐项回退到内置默认值（fail-open）。
    """
    policy = {
        "components": list(DEFAULT_COMPONENTS),
        "subpaths": list(DEFAULT_SUBPATHS),
        "prefixes": list(DEFAULT_PREFIXES),
    }
    try:
        payload = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) or {}
        protected = payload.get("protected_dirs") or {}
        for key in ("components", "subpaths", "prefixes"):
            value = protected.get(key)
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                policy[key] = [v.lower() for v in value]
    except Exception:  # noqa: BLE001 — policy 读不到就用默认，绝不因配置问题让守卫崩溃
        pass
    return policy


def _norm(token: str) -> str:
    t = token.strip().strip("\"'")
    t = re.sub(r"^(\./)+", "", t)
    return t.lower().replace("\\", "/")


def _touches_protected(command: str, policy: dict) -> bool:
    components = set(policy["components"])
    subpaths = policy["subpaths"]
    prefixes = policy["prefixes"]
    for raw in command.split():
        parts = [p.lower() for p in _SPLIT_PARTS.split(_norm(raw)) if p]
        for i, part in enumerate(parts):
            if part in components:
                return True
            joined = "/".join(parts[max(0, i) : i + 2])
            if any(joined.startswith(sub) for sub in subpaths):
                return True
            # 子路径也可能横跨 >2 个 token 的切片（如 docs/homogenization_verification/x）。
            # 反向前缀比对仅当切片本身含路径分隔符（即真实路径前缀）才进行：
            # 否则裸单词（循环变量 d、目录名 docs/mechanical 等）会经 sub.startswith
            # 前缀匹配被恒真命中，误拦一切含该 token 的 rm 命令（D-019 修复）。
            sl = "/".join(parts[i : i + 2])
            if "/" in sl and any(
                sl.startswith(sub) or sub.startswith(sl) for sub in subpaths
            ):
                return True
            # prefixes 通配（如 parent/datasets*：datasets、datasets_xxx、datasets19hole 等）
            if i + 1 < len(parts):
                joined2 = parts[i] + "/" + parts[i + 1]
                if any(joined2.startswith(pre) for pre in prefixes):
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
    if destructive:
        policy = _load_policy()
        if _touches_protected(command, policy):
            shown = "/".join(
                policy["components"] + policy["subpaths"] + policy["prefixes"]
            )
            sys.stderr.write(
                f"[AI-REOS guard] 已拦截：该命令会对受保护目录（{shown}）做删除操作。\n"
                "[AI-REOS guard] 保护清单单一真源：aiops/policy.yaml（修改保护范围改那里）。\n"
                "[AI-REOS guard] 如确需执行，先取得用户明确批准；临时绕过请移除命令中的"
                "保护路径或由用户在终端自行执行。守卫脚本：aiops/checks/hooks/guard_destructive.py"
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
