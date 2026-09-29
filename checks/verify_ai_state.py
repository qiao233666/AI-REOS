"""
aiops 治理状态一致性校验脚本。

用途
----
校验 aiops/ 目录下的治理文件是否齐备且自洽：

(a) 必需治理文件存在：CHARTER.md / STATE.md / EVIDENCE.yaml / DECISIONS.md / FAILURES.md
(b) STATE.md 包含必需章节标题：Current objective / Confirmed facts / Active hypotheses / Next 3 actions
(c) EVIDENCE.yaml 是可解析的 YAML 列表，且每条记录通过 `schemas/evidence.schema.json` 校验
    （字段、类型、枚举、id 格式均来自 schema 文件，不再在脚本里硬编码）
(d) EVIDENCE.yaml 内 id 全局唯一
(e) STATE.md 的 Confirmed facts 中引用的 E-xxx 必须都存在于 EVIDENCE.yaml
(f) aiops/**/*.yaml 均可被 yaml.safe_load 解析

设计说明
--------
只依赖 PyYAML 与标准库（不要求 jsonschema）；schema 校验由同目录的
`schema_validation` 模块完成。配置集中在模块级常量中，不使用命令行参数解析。
对外暴露 main(root: Path) -> int，便于在其他项目或测试中导入并传入临时目录复用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set, Tuple

import yaml

import schema_validation as sv

# 模块级配置：仓库根目录由脚本位置向上两级推出，所有校验规则集中定义于此
REPO_ROOT = Path(__file__).resolve().parents[2]
AIOPS_ROOT = REPO_ROOT / "aiops"

SCHEMAS_DIRNAME = "schemas"
EVIDENCE_SCHEMA_FILENAME = "evidence.schema.json"
EVIDENCE_FILENAME = "EVIDENCE.yaml"

# (a) 必需治理文件清单
REQUIRED_FILES: Tuple[str, ...] = (
    "CHARTER.md",
    "STATE.md",
    "EVIDENCE.yaml",
    "DECISIONS.md",
    "FAILURES.md",
)

# (b) STATE.md 必需章节标题
REQUIRED_STATE_HEADINGS: Tuple[str, ...] = (
    "## Current objective",
    "## Confirmed facts",
    "## Active hypotheses",
    "## Next 3 actions",
)

# (e) 在 STATE.md 中检索证据引用的正则
EVIDENCE_REFERENCE_PATTERN = re.compile(r"E-[0-9]{3,}")


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
    """
    加载 schema 文件，并确认其只使用受支持的关键字。

    Returns
    -------
    exit_code : int
        0 表示可用，非 0 表示缺失或不支持。
    schema : object
        成功时为 schema 字典，失败时为 None。
    """
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


def _extract_section(text: str, heading: str) -> str:
    """
    提取 Markdown 中某一二级标题到下一个二级标题之间的正文。

    Parameters
    ----------
    text : str
        完整的 Markdown 文本。
    heading : str
        目标二级标题，例如 "## Confirmed facts"。

    Returns
    -------
    section : str
        该章节正文；标题不存在时返回空字符串。
    """
    collected: List[str] = []
    collecting = False
    for line in text.splitlines():
        if line.strip() == heading:
            collecting = True
            continue
        if collecting and line.startswith("## "):
            break
        if collecting:
            collected.append(line)
    return "\n".join(collected)


def check_required_files(root: Path) -> int:
    """检查必需治理文件是否齐全，缺任一项即视为失败。"""
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        for name in missing:
            _report("FAIL", f"缺少必需治理文件: {root / name}")
        return 1
    _report("OK", f"必需治理文件齐备: {', '.join(REQUIRED_FILES)}")
    return 0


def check_state_headings(root: Path) -> int:
    """检查 STATE.md 是否包含全部必需章节标题。"""
    state_path = root / "STATE.md"
    if not state_path.is_file():
        _report("FAIL", f"STATE.md 不存在: {state_path}")
        return 1
    text = state_path.read_text(encoding="utf-8")
    missing = [heading for heading in REQUIRED_STATE_HEADINGS if heading not in text]
    if missing:
        for heading in missing:
            _report("FAIL", f"STATE.md 缺少章节标题: {heading}")
        return 1
    _report("OK", "STATE.md 包含全部必需章节标题")
    return 0


def check_evidence(root: Path) -> Tuple[int, Set[str]]:
    """
    按 schema 校验 EVIDENCE.yaml 的每条记录，并检查 id 唯一性。

    Returns
    -------
    exit_code : int
        0 表示通过，非 0 表示存在失败项。
    evidence_ids : Set[str]
        校验过程中收集到的合法证据 id 集合，供后续引用核对使用。
    """
    path = root / EVIDENCE_FILENAME
    if not path.is_file():
        _report("FAIL", f"EVIDENCE.yaml 不存在: {path}")
        return 1, set()

    schema_code, schema = _load_schema(root, EVIDENCE_SCHEMA_FILENAME)
    if schema_code != 0:
        return schema_code, set()

    ok, payload = _load_yaml_file(path)
    if not ok:
        _report("FAIL", f"EVIDENCE.yaml 无法解析: {payload}")
        return 1, set()

    if payload is None or (isinstance(payload, list) and not payload):
        _report("WARN", "EVIDENCE.yaml 为空列表，跳过逐条校验")
        return 0, set()
    if not isinstance(payload, list):
        _report("FAIL", "EVIDENCE.yaml 顶层必须是列表")
        return 1, set()

    exit_code = 0
    evidence_ids: Set[str] = set()
    for index, record in enumerate(payload):
        errors = sv.validate(record, schema, f"EVIDENCE.yaml 第 {index} 条")
        for message in errors:
            _report("FAIL", message)
            exit_code = 1
        if isinstance(record, dict):
            record_id = str(record.get("id", ""))
            if record_id and record_id in evidence_ids:
                _report("FAIL", f"EVIDENCE.yaml 存在重复 id: {record_id}")
                exit_code = 1
            evidence_ids.add(record_id)

    if exit_code == 0:
        _report("OK", f"EVIDENCE.yaml 按 schema 校验通过，共 {len(evidence_ids)} 条证据")
    return exit_code, evidence_ids


def check_state_facts(root: Path, evidence_ids: Set[str]) -> int:
    """核对 STATE.md 的 Confirmed facts 是否只引用已存在的证据 id。"""
    state_path = root / "STATE.md"
    if not state_path.is_file():
        _report("FAIL", f"STATE.md 不存在，无法核对证据引用: {state_path}")
        return 1
    text = state_path.read_text(encoding="utf-8")
    section = _extract_section(text, "## Confirmed facts")
    referenced = set(EVIDENCE_REFERENCE_PATTERN.findall(section))
    if not referenced:
        _report("OK", "STATE.md Confirmed facts 未引用任何 E-xxx")
        return 0
    unknown = sorted(referenced - evidence_ids)
    if unknown:
        for record_id in unknown:
            _report("FAIL", f"STATE.md Confirmed facts 引用了不存在的证据 id: {record_id}")
        return 1
    _report("OK", f"STATE.md Confirmed facts 引用的 {len(referenced)} 个证据 id 均存在")
    return 0


def check_all_yaml(root: Path) -> int:
    """检查 aiops 下所有 YAML 文件是否都能被安全解析。"""
    yaml_files = sorted(root.rglob("*.yaml"))
    if not yaml_files:
        _report("WARN", "aiops 目录下未发现 YAML 文件")
        return 0
    exit_code = 0
    for path in yaml_files:
        ok, error = _load_yaml_file(path)
        if not ok:
            _report("FAIL", f"{path} 无法解析: {error}")
            exit_code = 1
    if exit_code == 0:
        _report("OK", f"aiops 下 {len(yaml_files)} 个 YAML 文件均可解析")
    return exit_code


def main(root: Path) -> int:
    """
    执行全部 AI 状态校验。

    Parameters
    ----------
    root : Path
        aiops 目录路径（在原仓库中为 aiops/，在测试中可传入临时目录）。

    Returns
    -------
    exit_code : int
        0 表示全部通过，非 0 表示存在失败项。
    """
    root = Path(root)
    print(f"== verify_ai_state: {root} ==")
    exit_code = 0
    exit_code |= check_required_files(root)
    exit_code |= check_state_headings(root)
    evidence_code, evidence_ids = check_evidence(root)
    exit_code |= evidence_code
    exit_code |= check_state_facts(root, evidence_ids)
    exit_code |= check_all_yaml(root)
    if exit_code == 0:
        _report("OK", "汇总: AI 状态校验全部通过")
    else:
        _report("FAIL", "汇总: AI 状态校验存在失败项")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(AIOPS_ROOT))
