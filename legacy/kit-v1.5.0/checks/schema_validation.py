"""
JSON Schema（draft-07 子集）校验器。

用途
----
让 `aiops/schemas/*.json` 从"只是文档"变成"可执行的校验约定"：
校验脚本在运行时读取 schema 文件并据此校验结构化记录，schema 因此成为唯一真源，
避免在脚本里重复硬编码字段名（两者一旦不同步就会出现"改了 schema 但校验没跟上"）。

设计说明
--------
- **只依赖标准库**，不要求安装 jsonschema。
- 只支持本项目 schema 实际使用的关键字子集：
  `type` / `required` / `properties` / `additionalProperties` / `items` / `enum` / `const` / `pattern`。
- **遇到不支持的关键字会明确报错，而不是静默跳过**：否则会出现"看着校验通过、实际没校验"
  的假安全感。新增 schema 若用到新关键字，必须先在本模块补充支持。
- 对 YAML 的一处有意宽松：`type: string` 接受 `datetime.date` / `datetime.datetime`。
  因为 YAML 里未加引号的日期（如 `recorded_at: 2026-09-28`）会被解析成日期对象，
  若严格按 JSON Schema 语义会误报。

对外接口
--------
- `validate(instance, schema) -> List[str]`：返回错误说明列表，空列表表示通过。
- `find_unsupported_keywords(schema) -> List[str]`：扫描 schema 中不支持的关键字。
- `load_schema(path) -> Tuple[bool, object]`：读取并解析 schema 文件。
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 本模块支持的关键字；不在其中的关键字会被显式报告为"不支持"
SUPPORTED_KEYWORDS = frozenset(
    {
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "minLength",
        "maxLength",
        "anyOf",
        "enum",
        "const",
        "pattern",
    }
)

# 纯元数据关键字：对校验无影响，允许出现
METADATA_KEYWORDS = frozenset({"$schema", "title", "description"})

# JSON Schema 类型名 -> Python 判定函数
_TYPE_CHECKERS: Dict[str, Any] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def load_schema(path: Path) -> Tuple[bool, object]:
    """
    读取并解析 JSON Schema 文件。

    Parameters
    ----------
    path : Path
        schema 文件路径。

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
        return True, json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"JSON 解析失败: {exc}"


def _type_name(value: object) -> str:
    """返回与 JSON Schema 类型名对应的可读类型名，用于错误信息。"""
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _matches_type(instance: object, expected: str, checker: Any) -> bool:
    """判断实例是否符合期望类型；对 YAML 日期做 string 宽松处理。"""
    if checker(instance):
        return True
    if expected == "string" and isinstance(instance, (datetime.date, datetime.datetime)):
        return True
    return False


def find_unsupported_keywords(schema: object, pointer: str = "$") -> List[str]:
    """
    递归扫描 schema 中本模块不支持的关键字。

    Parameters
    ----------
    schema : object
        schema 片段（通常为 dict）。
    pointer : str
        当前位置的 JSON 指针，用于定位错误。

    Returns
    -------
    problems : List[str]
        不支持关键字的说明列表；空列表表示全部支持。
    """
    problems: List[str] = []
    if not isinstance(schema, dict):
        return problems
    for keyword, value in schema.items():
        if keyword in METADATA_KEYWORDS:
            continue
        if keyword == "properties" and isinstance(value, dict):
            for name, sub_schema in value.items():
                problems.extend(find_unsupported_keywords(sub_schema, f"{pointer}.properties.{name}"))
            continue
        if keyword == "items":
            problems.extend(find_unsupported_keywords(value, f"{pointer}.items"))
            continue
        if keyword == "anyOf" and isinstance(value, list):
            for index, sub_schema in enumerate(value):
                problems.extend(
                    find_unsupported_keywords(sub_schema, f"{pointer}.anyOf[{index}]")
                )
            continue
        if keyword in SUPPORTED_KEYWORDS:
            continue
        problems.append(f"{pointer} 使用了不支持的 JSON Schema 关键字: {keyword}")
    return problems


def _validate_object(instance: Dict[Any, Any], schema: Dict[str, Any], pointer: str) -> List[str]:
    """校验对象实例的 required / properties / additionalProperties。"""
    errors: List[str] = []
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    for name in schema.get("required", []) or []:
        if name not in instance:
            errors.append(f"{pointer}: 缺少必需字段 {name!r}")

    for name, value in instance.items():
        if name in properties:
            errors.extend(validate(value, properties[name], f"{pointer}.{name}"))
        elif schema.get("additionalProperties") is False:
            errors.append(f"{pointer}: 出现 schema 未定义的字段 {name!r}")
    return errors


def validate(instance: object, schema: object, pointer: str = "$") -> List[str]:
    """
    按 schema 校验实例。

    Parameters
    ----------
    instance : object
        待校验的数据（通常由 YAML 解析得到）。
    schema : object
        JSON Schema 片段。
    pointer : str
        当前位置的 JSON 指针，用于定位错误。

    Returns
    -------
    errors : List[str]
        错误说明列表；空列表表示校验通过。
    """
    if not isinstance(schema, dict):
        return []

    errors: List[str] = []

    if "type" in schema:
        expected = schema["type"]
        expected_types = expected if isinstance(expected, list) else [expected]
        unknown = [name for name in expected_types if name not in _TYPE_CHECKERS]
        if unknown:
            errors.append(f"{pointer}: schema 声明了未知 type {unknown!r}")
        elif not any(_matches_type(instance, name, _TYPE_CHECKERS[name]) for name in expected_types):
            allowed = "/".join(str(name) for name in expected_types)
            errors.append(f"{pointer}: 期望 type={allowed}，实际为 {_type_name(instance)}")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{pointer}: 期望常量 {schema['const']!r}，实际为 {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{pointer}: 取值 {instance!r} 不在允许集合 {schema['enum']} 内")

    if "pattern" in schema and isinstance(instance, str):
        try:
            matched = re.search(str(schema["pattern"]), instance)
        except re.error as exc:
            errors.append(f"{pointer}: schema 的 pattern 非法: {exc}")
        else:
            if matched is None:
                errors.append(f"{pointer}: 字符串 {instance!r} 不匹配 pattern {schema['pattern']!r}")

    if isinstance(instance, dict):
        errors.extend(_validate_object(instance, schema, pointer))

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{pointer}[{index}]"))

    if isinstance(instance, list) and "minItems" in schema:
        if len(instance) < schema["minItems"]:
            errors.append(
                f"{pointer}: 列表长度 {len(instance)} 小于 minItems={schema['minItems']}（不得为空列表）"
            )

    if "minLength" in schema or "maxLength" in schema:
        # YAML 未加引号日期会被解析成日期对象，按 string 宽松处理时同样豁免长度校验
        is_text = isinstance(instance, str) or isinstance(
            instance, (datetime.date, datetime.datetime)
        )
        if not is_text:
            errors.append(
                f"{pointer}: minLength/maxLength 仅适用于字符串，实际为 {_type_name(instance)}"
            )
        else:
            text = instance.isoformat() if hasattr(instance, "isoformat") else str(instance)
            if "minLength" in schema and len(text) < schema["minLength"]:
                errors.append(
                    f"{pointer}: 字符串长度 {len(text)} 小于 minLength={schema['minLength']}（不得为空/过短）"
                )
            if "maxLength" in schema and len(text) > schema["maxLength"]:
                errors.append(
                    f"{pointer}: 字符串长度 {len(text)} 大于 maxLength={schema['maxLength']}"
                )

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        branches = schema["anyOf"]
        results = [validate(instance, branch, pointer) for branch in branches]
        if not any(not errs for errs in results):
            detail = "; ".join(
                f"分支[{i}]：{'、'.join(errs) if errs else '通过'}"
                for i, errs in enumerate(results)
            )
            errors.append(f"{pointer}: 不满足 anyOf 任一分支（{detail}）")

    return errors
