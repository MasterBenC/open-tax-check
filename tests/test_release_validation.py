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

    def test_tracked_non_utf8_unknown_content_fails_with_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            public_file = root / "tracked.payload"
            public_file.write_bytes(b"\xff\xfe\x00\x80")
            subprocess.run(
                ["git", "add", "tracked.payload"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertEqual(
            message,
            "unable to decode public release content: "
            "tracked.payload (undecodable_public_content)\n",
        )

    def test_untracked_non_utf8_unknown_content_fails_with_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            (root / "untracked.payload").write_bytes(b"\xff\xfe\x00\x80")
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertEqual(
            message,
            "unable to decode public release content: "
            "untracked.payload (undecodable_public_content)\n",
        )

    def test_untracked_forbidden_binary_suffixes_fail_closed(self):
        suffixes = (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".zip",
            ".7z",
            ".rar",
            ".tar",
            ".gz",
        )
        private_bytes = b"private-client-binary-contents-\xff\xfe\x00\x80"

        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    relative_path = Path(f"public{suffix}")
                    (root / relative_path).write_bytes(private_bytes)
                    result, message = self.run_validator(root)

                self.assertEqual(result, 1, message)
                self.assertEqual(
                    message,
                    "forbidden public binary content in "
                    f"{relative_path}: binary_public_content\n",
                )
                self.assertNotIn("private-client-binary-contents", message)

    def test_tracked_forbidden_binary_suffix_fails_closed_case_insensitively(self):
        private_bytes = b"tracked-private-client-binary-contents"
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            relative_path = Path("evidence.PnG")
            (root / relative_path).write_bytes(private_bytes)
            subprocess.run(
                ["git", "add", str(relative_path)],
                cwd=root,
                check=True,
                capture_output=True,
            )
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertEqual(
            message,
            "forbidden public binary content in "
            "evidence.PnG: binary_public_content\n",
        )
        self.assertNotIn("tracked-private-client-binary-contents", message)

    def test_tracked_forbidden_binary_remains_rejected_after_it_is_ignored(self):
        private_bytes = b"ignored-private-client-binary-contents"
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_repository(directory)
            relative_path = Path("client-document.pdf")
            (root / relative_path).write_bytes(private_bytes)
            subprocess.run(
                ["git", "add", str(relative_path)],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / ".gitignore").write_text(
                f"{relative_path}\n", encoding="utf-8"
            )
            result, message = self.run_validator(root)

        self.assertEqual(result, 1, message)
        self.assertEqual(
            message,
            "forbidden public binary content in "
            "client-document.pdf: binary_public_content\n",
        )
        self.assertNotIn("ignored-private-client-binary-contents", message)

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
