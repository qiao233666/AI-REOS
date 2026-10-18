#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研路线判断的结构门（v2）。

本模块检查**结构、引用与一致性**：
  - RD 文件符合 research-review.schema.json v2；
  - 引用的 E-/D- 编号真实存在；
  - 三类判断有对象与版本；
  - primary 与 selected_actions、options.ruling 一致；
  - 同一对象不得同时退役与继续投入；
  - approval 与 approval_ref/approved_at/input_digest 一致；
  - supersedes 链不悬空、不自指；
  - gate 语义（close_line 需 stop_pause、extra_budget 需预算与人工批准等）。

它**不能**判定：
  - 理由是否充分、科学问题是否有价值、方法是否真有区分力；
  - 授权是否真由用户签署（approval_ref 只证明"引用了某个 D 条目"）；
  - 证据分析是否正确。

用法
----
    python aiops/checks/verify_research_reviews.py --repo . --gate audit
    python aiops/checks/verify_research_reviews.py --repo . --line RL-xxx --gate decision
    python aiops/checks/verify_research_reviews.py --repo . --line RL-xxx --gate close_line

退出码：0 = 通过/跳过；1 = 结构或语义失败；2 = 环境或输入不可用。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

try:
    from jsonschema import Draft7Validator, FormatChecker
except ImportError:  # 治理环境不保证有 jsonschema（见 ASSUMPTIONS A-002）
    Draft7Validator = None
    FormatChecker = None

OPTIONS: Tuple[str, ...] = (
    "narrow_write",
    "repair_method",
    "pivot_question",
    "stop_pause",
    "continue_existing",
)
GATES: Tuple[str, ...] = (
    "audit",
    "decision",
    "scope_freeze",
    "close_line",
    "extra_budget",
    "evidence_update",
)
EMPTY_PHRASES: Set[str] = {
    "TODO", "TBD", "UNKNOWN", "进一步研究", "需要增加免责声明",
    "待填写", "暂无", "无", "n/a", "N/A", "-",
}
KEEP_KINDS: Tuple[str, ...] = (
    "repair_method", "pivot_question", "continue_existing", "narrow_write",
)

LINE_ID_RE = re.compile(r"^RL-[0-9]{3,}$")
DECISION_ID_RE = re.compile(r"^D-[0-9]+$")


# --------------------------------------------------------------------------
# 基础 IO
# --------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _resolve_schema(repo: Path, script: Path) -> Path:
    installed = repo / "aiops" / "schemas" / "research-review.schema.json"
    if installed.is_file():
        return installed
    # 测试/离线检查路径；不构成已安装的证据。
    return script.parent.parent / "schemas" / "research-review.schema.json"


def _collect_files(repo: Path, file: Optional[str]) -> List[Path]:
    if file:
        candidate = Path(file)
        if not candidate.is_absolute():
            candidate = repo / candidate
        return [candidate]
    folder = repo / "aiops" / "research_reviews"
    if not folder.is_dir():
        return []
    return sorted({*folder.glob("RD-*.yaml"), *folder.glob("RD-*.yml")})


# --------------------------------------------------------------------------
# 账本索引
# --------------------------------------------------------------------------

def _evidence_ids(repo: Path) -> Optional[Set[str]]:
    path = repo / "aiops" / "EVIDENCE.yaml"
    if not path.is_file():
        return None
    data = _load_yaml(path)
    if not isinstance(data, list):
        raise ValueError("aiops/EVIDENCE.yaml 不是列表，无法核对引用")
    ids: List[str] = [
        x["id"] for x in data
        if isinstance(x, dict) and isinstance(x.get("id"), str)
    ]
    if len(set(ids)) != len(ids):
        raise ValueError("证据账本存在重复 ID，不能安全核对引用")
    return set(ids)


def _decision_ids(repo: Path) -> Set[str]:
    """从 DECISIONS.md 的 '## D-xxx' 标题提取真实存在的 D 编号。"""
    path = repo / "aiops" / "DECISIONS.md"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^## (D-[0-9]+)", text, re.M))


# --------------------------------------------------------------------------
# 单文件语义检查
# --------------------------------------------------------------------------

def _refs_of(payload: Dict[str, Any]) -> List[str]:
    refs: List[str] = list(payload["trigger"]["evidence_refs"])
    for key in OPTIONS:
        refs.extend(payload["options"][key]["evidence_refs"])
    for claim in payload["judgments"]["claim"]:
        refs.extend(claim.get("evidence_refs") or [])
    return refs


def _is_placeholder(text: str) -> bool:
    """判断文本是否只是占位符。

    三种命中：整串等于占位词；整串以 '<' 开头（未填的模板槽）；
    或去掉占位词后不再剩实质内容（如 "TODO TODO TODO"）。
    """
    stripped = text.strip()
    if stripped in EMPTY_PHRASES or stripped.startswith("<"):
        return True
    remainder = stripped
    for phrase in EMPTY_PHRASES:
        remainder = remainder.replace(phrase, "")
    # 剩余的标点/空白不算内容
    return not re.sub(r"[\s\W_]+", "", remainder)


def _semantic_errors(
    payload: Dict[str, Any],
    evidence: Optional[Set[str]],
    decisions: Set[str],
) -> List[str]:
    errors: List[str] = []
    rec = payload["recommendation"]
    primary = rec["primary"]
    actions = payload["selected_actions"]

    # --- 1. primary 必须与 options.ruling 和 selected_actions 一致 -----------
    if payload["options"][primary]["ruling"] != "recommend":
        errors.append(f"主推荐 {primary} 在 options 中的 ruling 不是 recommend")

    executed = [a for a in actions if a["mode"] == "execute"]
    if not any(a["kind"] == primary for a in executed):
        errors.append(
            f"主推荐 {primary} 没有对应的 mode=execute 动作；"
            "selected_actions 中缺少实际承担该处置的对象"
        )

    # --- 2. 同一对象不得同时退役与继续投入 ----------------------------------
    stop_targets = {
        a["target_ref"] for a in executed if a["kind"] == "stop_pause"
    }
    keep_targets = {
        a["target_ref"] for a in executed if a["kind"] in KEEP_KINDS
    }
    both = stop_targets & keep_targets
    if both:
        errors.append(
            "同一对象同时被退役与继续投入：" + "、".join(sorted(both))
        )

    # --- 3. candidate_only 不得携带任务；execute 必须有任务 ----------------
    for action in actions:
        if action["mode"] == "candidate_only" and action["task_ref"]:
            errors.append(
                f"动作 {action['action_id']} 是 candidate_only，不应绑定执行任务 "
                f"{action['task_ref']}（候选不产生执行许可）"
            )
        if action["mode"] == "execute" and not action["task_ref"]:
            errors.append(
                f"动作 {action['action_id']} 是 execute，必须绑定执行任务"
            )

    # --- 4. 路线判断与 lifecycle / stop 动作的一致性 -----------------------
    value_verdict = payload["judgments"]["line"]["value_verdict"]
    to_state = payload["lifecycle"]["to"]
    if value_verdict == "retire" and to_state != "retired":
        errors.append(
            "judgments.line.value_verdict=retire 但 lifecycle.to 不是 retired"
        )
    if value_verdict == "retire":
        if not any(a["kind"] == "stop_pause" for a in executed):
            errors.append(
                "路线判断为 retire，但 selected_actions 中没有 mode=execute "
                "的 stop_pause 动作"
            )
    if to_state == "retired" and payload["options"]["stop_pause"]["ruling"] != "recommend":
        errors.append(
            "lifecycle.to=retired 但 options.stop_pause 的 ruling 不是 recommend"
        )
    if to_state == "completed":
        errors.append(
            "lifecycle.to=completed 需要显式满足该版本的 answer_criterion；"
            "当前检查器不自动判定，请改用 retired/paused 并说明依据"
        )

    # --- 5. approval 字段一致性 --------------------------------------------
    approval = payload["approval"]
    if approval["status"] == "approved":
        if not approval["approval_ref"]:
            errors.append("approval.status=approved 但 approval_ref 为空")
        if not approval["approved_at"]:
            errors.append("approval.status=approved 但 approved_at 为空")
        if not approval["input_digest"]:
            errors.append(
                "approval.status=approved 但 input_digest 为空；"
                "工作区可能有未提交修改，source_commit 不足以冻结输入"
            )
    else:
        if approval["approval_ref"] is not None:
            errors.append("未获批准却提前填写了 approval_ref")
        if approval["approved_at"] is not None:
            errors.append("未获批准却填写了 approved_at")

    # --- 6. approval_ref 必须是真实存在的 D 条目 ---------------------------
    ref = approval["approval_ref"]
    if ref:
        if not DECISION_ID_RE.match(ref):
            errors.append(f"approval_ref 格式非法：{ref}")
        elif decisions and ref not in decisions:
            errors.append(
                f"approval_ref 指向不存在的决策条目 {ref}；"
                "格式合法不等于授权存在"
            )

    # --- 7. 占位词与空话 ---------------------------------------------------
    for key in ("why_over_alternatives", "next_action",
                "expected_knowledge_gain", "change_my_mind_if"):
        if _is_placeholder(rec[key]):
            errors.append(f"recommendation.{key} 只有模板占位词")

    # --- 8. 选择修改方法/问题时必须有判别设计 ------------------------------
    for kind in ("repair_method", "pivot_question"):
        option = payload["options"][kind]
        if option["ruling"] == "recommend":
            disc = option.get("discriminator")
            if not disc or _is_placeholder(disc):
                errors.append(
                    f"建议 {kind} 但未给出最小有区分力的观测/判别过程；"
                    "不能只写进一步研究"
                )

    # --- 9. 无可探索机会时不得同时列问题 -----------------------------------
    opp = payload["opportunities"]
    if opp["assessment"] == "no_viable_opportunity" and opp["questions"]:
        errors.append("声明 no_viable_opportunity 却列出待检验问题")

    # --- 10. 引用存在性 ----------------------------------------------------
    if evidence is not None:
        for ref_id in sorted(set(_refs_of(payload)) - evidence):
            errors.append(f"引用不存在的 EVIDENCE ID: {ref_id}")

    # --- 11. derived_from 指向的裁决必须存在 -------------------------------
    derived = payload["line"]["derived_from"]
    if derived and decisions and derived["decision_ref"] not in decisions:
        errors.append(
            "line.derived_from.decision_ref 指向不存在的决策 "
            f"{derived['decision_ref']}"
        )

    return errors


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------

def _gate_errors(payload: Dict[str, Any], gate: str) -> List[str]:
    errors: List[str] = []
    rec = payload["recommendation"]
    primary = rec["primary"]
    manuscript = rec["manuscript_action"]
    approval = payload["approval"]
    executed = [a for a in payload["selected_actions"] if a["mode"] == "execute"]

    if gate == "close_line":
        if primary != "stop_pause":
            errors.append(
                "关闭研究线需要主推荐为 stop_pause 的复盘；"
                "任务失败并不等于研究线无价值"
            )
        if approval["status"] != "approved" or not approval["approval_ref"]:
            errors.append(
                "终止重要研究线必须经真实负责人授权，Agent 不可自批"
            )

    elif gate == "scope_freeze":
        if manuscript not in ("narrow", "write_existing_supported_claims",
                              "withdraw_invalid_claims"):
            errors.append(
                "冻结稿件范围需要明确的 manuscript_action"
                "（narrow / write_existing_supported_claims / "
                "withdraw_invalid_claims）"
            )
        # 被否决的复盘不得用来冻结稿件；唯一例外是撤下错误主张（纠错）。
        if approval["status"] == "rejected" and manuscript != "withdraw_invalid_claims":
            errors.append(
                "复盘的审批状态为 rejected，不能用于冻结稿件范围；"
                "撤下错误主张请用 manuscript_action=withdraw_invalid_claims"
            )

    elif gate == "extra_budget":
        if approval["status"] != "approved" or not approval["approval_ref"]:
            errors.append(
                "资源续投必须经真实负责人授权，Agent 不可自批"
            )
        ceiling = rec["resource_ceiling"]
        if ceiling["cpu_hours"] is None and ceiling["human_minutes"] is None:
            errors.append(
                "未提出任何资源上限，禁止申请开放式预算"
                "（null 表示未核定，不是无限）"
            )
        # 申请预算必须对应实际研究动作，不能靠暂停或免责声明取得。
        if not any(a["kind"] in ("repair_method", "pivot_question", "continue_existing")
                   for a in executed):
            errors.append(
                "新增预算必须对应实际受评估的研究动作"
                "（repair_method / pivot_question / continue_existing）"
            )

    return errors


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def check(
    repo: Path,
    gate: str,
    line: Optional[str] = None,
    file: Optional[str] = None,
) -> int:
    if gate == "evidence_update":
        print("[PASS] 证据纠错/负证据更新不要求路线复盘；未验证任何研究结论")
        return 0

    if Draft7Validator is None:
        print("[FAIL] 缺少 jsonschema，无法执行结构校验（治理环境需安装）")
        return 2

    try:
        schema_path = _resolve_schema(repo, Path(__file__).resolve())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft7Validator.check_schema(schema)
    except Exception as exc:  # noqa: BLE001 - 任何 schema 故障都报环境错误
        print(f"[FAIL] 路线复盘 schema 不可用：{exc}")
        return 2

    try:
        evidence = _evidence_ids(repo)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"[FAIL] 证据账本不可用：{exc}")
        return 2
    if gate != "audit" and evidence is None:
        print("[FAIL] 缺少真实 aiops/EVIDENCE.yaml，不能放行正式路线决策")
        return 2

    decisions = _decision_ids(repo)
    files = _collect_files(repo, file)
    if not files:
        if gate == "audit" and line is None:
            print("[SKIP] 未发现任何研究路线复盘文件；没有声称科研决策校验通过")
            return 0
        print(f"[FAIL] 研究线 {line or '(未指定)'} 缺少路线复盘工件")
        return 1

    validator = Draft7Validator(schema, format_checker=FormatChecker())
    documents: List[Tuple[Path, Dict[str, Any]]] = []
    has_failure = False
    seen_ids: Set[str] = set()

    for path in files:
        if not path.is_file():
            print(f"[FAIL] 路线复盘文件不存在：{path}")
            has_failure = True
            continue
        try:
            doc = _load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            print(f"[FAIL] {path.name}: YAML 读取/解析失败: {exc}")
            has_failure = True
            continue
        if not isinstance(doc, dict):
            print(f"[FAIL] {path.name}: 顶层不是映射")
            has_failure = True
            continue

        failures = [
            f"{'.'.join(map(str, err.absolute_path)) or '(root)'}: {err.message}"
            for err in validator.iter_errors(doc)
        ]
        if failures:
            for message in failures:
                print(f"[FAIL] {path.name} {message}")
            has_failure = True
            continue

        if doc["review_id"] in seen_ids:
            failures.append(f"review_id 重复：{doc['review_id']}")
        seen_ids.add(doc["review_id"])

        failures.extend(_semantic_errors(doc, evidence, decisions))

        if doc["review_id"] in doc["supersedes"]:
            failures.append("supersedes 不能包含自身")

        if failures:
            for message in failures:
                print(f"[FAIL] {path.name}: {message}")
            has_failure = True
        else:
            documents.append((path, doc))
            print(
                f"[OK] 结构通过: {doc['review_id']} | {doc['line']['line_id']} "
                f"| primary={doc['recommendation']['primary']}"
            )

    for path, doc in documents:
        for old in doc["supersedes"]:
            if old not in seen_ids:
                print(
                    f"[WARN] {path.name}: supersedes 指向未找到的复盘 {old}"
                    "（可能已归档；若确为笔误请修正）"
                )

    if has_failure:
        return 1
    if gate == "audit" and line is None:
        print(f"[PASS] {len(documents)} 个文件结构检查完成；科研价值未自动评价")
        return 0

    if not line:
        print(f"[FAIL] --gate {gate} 必须指定 --line RL-xxx")
        return 2
    if not LINE_ID_RE.match(line):
        print("[FAIL] --line 需要 RL-xxx 格式（研究线 id，不是任务号）")
        return 2

    matching = [(p, d) for p, d in documents if d["line"]["line_id"] == line]
    if not matching:
        print(f"[FAIL] {line} 尚无可用研究路线复盘")
        return 1
    if len(matching) > 1:
        print(
            f"[FAIL] {line} 存在多份路线复盘，请用 --file 精确指定当前版本；"
            "禁止无声选择最新文件"
        )
        return 1

    path, chosen = matching[0]
    errors = _gate_errors(chosen, gate)
    if errors:
        for message in errors:
            print(f"[FAIL] {path.name}: {message}")
        return 1

    if gate in ("extra_budget", "close_line"):
        print("[WARN] 授权引用存在并不证明人为签署真实；必须由外部权限/人工流程核验")

    print(
        f"[PASS] 结构门通过：gate={gate}, line={line}, file={path.name}"
        "（不等于科学价值审核）"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="科研路线复盘结构门；不替代科学判断与人类授权"
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="本地仓库根目录")
    parser.add_argument("--gate", choices=GATES, default="audit")
    parser.add_argument("--line", type=str, default=None,
                        help="研究线 id，格式 RL-xxx")
    parser.add_argument("--file", type=str, default=None,
                        help="单个复盘文件；相对路径以 repo 为基准")
    args = parser.parse_args()
    return check(args.repo.resolve(), args.gate, args.line, args.file)


if __name__ == "__main__":
    sys.exit(main())
