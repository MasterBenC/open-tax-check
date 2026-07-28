from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL_PUBLIC_LIMIT_BYTES = 250 * 1024
PUBLIC_RELEASE_LIMIT_BYTES = 5 * 1024 * 1024
DISTRIBUTION_FILE_LIMIT_BYTES = 1 * 1024 * 1024
REQUIRED_WHEEL_MEMBER = (
    "open_tax_check/tax_packs/vat-cn-2023-2026.prototype.json"
)
REQUIRED_SDIST_MEMBER = (
    "open_tax_check-0.1.0/src/open_tax_check/tax_packs/"
    "vat-cn-2023-2026.prototype.json"
)
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
    ".bin",
    ".ckpt",
    ".gguf",
    ".h5",
    ".hdf5",
    ".keras",
    ".model",
    ".onnx",
    ".ot",
    ".pt",
    ".pth",
    ".safetensors",
    ".tflite",
}
ALLOWED_BINARY_FIXTURES = {
    "tests/fixtures/fictional-journal.xls": (
        "5a9bfece99db9cbae5442add9e206973a20d3226534664d747a205433f546195",
        5632,
    ),
}


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


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


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def safe_public_candidate(
    root: Path,
    relative_path: Path,
) -> bool:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return False
    try:
        root_metadata = os.lstat(root)
    except OSError:
        return False
    if _is_link_or_reparse(root_metadata) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        return False
    parent = root
    for part in relative_path.parts[:-1]:
        parent /= part
        try:
            metadata = os.lstat(parent)
        except OSError:
            return False
        if _is_link_or_reparse(metadata) or not stat.S_ISDIR(
            metadata.st_mode
        ):
            return False
    path = root / relative_path
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not _is_link_or_reparse(
        metadata
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


def public_content_size(root: Path, paths: tuple[Path, ...]) -> int | None:
    total = 0
    for relative_path in paths:
        if not safe_public_candidate(root, relative_path):
            return None
        try:
            metadata = os.lstat(root / relative_path)
        except OSError:
            return None
        total += metadata.st_size
    return total


def skill_public_content_size(
    root: Path,
    paths: tuple[Path, ...],
) -> int | None:
    skill_root = Path("skills/open-tax-check")
    skill_paths = tuple(
        relative_path
        for relative_path in paths
        if relative_path == skill_root or skill_root in relative_path.parents
    )
    return public_content_size(root, skill_paths)


def distribution_artifacts(root: Path) -> tuple[Path, ...]:
    distribution = root / "dist"
    if not distribution.is_dir():
        return ()
    return tuple(
        sorted(
            (
                path
                for path in distribution.iterdir()
                if path.is_file()
                and (
                    path.name.casefold().endswith(".whl")
                    or path.name.casefold().endswith(".tar.gz")
                )
            ),
            key=lambda path: path.name.casefold(),
        )
    )


def distribution_member_names(artifact: Path) -> set[str] | None:
    try:
        if artifact.name.casefold().endswith(".whl"):
            with zipfile.ZipFile(artifact) as archive:
                return set(archive.namelist())
        with tarfile.open(artifact, mode="r:gz") as archive:
            return set(archive.getnames())
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return None


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
    _configure_utf8_output()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-distributions",
        action="store_true",
        help="require exactly one wheel and one source distribution",
    )
    args = parser.parse_args()

    candidates = public_paths(ROOT)
    if candidates is None:
        print("unable to enumerate public release content")
        return 1
    for relative_path in candidates:
        if safe_public_candidate(ROOT, relative_path):
            continue
        if relative_path.suffix.lower() in FORBIDDEN_BINARY_SUFFIXES:
            print(
                "forbidden public binary content in "
                f"{relative_path}: binary_public_content"
            )
        else:
            print(
                "unsafe public release path: "
                f"{relative_path} (unsafe_public_path)"
            )
        return 1
    candidate_set = set(candidates)
    missing = [name for name in REQUIRED if Path(name) not in candidate_set]
    if missing:
        print("missing required files:", ", ".join(missing))
        return 1
    skill_size = skill_public_content_size(ROOT, candidates)
    public_size = public_content_size(ROOT, candidates)
    if skill_size is None or public_size is None:
        print("unable to measure public release content")
        return 1
    if skill_size >= SKILL_PUBLIC_LIMIT_BYTES:
        print(
            f"Skill 公开文件超过 {SKILL_PUBLIC_LIMIT_BYTES} 字节："
            f"实际 {skill_size} 字节"
        )
        return 1
    if public_size >= PUBLIC_RELEASE_LIMIT_BYTES:
        print(
            f"Git 公开发布内容超过 {PUBLIC_RELEASE_LIMIT_BYTES} 字节："
            f"实际 {public_size} 字节"
        )
        return 1
    artifacts = distribution_artifacts(ROOT)
    if args.require_distributions:
        wheels = tuple(
            artifact
            for artifact in artifacts
            if artifact.name.casefold().endswith(".whl")
        )
        sdists = tuple(
            artifact
            for artifact in artifacts
            if artifact.name.casefold().endswith(".tar.gz")
        )
        if len(wheels) != 1 or len(sdists) != 1:
            print(
                "invalid distribution artifacts: expected exactly one wheel "
                f"and one sdist (found wheels={len(wheels)}, "
                f"sdists={len(sdists)})"
            )
            return 1
        for artifact, required_member in (
            (wheels[0], REQUIRED_WHEEL_MEMBER),
            (sdists[0], REQUIRED_SDIST_MEMBER),
        ):
            member_names = distribution_member_names(artifact)
            if member_names is None:
                print(f"unable to inspect distribution artifact: {artifact.name}")
                return 1
            if required_member not in member_names:
                print(f"missing required package data: {required_member}")
                return 1
    measured_artifacts = []
    for artifact in artifacts:
        try:
            size = artifact.stat().st_size
        except OSError:
            print(f"unable to measure distribution artifact: {artifact.name}")
            return 1
        if size >= DISTRIBUTION_FILE_LIMIT_BYTES:
            print(
                f"构建产物 {artifact.name} 超过 "
                f"{DISTRIBUTION_FILE_LIMIT_BYTES} 字节：实际 {size} 字节"
            )
            return 1
        measured_artifacts.append((artifact.name, size))
    for relative_path in candidates:
        path = ROOT / relative_path
        if path.suffix.lower() in FORBIDDEN_BINARY_SUFFIXES:
            if allowed_binary_fixture(relative_path, path):
                continue
            print(
                "forbidden public binary content in "
                f"{relative_path}: binary_public_content"
            )
            return 1
        if not path.is_file():
            continue
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
    print(
        f"Skill 公开文件：{skill_size} 字节"
        f"（上限 {SKILL_PUBLIC_LIMIT_BYTES} 字节）"
    )
    print(
        f"Git 公开发布内容：{public_size} 字节"
        f"（上限 {PUBLIC_RELEASE_LIMIT_BYTES} 字节）"
    )
    for name, size in measured_artifacts:
        print(
            f"构建产物 {name}：{size} 字节"
            f"（上限 {DISTRIBUTION_FILE_LIMIT_BYTES} 字节）"
        )
    print("release validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
