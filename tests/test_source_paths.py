import os
from pathlib import Path

import pytest

from open_tax_check.source_paths import resolve_safe_source


def test_resolve_safe_source_returns_resolved_ordinary_file(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    source = root / "document.pdf"
    source.write_bytes(b"content")

    assert resolve_safe_source(source, root) == source.resolve(strict=True)


@pytest.mark.parametrize("kind", ["outside", "directory", "missing"])
def test_resolve_safe_source_rejects_non_source_paths(tmp_path, kind):
    root = tmp_path / "input"
    root.mkdir()
    if kind == "outside":
        candidate = tmp_path / "outside.pdf"
        candidate.write_bytes(b"content")
    elif kind == "directory":
        candidate = root / "nested"
        candidate.mkdir()
    else:
        candidate = root / "missing.pdf"

    with pytest.raises(ValueError, match="^unsafe_source_path$"):
        resolve_safe_source(candidate, root)


def test_resolve_safe_source_rejects_symlink(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    target = root / "target.pdf"
    target.write_bytes(b"content")
    link = root / "linked.pdf"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="^unsafe_source_path$"):
        resolve_safe_source(link, root)


def test_resolve_safe_source_rejects_linked_root(tmp_path):
    real_root = tmp_path / "real-input"
    real_root.mkdir()
    source = real_root / "document.pdf"
    source.write_bytes(b"content")
    linked_root = tmp_path / "linked-input"
    try:
        os.symlink(real_root, linked_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(ValueError, match="^unsafe_source_path$"):
        resolve_safe_source(linked_root / source.name, linked_root)


def test_resolve_safe_source_rejects_windows_reparse_attribute(tmp_path, monkeypatch):
    root = tmp_path / "input"
    root.mkdir()
    source = root / "document.pdf"
    source.write_bytes(b"content")
    original_lstat = os.lstat

    def reparse_lstat(path):
        result = original_lstat(path)
        if Path(path) != source:
            return result
        values = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
        values["st_file_attributes"] = 0x400
        return type("ReparseStat", (), values)()

    monkeypatch.setattr("open_tax_check.source_paths.os.lstat", reparse_lstat)

    with pytest.raises(ValueError, match="^unsafe_source_path$"):
        resolve_safe_source(source, root)
