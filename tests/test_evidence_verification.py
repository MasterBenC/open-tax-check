import hashlib

import pytest

from open_tax_check.evidence import verify_evidence_file


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
    assert "secret customer content" not in repr(result)


def test_evidence_verification_rejects_escape_and_links(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="unsafe_evidence_path"):
        verify_evidence_file(root, "../outside.pdf", "0" * 64)
