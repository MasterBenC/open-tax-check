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
API_KEY_NAME = r"(?:[a-z0-9]+[_-])*[a-z0-9]*api[_-]?key"
API_KEY_TOKEN = (
    rf"(?:\"{API_KEY_NAME}\"|'{API_KEY_NAME}'|"
    rf"(?<![a-z0-9_'\"-]){API_KEY_NAME}(?![a-z0-9_'\"-]))"
)
API_KEY_ASSIGNMENT = re.compile(
    API_KEY_TOKEN
    + r"[ \t]*[:=][ \t]*(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|"
    + r"(?:(?:\"\"|'')+)?[^\s'\"#;,|=\r\n]+)",
    re.IGNORECASE,
)
API_KEY_BLOCK_SCALAR = re.compile(
    API_KEY_TOKEN
    + r"[ \t]*:[ \t]*\|[+-]?[ \t]*(?:#[^\r\n]*)?\r?\n"
    + r"[ \t]+(?=[^ \t\r\n])[^\r\n]+",
    re.IGNORECASE,
)
CREDENTIAL_PREFIX = re.compile(("f" + "n_sk_"), re.IGNORECASE)
PRIVATE_KEY = re.compile(
    r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY",
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


def sensitive_category(content: str) -> str | None:
    candidates = []
    for pattern, category in (
        (API_KEY_ASSIGNMENT, "api_key"),
        (API_KEY_BLOCK_SCALAR, "api_key"),
        (CREDENTIAL_PREFIX, "credential_prefix"),
        (PRIVATE_KEY, "private_key"),
    ):
        match = pattern.search(content)
        if match:
            candidates.append((match.start(), category))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


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
        category = sensitive_category(content)
        if category:
            print(f"forbidden public content in {relative_path}: {category}")
            return 1
    print("release validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
