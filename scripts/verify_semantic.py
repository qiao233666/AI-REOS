"""显式使用已预置的本地模型，检查真实编码、缓存及增量；不下载或调用对话模型。"""
import argparse
import importlib.metadata
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在；拒绝覆盖历史验证")
    environment = {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    with patch.dict(os.environ, environment), patch("socket.socket.connect", side_effect=AssertionError("离线验证禁止网络")), tempfile.TemporaryDirectory(prefix="aireos-semantic-") as temporary:
        root = Path(temporary) / "project"
        initialized = Store.initialize(root)
        project = Project(root, Path(initialized["credential_file"]).read_text().strip())
        task = project.create_task("合成记忆验证", "仅验证检索组件", ["缓存和增量可核对"], ["outputs"], "task")
        context = project.prepare(task["id"], "合成验证", "context")
        project.start_task(task["id"], context["id"], "start")
        project.add_item("旧失败", "先前计算因离散网格不一致而高估", "unknown", "合成", [], task["id"], "old")
        service = MemoryService(project.store)
        config = service.configure("hybrid", "configure", args.model_name, args.model_directory, 32)
        first = service.query("之前为什么预测偏高")
        repeated = service.query("之前为什么预测偏高")
        assert first["retrieval"]["embedded_chunks"] > 0
        assert first["retrieval"]["embedding_complete"]
        assert first["retrieval"]["query_encoded"]
        assert repeated["retrieval"]["embedded_chunks"] == 0
        assert not repeated["retrieval"]["query_encoded"]
        project.add_item("后来检查", "对齐网格后需要重新比较", "unknown", "合成", [], task["id"], "new")
        incremental = service.query("之前为什么预测偏高")
        assert incremental["retrieval"]["embedded_chunks"] == 1
        assert not incremental["retrieval"]["query_encoded"]
        assert incremental["retrieval"]["embedding_complete"]
        assert len(incremental["references"]) >= 2
        report = dict(passed=True, synthetic_only=True, network_blocked=True, dialog_model_calls=0,
            model=args.model_name, model_fingerprint=config["data"]["model_fingerprint"],
            components=config["data"]["components"], dimension=config["data"]["dimension"],
            first=first["retrieval"], repeated=repeated["retrieval"], incremental=incremental["retrieval"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": True, "dialog_model_calls": 0, "network_blocked": True}))


if __name__ == "__main__":
    main()
