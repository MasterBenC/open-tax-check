"""Shared source-path validation for case-controlled input files."""

from __future__ import annotations

import os
from pathlib import Path
import stat


_ERROR_CODE = "unsafe_source_path"


def _unsafe() -> ValueError:
    return ValueError(_ERROR_CODE)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_link_or_reparse(path: Path) -> os.stat_result:
    metadata = os.lstat(path)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    ):
        raise _unsafe()
    return metadata


def resolve_safe_source(path: Path, root: Path) -> Path:
    """Return a resolved ordinary file contained by an ordinary input root."""

    try:
        lexical_root = _absolute_lexical(root)
        lexical_path = _absolute_lexical(path)
        if lexical_path == lexical_root:
            raise _unsafe()
        lexical_path.relative_to(lexical_root)

        current = lexical_path
        source_metadata: os.stat_result | None = None
        while True:
            metadata = _reject_link_or_reparse(current)
            if current == lexical_path:
                source_metadata = metadata
            if current == lexical_root:
                break
            current = current.parent

        if source_metadata is None or not stat.S_ISREG(source_metadata.st_mode):
            raise _unsafe()
        root_metadata = _reject_link_or_reparse(lexical_root)
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise _unsafe()

        resolved_root = lexical_root.resolve(strict=True)
        resolved_path = lexical_path.resolve(strict=True)
        if resolved_path == resolved_root:
            raise _unsafe()
        resolved_path.relative_to(resolved_root)
        return resolved_path
    except (OSError, RuntimeError, ValueError):
        raise _unsafe() from None
