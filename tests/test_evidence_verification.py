import hashlib
import os

import pytest

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
