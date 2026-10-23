# -*- coding: utf-8 -*-
"""科研动作入口守卫。

职责
----
在**真正执行**研究动作（批次申报、预算追加、稿件冻结、研究线状态变更）
之前做最后一道核对，并在放行后登记授权消费。

设计要点
--------
1. **先核对，后写账**：所有拒绝路径都在写任何记录之前返回，保证
   "拒绝时无副作用"。
2. **防重复消费**：每次放行写入一条消费记录，键为
   `(review_id, action_id, event_id)`。同一键重复提交视为幂等重试，
   直接沿用旧记录，不重复追加额度、不重复执行。
3. **超额被拒**：累计已消费额度 + 本次申请额度 > resource_ceiling 即拒绝。
4. **纠错放行**：`evidence_update` 类动作永不经过本守卫（直接放行），
   与协议 §8 的豁免一致。
5. **引用≠授权**：本守卫只验证 approval_ref 指向的 D 条目**存在**且
   状态为 approved；它不证明 D 条目确由用户签署。真实签署须由外部
   权限流程核验，本模块在放行时始终打印该警告。

本模块**不做**：物理正确性判断、科学价值判断、真实身份认证。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    from jsonschema import Draft7Validator
except ImportError:  # 治理环境不保证有 jsonschema
    Draft7Validator = None

LEDGER_NAME = "research_consumption.yaml"


class GuardResult:
    """守卫结果：allowed 为 True 时才能执行；reason 说明原因。"""

    __slots__ = ("allowed", "reason", "warnings", "consumed")

    def __init__(
        self,
        allowed: bool,
        reason: str,
        warnings: Optional[List[str]] = None,
        consumed: bool = False,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.warnings = warnings or []
        self.consumed = consumed

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (
            f"GuardResult(allowed={self.allowed}, reason={self.reason!r}, "
            f"consumed={self.consumed})"
        )


# --------------------------------------------------------------------------
# 账本读写
# --------------------------------------------------------------------------

def _ledger_path(repo: Path) -> Path:
    return repo / "aiops" / "research_reviews" / LEDGER_NAME


def _read_ledger(repo: Path) -> List[Dict[str, Any]]:
    path = _ledger_path(repo)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else []


def _write_ledger(repo: Path, records: List[Dict[str, Any]]) -> None:
    path = _ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(records, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _consumed_totals(records: List[Dict[str, Any]], review_id: str) -> Dict[str, float]:
    cpu = 0.0
    human = 0.0
    for record in records:
        if record.get("review_id") != review_id:
            continue
        amount = record.get("amount") or {}
        cpu += float(amount.get("cpu_hours") or 0.0)
        human += float(amount.get("human_minutes") or 0.0)
    return {"cpu_hours": cpu, "human_minutes": human}


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def _load_review(repo: Path, review_path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    if not review_path.is_file():
        return None, f"路线复盘文件不存在：{review_path}"
    try:
        data = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"路线复盘无法解析：{exc}"
    if not isinstance(data, dict):
        return None, "路线复盘顶层不是映射"
    return data, ""


def _decision_status(repo: Path, decision_ref: str) -> Tuple[bool, str]:
    """检查 D 条目存在，并尽量读取其 status。

    DECISIONS.md 目前是散文格式，没有结构化 status 字段；
    返回 (exists, status)，status 未知时为空串。
    """
    path = repo / "aiops" / "DECISIONS.md"
    if not path.is_file():
        return False, ""
    text = path.read_text(encoding="utf-8")
    import re
    header = re.search(rf"^## {re.escape(decision_ref)} —", text, re.M)
    if not header:
        return False, ""
    tail = text[header.end():]
    next_header = re.search(r"^## ", tail, re.M)
    body = tail[: next_header.start()] if next_header else tail
    if "status: superseded" in body or "已被取代" in body:
        return True, "superseded"
    return True, "active"


def _check_action(
    repo: Path,
    review: Dict[str, Any],
    action_id: str,
    event_id: str,
    amount: Optional[Dict[str, float]],
    records: List[Dict[str, Any]],
) -> GuardResult:
    warnings: List[str] = []

    # --- 0. 幂等：同一事件同一动作已消费则直接放行（不重复记账） -----------
    for record in records:
        if (
            record.get("review_id") == review.get("review_id")
            and record.get("action_id") == action_id
            and record.get("event_id") == event_id
        ):
            return GuardResult(
                True,
                "同一事件的重试；沿用既有消费记录，不重复执行",
                warnings,
                consumed=False,
            )

    # --- 1. 审批状态 -------------------------------------------------------
    approval = review.get("approval") or {}
    if approval.get("status") != "approved":
        return GuardResult(
            False,
            f"审批状态为 {approval.get('status')!r}，未获批准的动作不得执行",
            warnings,
        )
    ref = approval.get("approval_ref")
    if not ref:
        return GuardResult(False, "approved 但缺少 approval_ref", warnings)

    exists, status = _decision_status(repo, ref)
    if not exists:
        return GuardResult(
            False,
            f"approval_ref 指向不存在的决策条目 {ref}；格式合法不等于授权存在",
            warnings,
        )
    if status == "superseded":
        return GuardResult(False, f"决策条目 {ref} 已被取代，不能作为现行授权", warnings)

    if not approval.get("input_digest"):
        return GuardResult(
            False,
            "approved 但缺少 input_digest；无法证明动作基于已冻结的输入",
            warnings,
        )

    # --- 2. 动作存在性、模式与任务 ----------------------------------------
    actions = review.get("selected_actions") or []
    target = None
    for action in actions:
        if action.get("action_id") == action_id:
            target = action
            break
    if target is None:
        return GuardResult(False, f"复盘 {review.get('review_id')} 中无动作 {action_id}", warnings)

    if target.get("mode") != "execute":
        return GuardResult(
            False,
            f"动作 {action_id} 为 candidate_only，只保留候选，不产生执行许可",
            warnings,
        )
    if not target.get("task_ref"):
        return GuardResult(False, f"动作 {action_id} 未绑定执行任务", warnings)

    # --- 3. 预算 ----------------------------------------------------------
    if amount:
        ceiling = (review.get("recommendation") or {}).get("resource_ceiling") or {}
        totals = _consumed_totals(records, str(review.get("review_id")))
        for key in ("cpu_hours", "human_minutes"):
            cap = ceiling.get(key)
            request = float(amount.get(key) or 0.0)
            if request <= 0:
                continue
            if cap is None:
                return GuardResult(
                    False,
                    f"资源上限 {key} 未核定（null 不是无限），拒绝新增占用",
                    warnings,
                )
            if totals[key] + request > float(cap) + 1e-9:
                return GuardResult(
                    False,
                    f"超出 {key} 上限：已消费 {totals[key]} + 本次 {request} "
                    f"> 上限 {cap}",
                    warnings,
                )

    warnings.append(
        "授权引用存在并不证明人为签署真实；必须由外部权限/人工流程核验"
    )
    return GuardResult(True, "结构核对通过", warnings)


# --------------------------------------------------------------------------
# 公开入口
# --------------------------------------------------------------------------

def guard_action(
    repo: Path,
    review_path: Path,
    action_id: str,
    event_id: str,
    amount: Optional[Dict[str, float]] = None,
    action_kind: str = "research",
) -> GuardResult:
    """执行前的最后核对。

    Parameters
    ----------
    repo : 仓库根目录
    review_path : RD 文件路径（相对 repo 或绝对）
    action_id : 要执行的 selected_actions[].action_id
    event_id : 本次事件唯一标识（用于幂等与防重复消费）
    amount : 本次申请的 {cpu_hours, human_minutes}，可省略（不申请预算时）
    action_kind : `evidence_update` 直接放行（纠错不受阻）

    Returns
    -------
    GuardResult；allowed=True 表示可以执行，且（若首次）已登记消费。
    """
    repo = Path(repo)

    # 纠错豁免：证据操作不经路线门
    if action_kind == "evidence_update":
        return GuardResult(True, "证据纠错豁免；不经过路线门", [], consumed=False)

    path = Path(review_path)
    if not path.is_absolute():
        path = repo / path

    review, error = _load_review(repo, path)
    if review is None:
        return GuardResult(False, error)

    records = _read_ledger(repo)
    result = _check_action(repo, review, action_id, event_id, amount, records)
    if not result.allowed:
        return result  # 拒绝路径：未写任何记录

    # 放行：登记消费（若为首次）
    already = any(
        r.get("review_id") == review.get("review_id")
        and r.get("action_id") == action_id
        and r.get("event_id") == event_id
        for r in records
    )
    if not already:
        records.append(
            {
                "review_id": review.get("review_id"),
                "action_id": action_id,
                "event_id": event_id,
                "task_ref": None,
                "amount": amount or {"cpu_hours": 0.0, "human_minutes": 0.0},
                "recorded_at": str(review.get("provenance", {}).get("created_at") or ""),
            }
        )
        for action in review.get("selected_actions") or []:
            if action.get("action_id") == action_id:
                records[-1]["task_ref"] = action.get("task_ref")
                break
        _write_ledger(repo, records)
        result.consumed = True

    return result


def main() -> int:  # pragma: no cover - CLI 便于人工演练
    import argparse

    parser = argparse.ArgumentParser(description="科研动作入口守卫")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--review", required=True, help="RD 文件路径")
    parser.add_argument("--action", required=True, help="action_id")
    parser.add_argument("--event", required=True, help="事件唯一 id")
    parser.add_argument("--cpu-hours", type=float, default=0.0)
    parser.add_argument("--human-minutes", type=float, default=0.0)
    parser.add_argument("--kind", default="research",
                        choices=["research", "evidence_update"])
    args = parser.parse_args()

    result = guard_action(
        args.repo.resolve(),
        Path(args.review),
        args.action,
        args.event,
        {"cpu_hours": args.cpu_hours, "human_minutes": args.human_minutes},
        args.kind,
    )
    for warning in result.warnings:
        print(f"[WARN] {warning}")
    print(f"[{'ALLOW' if result.allowed else 'DENY'}] {result.reason}")
    return 0 if result.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
