import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from open_tax_check import evidence
from open_tax_check.evidence import verify_evidence_file


def test_evidence_verification_returns_the_resolved_path_only_after_success(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    source = root / "contract.pdf"
    source.write_bytes(b"verified")
    digest = hashlib.sha256(b"verified").hexdigest()

    result = verify_evidence_file(root, "contract.pdf", digest)

    assert result.status == "verified"
    assert result.verified_path == source.resolve()


def test_evidence_verification_rejects_drive_relative_paths_and_linked_roots(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    with pytest.raises(ValueError, match="^unsafe_evidence_path$"):
        verify_evidence_file(root, "C:outside.pdf", "0" * 64)

    linked_root = tmp_path / "linked-evidence"
    try:
        os.symlink(root, linked_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(ValueError, match="^unsafe_evidence_path$"):
        verify_evidence_file(linked_root, "inside.pdf", "0" * 64)


def test_evidence_verification_rejects_windows_rooted_path_without_hanging(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    windows_rooted_path = r"\outside.pdf"
    program = f"""
from pathlib import Path
from open_tax_check.evidence import verify_evidence_file

try:
    verify_evidence_file(Path({str(root)!r}), {windows_rooted_path!r}, "0" * 64)
except ValueError as error:
    raise SystemExit(0 if str(error) == "unsafe_evidence_path" else 1)
raise SystemExit(1)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", program],
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("rooted Windows path validation hung")

    assert result.returncode == 0


def test_windows_drive_relative_path_is_rejected_independent_of_native_path_rules():
    assert evidence._is_unsafe_evidence_path("C:outside.pdf")


def test_evidence_verification_rejects_linked_root_ancestor(tmp_path, monkeypatch):
    linked_ancestor = tmp_path / "linked-ancestor"
    root = linked_ancestor / "evidence"
    root.mkdir(parents=True)
    source = root / "contract.pdf"
    source.write_bytes(b"verified")
    digest = hashlib.sha256(b"verified").hexdigest()
    monkeypatch.setattr(
        evidence,
        "_is_reparse_or_link",
        lambda path: path == linked_ancestor,
    )

    with pytest.raises(ValueError, match="^unsafe_evidence_path$"):
        verify_evidence_file(root, "contract.pdf", digest)


def test_evidence_verification_binds_source_hash_and_size(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    path = root / "contract.pdf"
    path.write_bytes(b"fictional evidence")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    result = verify_evidence_file(root, "contract.pdf", digest)

    assert result.status == "verified"
    assert result.sha256 == digest
    assert result.size_bytes == len(b"fictional evidence")


def test_evidence_verification_rejects_hash_mismatch_without_leaking_content(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "contract.pdf").write_bytes(b"secret customer content")

    result = verify_evidence_file(root, "contract.pdf", "0" * 64)

    assert result.status == "failed"
    assert result.error_code == "evidence_hash_mismatch"
    assert result.verified_path is None
    assert "secret customer content" not in repr(result)


def test_evidence_verification_rejects_escape_and_links(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="unsafe_evidence_path"):
        verify_evidence_file(root, "../outside.pdf", "0" * 64)
