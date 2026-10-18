from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture.common import clear_python_ast_cache, python_ast


def test_python_ast_reuses_unchanged_file(tmp_path: Path) -> None:
    clear_python_ast_cache()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    assert python_ast(source) is python_ast(source)


def test_python_ast_invalidates_rewritten_fixture(tmp_path: Path) -> None:
    clear_python_ast_cache()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    first = python_ast(source)
    source.write_text("value = 22\n", encoding="utf-8")
    second = python_ast(source)
    assert first is not second
