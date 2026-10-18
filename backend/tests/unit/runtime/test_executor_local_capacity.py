from __future__ import annotations

from pathlib import Path

from oh_goon.runtime import _executor_local_capacity as capacity


def test_effective_memory_capacity_includes_intermediate_cgroup_ancestors(
    tmp_path: Path, monkeypatch
) -> None:
    hierarchy = tmp_path / "cgroup"
    unit = hierarchy / "user.slice" / "user-1000.slice" / "run-certification.service"
    unit.mkdir(parents=True)
    (hierarchy / "memory.max").write_text("max\n", encoding="ascii")
    (hierarchy / "user.slice" / "memory.max").write_text("max\n", encoding="ascii")
    (hierarchy / "user.slice" / "user-1000.slice" / "memory.max").write_text(
        str(3 * 1024 * 1024 * 1024) + "\n", encoding="ascii"
    )
    (unit / "memory.max").write_text("max\n", encoding="ascii")
    monkeypatch.setattr(capacity, "_CGROUP_V2_ROOT", hierarchy)

    assert capacity._observed_memory_bytes(unit) == 3 * 1024 * 1024 * 1024


def test_capacity_limit_paths_walk_root_to_delegated_leaf(tmp_path: Path, monkeypatch) -> None:
    hierarchy = tmp_path / "cgroup"
    leaf = hierarchy / "a" / "b"
    leaf.mkdir(parents=True)
    monkeypatch.setattr(capacity, "_CGROUP_V2_ROOT", hierarchy)

    assert capacity._capacity_limit_paths("memory.max", leaf) == (
        hierarchy / "memory.max",
        hierarchy / "a" / "memory.max",
        leaf / "memory.max",
    )
