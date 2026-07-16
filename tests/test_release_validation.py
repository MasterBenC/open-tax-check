import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import validate_release


ROOT = Path(__file__).resolve().parents[1]


class ReleaseValidationTests(unittest.TestCase):
    def create_repository(self, directory, missing=()):
        root = Path(directory)
        for name in validate_release.REQUIRED:
            if name in missing:
                continue
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("safe public content\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet"], cwd=root, check=True, capture_output=True
        )
        return root

    def run_validator(self, root):
        output = io.StringIO()
        with mock.patch.object(validate_release, "ROOT", root):
            with contextlib.redirect_stdout(output):
                result = validate_release.main()
        return result, output.getvalue()

    def test_release_validator_passes(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_release.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("release validation passed", proc.stdout.lower())

    def test_missing_required_files_are_listed(self):
        missing = {"SECURITY.md", "skills/open-tax-check/agents/openai.yaml"}
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory, missing=missing)
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        for name in missing:
            self.assertIn(name, message)

    def test_ignored_local_artifact_secrets_are_not_scanned(self):
        secret = ("f" + "n_sk_") + "synthetic-release-test"
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            (root / ".gitignore").write_text(
                ".venv/\nwork/\ndist/\n.superpowers/\n", encoding="utf-8"
            )
            for name in (".venv", "work", "dist", ".superpowers"):
                path = root / name / "local.txt"
                path.parent.mkdir(parents=True)
                path.write_text(secret, encoding="utf-8")
            result, message = self.run_validator(root)

        self.assertEqual(result, 0, message)
        self.assertIn("release validation passed", message.lower())
        self.assertNotIn(secret, message)

    def test_tracked_content_remains_scanned_after_it_is_ignored(self):
        secret = ("f" + "n_sk_") + "synthetic-tracked-test"
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            public_file = root / "public.txt"
            public_file.write_text(secret, encoding="utf-8")
            subprocess.run(
                ["git", "add", "public.txt"], cwd=root, check=True, capture_output=True
            )
            (root / ".gitignore").write_text("public.txt\n", encoding="utf-8")
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertIn("public.txt", message)
        self.assertNotIn(secret, message)

    def test_untracked_public_content_is_scanned_without_echoing_secret(self):
        secret = ("f" + "n_sk_") + "synthetic-untracked-test"
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            (root / "public.txt").write_text(secret, encoding="utf-8")
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertIn("public.txt", message)
        self.assertNotIn(secret, message)

    def test_tracked_read_error_fails_without_exception_details(self):
        exception_detail = "sensitive operating-system detail"
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            unreadable = root / "unreadable.txt"
            unreadable.write_text("safe public content\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "unreadable.txt"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            original_read_text = Path.read_text

            def read_text(path, *args, **kwargs):
                if path == unreadable:
                    raise PermissionError(exception_detail)
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", read_text):
                result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertEqual(
            message, "unable to read public release content: unreadable.txt\n"
        )
        self.assertNotIn(exception_detail, message)


if __name__ == "__main__":
    unittest.main()
