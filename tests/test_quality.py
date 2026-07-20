import unittest

from open_tax_check.quality import validate_journal_quality, validate_voucher_balance


class DataQualityTests(unittest.TestCase):
    def _date_row(self, value: str) -> dict:
        return {
            "voucher_no": "记-001",
            "date": value,
            "account_name": "银行存款",
            "debit": "0",
            "credit": "0",
            "source_file": "journal.csv",
            "source_row": 2,
        }

    def test_reports_unbalanced_voucher_with_source_rows(self):
        rows = [
            {"voucher_no": "记-001", "debit": "100.00", "credit": "0", "source_row": 2},
            {"voucher_no": "记-001", "debit": "0", "credit": "100.00", "source_row": 3},
            {"voucher_no": "记-002", "debit": "80", "credit": "0", "source_row": 4},
            {"voucher_no": "记-002", "debit": "0", "credit": "70", "source_row": 5},
        ]

        findings = validate_voucher_balance(rows)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["voucher_no"], "记-002")
        self.assertEqual(findings[0]["difference"], "10.00")
        self.assertEqual(findings[0]["source_rows"], [4, 5])

    def test_voucher_balance_is_scoped_by_period_and_normalizes_supported_dates(self):
        rows = [
            {
                "voucher_no": "记-001",
                "date": "2025/1/2",
                "account_name": "银行存款",
                "debit": "100",
                "credit": "",
                "source_file": "journal.csv",
                "source_row": 2,
            },
            {
                "voucher_no": "记-001",
                "date": "2025.2.3",
                "account_name": "银行存款",
                "debit": "",
                "credit": "100",
                "source_file": "journal.csv",
                "source_row": 3,
            },
        ]

        findings = validate_journal_quality(rows)

        balance_findings = [item for item in findings if item["rule_id"] == "DQ-VOUCHER-001"]
        self.assertEqual(len(balance_findings), 2)
        self.assertEqual(
            [(item["period"], item["difference"]) for item in balance_findings],
            [("2025-01", "100.00"), ("2025-02", "-100.00")],
        )
        self.assertEqual(rows[0]["date"], "2025-01-02")
        self.assertEqual(rows[0]["period"], "2025-01")
        self.assertEqual(rows[0]["tax_year"], "2025")
        self.assertEqual(rows[1]["date"], "2025-02-03")

    def test_missing_required_values_and_both_blank_amounts_are_blocking_without_values(self):
        rows = [
            {
                "voucher_no": "",
                "date": "",
                "account_name": "",
                "summary": "sensitive narrative must not leak",
                "debit": " ",
                "credit": "\t",
                "source_file": "journal.csv",
                "source_sheet": "CSV",
                "source_row": 9,
            }
        ]

        findings = validate_journal_quality(rows)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "DQ-ROW-001")
        self.assertEqual(
            findings[0]["fields"],
            ["voucher_no", "date", "account_name", "debit", "credit"],
        )
        self.assertEqual(findings[0]["source_file"], "journal.csv")
        self.assertEqual(findings[0]["source_row"], 9)
        self.assertNotIn("summary", findings[0])
        self.assertNotIn("values", findings[0])

    def test_invalid_date_is_blocking_and_does_not_guess_a_period(self):
        rows = [
            {
                "voucher_no": "记-009",
                "date": "2025-02-30",
                "account_name": "银行存款",
                "debit": "1",
                "credit": "0",
                "source_file": "journal.csv",
                "source_row": 4,
            }
        ]

        findings = validate_journal_quality(rows)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "DQ-DATE-001")
        self.assertEqual(findings[0]["fields"], ["date"])
        self.assertNotIn("period", rows[0])
        self.assertNotIn("tax_year", rows[0])

    def test_supported_date_contract_is_explicit_and_normalized(self):
        cases = (
            ("2025-01-02", "2025-01-02"),
            ("2025/1/2", "2025-01-02"),
            ("2025/01/02", "2025-01-02"),
            ("2025.1.2", "2025-01-02"),
            ("2025.01.02", "2025-01-02"),
            ("2024-02-29", "2024-02-29"),
            (" 2025-01-02 ", "2025-01-02"),
        )

        for supplied, expected in cases:
            with self.subTest(supplied=supplied):
                row = self._date_row(supplied)
                findings = validate_journal_quality([row])
                self.assertEqual(findings, [])
                self.assertEqual(row["date"], expected)
                self.assertEqual(row["period"], expected[:7])
                self.assertEqual(row["tax_year"], expected[:4])

    def test_unsupported_or_invalid_dates_fail_closed(self):
        values = (
            "2025-1-2",
            "2023-02-29",
            "2025-01-02T10:00:00",
            "2025-01-02 00:00:00",
            "２０２５-０１-０２",
            "٢٠٢٥/١/٢",
            "२०२५.१.२",
        )

        for supplied in values:
            with self.subTest(supplied=supplied):
                row = self._date_row(supplied)
                findings = validate_journal_quality([row])
                self.assertEqual([item["rule_id"] for item in findings], ["DQ-DATE-001"])
                self.assertNotIn("period", row)
                self.assertNotIn("tax_year", row)

    def test_invalid_numeric_lexemes_are_blocking_without_echoing_values(self):
        invalid_values = (
            "1,2,3",
            "1,,000",
            "12,34",
            "NaN",
            "sNaN",
            "Infinity",
            "-Infinity",
            "1e3",
            "１２３",
            "1 000",
            "¥100",
            "(100)",
        )

        for supplied in invalid_values:
            with self.subTest(supplied=supplied):
                row = self._date_row("2025-01-02")
                row["debit"] = supplied
                findings = validate_journal_quality([row])
                numeric = [
                    item for item in findings if item["rule_id"] == "DQ-NUMERIC-001"
                ]
                self.assertEqual(len(numeric), 1)
                self.assertEqual(numeric[0]["fields"], ["debit"])
                self.assertEqual(numeric[0]["source_file"], "journal.csv")
                self.assertEqual(numeric[0]["source_row"], 2)
                self.assertNotIn("value", numeric[0])
                self.assertNotIn("values", numeric[0])
                self.assertNotIn(supplied, repr(numeric[0]))
                self.assertFalse(
                    any(item["rule_id"] == "DQ-VOUCHER-001" for item in findings)
                )

    def test_supported_numeric_lexemes_use_decimal_balance_semantics(self):
        supported_pairs = (
            ("", "0"),
            ("  +1000.25  ", "1,000.25"),
            ("-1,000", "-1000.00"),
            ("0012.50", "12.5"),
        )

        for debit, credit in supported_pairs:
            with self.subTest(debit=debit, credit=credit):
                row = self._date_row("2025-01-02")
                row.update({"debit": debit, "credit": credit})
                self.assertEqual(validate_journal_quality([row]), [])

    def test_csv_structure_issues_block_rows_before_other_quality_checks(self):
        for issue in ("extra_cells", "missing_cells"):
            with self.subTest(issue=issue):
                row = self._date_row("2025-01-02")
                row["_csv_structure_issues"] = [issue]

                findings = validate_journal_quality([row])

                self.assertEqual(
                    findings,
                    [
                        {
                            "rule_id": "DQ-CSV-STRUCTURE-001",
                            "rule_version": "0.1.0",
                            "status": "blocking",
                            "issue": issue,
                            "source_file": "journal.csv",
                            "source_row": 2,
                        }
                    ],
                )


if __name__ == "__main__":
    unittest.main()
