"""
claim 记录一致性校验脚本。

用途
----
校验 aiops/ 下的 claim 记录（论文/报告中的强断言）。若仓库尚无 claim 文件，
则扫描 aiops/** 下名为 claims*.yaml 或 C-*.yaml 的文件；一个都没有时通过并打印 WARN。

逐条按 `schemas/claim.schema.json` 校验（claim_id 格式、classification 枚举、
supports / limitations 类型均来自 schema 文件），并补充 schema 之外的语义规则：

- **科学 CI 核心规则**：`classification == fact` 时 `supports` 不得为空
  （不得存在无 Evidence 支撑的事实性断言）；
- **引用完整性（v1.3）**：`supports` 里每个 E-xxx 必须真实存在于 `aiops/EVIDENCE.yaml`
  且 `status: active`——编造 id 或引用已废弃证据均判 FAIL。

只依赖 PyYAML 与标准库；对外暴露 main(root: Path) -> int 以便复用。
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
CLAIM_SCHEMA_FILENAME = "claim.schema.json"
CLAIM_FILE_PATTERNS: Tuple[str, ...] = ("claims*.yaml", "C-*.yaml")
FACT_CLASSIFICATION = "fact"
EVIDENCE_FILENAME = "EVIDENCE.yaml"


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


def _collect_claim_files(root: Path) -> List[Path]:
    """扫描 aiops 下所有 claim 记录文件，按路径去重并排序。"""
    found: Set[Path] = set()
    for pattern in CLAIM_FILE_PATTERNS:
        for path in root.rglob(pattern):
            if path.is_file():
                found.add(path)
    return sorted(found)


def _iter_claims(payload: object) -> Optional[List[object]]:
    """
    将 claim 文件内容归一化为 claim 记录列表。

    Returns
    -------
    claims : Optional[List[object]]
        归一化后的记录列表；结构不符合预期时返回 None。
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("claims"), list):
            return payload["claims"]
        return [payload]
    return None


def _load_evidence_index(root: Path) -> Tuple[int, Optional[Dict[str, str]]]:
    """
    读取 aiops/EVIDENCE.yaml，返回 {证据 id: status} 索引。

    Returns
    -------
    exit_code : int
        0 表示索引可用；1 表示文件缺失或不可解析（此时无法核对引用，调用方判 FAIL）。
    index : Optional[Dict[str, str]]
        id -> status 映射；失败时为 None。
    """
    path = root / EVIDENCE_FILENAME
    if not path.is_file():
        _report("FAIL", f"缺少证据账本，无法核对 claim 引用: {path}")
        return 1, None
    ok, payload = _load_yaml_file(path)
    if not ok or not isinstance(payload, list):
        _report("FAIL", f"{EVIDENCE_FILENAME} 缺失或不是记录列表，无法核对 claim 引用")
        return 1, None
    index: Dict[str, str] = {}
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            index[entry["id"]] = str(entry.get("status", ""))
    return 0, index


def _validate_claim(
    record: object,
    path: Path,
    index: int,
    schema: object,
    evidence_index: Optional[Dict[str, str]],
) -> int:
    """校验单条 claim 记录，返回退出码增量。"""
    location = f"{path.name} 第 {index} 条"
    if not isinstance(record, dict):
        _report("FAIL", f"{location} 不是映射")
        return 1

    exit_code = 0
    errors = sv.validate(record, schema, location)
    for message in errors:
        _report("FAIL", message)
        exit_code = 1

    # 语义规则 1：事实性断言必须有 Evidence 支撑（schema 无法表达这种跨字段约束）
    if record.get("classification") == FACT_CLASSIFICATION:
        supports = record.get("supports")
        if isinstance(supports, list) and not supports:
            _report("FAIL", f"{location} classification 为 fact 但 supports 为空")
            exit_code = 1

    # 语义规则 2（v1.3）：引用完整性——supports 里的证据必须存在且 active
    supports = record.get("supports")
    if isinstance(supports, list) and evidence_index is not None:
        for sid in supports:
            if not isinstance(sid, str):
                continue
            if sid not in evidence_index:
                _report("FAIL", f"{location} 引用了不存在的证据 id: {sid}")
                exit_code = 1
            elif evidence_index[sid] != "active":
                _report(
                    "FAIL",
                    f"{location} 引用的证据 {sid} 状态为 {evidence_index[sid]!r}"
                    "（非 active），事实性断言不得依赖已废弃/被取代的证据",
                )
                exit_code = 1

    if exit_code == 0:
        _report("OK", f"{location} 按 schema 校验通过: {record.get('claim_id')}")
    return exit_code


def _validate_claim_file(
    path: Path, schema: object, evidence_index: Optional[Dict[str, str]]
) -> int:
    """校验单个 claim 文件，返回退出码增量。"""
    ok, payload = _load_yaml_file(path)
    if not ok:
        _report("FAIL", f"{path.name}: {payload}")
        return 1
    claims = _iter_claims(payload)
    if claims is None:
        _report("FAIL", f"{path.name}: 结构不符合预期（应为列表或含 claims 键的映射）")
        return 1
    if not claims:
        _report("WARN", f"{path.name}: claim 列表为空，跳过")
        return 0
    exit_code = 0
    for index, record in enumerate(claims):
        exit_code |= _validate_claim(record, path, index, schema, evidence_index)
    return exit_code


def main(root: Path) -> int:
    """
    执行 claim 记录校验。

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
    print(f"== verify_claims: {root} ==")
    claim_files = _collect_claim_files(root)
    if not claim_files:
        _report("WARN", "未发现 claim 记录，跳过")
        _report("OK", "汇总: claim 校验通过（无记录）")
        return 0

    schema_code, schema = _load_schema(root, CLAIM_SCHEMA_FILENAME)
    if schema_code != 0:
        _report("FAIL", "汇总: claim 校验存在失败项")
        return schema_code

    ev_code, evidence_index = _load_evidence_index(root)
    exit_code = ev_code
    for path in claim_files:
        exit_code |= _validate_claim_file(path, schema, evidence_index)

    if exit_code == 0:
        _report("OK", f"汇总: claim 校验全部通过，共 {len(claim_files)} 个文件")
    else:
        _report("FAIL", "汇总: claim 校验存在失败项")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(AIOPS_ROOT))
