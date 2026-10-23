import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="aireos-cli-test-")
        cls.root = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.project = self.root / ("project-" + self.id().rsplit(".", 1)[-1])

    def run_cli(self, *arguments, cwd=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SOURCE_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "aireos", *map(str, arguments)],
            cwd=cwd or SOURCE_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def write_input(self, value, name="input.json", raw=False):
        path = self.root / name
        if raw:
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def initialize(self):
        result = self.run_cli("--project", self.project, "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_init_and_read_only_get_list_from_different_cwd(self):
        initialized = self.initialize()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        listed = self.run_cli("--project", self.project, "list", cwd=elsewhere)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout), [])
        fetched = self.run_cli("--project", self.project, "get",
                               "--input", self.write_input({"identifier": "item-missing"}), cwd=elsewhere)
        self.assertEqual(fetched.returncode, 2)
        self.assertIsInstance(json.loads(fetched.stderr), dict)
        self.assertTrue(Path(initialized["credential_file"]).is_file())

    def test_help_lists_commands(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prepare-external-review", result.stdout)
        self.assertIn("register-resource", result.stdout)

    def test_write_without_explicit_credential_does_not_load_owner_key(self):
        self.initialize()
        payload = {"title": "synthetic", "objective": "synthetic", "acceptance": ["done"], "outputs": ["out"]}
        result = self.run_cli("--project", self.project, "create-task", "--request-id", "no-key-write",
                              "--input", self.write_input(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        events = self.run_cli("--project", self.project, "events")
        self.assertEqual(events.returncode, 0, events.stderr)
        self.assertEqual(json.loads(events.stdout), [])

    def test_request_id_input_override_is_rejected(self):
        self.initialize()
        payload = {"request_id": "injected", "title": "x"}
        result = self.run_cli("--project", self.project, "create-task", "--request-id", "cli-id",
                              "--input", self.write_input(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("request_id", json.loads(result.stderr)["error"])

    def test_invalid_json_shape_and_unknown_command_return_json_error(self):
        self.initialize()
        bad_shape = self.run_cli("--project", self.project, "list", "--input",
                                 self.write_input([], name="bad-shape.json"))
        self.assertEqual(bad_shape.returncode, 2)
        self.assertIn("error", json.loads(bad_shape.stderr))
        bad_json = self.run_cli("--project", self.project, "list", "--input",
                                self.write_input("{", name="bad-json.json", raw=True))
        self.assertEqual(bad_json.returncode, 2)
        self.assertIn("error", json.loads(bad_json.stderr))
        unknown = self.run_cli("--project", self.project, "not-a-command")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("error", json.loads(unknown.stderr))

    def test_history_commands_keep_old_material_as_reference(self):
        initialized = self.initialize()
        source = self.project / "ledger.md"
        source.write_text("## F-001 — 合成旧记录\nStatus: rejected\n旧方法失效。\n", encoding="utf-8")
        configured = self.run_cli("--project", self.project, "--credential-file", initialized["credential_file"],
            "history-configure", "--input", self.write_input({"sources":[{"path":"ledger.md","format":"markdown"}]}),
            "--request-id", "history-config")
        self.assertEqual(0, configured.returncode, configured.stderr)
        found = self.run_cli("--project", self.project, "history-query", "--input", self.write_input({"query":"F-001"}))
        self.assertEqual(0, found.returncode, found.stderr)
        hit = json.loads(found.stdout)["matches"][0]
        self.assertEqual("historical_reference", hit["authority"])
        full = self.run_cli("--project", self.project, "history-get", "--input", self.write_input({
            "path":"ledger.md", "legacy_id":"F-001", "source_sha256":hit["source"]["sha256"]}))
        self.assertEqual(0, full.returncode, full.stderr)
        self.assertIn("旧方法失效", json.loads(full.stdout)["text"])
        nav = self.run_cli("--project", self.project, "session-context")
        self.assertEqual(0, nav.returncode, nav.stderr)


if __name__ == "__main__":
    unittest.main()
