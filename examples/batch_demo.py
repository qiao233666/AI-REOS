"""用全新本地项目演示一次合成交付、核查、采纳和手账追溯。"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path,
                        help="必须是尚不存在的独立示例目录")
    args = parser.parse_args(argv)
    root = args.project.resolve()
    if root.exists():
        parser.error("示例目标必须是尚不存在的新目录；拒绝覆盖")

    source = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source)
    environment["PYTHONUTF8"] = "1"
    request_number = 0

    def call(command, data=None, credential=None, request_id=None, readonly=False):
        nonlocal request_number
        request_number += 1
        command_line = [sys.executable, "-B", "-m", "aireos", "--project", str(root)]
        if credential is not None:
            command_line.extend(["--credential-file", str(credential)])
        command_line.append(command)
        if command != "init":
            input_dir = root / "demo-inputs"
            input_dir.mkdir(exist_ok=True)
            input_path = input_dir / f"{request_number:03d}.json"
            with input_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(data or {}, ensure_ascii=False))
            command_line.extend(["--input", str(input_path)])
            if request_id is not None:
                command_line.extend(["--request-id", request_id])
        result = subprocess.run(command_line, env=environment, capture_output=True,
                                text=True, encoding="utf-8", shell=False, timeout=60)
        if result.returncode:
            raise RuntimeError(f"{command} failed ({result.returncode}): {result.stderr.strip()}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{command} did not return JSON") from exc

    initialized = call("init")
    owner_key = initialized["credential_file"]
    worker_key = call("grant", {
        "actor": "demo-worker", "capabilities": ["execute"], "scopes": ["results"]
    }, credential=owner_key, request_id="batch-demo-grant-worker")["credential_file"]
    reviewer_key = call("grant", {
        "actor": "demo-reviewer", "capabilities": ["review"], "scopes": ["results"]
    }, credential=owner_key, request_id="batch-demo-grant-reviewer")["credential_file"]

    task = call("create-task", {
        "title": "Summarize synthetic values",
        "objective": "Demonstrate the managed delivery and review flow with [1, 2, 3].",
        "acceptance": ["The saved synthetic summary reports count 3, sum 6, and mean 2."],
        "outputs": ["results"],
        "inputs": [{"type": "note", "text": "Synthetic example only; no research data."}],
    }, credential=owner_key, request_id="batch-demo-create-task")

    numbers = [1, 2, 3]
    summary = {"values": numbers, "count": len(numbers), "sum": sum(numbers),
               "mean": sum(numbers) / len(numbers)}
    assert summary["count"] == 3 and summary["sum"] == 6 and summary["mean"] == 2
    results_dir = root / "results"
    results_dir.mkdir()
    with (results_dir / "summary.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    delivery = call("deliver-batch", {
        "task_id": task["id"],
        "artifacts": [{"key": "summary", "path": "results/summary.json",
                       "title": "Synthetic summary"}],
        "claims": [{"key": "mean", "title": "Synthetic mean",
                    "text": "For the synthetic values [1, 2, 3], the arithmetic mean is 2.",
                    "classification": "fact", "scope": "This synthetic example only.",
                    "supports": ["summary"]}],
        "query": "synthetic example summary",
    }, credential=worker_key, request_id="batch-demo-deliver")
    item = next(entry for entry in delivery["data"]["result"]["items"] if entry["key"] == "mean")

    review_note = "This example program directly checked synthetic arithmetic; it is not an independent scientific review."
    reviews = call("record-reviews", {
        "task_id": task["id"],
        "reviews": [{"item_id": entry["id"], "revision": entry["rev"], "verdict": "pass",
                     "method": review_note,
                     "checks": ["The generated synthetic JSON has count=3, sum=6, mean=2.",
                                "No real research data, model, or external tool was used."]}
                    for entry in delivery["data"]["result"]["items"]],
    }, credential=reviewer_key, request_id="batch-demo-reviews")
    review_id = next(entry["id"] for entry in reviews["data"]["result"]["reviews"] if entry["item_id"] == item["id"])

    finalized = call("finalize-task", {
        "task_id": task["id"],
        "adoptions": [{"item_id": item["id"], "review_id": review_id}],
        "summary": "Synthetic batch completed; arithmetic was checked by the example script. This does not validate any scientific result.",
        "publish": True,
    }, credential=owner_key, request_id="batch-demo-finalize")
    stage_id = finalized["data"]["result"]["stage"]
    trace = call("trace", {"stage_id": stage_id}, readonly=True)
    print(json.dumps({"status": "completed", "task_id": task["id"],
                      "stage_id": stage_id, "handoff_trace": trace,
                      "synthetic_only": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
