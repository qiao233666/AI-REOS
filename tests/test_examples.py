"""独立脚本边界和整条命令行示例。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExampleTests(unittest.TestCase):
    def test_summary_validates_numbers_and_never_overwrites_output(self):
        spec = importlib.util.spec_from_file_location("summary_example", ROOT / "examples/summarize.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(2.0, module.summarize([1, 2, 3])["mean"])
        for value in ([], [True], [float("nan")], [float("inf")], ["2"]):
            with self.assertRaises(ValueError):
                module.summarize(value)
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.json", Path(directory) / "result.json"
            source.write_text("[1,2,3]")
            command = [sys.executable, str(ROOT / "examples/summarize.py"), "--input", str(source), "--output", str(output)]
            self.assertEqual(0, subprocess.run(command, capture_output=True).returncode)
            old = output.read_bytes()
            self.assertEqual(1, subprocess.run(command, capture_output=True).returncode)
            self.assertEqual(old, output.read_bytes())

    def test_full_cli_example_from_new_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONUTF8="1")
            result = subprocess.run([sys.executable, "-B", str(ROOT / "examples/demo.py"), "--project", str(Path(directory) / "new-project")],
                capture_output=True, encoding="utf-8", env=env, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual("completed", output["status"])
            self.assertTrue(output["no_paid_or_remote_calls"])
            self.assertTrue(Path(output["journal"]).is_file())


if __name__ == "__main__":
    unittest.main()
