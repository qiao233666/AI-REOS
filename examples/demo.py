"""在全新指定目录演示真实CLI闭环；只使用合成数值，不调用模型或远端。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.project.resolve()
    if root.exists():
        parser.error("示例目标必须是尚不存在的新目录，避免混入真实项目")
    source = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source)
    environment["PYTHONUTF8"] = "1"
    number = 0
    def call(command, data=None, credential=None, write=True):
        nonlocal number
        number += 1
        argv = [sys.executable, "-B", "-m", "aireos", "--project", str(root)]
        if credential:
            argv += ["--credential-file", str(credential)]
        argv += [command]
        if command != "init":
            inputs = root / "demo-inputs"
            inputs.mkdir(exist_ok=True)
            filename = inputs / (str(number).zfill(3) + ".json")
            filename.write_text(json.dumps(data or {}, ensure_ascii=False), encoding="utf-8")
            argv += ["--input", str(filename)]
            if write:
                argv += ["--request-id", "demo-" + str(number)]
        result = subprocess.run(argv, env=environment, capture_output=True, encoding="utf-8", shell=False)
        if result.returncode:
            raise RuntimeError(command + ": " + result.stderr)
        return json.loads(result.stdout)
    initialized = call("init")
    owner = initialized["credential_file"]
    worker = call("grant", dict(actor="example-worker", capabilities=["execute"], scopes=["outputs"]), owner)["credential_file"]
    reviewer = call("grant", dict(actor="example-checker", capabilities=["review"], scopes=["outputs"]), owner)["credential_file"]
    task = call("create-task", dict(title="核对合成数据汇总", objective="验证三个合成数值的平均值和适用范围",
        acceptance=["输出可复查，未推广到真实业务"], outputs=["outputs"]), owner)
    packet = call("prepare", dict(task_id=task["id"], query="合成统计和既有失败"), worker)
    call("start-task", dict(task_id=task["id"], receipt_id=packet["id"]), worker)
    (root / "tools").mkdir()
    (root / "outputs").mkdir()
    shutil.copyfile(source / "examples/summarize.py", root / "tools/summarize.py")
    (root / "outputs/input.json").write_text("[1,2,3]", encoding="utf-8")
    manifest = dict(name="example-summary", version="1.0", api=1, entry="tools/summarize.py", purpose="合成统计",
        aliases=["汇总"], inputs="JSON数值数组", outputs="count/sum/mean", owner="example-owner",
        limitations="只用于合成数值示范", verification="[1,2,3]平均值应为2", dependencies=[])
    tool = call("register-tool", dict(manifest=manifest), owner)
    packet = call("prepare", dict(task_id=task["id"], query="合成统计"), worker)
    run = call("run-tool", dict(tool_id=tool["id"], task_id=task["id"], receipt_id=packet["id"],
        arguments=["--input", str(root / "outputs/input.json"), "--output", "summary.json"]), worker)
    assert run["data"]["status"] == "completed"
    shutil.copyfile(root / run["data"]["directory"] / "summary.json", root / "outputs/summary.json")
    assert json.loads((root / "outputs/summary.json").read_text())["mean"] == 2.0
    artifact = call("add-artifact", dict(path="outputs/summary.json", title="合成汇总输出", task_id=task["id"]), worker)
    call("review", dict(item_id=artifact["id"], expected_revision=artifact["rev"], verdict="pass",
        method="核对演示中的文件字节及mean字段；不是实际科研独立复核", checks=["实际输出mean==2"]), reviewer)
    item = call("add-item", dict(title="三项合成数据的平均值", text="[1,2,3]的平均值为2，尚未检验真实业务数据。", classification="fact",
        scope="仅本次三个合成数值", supports=[artifact["id"]], task_id=task["id"]), worker)
    packet = call("prepare", dict(task_id=task["id"], query="合成汇总"), worker)
    call("submit", dict(task_id=task["id"], items=[item["id"]], receipt_id=packet["id"]), worker)
    review = call("review", dict(item_id=item["id"], expected_revision=item["rev"], verdict="pass", method="演示程序断言实际输出mean==2；不是实际科研独立复核",
        checks=["实际运行输出与预先指定合成答案相同"]), reviewer)
    packet = call("prepare", dict(task_id=task["id"], query="合成汇总"), owner)
    call("adopt", dict(item_id=item["id"], review_id=review["id"], receipt_id=packet["id"]), owner)
    closed = call("close-task", dict(task_id=task["id"], summary="已核对三个合成数值的汇总，说明从执行到审核、采纳和追溯的流程可运行。真实业务仍需另行验证。"), owner)
    trace = call("trace", dict(stage_id=closed["stage"]["id"]), write=False)
    assert artifact["data"]["blob"] in trace["snapshots"]
    views = call("publish", {}, owner)
    print(json.dumps(dict(project=str(root), status="completed", stage=closed["stage"]["id"],
        journal=str(root / views["directory"] / "JOURNAL.md"), no_paid_or_remote_calls=True), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
