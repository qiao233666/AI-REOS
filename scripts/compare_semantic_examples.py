"""合成改写问法的词面/语义对照；仅使用已预置本地模型，不是总体效果基准。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aireos.project import Project
from aireos.semantic import MemoryService
from aireos.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-directory", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，拒绝覆盖")
    corpus = [
        ("LIB-A17", "库已经安装。导入报错的原因是程序启动了另一个解释器，其虚拟环境并不相同。"),
        ("NUM-B28", "离散网格口径不一致使得预测系统性偏大。对齐采样单元后需要重新比较。"),
        ("COUNT-C39", "统计出现重复条目，是因为进程中断恢复时再次累计了已经完成的任务。"),
        ("SENSOR-D41", "传感器校准系数错误导致温度读数过低，重新标定后再检查。"),
        ("OUT-E52", "两个进程写入同一个文件夹，后写入的内容覆盖了前一个候选。"),
        ("TIME-F63", "时间轴采用不同的时区，导致记录看起来顺序颠倒。"),
        ("MASK-G74", "边界节点被掩码排除，结果不代表完整区域。"),
        ("UNIT-H85", "输入用毫米，脚本却按米解释，必须先对齐单位。"),
    ]
    questions = [
        ("依赖明明装过，怎么还说没有", "LIB-A17", "paraphrase"),
        ("数值一直高估是怎么回事", "NUM-B28", "paraphrase"),
        ("中途接着跑为什么总数翻倍了", "COUNT-C39", "paraphrase"),
        ("LIB-A17", "LIB-A17", "identifier"),
    ]
    results = {}
    with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}), patch("socket.socket.connect", side_effect=AssertionError("禁止网络")), tempfile.TemporaryDirectory(prefix="aireos-paraphrase-") as temporary:
        root = Path(temporary) / "project"
        initialized = Store.initialize(root)
        project = Project(root, Path(initialized["credential_file"]).read_text().strip())
        task = project.create_task("合成语义对照", "仅检查组件能力", ["保留原文"], ["outputs"], "task")
        packet = project.prepare(task["id"], "合成对照", "context")
        project.start_task(task["id"], packet["id"], "start")
        ids = {}
        for index, (title, text) in enumerate(corpus):
            item = project.add_item(title, text, "unknown", "仅合成记录", [], task["id"], "item-" + str(index))
            ids[item["id"]] = title
        service = MemoryService(project.store)
        for mode in ("fts", "hybrid"):
            service.configure(mode, "configure-" + mode, args.model_name if mode == "hybrid" else None,
                args.model_directory if mode == "hybrid" else None, 32)
            rows = []
            for query, expected, kind in questions:
                result = service.query(query, limit=3)
                found = [ids[entry["id"]] for entry in result["matches"] if entry["id"] in ids]
                rows.append(dict(query=query, type=kind, expected=expected, retrieved=found,
                    hit_at_1=bool(found and found[0] == expected), hit_at_3=expected in found))
            results[mode] = rows
    report = dict(synthetic_only=True, network_blocked=True, dialog_model_calls=0, model=args.model_name,
        corpus=corpus, results=results, limitations=["仅8条人为构造材料及4题，不能代表真实历史", "只测材料定位，不测最终答案或科学真假", "不能用该例题调参后声称总体进步"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({mode: dict(hit_at_1=sum(row["hit_at_1"] for row in rows), hit_at_3=sum(row["hit_at_3"] for row in rows), questions=len(rows)) for mode, rows in results.items()}))


if __name__ == "__main__":
    main()
