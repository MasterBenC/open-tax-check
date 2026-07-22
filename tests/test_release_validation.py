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

    def write_api_key_assignment(
        self, path, variable_name, value, separator="=", quote="", spacing=""
    ):
        assignment = (
            variable_name
            + spacing
            + separator
            + spacing
            + quote
            + value
            + quote
            + "\n"
        )
        path.write_text(assignment, encoding="utf-8")
        return assignment

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

    def test_api_key_assignments_fail_closed_without_echoing_values(self):
        cases = (
            ("FN_API_KEY", "=", "", "", "untracked"),
            ("API_KEY", "=", '"', " ", "tracked"),
            ("api_key", ":", "'", " ", "tracked_after_ignore"),
            ("BAIDU_OCR_API_KEY", ":", "", " ", "untracked"),
            ("vendor-api-key", "=", '"', "", "tracked"),
        )

        for variable_name, separator, quote, spacing, repository_state in cases:
            with self.subTest(
                variable_name=variable_name,
                separator=separator,
                quote=quote,
                repository_state=repository_state,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    relative_path = Path("public.txt")
                    secret = "synthetic" + "_secret"
                    assignment = self.write_api_key_assignment(
                        root / relative_path,
                        variable_name,
                        secret,
                        separator=separator,
                        quote=quote,
                        spacing=spacing,
                    )
                    if repository_state in {"tracked", "tracked_after_ignore"}:
                        subprocess.run(
                            ["git", "add", str(relative_path)],
                            cwd=root,
                            check=True,
                            capture_output=True,
                        )
                    if repository_state == "tracked_after_ignore":
                        (root / ".gitignore").write_text(
                            f"{relative_path}\n", encoding="utf-8"
                        )

                    result, message = self.run_validator(root)

                self.assertEqual(result, 1, message)
                self.assertEqual(
                    message,
                    "forbidden public content in public.txt: api_key\n",
                )
                self.assertNotIn(secret, message)
                self.assertNotIn(assignment.strip(), message)

    def test_empty_api_key_assignments_do_not_fail_validation(self):
        cases = (
            ("FN_API_KEY", "=", "", ""),
            ("API_KEY", "=", '"', ""),
            ("api_key", ":", "'", ""),
            ("BAIDU_OCR_API_KEY", ":", "", ""),
            ("FN_API_KEY", "=", "", "|"),
        )

        for variable_name, separator, quote, value in cases:
            with self.subTest(
                variable_name=variable_name,
                separator=separator,
                quote=quote,
                value=value,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    self.write_api_key_assignment(
                        root / "public.txt",
                        variable_name,
                        value,
                        separator=separator,
                        quote=quote,
                        spacing=" ",
                    )
                    result, message = self.run_validator(root)

                self.assertEqual(result, 0, message)
                self.assertEqual(message, "release validation passed\n")

    def test_extended_api_key_config_syntaxes_fail_without_echoing_values(self):
        secret = "synthetic" + "_config_value"
        snake_name = "FN" + "_API" + "_KEY"
        lower_name = "api" + "_key"
        camel_name = "baidu" + "Ocr" + "Api" + "Key"
        cases = (
            ("camel_case", "".join((camel_name, " = ", secret))),
            (
                "double_quoted_json_key",
                "".join(('"', snake_name, '"', ': "', secret, '"')),
            ),
            (
                "double_quoted_camel_case_key",
                "".join(('"', camel_name, '"', ': "', secret, '"')),
            ),
            (
                "single_quoted_yaml_key",
                "".join(("'", lower_name, "'", ": '", secret, "'")),
            ),
            ("shell_adjacent_single_quotes", "".join((snake_name, "=", "''", secret))),
            ("shell_adjacent_double_quotes", "".join((snake_name, "=", '""', secret))),
            ("leading_colon_value", "".join((snake_name, "=", ":", secret))),
        )

        for syntax, payload in cases:
            with self.subTest(syntax=syntax):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    (root / "public.txt").write_text(payload, encoding="utf-8")
                    result, message = self.run_validator(root)

                self.assertEqual(result, 1, message)
                self.assertEqual(
                    message,
                    "forbidden public content in public.txt: api_key\n",
                )
                self.assertNotIn(secret, message)
                self.assertNotIn(payload, message)

    def test_yaml_block_scalar_api_keys_require_indented_nonempty_content(self):
        secret = "synthetic" + "_block_value"
        variable_name = "API" + "_KEY"

        for indicator in ("|", "|-", "|+"):
            with self.subTest(indicator=indicator):
                payload = "".join(
                    (variable_name, ": ", indicator, "\n", "  ", secret, "\n")
                )
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    (root / "public.yaml").write_text(payload, encoding="utf-8")
                    result, message = self.run_validator(root)

                self.assertEqual(result, 1, message)
                self.assertEqual(
                    message,
                    "forbidden public content in public.yaml: api_key\n",
                )
                self.assertNotIn(secret, message)
                self.assertNotIn(payload, message)

    def test_yaml_block_scalar_skips_only_blank_lines_before_content(self):
        secret = "synthetic" + "_delayed_block_value"
        variable_name = "API" + "_KEY"

        for indicator in ("|", "|-", "|+"):
            for line_ending in ("\n", "\r\n"):
                blank_prefixes = (
                    line_ending,
                    "".join(("  ", line_ending, "\t", line_ending)),
                )
                for blank_prefix in blank_prefixes:
                    with self.subTest(
                        indicator=indicator,
                        line_ending=repr(line_ending),
                        blank_line_count=blank_prefix.count(line_ending),
                    ):
                        payload = "".join(
                            (
                                variable_name,
                                ": ",
                                indicator,
                                line_ending,
                                blank_prefix,
                                "  ",
                                secret,
                                line_ending,
                            )
                        )
                        with tempfile.TemporaryDirectory() as directory:
                            root = self.create_repository(directory)
                            (root / "public.yaml").write_text(
                                payload, encoding="utf-8", newline=""
                            )
                            result, message = self.run_validator(root)

                        self.assertEqual(result, 1, message)
                        self.assertEqual(
                            message,
                            "forbidden public content in public.yaml: api_key\n",
                        )
                        self.assertNotIn(secret, message)
                        self.assertNotIn(payload, message)

    def test_nested_yaml_block_scalar_requires_content_deeper_than_key(self):
        secret = "synthetic" + "_nested_block_value"
        variable_name = "API" + "_KEY"

        for indicator in ("|", "|-", "|+"):
            for line_ending in ("\n", "\r\n"):
                with self.subTest(
                    indicator=indicator, line_ending=repr(line_ending)
                ):
                    payload = "".join(
                        (
                            "outer:",
                            line_ending,
                            "  ",
                            variable_name,
                            ": ",
                            indicator,
                            line_ending,
                            "  ",
                            line_ending,
                            "    ",
                            secret,
                            line_ending,
                        )
                    )
                    with tempfile.TemporaryDirectory() as directory:
                        root = self.create_repository(directory)
                        (root / "public.yaml").write_text(
                            payload, encoding="utf-8", newline=""
                        )
                        result, message = self.run_validator(root)

                    self.assertEqual(result, 1, message)
                    self.assertEqual(
                        message,
                        "forbidden public content in public.yaml: api_key\n",
                    )
                    self.assertNotIn(secret, message)
                    self.assertNotIn(payload, message)

    def test_nested_yaml_block_scalar_stops_at_same_or_lower_indentation(self):
        secret = "synthetic" + "_later_nested_value"
        variable_name = "API" + "_KEY"

        for line_ending in ("\n", "\r\n"):
            cases = (
                "".join(
                    (
                        "outer:",
                        line_ending,
                        "  ",
                        variable_name,
                        ": |",
                        line_ending,
                        "  sibling: safe",
                        line_ending,
                    )
                ),
                "".join(
                    (
                        "outer:",
                        line_ending,
                        "  ",
                        variable_name,
                        ": |+",
                        line_ending,
                        "  ",
                        line_ending,
                        "  sibling: safe",
                        line_ending,
                        "    ",
                        secret,
                        line_ending,
                    )
                ),
                "".join(
                    (
                        "outer:",
                        line_ending,
                        "  ",
                        variable_name,
                        ": |-",
                        line_ending,
                        "    ",
                        line_ending,
                        "next: safe",
                        line_ending,
                        "    ",
                        secret,
                        line_ending,
                    )
                ),
                "".join(
                    (
                        "items:",
                        line_ending,
                        "  - ",
                        variable_name,
                        ": |",
                        line_ending,
                        "    sibling: safe",
                        line_ending,
                    )
                ),
            )
            for case_index, payload in enumerate(cases):
                with self.subTest(
                    line_ending=repr(line_ending), case_index=case_index
                ):
                    with tempfile.TemporaryDirectory() as directory:
                        root = self.create_repository(directory)
                        (root / "public.yaml").write_text(
                            payload, encoding="utf-8", newline=""
                        )
                        result, message = self.run_validator(root)

                    self.assertEqual(result, 0, message)
                    self.assertEqual(message, "release validation passed\n")
                    self.assertNotIn(secret, message)

    def test_empty_and_regex_like_api_key_forms_remain_allowed(self):
        variable_name = "FN" + "_API" + "_KEY"
        non_key_value = "synthetic" + "_non_key_value"
        cases = (
            "".join((variable_name, "=")),
            "".join((variable_name, "=", "''")),
            "".join((variable_name, "=", '""')),
            "".join((variable_name, "=", "|", "api[_-]?key")),
            "".join((variable_name, ": ", "|", "\n")),
            "".join((variable_name, ": ", "|-", "\n", "  ", "\n")),
            "".join((variable_name, ": ", "|+", "\n", "not_indented", "\n")),
            "".join(
                (
                    variable_name,
                    ": ",
                    "|",
                    "\n",
                    "not_indented",
                    "\n",
                    "  ",
                    non_key_value,
                    "\n",
                )
            ),
            "".join(
                (variable_name, ": ", "|+", "\r\n", " \r\n", "\t\r\n")
            ),
            "".join(('"', variable_name, "'", ": ", non_key_value)),
            "".join(("'", variable_name, '"', ": ", non_key_value)),
        )

        for payload in cases:
            with self.subTest(payload_length=len(payload)):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    (root / "public.txt").write_text(payload, encoding="utf-8")
                    result, message = self.run_validator(root)

                self.assertEqual(result, 0, message)
                self.assertEqual(message, "release validation passed\n")

    def test_existing_sensitive_patterns_keep_safe_fixed_categories(self):
        cases = (
            (("f" + "n_sk_") + "synthetic-existing-test", "credential_prefix"),
            ("BEGIN " + "RSA " + "PRIVATE KEY", "private_key"),
        )

        for payload, category in cases:
            with self.subTest(category=category):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.create_repository(directory)
                    (root / "public.txt").write_text(payload, encoding="utf-8")
                    result, message = self.run_validator(root)

                self.assertEqual(result, 1, message)
                self.assertEqual(
                    message,
                    f"forbidden public content in public.txt: {category}\n",
                )
                self.assertNotIn(payload, message)

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
            ".bmp",
            ".gif",
            ".heic",
            ".heif",
            ".ico",
            ".jpeg",
            ".jpg",
            ".png",
            ".svg",
            ".tif",
            ".tiff",
            ".webp",
            ".doc",
            ".docm",
            ".docx",
            ".odt",
            ".ods",
            ".odp",
            ".pdf",
            ".ppt",
            ".pptm",
            ".pptx",
            ".xls",
            ".xlsb",
            ".xlsm",
            ".xlsx",
            ".7z",
            ".bz2",
            ".gz",
            ".rar",
            ".tar",
            ".tgz",
            ".xz",
            ".zip",
        )
        private_bytes = b"safe-looking-utf8-private-client-binary-contents"

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
