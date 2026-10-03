"""
Task Spec 一致性校验脚本。

用途
----
遍历 aiops/specs/ 下除 TEMPLATE.* 之外的 *.yaml，逐条按 `schemas/task.schema.json`
校验（必填字段、枚举、id 格式均来自 schema 文件），并补充 schema 之外的语义检查：

- id 在全部 spec 内唯一
- convergence 一致性：`convergence.required == true` 且 `status == complete` 时，
  `convergence.result` 不得仍为 `pending`（否则等于"任务已完成但收敛判定没做"）
- plan 提示：`plan.required == true` 但 `plan.file` 为空时给出 WARN
  （允许把 HOW 内联在 spec 中，故不判失败）

specs 目录为空时通过并打印 WARN。对外暴露 main(root: Path) -> int 以便复用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

import schema_validation as sv

# 模块级配置：所有规则与路径集中定义，不使用命令行参数
REPO_ROOT = Path(__file__).resolve().parents[2]
AIOPS_ROOT = REPO_ROOT / "aiops"

SCHEMAS_DIRNAME = "schemas"
TASK_SCHEMA_FILENAME = "task.schema.json"
SPECS_DIRNAME = "specs"
EXCLUDED_NAME_PREFIXES: Tuple[str, ...] = ("TEMPLATE.",)
# v1.3：*.example.yaml 是文档示例，不是真实任务
# kit 示例文件两种命名形态都排除（v1.3 实测：complete-example.yaml 不匹配 .example.yaml 后缀）
EXCLUDED_NAME_SUFFIXES: Tuple[str, ...] = (".example.yaml", "-example.yaml")

COMPLETE_STATUS = "complete"
PENDING_RESULT = "pending"


def _report(prefix: str, message: str) -> None:
    """按统一前缀打印一行中文校验信息。"""
    print(f"[{prefix}] {message}")


def _load_yaml_file(path: Path) -> Tuple[bool, object]:
    """
    安全加载 YAML 文件。

    Parameters
    ----------
    path : Path
        待加载的 YAML 文件路径。

    Returns
    -------
    ok : bool
        是否成功读取并解析。
    payload : object
        成功时为解析结果，失败时为错误说明字符串。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"读取失败: {exc}"
    try:
        return True, yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return False, f"YAML 解析失败: {exc}"


def _load_schema(root: Path, filename: str) -> Tuple[int, object]:
    """加载 schema 并确认其只使用受支持的关键字。"""
    path = root / SCHEMAS_DIRNAME / filename
    ok, payload = sv.load_schema(path)
    if not ok:
        _report("FAIL", f"schema 不可用 ({path}): {payload}")
        return 1, None
    unsupported = sv.find_unsupported_keywords(payload)
    if unsupported:
        for problem in unsupported:
            _report("FAIL", f"{filename} {problem}")
        return 1, None
    return 0, payload


def _collect_spec_files(specs_dir: Path) -> List[Path]:
    """收集除 TEMPLATE.* 之外的 Task Spec 文件。"""
    files: List[Path] = []
    for path in sorted(specs_dir.glob("*.yaml")):
        if path.name.startswith(EXCLUDED_NAME_PREFIXES) or path.name.endswith(
            EXCLUDED_NAME_SUFFIXES
        ):
            continue
        files.append(path)
    return files


def _check_convergence(payload: Dict[str, object], name: str) -> int:
    """校验 convergence 与 status 的一致性。"""
    convergence = payload.get("convergence")
    if not isinstance(convergence, dict):
        return 0
    required = convergence.get("required") is True
    result = convergence.get("result")
    if required and payload.get("status") == COMPLETE_STATUS and result == PENDING_RESULT:
        _report(
            "FAIL",
            f"{name}: convergence.required=true 且 status=complete，但 convergence.result 仍为 pending",
        )
        return 1
    return 0


def _check_plan(payload: Dict[str, object], name: str) -> int:
    """对 plan 声明与实际文件的一致性给出提示（不判失败）。"""
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return 0
    if plan.get("required") is True and not plan.get("file"):
        _report(
            "WARN",
            f"{name}: plan.required=true 但 plan.file 为空（若 HOW 已内联在 spec 中可忽略）",
        )
    return 0


def _validate_spec(
    path: Path, schema: object, seen_ids: Set[str]
) -> Tuple[int, Optional[str]]:
    """
    按 schema 校验单个 Task Spec，并执行语义检查。

    Returns
    -------
    exit_code : int
        0 表示通过，非 0 表示失败。
    spec_id : Optional[str]
        通过时返回 id，失败时返回 None。
    """
    ok, payload = _load_yaml_file(path)
    if not ok:
        _report("FAIL", f"{path.name}: {payload}")
        return 1, None
    if not isinstance(payload, dict):
        _report("FAIL", f"{path.name}: 顶层必须是映射")
        return 1, None

    errors = sv.validate(payload, schema, path.name)
    if errors:
        for message in errors:
            _report("FAIL", message)
        return 1, None

    spec_id = str(payload.get("id"))
    exit_code = 0
    if spec_id in seen_ids:
        _report("FAIL", f"{path.name}: spec id 重复: {spec_id}")
        exit_code = 1
    seen_ids.add(spec_id)

    exit_code |= _check_convergence(payload, path.name)
    _check_plan(payload, path.name)

    if exit_code == 0:
        _report("OK", f"{path.name}: 按 schema 校验通过（id={spec_id}, status={payload.get('status')}）")
    return exit_code, spec_id


def main(root: Path) -> int:
    """
    执行 Task Spec 校验。

    Parameters
    ----------
    root : Path
        aiops 目录路径（测试中可传入临时目录）。

    Returns
    -------
    exit_code : int
        0 表示通过，非 0 表示存在失败项。
    """
    root = Path(root)
    specs_dir = root / SPECS_DIRNAME
    print(f"== verify_task_specs: {specs_dir} ==")

    if not specs_dir.is_dir():
        _report("FAIL", f"specs 目录不存在: {specs_dir}")
        _report("FAIL", "汇总: Task Spec 校验存在失败项")
        return 1

    schema_code, schema = _load_schema(root, TASK_SCHEMA_FILENAME)
    if schema_code != 0:
        _report("FAIL", "汇总: Task Spec 校验存在失败项")
        return schema_code

    files = _collect_spec_files(specs_dir)
    if not files:
        _report("WARN", "specs 目录为空，未发现 Task Spec，跳过逐条校验")
        _report("OK", "汇总: Task Spec 校验通过")
        return 0

    exit_code = 0
    seen_ids: Set[str] = set()
    for path in files:
        file_code, _ = _validate_spec(path, schema, seen_ids)
        exit_code |= file_code
    _report("OK", f"共发现 {len(files)} 个 Task Spec")

    if exit_code == 0:
        _report("OK", "汇总: Task Spec 校验全部通过")
    else:
        _report("FAIL", "汇总: Task Spec 校验存在失败项")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(AIOPS_ROOT))
