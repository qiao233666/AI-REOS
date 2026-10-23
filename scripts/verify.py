"""验证通用框架源码并保存真实测试结果；不访问实际项目或真实模型。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = (args.output or root / "verification").resolve()
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("aireos", "tests", "examples", "scripts"):
        for path in (root / directory).rglob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path.relative_to(root)), "exec")
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
    result = subprocess.run(command, cwd=root, env=environment, capture_output=True, encoding="utf-8", timeout=120)
    log = result.stdout + result.stderr
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_name = "unittest-" + stamp + ".log"
    (output / log_name).write_text(log, encoding="utf-8")
    found = re.search(r"Ran (\d+) tests?", log)
    passed = result.returncode == 0 and found is not None and "skipped=" not in log and int(found[1]) > 0
    checksums = {}
    for directory in ("aireos", "tests", "examples", "scripts", "template"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                checksums[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = dict(timestamp_utc=stamp, passed=passed, returncode=result.returncode,
        tests=int(found[1]) if found else 0, skipped=0 if "skipped=" not in log else "see log",
        python=platform.python_version(), sqlite=sqlite3.sqlite_version, system=platform.system(),
        command="python -B -m unittest discover -s tests -v", log=log_name, source_sha256=checksums,
        limitations=["合成测试不是实际科研正确性或真实历史召回评估", "外部模型由假提供方测试，没有真实付费调用", "没有迁移已有项目"])
    (output / ("result-" + stamp + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text("# 验证报告\n\n" +
        f"时间（UTC）：{stamp}。Python {report['python']}，SQLite {report['sqlite']}，{report['system']}。\n\n" +
        f"结果：{'通过' if passed else '失败'}；实际运行 {report['tests']} 项测试。返回码 {result.returncode}；跳过 {report['skipped']} 项。\n\n" +
        f"命令：`{report['command']}`。完整输出：[{log_name}]({log_name})。源码摘要及环境：[JSON](result-{stamp}.json)。\n\n" +
        "覆盖事务回滚、交错重试、多个本地进程、逐项审核与采纳、来源变化、旧版召回、中文检索、争议传播、接管迟到、范围权限、时间回拨、阶段追溯、视图恢复、工具真实执行/失败/超时、资源占用和真实CLI合成示例。\n\n" +
        "边界：合成测试不能证明真实研究正确性、历史召回率、人的理解效果或分布式一致性。外部审核仅由假提供方测试，没有真实付费调用；未迁移已有项目。\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "tests", "python", "sqlite", "timestamp_utc")}, ensure_ascii=False))
    if not passed:
        print(log, file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
