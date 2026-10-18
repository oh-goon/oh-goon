from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.integrations import content_snapshot_dereferenced as subject
from oh_goon.integrations.content_snapshot import snapshot_identity


def test_stable_dereferenced_snapshot_materializes_twice_and_promotes_second_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "managed-runtime"
    (source / "bin").mkdir(parents=True)
    interpreter = tmp_path / "host-python"
    interpreter.write_bytes(b"ELF-placeholder")
    interpreter.chmod(0o755)
    (source / "bin" / "python").symlink_to(interpreter)
    (source / "metadata.txt").write_text("stable", encoding="utf-8")
    destination = tmp_path / "frozen-runtime"

    original = subject._copy_tree_dereferenced
    materializations: list[Path] = []

    def observed_copy(*args, **kwargs) -> None:
        destination_arg = args[1]
        if destination_arg.name == "tree" and destination_arg.parent.parent == tmp_path:
            materializations.append(destination_arg)
        original(*args, **kwargs)

    monkeypatch.setattr(subject, "_copy_tree_dereferenced", observed_copy)

    identity = subject.snapshot_directory_dereferenced(
        source,
        destination,
        label="managed runtime economics",
    )

    assert len(materializations) == 2
    assert snapshot_identity(destination) == identity
    assert (destination / "bin" / "python").is_file()
    assert not (destination / "bin" / "python").is_symlink()
    assert not list(tmp_path.glob(".oh-goon-dereferenced-before-*"))
    assert not list(tmp_path.glob(".oh-goon-dereferenced-after-*"))


def test_unstable_dereferenced_snapshot_retries_without_leaking_proof_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload.txt"
    payload.write_text("v1", encoding="utf-8")
    destination = tmp_path / "destination"

    original = subject._materialize_dereferenced_tree
    calls = 0

    def mutate_after_materialization(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls in {1, 3, 5}:
            payload.write_text(f"v{calls + 1}", encoding="utf-8")
        return result

    monkeypatch.setattr(subject, "_materialize_dereferenced_tree", mutate_after_materialization)

    with pytest.raises(subject.FleetPlanInvalid, match="changed while Fleet was creating"):
        subject.snapshot_directory_dereferenced(source, destination, label="unstable runtime")

    assert calls == 6
    assert not destination.exists()
    assert not list(tmp_path.glob(".oh-goon-dereferenced-before-*"))
    assert not list(tmp_path.glob(".oh-goon-dereferenced-after-*"))
