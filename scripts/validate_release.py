from __future__ import annotations

import re
import subprocess
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
    r"(?P<credential_prefix>" + ("f" + "n_sk_") + r")"
    + r"|(?P<api_key>\b(?:[a-z0-9]+[_-])*api[_-]?key\b"
    + r"[ \t]*[:=][ \t]*(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|"
    + r"[^\s'\"#;,|:=]+))"
    + r"|(?P<private_key>BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY)",
    re.IGNORECASE,
)
FORBIDDEN_BINARY_SUFFIXES = {
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
}


def public_paths(root: Path) -> tuple[Path, ...] | None:
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    names = sorted(name for name in proc.stdout.split(b"\0") if name)
    return tuple(Path(name.decode("utf-8", errors="surrogateescape")) for name in names)


def main() -> int:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        print("missing required files:", ", ".join(missing))
        return 1
    candidates = public_paths(ROOT)
    if candidates is None:
        print("unable to enumerate public release content")
        return 1
    for relative_path in candidates:
        path = ROOT / relative_path
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN_BINARY_SUFFIXES:
            print(
                "forbidden public binary content in "
                f"{relative_path}: binary_public_content"
            )
            return 1
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(
                "unable to decode public release content: "
                f"{relative_path} (undecodable_public_content)"
            )
            return 1
        except OSError:
            print(f"unable to read public release content: {relative_path}")
            return 1
        match = FORBIDDEN.search(content)
        if match:
            category = match.lastgroup or "sensitive_content"
            print(f"forbidden public content in {relative_path}: {category}")
            return 1
    print("release validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
