from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.integrations.exact_file_write import require_expected_revision
from oh_goon.runtime.exact_file_store import ExactFileStore
from oh_goon.runtime.league_lineups import LeagueLineupFileStore
from oh_goon.runtime.shared_plans import SharedPlanFileStore


def test_local_yaml_stores_share_one_exact_file_store_owner() -> None:
    assert issubclass(SharedPlanFileStore, ExactFileStore)
    assert issubclass(LeagueLineupFileStore, ExactFileStore)


def test_revision_proof_accepts_only_unfenced_create_or_exact_match() -> None:
    def changed(current, expected):
        return RuntimeError(f"changed:{current}:{expected}")

    def removed():
        return RuntimeError("removed")

    require_expected_revision(None, None, changed_error=changed, removed_error=removed)
    require_expected_revision("a" * 64, "a" * 64, changed_error=changed, removed_error=removed)

    with pytest.raises(RuntimeError, match="changed"):
        require_expected_revision("a" * 64, None, changed_error=changed, removed_error=removed)
    with pytest.raises(RuntimeError, match="changed"):
        require_expected_revision(
            "a" * 64,
            "b" * 64,
            changed_error=changed,
            removed_error=removed,
        )
    with pytest.raises(RuntimeError, match="removed"):
        require_expected_revision(
            None,
            "a" * 64,
            changed_error=changed,
            removed_error=removed,
        )


def test_shared_plan_fenced_read_uses_store_revision_authority(tmp_path: Path) -> None:
    store = SharedPlanFileStore(tmp_path / "plans")
    original = store.write("python-quality", "goons: [python/test]\n")
    store.write(
        "python-quality",
        "goons: [python/ruff]\n",
        expected_revision=original.sha256,
    )

    with pytest.raises(Exception, match="changed since it was read"):
        store.read("python-quality", expected_revision=original.sha256)


def test_authored_tree_completion_follows_native_directory_durability(tmp_path, monkeypatch):
    import os
    import stat

    from oh_goon.integrations.exact_file_write import publish_new_text_tree

    output = tmp_path / "workflow"
    events = []
    fsync = os.fsync
    link = os.link

    def directory_identity(path):
        status = path.stat()
        return status.st_dev, status.st_ino

    def sync(descriptor):
        status = os.fstat(descriptor)
        if stat.S_ISDIR(status.st_mode):
            events.append(("sync", (status.st_dev, status.st_ino)))
        return fsync(descriptor)

    def publish(source, target, **kwargs):
        if not any(kind == "link" for kind, _ in events):
            # Exclusive creation of the output itself must already be durable.
            assert ("sync", directory_identity(tmp_path)) in events
        if target == "run.yml":
            companion = max(index for index, (kind, _) in enumerate(events) if kind == "link")
            pending = events[companion + 1 :]
            directories = [
                output / "goons/agents/review-repair",
                output / "goons/agents",
                output / "goons",
                output,
                tmp_path,
            ]
            positions = [
                max(
                    index
                    for index, item in enumerate(pending)
                    if item == ("sync", directory_identity(directory))
                )
                for directory in directories
            ]
            assert positions == sorted(positions)
        events.append(("link", target))
        return link(source, target, **kwargs)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "link", publish)
    publish_new_text_tree(
        output,
        {
            "goons/agents/review-repair/goon.yml": "version: 1\n",
            "plan.yml": "version: 1\n",
            "run.yml": "version: 1\n",
        },
        commit_last="run.yml",
    )
    assert (output / "run.yml").is_file()


def test_companion_directory_durability_failure_prevents_completion(tmp_path, monkeypatch):
    import errno
    import os

    from oh_goon.integrations.exact_file_write import publish_new_text_tree

    output = tmp_path / "workflow"
    linked = os.link
    fsync = os.fsync
    companion_published = False

    def publish(source, target, **kwargs):
        nonlocal companion_published
        result = linked(source, target, **kwargs)
        companion_published = True
        return result

    def sync(descriptor):
        if companion_published:
            actual = os.fstat(descriptor)
            ancestor = (output / "goons").stat()
            if (actual.st_dev, actual.st_ino) == (ancestor.st_dev, ancestor.st_ino):
                raise OSError(errno.EIO, "directory durability failed")
        return fsync(descriptor)

    monkeypatch.setattr(os, "link", publish)
    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(OSError, match="directory durability failed"):
        publish_new_text_tree(
            output,
            {"goons/agents/review/goon.yml": "version: 1\n", "run.yml": "version: 1\n"},
            commit_last="run.yml",
        )
    assert (output / "goons/agents/review/goon.yml").is_file()
    assert not (output / "run.yml").exists()
