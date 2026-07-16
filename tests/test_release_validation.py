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
    def test_release_validator_passes(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_release.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("release validation passed", proc.stdout.lower())

    def test_scans_tracked_content_but_ignores_local_artifacts_without_echoing_secret(self):
        secret = ("f" + "n_sk_") + "synthetic-release-test"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in validate_release.REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("safe public content\n", encoding="utf-8")
            (root / ".gitignore").write_text(
                ".venv/\nwork/\ndist/\n.superpowers/\n", encoding="utf-8"
            )
            public_file = root / "public.txt"
            public_file.write_text(secret, encoding="utf-8")
            for name in (".venv", "work", "dist", ".superpowers"):
                path = root / name / "local.txt"
                path.parent.mkdir(parents=True)
                path.write_text(secret, encoding="utf-8")
            subprocess.run(
                ["git", "init", "--quiet"], cwd=root, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "add", ".gitignore", "public.txt"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            output = io.StringIO()
            with mock.patch.object(validate_release, "ROOT", root):
                with contextlib.redirect_stdout(output):
                    result = validate_release.main()

        message = output.getvalue()
        self.assertEqual(result, 1, message)
        self.assertIn("public.txt", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("local.txt", message)


if __name__ == "__main__":
    unittest.main()
