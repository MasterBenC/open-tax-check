# Task 1 Report: Common Template Parsing

Status: complete

Implemented `template_documents.py` with frozen slot dataclasses, deterministic header aliases, stable location-only diagnostics, blank-row skipping, formula and amount validation, canonical two-decimal money, physical sheet anchors, explicit financial totals, declared tax value types, and validation through the existing structured-document contracts. Exported the new API from `open_tax_check` and included the required parser tests.

Commit:

- `feat: parse common financial tax templates` (created after verification)

Verification evidence:

- Command: `$env:PYTHONPATH=(Resolve-Path src).Path; & 'C:\Users\benhu\Documents\Codex\2026-07-13\tax\open-tax-check\.venv\Scripts\python.exe' -m pytest tests/test_template_documents.py -q`
  Output: `5 passed in 0.19s`
- Command: `$env:PYTHONPATH=(Resolve-Path src).Path; & 'C:\Users\benhu\Documents\Codex\2026-07-13\tax\open-tax-check\.venv\Scripts\python.exe' -m pytest tests/test_template_documents.py tests/test_structured_documents.py -q`
  Output: `35 passed in 0.65s`
- Command: `$env:PYTHONPATH=(Resolve-Path src).Path; & 'C:\Users\benhu\Documents\Codex\2026-07-13\tax\open-tax-check\.venv\Scripts\python.exe' -m pytest tests/test_package_contract.py tests/test_structured_document_cli.py -q`
  Output: `15 passed in 3.15s`
- Command: `$env:PYTHONPATH=(Resolve-Path src).Path; & 'C:\Users\benhu\Documents\Codex\2026-07-13\tax\open-tax-check\.venv\Scripts\python.exe' -m compileall -q src/open_tax_check/template_documents.py`
  Output: exit code 0
- Command: `git diff --check`
  Output: exit code 0

Concerns:

- The repository-wide `pytest -q` run exceeded the 120-second command budget and terminated with pytest's Windows stdout flush `OSError: [Errno 22] Invalid argument`; no parser or structured-document failure was emitted before timeout, but the full suite remains unverified in this environment.
- Header diagnostics intentionally suppress a missing-header issue when duplicate or unknown headers are already present, matching the required test contract while keeping the result blocked.
