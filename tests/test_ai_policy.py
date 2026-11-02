"""Consumer-visible merge gate boundaries; no real credentials in fixtures."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("ai_policy", Path(__file__).parents[1] / ".github" / "ai_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class MergePolicy(unittest.TestCase):
    def test_private_and_generated_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for name in (".env", ".env.production", "keys/id_rsa", "cert.pem", "a/.venv/lib.py", "credentials.json"):
                with self.subTest(name=name):
                    self.assertTrue(policy.inspect_path(name, root)[0])
            self.assertEqual(policy.inspect_path(".env.example", root), ([], False))

    def test_size_and_json_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "broken.json").write_text('{"unfinished":', encoding="utf-8")
            self.assertTrue(policy.inspect_path("broken.json", root)[0])
            with Path(root, "large.bin").open("wb") as file:
                file.truncate(10 * 1024 * 1024 + 1)
            self.assertTrue(policy.inspect_path("large.bin", root)[0])

    def test_workflows_and_policy_require_explicit_review(self):
        with tempfile.TemporaryDirectory() as root:
            for name in (".github/workflows/release.yml", ".github/ai_policy.py", "infra/main.tf", "migrations/001.sql", "deploy.py"):
                with self.subTest(name=name):
                    self.assertTrue(policy.inspect_path(name, root)[1])
            self.assertFalse(policy.inspect_path("settings_ui.py", root)[1])

    def test_credentials_rejected_but_references_allowed(self):
        examples = ["ghp_" + "x" * 36, "sk-" + "x" * 24, "AKIA" + "A" * 16,
                    'password = "' + "a" * 20 + '"']
        for value in examples:
            with self.subTest(kind=value[:4]):
                self.assertTrue(policy.contains_secret(value))
        self.assertFalse(policy.contains_secret('token = "${{ secrets.GITHUB_TOKEN }}"'))
        self.assertFalse(policy.contains_secret('password = "example-value-for-docs"'))
