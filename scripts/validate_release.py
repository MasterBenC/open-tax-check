from __future__ import annotations

import hashlib
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
API_KEY_BLOCK_HEADER = re.compile(
    r"^(?P<prefix>[ \t]*(?:-[ \t]+)?)"
    + rf"(?P<key>{API_KEY_TOKEN})"
    + r"[ \t]*:[ \t]*\|[+-]?[ \t]*(?:#[^\r\n]*)?$",
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
ALLOWED_BINARY_FIXTURES = {
    "tests/fixtures/fictional-journal.xls": (
        "5a9bfece99db9cbae5442add9e206973a20d3226534664d747a205433f546195",
        5632,
    ),
}


def allowed_binary_fixture(relative_path: Path, path: Path) -> bool:
    expected = ALLOWED_BINARY_FIXTURES.get(relative_path.as_posix())
    if expected is None:
        return False
    expected_hash, expected_size = expected
    try:
        if path.stat().st_size != expected_size:
            return False
        with path.open("rb") as source:
            content = source.read(expected_size + 1)
    except OSError:
        return False
    return (
        len(content) == expected_size
        and hashlib.sha256(content).hexdigest() == expected_hash
    )


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


def _indent_width(prefix: str) -> int:
    return len(prefix.expandtabs(8))


def api_key_block_scalar_position(content: str) -> int | None:
    lines = content.splitlines(keepends=True)
    offset = 0
    for index, line in enumerate(lines):
        text = line.rstrip("\r\n")
        header = API_KEY_BLOCK_HEADER.fullmatch(text)
        if header:
            key_column = _indent_width(text[: header.start("key")])
            for following_line in lines[index + 1 :]:
                following_text = following_line.rstrip("\r\n")
                if not following_text.strip():
                    continue
                leading_length = len(following_text) - len(
                    following_text.lstrip(" \t")
                )
                leading = following_text[:leading_length]
                if _indent_width(leading) > key_column:
                    return offset + header.start("key")
                break
        offset += len(line)
    return None


def sensitive_category(content: str) -> str | None:
    candidates = []
    for pattern, category in (
        (API_KEY_ASSIGNMENT, "api_key"),
        (CREDENTIAL_PREFIX, "credential_prefix"),
        (PRIVATE_KEY, "private_key"),
    ):
        match = pattern.search(content)
        if match:
            candidates.append((match.start(), category))
    block_position = api_key_block_scalar_position(content)
    if block_position is not None:
        candidates.append((block_position, "api_key"))
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
            if allowed_binary_fixture(relative_path, path):
                continue
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
