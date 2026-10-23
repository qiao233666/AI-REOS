import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SOURCE_ROOT / "examples" / "batch_demo.py"


class BatchExampleTests(unittest.TestCase):
    def test_success_and_refuses_existing_target(self):
        with tempfile.TemporaryDirectory(prefix="aireos-batch-demo-") as temporary:
            target = Path(temporary) / "fresh-project"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            environment["PYTHONUTF8"] = "1"
            command = [sys.executable, "-B", str(SCRIPT), "--project", str(target)]

            completed = subprocess.run(command, cwd=temporary, env=environment,
                                       capture_output=True, text=True, encoding="utf-8",
                                       shell=False, timeout=90)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["synthetic_only"])
            self.assertTrue(result["handoff_trace"])
            artifact = target / "results" / "summary.json"
            original = artifact.read_bytes()

            repeated = subprocess.run(command, cwd=temporary, env=environment,
                                      capture_output=True, text=True, encoding="utf-8",
                                      shell=False, timeout=30)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("拒绝覆盖", repeated.stderr)
            self.assertEqual(artifact.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
