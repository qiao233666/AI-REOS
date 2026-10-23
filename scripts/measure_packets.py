"""用合成文本比较同一检索结果的全文与片段显示量，不估算token或费用。"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aireos.project import Project
from aireos.semantic import MemoryService
from aireos.storage import Store, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，拒绝覆盖")
    with tempfile.TemporaryDirectory(prefix="aireos-packets-") as temporary:
        root = Path(temporary) / "project"
        initialized = Store.initialize(root)
        project = Project(root, Path(initialized["credential_file"]).read_text().strip())
        task = project.create_task("合成片段显示", "检查显示量", ["原文可恢复"], ["outputs"], "task")
        packet = project.prepare(task["id"], "合成材料", "context")
        project.start_task(task["id"], packet["id"], "start")
        marker = "matched_tail_marker"
        text = "unrelated padding. " * 700 + "\n" + marker + "：只在这个合成条件下出现偏差，需要核对来源。"
        original = project.add_item("合成长记录", text, "unknown", "仅合成", [], task["id"], "item")
        service = MemoryService(project.store)
        full = service.query(marker, max_chars=50000, view="full")
        brief = service.query(marker, max_chars=50000, view="brief")
        full_chars, brief_chars = len(canonical(full)), len(canonical(brief))
        assert full["references"] == brief["references"]
        assert marker in brief["matches"][0]["body"]
        assert project.store.get(original["id"], 1)["data"]["text"] == text
        assert brief_chars < full_chars
        report = dict(synthetic_only=True, dialog_model_calls=0, full_response_chars=full_chars,
            brief_response_chars=brief_chars, reduction_fraction=1 - brief_chars / full_chars,
            same_reference_set=True, original_preserved=True, query_fragment_preserved=True,
            limitations=["单个合成长记录的显示量比较", "字符数量不是token或实际账单", "未证明所有问题的答案充分性"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
