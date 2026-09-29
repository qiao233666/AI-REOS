# -*- coding: utf-8 -*-
"""ZCode SessionStart hook：会话启动时确定性注入 AI-REOS 状态指针。

设计原则（对应 .trae/rules/aiops.md 在 Trae 侧的角色）：
- 只放"指针"，不放正文：真源永远是 aiops/ 下的文件，本脚本输出的文本约 20 行，
  告诉 Agent 去哪里读、什么时候读。
- 输出 hookSpecificOutput JSON；若 ZCode 校验失败则该注入被丢弃（exit 0，不影响会话），
  底层保障仍由 AGENTS.md（确定性注入）承担，本 hook 是增量强化而非单点依赖。

验证方式：
    echo '{}' | python aiops/checks/hooks/session_start_context.py
    # 期望：exit 0，stdout 为一行合法 JSON
"""
import json
import sys

# 与 .trae/rules/aiops.md 同源的触发表（薄 delta，唯一同步点是这一段文本）
CONTEXT = """[AI-REOS 自动注入] 本仓库的跨工具治理真源是 aiops/（AGENTS.md 中 BEGIN GENERATED 区块为索引）。
开始非琐碎任务前，先读一次 aiops/STATE.md（当前目标/已确认事实/活跃假设/阻塞项）。
按需读取的触发条件：
- 要下科学结论 → aiops/EVIDENCE.yaml（证据）+ aiops/FAILURES.md（已被证伪的做法，勿重蹈覆辙）
- 要改代码 → aiops/protocols/code-change-protocol.md；要跑仿真/批量实验 → aiops/protocols/experiment-protocol.md
- 派子代理 → aiops/protocols/subagent-contract.md；任务交接 → aiops/protocols/handoff-protocol.md
- 依据不充分的前提 → aiops/ASSUMPTIONS.md；历史决策 → aiops/DECISIONS.md
收尾前：提交由 pre-commit 门禁自动跑 aiops/checks/run_all_checks.py（5 项校验）。
禁止未经批准删除/覆盖 results/、outputs/、data/、archive/、runs/、model_parameters/ 等结果目录
（ZCode 侧由 PreToolUse 守卫 hook 程序化拦截；守卫脚本：aiops/checks/hooks/guard_destructive.py）。"""


def main() -> int:
    # 消费 stdin（hook 输入），解析失败也不影响：本脚本不依赖输入内容
    try:
        sys.stdin.read()
    except Exception:
        pass
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": CONTEXT,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
