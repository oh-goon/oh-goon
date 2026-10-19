from __future__ import annotations

import ast
import os
from typing import Any

import pytest
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    python_ast,
    python_source_snapshot,
)


def test_each_invocation_observes_equal_size_timestamp_preserving_edits(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.py"
    path.write_text("VALUE = 1\n")
    metadata = path.stat()
    with python_source_snapshot():
        assert ast.unparse(python_ast(path)) == "VALUE = 1"
    path.write_text("VALUE = 2\n")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    with python_source_snapshot():
        assert ast.unparse(python_ast(path)) == "VALUE = 2"
    path.write_text("VALUE = 3\n")
    assert ast.unparse(python_ast(path)) == "VALUE = 3"


def test_checks_share_one_source_read_within_the_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "source.py"
    path.write_text("VALUE = 1\n")
    native = Path.read_text
    reads = []

    def observe_read(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self)
        return native(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observe_read)
    with python_source_snapshot():
        first = python_ast(path)
        with python_source_snapshot():
            assert python_ast(path) is first
    assert reads == [path]
