"""Install the built wheel in an isolated environment and run the full fixture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def module_origin_is_isolated(
    origin: Path, environment: Path, repository: Path
) -> bool:
    """Require an installed package origin, never the repository source tree."""

    parts = {part.casefold() for part in origin.parts}
    return (
        _contained(origin, environment)
        and "site-packages" in parts
        and not _contained(origin, repository / "src")
    )


def _python_path(environment: Path) -> Path:
    return (
        environment / "Scripts/python.exe"
        if os.name == "nt"
        else environment / "bin/python"
    )


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _copy_acceptance_driver(repository: Path, target: Path) -> Path:
    runner = target / "scripts/run_fictional_example.py"
    runner.parent.mkdir(parents=True)
    shutil.copy2(repository / "scripts/run_fictional_example.py", runner)
    example = target / "examples/fictional-company/input"
    example.mkdir(parents=True)
    shutil.copy2(
        repository / "examples/fictional-company/input/journal.csv",
        example / "journal.csv",
    )
    fixtures = target / "tests/fixtures/ocr"
    fixtures.mkdir(parents=True)
    for name in ("fictional-tax-note.png", "fictional-digital.pdf"):
        shutil.copy2(repository / "tests/fixtures/ocr" / name, fixtures / name)
    return runner


def validate_installed_wheel(distribution: Path, repository: Path = ROOT) -> dict[str, object]:
    wheels = tuple(sorted(distribution.glob("*.whl")))
    if len(wheels) != 1:
        raise ValueError("expected_exactly_one_wheel")
    if sys.version_info[:2] not in {(3, 11), (3, 12), (3, 13)}:
        raise ValueError("unsupported_validation_python")
    with tempfile.TemporaryDirectory(prefix="otc-wheel-gate-") as directory:
        root = Path(directory)
        environment = root / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(environment)],
            check=True,
            cwd=root,
            env=_clean_environment(),
        )
        python = _python_path(environment)
        subprocess.run(
            [str(python), "-m", "pip", "install", f"{wheels[0].resolve()}[excel,report]"],
            check=True,
            cwd=root,
            env=_clean_environment(),
        )
        origin_run = subprocess.run(
            [
                str(python),
                "-c",
                "import json, open_tax_check; print(json.dumps({'origin': open_tax_check.__file__}))",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=root,
            env=_clean_environment(),
        )
        origin = Path(json.loads(origin_run.stdout)["origin"])
        if not module_origin_is_isolated(origin, environment, repository):
            raise ValueError("package_not_loaded_from_isolated_site_packages")
        browser_override = os.environ.get("OPENTAXCHECK_CHROMIUM_EXECUTABLE")
        if not browser_override:
            subprocess.run(
                [str(python), "-m", "playwright", "install", "chromium"],
                check=True,
                cwd=root,
                env=_clean_environment(),
            )
        asset_run = subprocess.run(
            [
                str(python), "-c",
                "from importlib import resources; "
                "r=resources.files('open_tax_check.report_assets'); "
                "assert r.joinpath('report.css').read_text('utf-8'); "
                "assert r.joinpath('fonts/OFL.txt').read_text('utf-8'); "
                "assert r.joinpath('fonts/NotoSansSC-VF.ttf').read_bytes()[:4] in "
                "(b'\\x00\\x01\\x00\\x00',b'OTTO',b'ttcf',b'true')",
            ],
            check=True,
            capture_output=True,
            cwd=root,
            env=_clean_environment(),
        )

        acceptance = root / "acceptance"
        runner = _copy_acceptance_driver(repository, acceptance)
        execution = root / "execution"
        execution.mkdir()
        case = root / "case"
        completed = subprocess.run(
            [str(python), str(runner), "--output", str(case), "--complete"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=execution,
            env=_clean_environment(),
        )
        result = json.loads(completed.stdout)
        if result.get("status") != "completed_with_limitations":
            raise ValueError("complete_fixture_did_not_close")
        pointer = json.loads((case / "work/delivery.json").read_text(encoding="utf-8"))
        revision = case / pointer["revision_path"]
        if not (revision / "税务健康检查报告.pdf").is_file():
            raise ValueError("installed_wheel_pdf_missing")
        if any(revision.glob("*.docx")):
            raise ValueError("installed_wheel_docx_present")
        return {
            "status": "verified",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "module_origin": str(origin),
            "case_status": result["status"],
            "report_schema_version": pointer["report_schema_version"],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        result = validate_installed_wheel(args.dist)
    except Exception:
        print(json.dumps({"status": "failed", "error": "installed_wheel_validation_failed"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
