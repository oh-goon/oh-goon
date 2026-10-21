from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.release.package_slim import canonical_source_identity


def test_canonical_source_identity_binds_committed_backend_uv_lock(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    source = tmp_path / "backend/source.txt"
    source.write_text("source\n")
    before = canonical_source_identity(tmp_path)
    (tmp_path / "backend/uv.lock").write_text("version = 1\n")
    after = canonical_source_identity(tmp_path)
    assert after != before


def test_canonical_source_identity_ignores_hashmarks_derived_state(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    source = tmp_path / "backend/source.txt"
    source.write_text("source\n")
    before = canonical_source_identity(tmp_path)

    state = tmp_path / ".hashmarks/codemap.sqlite3"
    state.parent.mkdir()
    state.write_text("derived repository intelligence\n")

    assert canonical_source_identity(tmp_path) == before


def test_canonical_source_identity_changes_for_packageable_source(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    source = tmp_path / "backend/source.txt"
    source.write_text("one\n")
    before = canonical_source_identity(tmp_path)
    source.write_text("two\n")
    assert canonical_source_identity(tmp_path) != before


def test_canonical_source_identity_changes_when_packageable_source_is_renamed(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    source = tmp_path / "backend/source.txt"
    source.write_text("same bytes\n")
    before = canonical_source_identity(tmp_path)

    source.rename(tmp_path / "backend/renamed-source.txt")

    assert canonical_source_identity(tmp_path) != before


def test_canonical_source_identity_ignores_execution_artifacts(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    source = tmp_path / "backend/source.txt"
    source.write_text("authoritative source\n")
    before = canonical_source_identity(tmp_path)

    generated = {
        "backend/__pycache__/source.cpython-313.pyc": b"bytecode",
        "backend/.pytest_cache/v/cache/nodeids": b"[]",
        "backend/.mypy_cache/state.json": b"{}",
        "backend/.ruff_cache/cache": b"ruff",
        "backend/module.pyc": b"bytecode",
        "frontend/node_modules/example/index.js": b"generated",
        "playwright-report/index.html": b"report",
        "test-results/result.txt": b"result",
        "coverage-python.json": b"{}",
    }
    for relative, payload in generated.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    assert canonical_source_identity(tmp_path) == before


def test_hashmarks_looking_unknown_top_level_is_not_silently_transient(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/source.txt").write_text("source\n")
    authored = tmp_path / ".hashmarks-authored/source.py"
    authored.parent.mkdir()
    authored.write_text("print('authored')\n")

    import pytest

    with pytest.raises(
        ValueError, match="unowned top-level entries: .hashmarks-authored"
    ):
        canonical_source_identity(tmp_path)


def test_hashmarks_looking_name_under_owned_source_is_canonical_source(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    source = tmp_path / "backend/source.txt"
    source.write_text("one\n")
    authored = tmp_path / "backend/.hashmarks-authored/source.py"
    authored.parent.mkdir()
    authored.write_text("one\n")
    before = canonical_source_identity(tmp_path)

    authored.write_text("two\n")

    assert canonical_source_identity(tmp_path) != before
