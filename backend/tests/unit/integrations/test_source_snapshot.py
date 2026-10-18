from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.source_snapshot import snapshot_source_directory, source_snapshot_identity


def test_source_snapshot_excludes_generated_operational_state_but_retains_committed_dependency_lock(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "snapshot"
    (source / "backend/.venv").mkdir(parents=True)
    (source / "backend/.venv/generated.txt").write_text("venv", encoding="utf-8")
    (source / "backend/uv.lock").write_text("lock", encoding="utf-8")
    (source / "frontend/node_modules/pkg").mkdir(parents=True)
    (source / "frontend/node_modules/pkg/index.js").write_text("generated", encoding="utf-8")
    (source / ".git/objects").mkdir(parents=True)
    (source / ".git/objects/private").write_text("git-metadata", encoding="utf-8")
    (source / ".oh-goon-data/run-logs").mkdir(parents=True)
    (source / ".oh-goon-data/run-logs/latest.log").write_text("operational", encoding="utf-8")
    (source / "README.md").write_text("source", encoding="utf-8")

    expected = source_snapshot_identity(source)
    observed = snapshot_source_directory(source, destination, label="test source")

    assert observed == expected == source_snapshot_identity(destination)
    assert observed == snapshot_identity(destination)
    assert (destination / "backend/uv.lock").read_text(encoding="utf-8") == "lock"
    assert (destination / "README.md").is_file()
    assert not (destination / "backend/.venv").exists()
    assert not (destination / "frontend/node_modules").exists()
    assert not (destination / ".git").exists()
    assert not (destination / ".oh-goon-data").exists()


def test_filtered_source_identity_is_the_canonical_execution_tree_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "snapshot"
    (source / "backend").mkdir(parents=True)
    (source / "backend/uv.lock").write_text("lock", encoding="utf-8")
    (source / "docs").mkdir()
    (source / "docs/readme.md").write_text("docs", encoding="utf-8")
    (source / ".git/objects").mkdir(parents=True)
    (source / ".git/objects/private").write_text("ignored", encoding="utf-8")
    (source / "README.md").write_text("source", encoding="utf-8")

    bound = snapshot_source_directory(source, destination, label="canonical source")
    assert bound == source_snapshot_identity(destination) == snapshot_identity(destination)
    assert (destination / "backend/uv.lock").read_text(encoding="utf-8") == "lock"

    (source / "backend/uv.lock").write_text("different-committed-lock", encoding="utf-8")
    assert source_snapshot_identity(source) != bound


def test_source_snapshot_rejects_symlink_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    (source / "escape").symlink_to(outside)
    with pytest.raises(
        FleetPlanInvalid, match="escaping symlink|must be relative|escapes repository root"
    ):
        source_snapshot_identity(source)


def test_source_snapshot_allows_contained_relative_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "snapshot"
    (source / "docs").mkdir(parents=True)
    (source / "docs/target.txt").write_text("safe", encoding="utf-8")
    (source / "link.txt").symlink_to("docs/target.txt")
    observed = snapshot_source_directory(source, destination, label="safe source")
    assert observed == source_snapshot_identity(destination)
    assert (destination / "link.txt").is_symlink()
    assert (destination / "link.txt").readlink() == Path("docs/target.txt")


def test_source_snapshot_rejects_symlink_to_generated_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "backend/.venv").mkdir(parents=True)
    (source / "backend/.venv/python").write_text("generated", encoding="utf-8")
    (source / "python-link").symlink_to("backend/.venv/python")
    with pytest.raises(FleetPlanInvalid, match="generated/operational state"):
        source_snapshot_identity(source)
