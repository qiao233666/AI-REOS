"""在指定私有目录评测冻结语料；源码库不预装真实项目内容。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aireos.memory_eval import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=["fts", "hybrid", "prototype"], default="fts")
    parser.add_argument("--model-name")
    parser.add_argument("--model-directory")
    args = vars(parser.parse_args())
    args["corpus_path"], args["questions_path"] = args.pop("corpus"), args.pop("questions")
    result = evaluate(**args)
    print(json.dumps({"mode": result["mode"], "documents": result["documents"], "metrics": result["metrics"], "seconds": result["seconds"], "dialog_model_calls": result["dialog_model_calls"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
