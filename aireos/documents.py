"""登记时为小型UTF-8文本建立原文索引，不调用模型或全仓扫描。"""
from pathlib import Path

MAX_TEXT_BYTES = 256 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".py", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".js", ".ts"}


def searchable(path, content):
    coverage = dict(mode="metadata_only", max_text_bytes=MAX_TEXT_BYTES)
    if Path(path).suffix.lower() not in TEXT_SUFFIXES:
        return dict(search_coverage={**coverage, "reason": "该格式没有文本提取适配器"})
    if len(content) > MAX_TEXT_BYTES:
        return dict(search_coverage={**coverage, "reason": "超过自动正文索引预算"})
    try:
        text = content.decode("utf-8")
        if "\x00" in text:
            raise UnicodeError("含二进制空字节")
    except UnicodeError:
        return dict(search_coverage={**coverage, "reason": "不是支持的UTF-8正文"})
    return dict(search_text=text, search_coverage=dict(mode="full_text", encoding="utf-8", bytes=len(content)))
