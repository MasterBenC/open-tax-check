import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
