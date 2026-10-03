"""
实验元数据一致性校验脚本。

用途
----
遍历 aiops/experiments/ 下除 registry.yaml 与 TEMPLATE.* 之外的 *.yaml 文件，
逐条按 `schemas/experiment.schema.json` 校验（字段、类型、枚举、id 格式、列表非空
均来自 schema 文件，不再在脚本里硬编码），并补充 schema 之外的语义检查：

- id 在目录内唯一
- registry.yaml 中登记的 id 与目录内实验文件 id 完全一致（不一致则失败）

registry.yaml 支持两种形态：顶层为记录列表，或顶层映射的 experiments 键为记录列表，
每条记录含 id 字段。目录为空时通过并打印 WARN。对外暴露 main(root: Path) -> int 以便复用。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set, Tuple

import yaml

import schema_validation as sv

# 模块级配置：所有规则与路径集中定义，不使用命令行参数
REPO_ROOT = Path(__file__).resolve().parents[2]
AIOPS_ROOT = REPO_ROOT / "aiops"

SCHEMAS_DIRNAME = "schemas"
EXPERIMENT_SCHEMA_FILENAME = "experiment.schema.json"
EXPERIMENTS_DIRNAME = "experiments"
REGISTRY_FILENAME = "registry.yaml"
EXCLUDED_NAME_PREFIXES: Tuple[str, ...] = ("TEMPLATE.",)


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


def _collect_experiment_files(experiments_dir: Path) -> List[Path]:
    """收集除 registry.yaml 与 TEMPLATE.* 之外的实验元数据文件。"""
    files: List[Path] = []
    for path in sorted(experiments_dir.glob("*.yaml")):
        if path.name == REGISTRY_FILENAME:
            continue
        if path.name.startswith(EXCLUDED_NAME_PREFIXES):
            continue
        files.append(path)
    return files


def _validate_experiment(path: Path, schema: object) -> Tuple[int, Optional[str]]:
    """
    按 schema 校验单个实验元数据文件。

    Returns
    -------
    exit_code : int
        0 表示通过，非 0 表示失败。
    experiment_id : Optional[str]
        校验通过时返回 id，失败时返回 None。
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

    _report("OK", f"{path.name}: 按 schema 校验通过（id={payload.get('id')}）")
    return 0, str(payload.get("id"))


def _extract_registry_ids(payload: object) -> Optional[Set[str]]:
    """
    从 registry.yaml 数据中提取已登记的实验 id 集合。

    Returns
    -------
    ids : Optional[Set[str]]
        解析成功时返回 id 集合；结构不符合预期时返回 None。
    """
    if isinstance(payload, dict):
        records = payload.get("experiments")
    elif isinstance(payload, list):
        records = payload
    else:
        return None
    if not isinstance(records, list):
        return None
    ids: Set[str] = set()
    for record in records:
        if isinstance(record, dict) and "id" in record:
            ids.add(str(record["id"]))
    return ids


def _check_registry(registry_path: Path, file_ids: Set[str]) -> int:
    """检查 registry.yaml 登记的 id 与实验文件 id 是否一致。"""
    if not registry_path.is_file():
        _report("FAIL", f"registry.yaml 不存在: {registry_path}")
        return 1
    ok, payload = _load_yaml_file(registry_path)
    if not ok:
        _report("FAIL", f"registry.yaml 无法解析: {payload}")
        return 1
    registry_ids = _extract_registry_ids(payload)
    if registry_ids is None:
        _report("FAIL", "registry.yaml 结构不符合预期（应为记录列表或含 experiments 键）")
        return 1

    exit_code = 0
    only_in_files = sorted(file_ids - registry_ids)
    only_in_registry = sorted(registry_ids - file_ids)
    for experiment_id in only_in_files:
        _report("FAIL", f"实验文件 id 未在 registry.yaml 登记: {experiment_id}")
        exit_code = 1
    for experiment_id in only_in_registry:
        _report("FAIL", f"registry.yaml 登记了目录中不存在的实验 id: {experiment_id}")
        exit_code = 1
    if exit_code == 0:
        _report("OK", "registry.yaml 与实验文件 id 完全一致")
    return exit_code


def main(root: Path) -> int:
    """
    执行实验元数据校验。

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
    experiments_dir = root / EXPERIMENTS_DIRNAME
    print(f"== verify_experiment_metadata: {experiments_dir} ==")

    if not experiments_dir.is_dir():
        _report("FAIL", f"实验目录不存在: {experiments_dir}")
        _report("FAIL", "汇总: 实验元数据校验存在失败项")
        return 1

    schema_code, schema = _load_schema(root, EXPERIMENT_SCHEMA_FILENAME)
    if schema_code != 0:
        _report("FAIL", "汇总: 实验元数据校验存在失败项")
        return schema_code

    files = _collect_experiment_files(experiments_dir)
    if not files:
        # v1.3：空目录不再无条件通过——registry 登记了却无文件的"幽灵实验"必须报 FAIL
        registry_path = experiments_dir / REGISTRY_FILENAME
        reg_ok, registry_payload = _load_yaml_file(registry_path)
        ghost_ids: Set[str] = set()
        if reg_ok and registry_payload is not None:
            ids = _extract_registry_ids(registry_payload)
            if ids:
                ghost_ids = ids
        if ghost_ids:
            for ghost in sorted(ghost_ids):
                _report("FAIL", f"registry.yaml 登记了 {ghost} 但实验目录中无对应元数据文件（幽灵实验）")
            _report("FAIL", "汇总: 实验元数据校验存在失败项")
            return 1
        _report("WARN", "实验目录为空且 registry 无登记，跳过逐条校验")
        _report("OK", "汇总: 实验元数据校验通过")
        return 0

    exit_code = 0
    file_ids: Set[str] = set()
    for path in files:
        file_code, experiment_id = _validate_experiment(path, schema)
        exit_code |= file_code
        if experiment_id is not None:
            if experiment_id in file_ids:
                _report("FAIL", f"实验 id 在目录内重复: {experiment_id}")
                exit_code = 1
            file_ids.add(experiment_id)
    _report("OK", f"共发现 {len(files)} 个实验元数据文件")

    exit_code |= _check_registry(experiments_dir / REGISTRY_FILENAME, file_ids)

    if exit_code == 0:
        _report("OK", "汇总: 实验元数据校验全部通过")
    else:
        _report("FAIL", "汇总: 实验元数据校验存在失败项")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(AIOPS_ROOT))
