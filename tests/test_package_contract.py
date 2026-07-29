import json
import os
from importlib.resources import files
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackageContractTests(unittest.TestCase):
    def test_package_exposes_version(self):
        import open_tax_check

        self.assertEqual(open_tax_check.__version__, "0.1.0")

    def test_packaged_tax_pack_prototype_is_available_as_a_resource(self):
        manifest = files("open_tax_check.tax_packs").joinpath(
            "vat-cn-2023-2026.prototype.json"
        )

        self.assertTrue(manifest.is_file())
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["pack_id"], "vat-cn-2023-2026-prototype")
        self.assertEqual(payload["maturity"], "prototype")

    def test_packaged_tax_packs_include_catalog_and_all_formal_manifests(self):
        from open_tax_check.tax_registry import TAX_DEFINITIONS, load_builtin_tax_packs

        package = files("open_tax_check.tax_packs")
        names = {entry.name for entry in package.iterdir() if entry.name.endswith(".json")}
        self.assertIn("fact-catalog.zh-CN.v1.json", names)
        formal_ids = {
            pack.tax_id for pack in load_builtin_tax_packs() if pack.maturity.value == "screening"
        }
        self.assertEqual(formal_ids, {item.tax_id for item in TAX_DEFINITIONS})

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

    def test_excel_extra_is_bounded(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(
            data["project"]["optional-dependencies"]["excel"],
            ["openpyxl>=3.1.5,<4", "xlrd>=2.0.2,<3", "defusedxml>=0.7.1,<1"],
        )

    def test_document_extraction_contract_adds_no_runtime_dependency(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(data["project"]["dependencies"], [])

    def test_init_cli_emits_review_period(self):
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
                    "test entity",
                    "--period-start",
                    "2025-01-01",
                    "--period-end",
                    "2025-12-31",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["review_period"],
            {"start": "2025-01-01", "end": "2025-12-31"},
        )

    def test_set_scope_cli_emits_review_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case"
            init_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "open_tax_check.cli",
                    "init",
                    "--case",
                    str(case_dir),
                    "--entity",
                    "test entity",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "open_tax_check.cli",
                    "set-scope",
                    "--case",
                    str(case_dir),
                    "--period-start",
                    "2025-01-01",
                    "--period-end",
                    "2025-12-31",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

        self.assertEqual(init_proc.returncode, 0, init_proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["review_period"],
            {"start": "2025-01-01", "end": "2025-12-31"},
        )


if __name__ == "__main__":
    unittest.main()
