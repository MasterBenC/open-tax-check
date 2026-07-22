import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PackageContractTests(unittest.TestCase):
    def test_package_exposes_version(self):
        import open_tax_check

        self.assertEqual(open_tax_check.__version__, "0.1.0")

    def test_module_help_is_available(self):
        proc = subprocess.run(
            [sys.executable, "-m", "open_tax_check.cli", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OpenTaxCheck", proc.stdout)

    def test_cli_emits_utf8_when_inherited_encoding_cannot_encode_chinese(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp1252"
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "open_tax_check.cli",
                    "init",
                    "--case",
                    str(case_dir),
                    "--entity",
                    "中文测试企业",
                ],
                capture_output=True,
                env=env,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(payload["entity_name"], "中文测试企业")


if __name__ == "__main__":
    unittest.main()
