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


@pytest.mark.parametrize("collision", ["empty-directory", "symlink", "file"])
def test_atomic_directory_publication_never_replaces_existing_destinations(tmp_path, collision):
    from oh_goon.integrations.exact_file_write import publish_prepared_directory

    staging = tmp_path / ".oh-goon-init-owned"
    staging.mkdir(mode=0o700)
    (staging / "run.yml").write_text("complete selection")
    (staging / "goon.lock").write_text("matching lock")
    output = tmp_path / "workflow"
    outside = tmp_path / "outside"
    outside.mkdir()
    if collision == "empty-directory":
        output.mkdir()
    elif collision == "symlink":
        output.symlink_to(outside, target_is_directory=True)
    else:
        output.write_text("user data")
    with pytest.raises(FileExistsError):
        publish_prepared_directory(staging, output, exclusive=True)
    assert (staging / "goon.lock").read_text() == "matching lock"
    if collision == "empty-directory":
        assert list(output.iterdir()) == []
    elif collision == "symlink":
        assert output.is_symlink() and list(outside.iterdir()) == []
    else:
        assert output.read_text() == "user data"


def test_atomic_directory_commit_exposes_complete_tree_and_preserves_modes(tmp_path):
    import stat

    from oh_goon.integrations.exact_file_write import publish_prepared_directory

    staging = tmp_path / ".oh-goon-init-owned"
    staging.mkdir(mode=0o700)
    (staging / "run.yml").write_text("complete selection")
    (staging / "goon.lock").write_text("matching lock")
    output = tmp_path / "workflow"
    publish_prepared_directory(staging, output, exclusive=True)
    assert not staging.exists()
    assert sorted(path.name for path in output.iterdir()) == ["goon.lock", "run.yml"]
    assert (output / "run.yml").read_text() == "complete selection"
    assert stat.S_IMODE(output.stat().st_mode) == 0o700


def test_concurrent_directory_commits_have_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from oh_goon.integrations.exact_file_write import publish_prepared_directory

    output = tmp_path / "workflow"
    stages = [tmp_path / ".oh-goon-init-first", tmp_path / ".oh-goon-init-second"]
    for index, staging in enumerate(stages):
        staging.mkdir(mode=0o700)
        (staging / "run.yml").write_text(str(index))
        (staging / "goon.lock").write_text(str(index))
    barrier = Barrier(2, timeout=5)

    def publish(staging):
        barrier.wait()
        try:
            publish_prepared_directory(staging, output, exclusive=True)
        except FileExistsError:
            return "collision"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(publish, stages))
    assert sorted(results) == ["collision", "committed"]
    assert (output / "run.yml").read_text() == (output / "goon.lock").read_text()
    assert sum(staging.exists() for staging in stages) == 1


@pytest.mark.parametrize("fault", ["no-space", "interrupted"])
def test_precommit_failure_leaves_only_owned_staging(tmp_path, monkeypatch, fault):
    import errno

    from oh_goon.integrations import exact_file_write

    staging = tmp_path / ".oh-goon-init-owned"
    staging.mkdir(mode=0o700)
    (staging / "run.yml").write_text("complete selection")
    (staging / "goon.lock").write_text("matching lock")
    output = tmp_path / "workflow"

    def fail(*args):
        if fault == "interrupted":
            raise KeyboardInterrupt
        raise OSError(errno.ENOSPC, "native publication has no space")

    monkeypatch.setattr(exact_file_write, "_rename_directory_no_replace", fail)
    with pytest.raises(KeyboardInterrupt if fault == "interrupted" else OSError):
        exact_file_write.publish_prepared_directory(staging, output, exclusive=True)
    assert not output.exists()
    assert sorted(path.name for path in staging.iterdir()) == ["goon.lock", "run.yml"]


def test_staged_companion_directory_sync_failure_prevents_any_final_publication(
    tmp_path, monkeypatch
):
    import errno
    import os
    import stat

    from oh_goon.integrations.exact_file_write import (
        prepared_sibling_directory,
        publish_prepared_directory,
    )

    output = tmp_path / "workflow"
    sync = os.fsync
    with pytest.raises(OSError, match="directory durability failed"):
        with prepared_sibling_directory(output) as prepared:
            prepared.write_text("goons/general/example/goon.yml", "steps: []\n")
            prepared.write_text("run.yml", "complete selection")
            prepared.write_text("goon.lock", "matching lock")

            def fail(descriptor):
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError(errno.EIO, "directory durability failed")
                return sync(descriptor)

            monkeypatch.setattr(os, "fsync", fail)
            publish_prepared_directory(prepared.path, output, exclusive=True)
    assert not output.exists()
    assert not list(tmp_path.glob(".oh-goon-init-*"))


def test_postcommit_durability_failure_reports_complete_visible_tree_without_rollback(
    tmp_path, monkeypatch
):
    import errno
    import os

    from oh_goon.integrations import exact_file_write

    staging = tmp_path / ".oh-goon-init-owned"
    staging.mkdir(mode=0o700)
    (staging / "run.yml").write_text("complete selection")
    (staging / "goon.lock").write_text("matching lock")
    output = tmp_path / "workflow"
    rename = exact_file_write._rename_directory_no_replace
    sync = os.fsync
    committed = False

    def commit(*args):
        nonlocal committed
        rename(*args)
        committed = True

    def fail(descriptor):
        if committed:
            raise OSError(errno.EIO, "native directory durability failed")
        return sync(descriptor)

    monkeypatch.setattr(exact_file_write, "_rename_directory_no_replace", commit)
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(exact_file_write.ExactPublicationDurabilityError):
        exact_file_write.publish_prepared_directory(staging, output, exclusive=True)
    assert not staging.exists()
    assert (output / "run.yml").read_text() == "complete selection"
    assert (output / "goon.lock").read_text() == "matching lock"


def test_parent_retarget_after_pinning_never_redirects_atomic_commit(tmp_path, monkeypatch):
    from oh_goon.integrations import exact_file_write

    parent = tmp_path / "selected"
    parent.mkdir()
    staging = parent / ".oh-goon-init-owned"
    staging.mkdir(mode=0o700)
    (staging / "run.yml").write_text("complete selection")
    outside = tmp_path / "outside"
    outside.mkdir()
    sync = exact_file_write._sync_prepared_directory_descriptor

    def retarget(descriptor):
        parent.rename(tmp_path / "original")
        parent.symlink_to(outside, target_is_directory=True)
        return sync(descriptor)

    monkeypatch.setattr(exact_file_write, "_sync_prepared_directory_descriptor", retarget)
    with pytest.raises(OSError, match="directory changed"):
        exact_file_write.publish_prepared_directory(staging, parent / "workflow", exclusive=True)
    assert list(outside.iterdir()) == []
    assert not (tmp_path / "original/workflow").exists()


def test_staging_creation_and_cleanup_stay_with_the_pinned_parent(tmp_path, monkeypatch):
    import os

    from oh_goon.integrations.exact_file_write import prepared_sibling_directory

    parent = tmp_path / "selected"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    mkdir = os.mkdir

    def retarget(name, *args, **kwargs):
        if str(name).startswith(".oh-goon-init-"):
            parent.rename(tmp_path / "original")
            parent.symlink_to(outside, target_is_directory=True)
        return mkdir(name, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", retarget)
    with pytest.raises(OSError, match="directory changed"):
        with prepared_sibling_directory(parent / "workflow"):
            pytest.fail("A replaced parent must refuse before authoring")
    assert list(outside.iterdir()) == []
    assert list((tmp_path / "original").iterdir()) == []


def test_staging_write_and_cleanup_never_follow_a_retargeted_parent(tmp_path):
    from oh_goon.integrations.exact_file_write import prepared_sibling_directory

    parent = tmp_path / "selected"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(OSError, match="directory changed"):
        with prepared_sibling_directory(parent / "workflow") as prepared:
            prepared.write_text("run.yml", "owned authoring")
            parent.rename(tmp_path / "original")
            parent.symlink_to(outside, target_is_directory=True)
            foreign = outside / prepared.path.name
            foreign.mkdir()
            (foreign / "run.yml").write_text("user data")
            prepared.write_text("other.yml", "must never reach outside")
    assert (foreign / "run.yml").read_text() == "user data"
    assert not (foreign / "other.yml").exists()
    assert list((tmp_path / "original").iterdir()) == []


def test_staging_cleanup_refuses_a_replaced_stage_inode(tmp_path):
    from oh_goon.integrations.exact_file_write import prepared_sibling_directory

    with pytest.raises(OSError, match="directory changed"):
        with prepared_sibling_directory(tmp_path / "workflow") as prepared:
            prepared.write_text("run.yml", "owned authoring")
            prepared.path.rename(tmp_path / ".oh-goon-init-moved-owned")
            prepared.path.mkdir()
            (prepared.path / "user.txt").write_text("user data")
            prepared.require_current()
    assert (prepared.path / "user.txt").read_text() == "user data"
    assert (tmp_path / ".oh-goon-init-moved-owned/run.yml").read_text() == "owned authoring"
