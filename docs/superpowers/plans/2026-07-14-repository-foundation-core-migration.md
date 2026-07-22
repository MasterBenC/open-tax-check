# OpenTaxCheck Repository Foundation and Core Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the working v0.1 tax-health-check Skill into a GitHub-ready Python package and repository that installs cleanly, runs the deterministic CSV case workflow, ships a compatible Skill wrapper, and passes cross-platform CI.

**Architecture:** The Python package under `src/open_tax_check/` is the single source of truth for case state, mapping, quality gates, rules, semantic candidates, and report selection. The Skill under `skills/open-tax-check/` is a thin host adapter that invokes the package CLI. This phase preserves the verified v0.1 behavior while establishing packaging, documentation, community, security, demo, and CI foundations for the OCR, AI, rule-pack, and professional-report phases.

**Tech Stack:** Python 3.11-3.13, Python standard library, setuptools, unittest, GitHub Actions, Agent Skills Markdown/YAML.

## Global Constraints

- The project is personally initiated, fully open source, and licensed under Apache License 2.0.
- The public name is `OpenTaxCheck｜开源税务健康检查`; the repository name is `open-tax-check`.
- The public contact is WeChat `PosterNote`; users must add the note `GitHub`.
- The deterministic engine must run with AI disabled.
- AI output never changes source data, formulas, applicable periods, formal rules, or professional conclusions.
- Unresolved field mapping and blocking data-quality findings stop calculation.
- Every finding preserves source lineage, rule id, rule version, status, and review state.
- No real client data, credentials, third-party trademarks, or protected report templates enter the repository.
- This phase supports one legal entity and UTF-8/UTF-8-BOM CSV journals; unsupported inputs are reported explicitly.
- This phase does not implement Excel ingestion, OCR, cloud services, expanded rule packs, DOCX/PDF generation, or a graphical UI.

---

## Phase Sequence After This Plan

Each following subsystem receives its own implementation plan after this foundation is merged:

1. Excel/PDF/image intake and OCR adapters, including PP-OCRv6 and authorized PaddleOCR-VL-1.6 cloud enhancement.
2. Versioned tax-rule packs for 2024-2026 and the core tax families.
3. `off`/`assist`/`deep` AI task orchestration and evidence validation.
4. Professional review workbook, editable DOCX, client PDF, and evidence package.
5. Fengniao company-data adapter, privacy controls, full fictional case, and public release hardening.

---

### Task 1: Establish the Installable Python Project

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `src/open_tax_check/__init__.py`
- Create: `src/open_tax_check/cli.py`
- Test: `tests/test_package_contract.py`

**Interfaces:**
- Consumes: Python 3.11-3.13 and setuptools.
- Produces: importable package `open_tax_check`, console command `opentaxcheck`, and `open_tax_check.cli:main() -> int`.

- [ ] **Step 1: Write the failing package-contract test**

```python
# tests/test_package_contract.py
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
```

- [ ] **Step 2: Run the test and verify the package is missing**

Run: `python -m unittest tests.test_package_contract -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'open_tax_check'`.

- [ ] **Step 3: Add packaging metadata and the minimal CLI**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "open-tax-check"
version = "0.1.0"
description = "Open-source AI-assisted tax health check for Chinese enterprises"
readme = "README.md"
requires-python = ">=3.11,<3.14"
license = {text = "Apache-2.0"}
authors = [{name = "MasterBenC"}]
keywords = ["tax", "china", "audit", "risk", "agent-skill"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "License :: OSI Approved :: Apache Software License",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
]
dependencies = []

[project.scripts]
opentaxcheck = "open_tax_check.cli:main"

[project.urls]
Repository = "https://github.com/MasterBenC/open-tax-check"
Issues = "https://github.com/MasterBenC/open-tax-check/issues"

[tool.setuptools.packages.find]
where = ["src"]
```

```gitignore
# .gitignore
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.coverage
build/
dist/
.venv/
venv/
.env
.env.*
!.env.example
work/
tmp/
cases/
*.log
```

```markdown
<!-- README.md -->
# OpenTaxCheck｜开源税务健康检查

OpenTaxCheck is being prepared as an open-source, AI-assisted tax health check for Chinese enterprises. The current local phase establishes the deterministic CSV core; public release occurs only after the complete release acceptance criteria are met.
```

```python
# src/open_tax_check/__init__.py
"""OpenTaxCheck deterministic core and professional-review workflow."""

__version__ = "0.1.0"
```

```python
# src/open_tax_check/cli.py
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="opentaxcheck",
        description="OpenTaxCheck｜开源税务健康检查",
    )


def main() -> int:
    build_parser().parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Install the package in an isolated environment and run the tests**

Run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest tests.test_package_contract -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit the package foundation**

```powershell
git add pyproject.toml .gitignore README.md src/open_tax_check tests/test_package_contract.py
git commit -m "build: establish installable OpenTaxCheck package"
```

---

### Task 2: Migrate and Preserve the Verified Deterministic Modules

**Files:**
- Create: `src/open_tax_check/mapping.py`
- Create: `src/open_tax_check/quality.py`
- Create: `src/open_tax_check/rules.py`
- Create: `src/open_tax_check/semantic.py`
- Create: `src/open_tax_check/reporting.py`
- Create: `tests/test_mapping.py`
- Create: `tests/test_quality.py`
- Create: `tests/test_rules.py`
- Create: `tests/test_semantic.py`
- Create: `tests/test_reporting.py`

**Interfaces:**
- Consumes: journal headers and normalized journal row dictionaries.
- Produces: `map_headers(headers: list[str]) -> dict`, `validate_voucher_balance(rows: list[dict], tolerance: Decimal) -> list[dict]`, `business_entertainment_adjustment(actual_expense: Decimal, revenue: Decimal) -> dict`, `shareholder_loan_candidate(*, ending_balance: Decimal, is_individual_shareholder: bool, outstanding_at_year_end: bool, business_use_confirmed: bool) -> dict | None`, `detect_semantic_candidates(entries: list[dict]) -> list[dict]`, and `select_reportable_findings(findings: list[dict]) -> list[dict]`.

- [ ] **Step 1: Copy the verified tests into focused repository tests**

Use the assertions from `<legacy-skill-dir>/scripts/tests/test_engine.py` without weakening them. Split its test classes into the five named test files and change imports from `taxcheck.*` to `open_tax_check.*`.

The required import blocks are:

```python
# tests/test_mapping.py
from open_tax_check.mapping import map_headers
```

```python
# tests/test_quality.py
from open_tax_check.quality import validate_voucher_balance
```

```python
# tests/test_rules.py
from open_tax_check.rules import (
    business_entertainment_adjustment,
    shareholder_loan_candidate,
)
```

```python
# tests/test_semantic.py
from open_tax_check.semantic import detect_semantic_candidates
```

```python
# tests/test_reporting.py
from open_tax_check.reporting import select_reportable_findings
```

- [ ] **Step 2: Run the focused tests and verify all five modules are missing**

Run: `python -m unittest tests.test_mapping tests.test_quality tests.test_rules tests.test_semantic tests.test_reporting -v`

Expected: import failures for the five missing `open_tax_check` modules.

- [ ] **Step 3: Copy the verified module implementations without changing behavior**

Run these mechanical copy operations from the repository root:

```powershell
$source = "<legacy-skill-dir>\scripts\taxcheck"
Copy-Item -LiteralPath "$source\mapping.py" -Destination "src\open_tax_check\mapping.py"
Copy-Item -LiteralPath "$source\quality.py" -Destination "src\open_tax_check\quality.py"
Copy-Item -LiteralPath "$source\rules.py" -Destination "src\open_tax_check\rules.py"
Copy-Item -LiteralPath "$source\semantic.py" -Destination "src\open_tax_check\semantic.py"
Copy-Item -LiteralPath "$source\reporting.py" -Destination "src\open_tax_check\reporting.py"
```

Do not rename rule ids, statuses, field names, formulas, or return keys during this migration.

- [ ] **Step 4: Run the focused and package tests**

Run: `python -m unittest discover -s tests -v`

Expected: all migrated engine tests and the 2 package tests pass.

- [ ] **Step 5: Commit the deterministic modules**

```powershell
git add src/open_tax_check tests
git commit -m "feat: migrate deterministic screening modules"
```

---

### Task 3: Add Resumable Case Initialization

**Files:**
- Create: `src/open_tax_check/case.py`
- Modify: `src/open_tax_check/cli.py`
- Create: `tests/test_case.py`

**Interfaces:**
- Consumes: `case_dir: Path`, `entity_name: str`.
- Produces: `init_case(case_dir: Path, entity_name: str) -> dict` and CLI command `opentaxcheck init --case PATH --entity NAME`.

- [ ] **Step 1: Write the failing case contract test**

```python
# tests/test_case.py
import json
import tempfile
import unittest
from pathlib import Path

from open_tax_check.case import init_case


class CaseTests(unittest.TestCase):
    def test_init_creates_resumable_case(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_dir = Path(temp_dir) / "case-a"
            state = init_case(case_dir, "示例企业")

            saved = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "ready_for_input")
            self.assertEqual(saved["entity_name"], "示例企业")
            self.assertEqual(saved["next_action"], "add_source_files")
            self.assertTrue((case_dir / "input").is_dir())
            self.assertTrue((case_dir / "work").is_dir())
            self.assertTrue((case_dir / "output").is_dir())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify `open_tax_check.case` is missing**

Run: `python -m unittest tests.test_case -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'open_tax_check.case'`.

- [ ] **Step 3: Implement case initialization and wire the CLI**

```python
# src/open_tax_check/case.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def init_case(case_dir: Path, entity_name: str) -> dict:
    case_dir.mkdir(parents=True, exist_ok=True)
    for child in ("input", "work", "output"):
        (case_dir / child).mkdir(exist_ok=True)
    state = {
        "schema_version": "0.1.0",
        "entity_name": entity_name,
        "status": "ready_for_input",
        "completed_steps": ["init"],
        "next_action": "add_source_files",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [],
    }
    write_json(case_dir / "state.json", state)
    return state
```

Replace `src/open_tax_check/cli.py` with:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .case import init_case


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opentaxcheck",
        description="OpenTaxCheck｜开源税务健康检查",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="Create a resumable case directory")
    init_parser.add_argument("--case", required=True, type=Path)
    init_parser.add_argument("--entity", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        print(json.dumps(init_case(args.case, args.entity), ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the case and full test suites**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Commit resumable case initialization**

```powershell
git add src/open_tax_check/case.py src/open_tax_check/cli.py tests/test_case.py
git commit -m "feat: add resumable case initialization"
```

---

### Task 4: Migrate the End-to-End CSV Pipeline

**Files:**
- Create: `src/open_tax_check/pipeline.py`
- Modify: `src/open_tax_check/cli.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: initialized case directory containing one UTF-8 or UTF-8-BOM CSV journal and `ai_mode` in `off|assist|deep`.
- Produces: `run_case(case_dir: Path, ai_mode: str) -> tuple[int, dict]`, normalized JSONL, data-quality JSON, findings JSON, Markdown internal draft, and updated state.

- [ ] **Step 1: Copy and update the verified pipeline tests**

Copy `<legacy-skill-dir>/scripts/tests/test_pipeline.py` to `tests/test_pipeline.py`. Replace the script-path subprocess helper with:

```python
def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "open_tax_check.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
)
```

Keep the assertions for `CIT-BE-001`, `SEM-EXP-001`, `15000.00`, mapping stop exit code `3`, artifacts, and `review_findings` unchanged. Add this parser-contract test; do not assert different AI behavior in this phase:

```python
from open_tax_check.cli import build_parser


def test_deep_ai_mode_is_accepted_by_cli_parser():
    args = build_parser().parse_args(["run", "--case", "case-a", "--ai-mode", "deep"])
    assert args.ai_mode == "deep"
```

- [ ] **Step 2: Run the pipeline tests and verify `run` is unavailable**

Run: `python -m unittest tests.test_pipeline -v`

Expected: FAIL because the CLI does not recognize the `run` command.

- [ ] **Step 3: Copy the verified pipeline and add the run command**

Run:

```powershell
Copy-Item -LiteralPath "<legacy-skill-dir>\scripts\taxcheck\pipeline.py" -Destination "src\open_tax_check\pipeline.py"
```

The copied relative imports already resolve inside `open_tax_check`. Add this parser configuration to `build_parser()` in `src/open_tax_check/cli.py`:

```python
    run_parser = subparsers.add_parser("run", help="Run deterministic ingestion and screening")
    run_parser.add_argument("--case", required=True, type=Path)
    run_parser.add_argument("--ai-mode", choices=["off", "assist", "deep"], default="assist")
```

Add this import:

```python
from .pipeline import run_case
```

Add this branch before `return 2`:

```python
    if args.command == "run":
        code, result = run_case(args.case, args.ai_mode)
        print(json.dumps(result, ensure_ascii=False))
        return code
```

In `src/open_tax_check/pipeline.py`, replace the accepted legacy label `enhanced` only in user-facing documentation or tests; the function accepts the already validated `ai_mode` string without branching.

- [ ] **Step 4: Run all tests and a manual demo case**

Run:

```powershell
python -m unittest discover -s tests -v
python -m open_tax_check.cli init --case work\smoke-case --entity "示例企业"
```

Expected: all tests pass and the CLI prints JSON with `"status": "ready_for_input"`.

- [ ] **Step 5: Commit the end-to-end pipeline**

```powershell
git add src/open_tax_check/pipeline.py src/open_tax_check/cli.py tests/test_pipeline.py
git commit -m "feat: migrate resumable CSV screening pipeline"
```

---

### Task 5: Add the Thin Agent Skill

**Files:**
- Create: `skills/open-tax-check/SKILL.md`
- Create: `skills/open-tax-check/agents/openai.yaml`
- Create: `skills/open-tax-check/scripts/opentaxcheck.py`
- Create: `skills/open-tax-check/references/canonical-schema.md`
- Create: `skills/open-tax-check/references/methodology.md`
- Create: `skills/open-tax-check/references/rule-catalog.md`
- Create: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: an installed `open-tax-check` Python package or repository checkout.
- Produces: an Agent Skills-compatible package that invokes `open_tax_check.cli:main` and preserves mapping, data-quality, AI-candidate, and professional-review boundaries.

- [ ] **Step 1: Write the failing Skill contract test**

```python
# tests/test_skill_contract.py
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "open-tax-check"


class SkillContractTests(unittest.TestCase):
    def test_skill_declares_required_boundaries(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: open-tax-check", text)
        self.assertIn("needs_mapping_review", text)
        self.assertIn("blocked_data_quality", text)
        self.assertIn("professional review", text.lower())
        self.assertIn("--ai-mode", text)

    def test_skill_wrapper_imports_package_cli(self):
        text = (SKILL / "scripts" / "opentaxcheck.py").read_text(encoding="utf-8")
        self.assertIn("from open_tax_check.cli import main", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the Skill directory is missing**

Run: `python -m unittest tests.test_skill_contract -v`

Expected: FAIL with `FileNotFoundError` for `skills/open-tax-check/SKILL.md`.

- [ ] **Step 3: Copy the verified Skill resources and create the wrapper**

Copy these resources mechanically:

```powershell
$source = "<legacy-skill-dir>"
$target = "skills\open-tax-check"
New-Item -ItemType Directory -Path "$target\agents","$target\scripts","$target\references" -Force | Out-Null
Copy-Item -LiteralPath "$source\SKILL.md" -Destination "$target\SKILL.md"
Copy-Item -LiteralPath "$source\agents\openai.yaml" -Destination "$target\agents\openai.yaml"
Copy-Item -LiteralPath "$source\references\canonical-schema.md" -Destination "$target\references\canonical-schema.md"
Copy-Item -LiteralPath "$source\references\methodology.md" -Destination "$target\references\methodology.md"
Copy-Item -LiteralPath "$source\references\rule-catalog.md" -Destination "$target\references\rule-catalog.md"
```

Change Skill frontmatter `name` to `open-tax-check`, change command examples to `python <skill-dir>/scripts/opentaxcheck.py`, and change AI modes to `off`, `assist`, and `deep`. Preserve every non-negotiable boundary.

```python
# skills/open-tax-check/scripts/opentaxcheck.py
#!/usr/bin/env python3
"""Portable Agent Skill entry point for the installed OpenTaxCheck package."""

from open_tax_check.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

Set `skills/open-tax-check/agents/openai.yaml` to:

```yaml
interface:
  display_name: "OpenTaxCheck｜开源税务健康检查"
  short_description: "用确定性规则、AI税务助理和专业复核执行企业税务健康检查"
  default_prompt: "Use $open-tax-check to run a resumable Chinese enterprise tax health check and stop at every mapping, data-quality, cloud-authorization, and professional-review gate."
```

- [ ] **Step 4: Run the Skill contract and full tests**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the Skill adapter**

```powershell
git add skills/open-tax-check tests/test_skill_contract.py
git commit -m "feat: add portable OpenTaxCheck agent skill"
```

---

### Task 6: Add Public Documentation, License, and Community Controls

**Files:**
- Modify: `README.md`
- Create: `LICENSE`
- Create: `DISCLAIMER.md`
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `SECURITY.md`
- Create: `.github/ISSUE_TEMPLATE/bug.yml`
- Create: `.github/ISSUE_TEMPLATE/rule-proposal.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/pull_request_template.md`
- Test: `tests/test_public_repo_contract.py`

**Interfaces:**
- Consumes: approved design specification and public project identity.
- Produces: a repository that explains installation, boundaries, safe participation, rule contribution, and private security reporting.

- [ ] **Step 1: Write the failing public-repository contract test**

```python
# tests/test_public_repo_contract.py
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicRepositoryContractTests(unittest.TestCase):
    def test_required_public_files_exist(self):
        for name in (
            "README.md",
            "LICENSE",
            "DISCLAIMER.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
        ):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_readme_contains_contact_and_data_warning(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("PosterNote", text)
        self.assertIn("GitHub", text)
        self.assertIn("请勿", text)
        self.assertIn("客户", text)

    def test_rule_form_requires_authority_and_test_case(self):
        text = (ROOT / ".github/ISSUE_TEMPLATE/rule-proposal.yml").read_text(encoding="utf-8")
        self.assertIn("官方法规依据", text)
        self.assertIn("测试案例", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the contract test and verify the public files are missing**

Run: `python -m unittest tests.test_public_repo_contract -v`

Expected: failures naming each missing public file.

- [ ] **Step 3: Create the public files with approved content**

`README.md` must contain these sections in this order:

```markdown
# OpenTaxCheck｜开源税务健康检查

OpenTaxCheck combines a deterministic Python tax-rule engine, an AI tax project assistant, and mandatory professional review for Chinese enterprise tax health checks.

## Current release scope
## How it works
## Quick start
## Run the fictional example
## Outputs
## AI modes
## Privacy and cloud authorization
## Rule coverage and limitations
## Contributing tax rules
## Community and tax-dispute collaboration
## Disclaimer
## License
```

The community section must state: `微信：PosterNote；添加时请备注 GitHub。请勿通过 GitHub Issue、Discussion 或微信发送未经脱敏的客户资料。`

`DISCLAIMER.md` must state that output is tax-risk screening and consulting material, not audit, assurance, legal opinion, filing, or a tax authority determination; conclusions depend on supplied documents, applicable periods, assumptions, data quality, and professional review.

`CONTRIBUTING.md` must require tax, period, taxpayer type, trigger, exclusions, official authority, formula, evidence, false-positive analysis, fictional test input, expected output, and professional-review boundary for every rule proposal.

Obtain the unmodified Apache-2.0 text using `gh api licenses/apache-2.0 --jq .body`, inspect it, and add it to `LICENSE` with `apply_patch`. Do not add custom restrictions to the license text; put trademark, professional-use, and data warnings in `DISCLAIMER.md`.

Use the Contributor Covenant 2.1 text for `CODE_OF_CONDUCT.md`, with enforcement contact `微信 PosterNote，备注 Code of Conduct`. Do not ask reporters to disclose client facts when reporting community conduct.

`SECURITY.md` must instruct users not to open public issues for vulnerabilities, leaked credentials, or client data; use GitHub private vulnerability reporting.

Issue forms must use required checkboxes confirming no real client data and must separate bug reports from tax-rule proposals.

- [ ] **Step 4: Run repository contracts and scan for client identifiers or credentials**

Run:

```powershell
python -m unittest discover -s tests -v
rg -n --hidden --glob '!docs/superpowers/**' "f[n]_sk_|FN_API_KEY=|api[_-]?key\s*[:=]\s*['\"]|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY" .
```

Expected: all tests pass; the scan returns no matches outside design documentation. If documentation contains historical filenames, remove the filenames rather than allowlisting them.

- [ ] **Step 5: Commit documentation and community controls**

```powershell
git add README.md LICENSE DISCLAIMER.md CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md .github tests/test_public_repo_contract.py
git commit -m "docs: add open-source community and safety controls"
```

---

### Task 7: Add a Fully Fictional End-to-End Example

**Files:**
- Create: `examples/fictional-company/input/journal.csv`
- Create: `examples/fictional-company/README.md`
- Create: `scripts/run_fictional_example.py`
- Create: `tests/test_fictional_example.py`

**Interfaces:**
- Consumes: installed package and fictional balanced journal rows.
- Produces: reproducible example case with one business-entertainment calculation and one semantic expense candidate.

- [ ] **Step 1: Write the failing example test**

```python
# tests/test_fictional_example.py
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FictionalExampleTests(unittest.TestCase):
    def test_example_generates_expected_findings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_fictional_example.py"), "--output", temp_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            findings = json.loads(
                (Path(temp_dir) / "output/findings.json").read_text(encoding="utf-8")
            )
            by_rule = {item["rule_id"]: item for item in findings}
            self.assertEqual(by_rule["CIT-BE-001"]["tax_adjustment"], "15000.00")
            self.assertIn("SEM-EXP-001", by_rule)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the example runner is missing**

Run: `python -m unittest tests.test_fictional_example -v`

Expected: FAIL because `scripts/run_fictional_example.py` does not exist.

- [ ] **Step 3: Add the fictional journal and runner**

```csv
# examples/fictional-company/input/journal.csv
凭证号,记账日期,科目编码,科目名称,业务说明,借方发生额,贷方发生额
记-001,2025-01-02,1002,银行存款,确认销售收入,1000000,0
记-001,2025-01-02,6001,主营业务收入,确认销售收入,0,1000000
记-002,2025-03-08,6602,管理费用-业务招待费,客户接待,20000,0
记-002,2025-03-08,1002,银行存款,客户接待,0,20000
记-003,2025-09-18,6602,管理费用-办公费,购买中秋礼品及高档茶叶,5000,0
记-003,2025-09-18,1002,银行存款,购买中秋礼品及高档茶叶,0,5000
```

```python
# scripts/run_fictional_example.py
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from open_tax_check.case import init_case
from open_tax_check.pipeline import run_case


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "fictional-company" / "input" / "journal.csv"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    init_case(args.output, "开源示例科技有限公司")
    shutil.copy2(SOURCE, args.output / "input" / "journal.csv")
    code, result = run_case(args.output, "off")
    print(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
```

`examples/fictional-company/README.md` must explicitly state that the company, transactions, vouchers, amounts, people, and conclusions are fictional and licensed with the repository.

- [ ] **Step 4: Run the fictional example and all tests**

Run:

```powershell
python -m unittest discover -s tests -v
python scripts/run_fictional_example.py --output work\fictional-demo
```

Expected: tests pass; the demo state is `needs_professional_review`; `output/findings.json` and `output/report.md` exist.

- [ ] **Step 5: Commit the fictional example**

```powershell
git add examples scripts/run_fictional_example.py tests/test_fictional_example.py
git commit -m "test: add fictional end-to-end tax case"
```

---

### Task 8: Add CI, Build, and Portable Skill Validation

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/validate_release.py`
- Create: `tests/test_release_validation.py`

**Interfaces:**
- Consumes: repository checkout on Windows, macOS, or Linux with Python 3.11-3.13.
- Produces: tested wheel and source distribution, validated Skill package, secret/client-name scan, and clean fictional demo.

- [ ] **Step 1: Write the failing release-validator test**

```python
# tests/test_release_validation.py
import subprocess
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the validator is missing**

Run: `python -m unittest tests.test_release_validation -v`

Expected: FAIL because `scripts/validate_release.py` does not exist.

- [ ] **Step 3: Implement deterministic release validation**

```python
# scripts/validate_release.py
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "LICENSE",
    "DISCLAIMER.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "pyproject.toml",
    "skills/open-tax-check/SKILL.md",
    "skills/open-tax-check/agents/openai.yaml",
)
FORBIDDEN = re.compile(
    ("f" + "n_sk_")
    + r"|api[_-]?key\s*[:=]\s*['\"][^'\"]+"
    + r"|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"
)


def main() -> int:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        print("missing required files:", ", ".join(missing))
        return 1
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix in {".png", ".jpg", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        match = FORBIDDEN.search(text)
        if match:
            print(f"forbidden public content in {path.relative_to(ROOT)}: {match.group(0)}")
            return 1
    print("release validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -m pip install --upgrade pip build
      - run: python -m pip install -e .
      - run: python -m unittest discover -s tests -v
      - run: python scripts/validate_release.py
      - run: python scripts/run_fictional_example.py --output work/ci-demo
      - run: python -m build
```

- [ ] **Step 4: Run release validation, tests, and local builds**

Run:

```powershell
python scripts/validate_release.py
python -m unittest discover -s tests -v
python -m pip install build
python -m build
python -m pip install --force-reinstall dist\open_tax_check-0.1.0-py3-none-any.whl
opentaxcheck --help
```

Expected: validation passes, all tests pass, wheel and source archive are created, installation succeeds, and CLI help displays `OpenTaxCheck`.

- [ ] **Step 5: Commit CI and release validation**

```powershell
git add .github/workflows/ci.yml scripts/validate_release.py tests/test_release_validation.py
git commit -m "ci: validate OpenTaxCheck across supported platforms"
```

---

### Task 9: Verify the Phase and Prepare the GitHub Publish Boundary

**Files:**
- Modify: `README.md`
- Create: `docs/roadmap.md`
- Create: `CHANGELOG.md`

**Interfaces:**
- Consumes: completed Tasks 1-8.
- Produces: a clean local `main` branch ready for explicit authorization to create and push `MasterBenC/open-tax-check`.

- [ ] **Step 1: Add a phase-completion contract to the README test**

Append to `PublicRepositoryContractTests`:

```python
    def test_readme_links_scope_and_roadmap(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("CSV", text)
        self.assertIn("docs/roadmap.md", text)
        self.assertIn("尚未支持", text)
```

- [ ] **Step 2: Run the test and verify the roadmap link is missing**

Run: `python -m unittest tests.test_public_repo_contract -v`

Expected: FAIL because `README.md` does not yet link `docs/roadmap.md` or disclose the current unsupported scope.

- [ ] **Step 3: Add the roadmap and changelog**

`docs/roadmap.md` must list these ordered milestones with explicit current status:

```markdown
# Roadmap

1. Repository foundation and deterministic CSV core - complete
2. Excel/PDF/image intake and OCR adapters - planned
3. Versioned 2024-2026 core tax-rule packs - planned
4. AI assist/deep orchestration and evidence validation - planned
5. Excel/DOCX/PDF/evidence-pack professional deliverables - planned
6. Fengniao adapter, privacy hardening, fictional full case, and public v1 release - planned
```

`CHANGELOG.md` must use Keep a Changelog headings and record version `0.1.0` as an unreleased repository-foundation release until a GitHub tag is explicitly authorized.

Update `README.md` so that it truthfully describes the current CSV-only phase, links `docs/roadmap.md`, and uses the phrase `尚未支持` for Excel, OCR, DOCX, PDF, expanded rules, and cloud adapters.

- [ ] **Step 4: Run fresh final verification**

Run:

```powershell
python scripts/validate_release.py
python -m unittest discover -s tests -v
python -m build
git status --short --branch
git log --oneline --decorate -10
```

Expected: validation and tests pass; build exits 0; the only uncommitted files are the roadmap, changelog, and README/test updates prepared in this task.

- [ ] **Step 5: Commit phase completion without publishing**

```powershell
git add README.md docs/roadmap.md CHANGELOG.md tests/test_public_repo_contract.py
git commit -m "docs: publish deterministic core roadmap"
git status --short --branch
```

Expected: clean local `main`; no remote is configured and no network write has occurred.

Do not run `gh repo create`, `git remote add`, `git push`, create a release, or enable GitHub features until the user explicitly authorizes public publication.
