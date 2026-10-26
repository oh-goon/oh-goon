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


@pytest.mark.parametrize(
    "name", ["coverage", "playwright-report", "playwright-runtime-report", "test-results"]
)
def test_source_snapshot_binds_authored_report_names_and_excludes_frontend_outputs(
    tmp_path: Path, name: str
) -> None:
    source = tmp_path / "source"
    authored = source / "docs" / name / "analysis.md"
    authored.parent.mkdir(parents=True)
    authored.write_text("authored\n", encoding="utf-8")
    generated = source / "frontend" / name / "result.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated\n", encoding="utf-8")
    destination = tmp_path / "snapshot"

    before = snapshot_source_directory(source, destination, label="report source")
    assert (destination / "docs" / name / "analysis.md").read_text() == "authored\n"
    assert not (destination / "frontend" / name).exists()
    generated.write_text("changed output\n", encoding="utf-8")
    assert source_snapshot_identity(source) == before
    authored.write_text("changed source\n", encoding="utf-8")
    assert source_snapshot_identity(source) != before


@pytest.mark.parametrize("live_name", (".env", ".env.local", ".env.production"))
def test_application_snapshot_refuses_live_dotenv_before_reading_private_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_name: str
) -> None:
    from oh_goon.integrations import source_snapshot

    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "README.md").write_text("ordinary application source\n")
    for name in (".env.example", ".env.sample", ".env.template"):
        (source / "nested" / name).write_text("CONFIGURATION=placeholder\n")
    destination = tmp_path / "snapshot"
    baseline = snapshot_source_directory(
        source, destination, label="application", reject_dotenv_state=True
    )
    assert baseline == source_snapshot_identity(source, reject_dotenv_state=True)
    assert (destination / "nested/.env.example").is_file()
    private = source / "nested" / live_name
    private.write_text("PRIVATE_DOTENV_TEST_VALUE=must-not-be-read\n")
    original_open = source_snapshot.os.open

    def refuse_private_open(path, *args, **kwargs):
        assert Path(path) != private, "dotenv restriction must precede opening private bytes"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(source_snapshot.os, "open", refuse_private_open)
    with pytest.raises(FleetPlanInvalid, match="live dotenv state") as refused:
        snapshot_source_directory(
            source,
            tmp_path / "refused",
            label="application",
            reject_dotenv_state=True,
        )
    assert "must-not-be-read" not in str(refused.value)
    assert not (tmp_path / "refused").exists()
    # Generic repository and package snapshots keep their original input semantics.
    monkeypatch.undo()
    assert snapshot_source_directory(source, tmp_path / "generic", label="repository")
    assert (tmp_path / "generic/nested" / live_name).is_file()


@pytest.mark.parametrize("injection", ("before_copy", "source_after_copy", "snapshot_after_copy"))
def test_application_snapshot_refuses_dotenv_races_and_discards_partial_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    from oh_goon.integrations import source_snapshot

    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested/application.txt").write_text("authored\n")
    baseline = snapshot_source_directory(
        source, tmp_path / "baseline", label="application", reject_dotenv_state=True
    )
    assert baseline == source_snapshot_identity(source, reject_dotenv_state=True)
    destination = tmp_path / "raced"
    copy = source_snapshot._copy_source_tree
    private = (destination if injection == "snapshot_after_copy" else source) / "nested/.env"

    def inject_at_copy(root, current, target, *args, **kwargs):
        if current == root and injection == "before_copy":
            private.write_text("PRIVATE_RACE_TEST_VALUE=must-not-be-read\n")
        copy(root, current, target, *args, **kwargs)
        if current == root and injection != "before_copy":
            private.write_text("PRIVATE_RACE_TEST_VALUE=must-not-be-read\n")

    original_open = source_snapshot.os.open

    def refuse_private_open(path, *args, **kwargs):
        assert Path(path) != private, "raced dotenv must be refused before a private read"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(source_snapshot, "_copy_source_tree", inject_at_copy)
    monkeypatch.setattr(source_snapshot.os, "open", refuse_private_open)
    with pytest.raises(FleetPlanInvalid, match="live dotenv state") as refused:
        snapshot_source_directory(
            source, destination, label="application", reject_dotenv_state=True
        )
    assert "must-not-be-read" not in str(refused.value)
    assert not destination.exists()
