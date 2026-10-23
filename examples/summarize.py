"""Summarize a JSON list of finite numbers using the Python standard library."""

import argparse
import json
import math
import sys
from pathlib import Path


def summarize(value):
    if not isinstance(value, list):
        raise ValueError("input JSON must be a list of numbers")
    if not value:
        raise ValueError("input list must not be empty")

    numbers = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"item at index {index} must be a number, not a boolean or other value")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"item at index {index} must be finite")
        numbers.append(number)

    total = math.fsum(numbers)
    if not math.isfinite(total):
        raise ValueError("sum is not finite")
    mean = total / len(numbers)
    if not math.isfinite(mean):
        raise ValueError("mean is not finite")
    return {"count": len(numbers), "sum": total, "mean": mean}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="path to a JSON list of numbers")
    parser.add_argument("--output", required=True, type=Path, help="new JSON output path; existing files are preserved")
    args = parser.parse_args(argv)

    try:
        value = json.loads(args.input.read_text(encoding="utf-8"))
        result = summarize(value)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(output)
    except (OSError, UnicodeError, json.JSONDecodeError, OverflowError, ValueError) as exc:
        print(f"summarize.py: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
