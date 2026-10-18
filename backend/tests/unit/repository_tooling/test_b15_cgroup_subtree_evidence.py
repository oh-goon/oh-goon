"""B15: independent diagnostic cgroup-subtree evidence is never effect authority."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_lace_physical_preflight as probe


def scope(tmp_path: Path, **changes):
    root = tmp_path / "original-cgroup"
    root.mkdir(exist_ok=True)
    raw = {
        "run_id": "original-run",
        "subject_identity": "sha256:" + "a" * 64,
        "cgroup_path": str(root),
        "cgroup_device": root.stat().st_dev,
        "cgroup_inode": root.stat().st_ino,
        "tracked_pid": 1234567,
        "tracked_birth": "boot-test:123",
        "mount_targets": ["/private/original-mount"],
        "network_namespace": "testnet",
        "host_veth": "testveth",
        "nft_table": "testnft",
    }
    raw.update(changes)
    return raw


def _facts(path: Path, populated: int = 0, pids: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cgroup.events").write_text(f"populated {populated}\n", encoding="utf-8")
    (path / "cgroup.procs").write_text(pids, encoding="utf-8")


def test_cgroup_subtree_records_children_with_root_and_still_cannot_prove_absence(tmp_path):
    raw = scope(tmp_path)
    root = Path(raw["cgroup_path"])
    _facts(root, populated=0)
    _facts(root / "worker")
    _facts(root / "worker" / "nested")
    spec = probe.parse_scope(raw)
    receipt = probe._cgroup_subtree(spec)
    assert receipt["state"] == "PRESENT"
    assert "nodes=3; populated_nodes=0; observed_members=0" in receipt["detail"]
    assert "inventory_sha256=" in receipt["detail"]
    # Even a wholly empty original subtree remains physically PRESENT.
    result = probe.observe(spec, proc_root=tmp_path / "fake-proc")
    assert result["schema"].startswith("oh-goon/b19/")
    assert result["observations"]["subtree"] == receipt
    assert result["physical_absence_proven"] is False
    assert result["quiescence_proven"] is False
    assert result["teardown_proven"] is False
    assert result["effect_permission"] == "NONE" and result["gate_b"] == "BLOCKED"
    assert set(result["proof_obligations"].values()) == {"UNVERIFIED"}


def test_subtree_birth_identifies_running_members_without_issuing_claim(tmp_path):
    raw = scope(tmp_path)
    root = Path(raw["cgroup_path"])
    _facts(root, populated=1)
    _facts(root / "child", populated=1, pids="123\n")
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot-test\n")
    (proc / "123" / "stat").write_text(
        "123 (test process) " + " ".join(["S"] + ["0"] * 18 + ["777"])
    )
    result = probe._cgroup_subtree(probe.parse_scope(raw), proc)
    assert result["state"] == "PRESENT"
    assert "observed_members=1" in result["detail"]
    (proc / "123" / "stat").write_text(
        "123 (test process) " + " ".join(["S"] + ["0"] * 18 + ["778"])
    )
    changed = probe._cgroup_subtree(probe.parse_scope(raw), proc)
    assert changed["state"] == "PRESENT"
    assert changed["detail"] != result["detail"]


@pytest.mark.parametrize(
    "mutation", ["symlink", "missing-events", "invalid-pid", "missing-pid-stat", "bad-populated"]
)
def test_subtree_incomplete_child_is_unknown(tmp_path, mutation):
    raw = scope(tmp_path)
    root = Path(raw["cgroup_path"])
    _facts(root, populated=1)
    child = root / "child"
    if mutation == "symlink":
        child.symlink_to(tmp_path, target_is_directory=True)
    else:
        _facts(child, populated=1)
        if mutation == "missing-events":
            (child / "cgroup.events").unlink()
        elif mutation == "invalid-pid":
            (child / "cgroup.procs").write_text("invalid-pid")
        elif mutation == "missing-pid-stat":
            (child / "cgroup.procs").write_text("123\n")
        elif mutation == "bad-populated":
            (child / "cgroup.events").write_text("populated wrong\n")
    assert probe._cgroup_subtree(probe.parse_scope(raw))["state"] == "UNKNOWN"


def test_missing_or_replaced_cgroup_root_never_proves_absence(tmp_path):
    raw = scope(tmp_path)
    spec = probe.parse_scope(raw)
    root = Path(raw["cgroup_path"])
    root.rmdir()
    assert probe._cgroup_subtree(spec)["state"] == "NOT_OBSERVED"
    root.mkdir()
    _facts(root)
    assert (
        probe._cgroup_subtree(probe.parse_scope({**raw, "cgroup_inode": 1}))["state"] == "UNKNOWN"
    )


def test_racing_subtree_in_double_scan_becomes_unknown(tmp_path, monkeypatch):
    raw = scope(tmp_path)
    spec = probe.parse_scope(raw)
    sequence = iter([probe._component("PRESENT", "first"), probe._component("PRESENT", "second")])
    monkeypatch.setattr(probe, "_cgroup_subtree", lambda *_a: next(sequence))
    for name in ("_cgroup", "_process", "_mounts", "_network"):
        monkeypatch.setattr(
            probe, name, lambda *_a: probe._component("NOT_OBSERVED", "not observed")
        )
    result = probe.observe(spec)
    assert result["observations"]["subtree"]["state"] == "UNKNOWN"
    assert all(x == "UNVERIFIED" for x in result["proof_obligations"].values())
    assert result["gate_b"] == "BLOCKED"


def test_budget_caps_tree_instead_of_accepting_partial_observation(tmp_path):
    raw = scope(tmp_path)
    root = Path(raw["cgroup_path"])
    _facts(root)
    current = root
    for i in range(17):
        current = current / f"d{i}"
        _facts(current)
    assert probe._cgroup_subtree(probe.parse_scope(raw))["state"] == "UNKNOWN"


def test_no_original_production_writers_imported():
    source = Path(probe.__file__).read_text()
    assert "from oh_goon.runtime" not in source
    assert "from oh_goon.services" not in source
    assert 'QUIESCENCE_PROVEN"' not in source
    assert 'TEARDOWN_PROVEN"' not in source


def test_b15_manifest_selected_after_b14_before_witness():
    root = Path(__file__).resolve().parents[4]
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert (
        "if [[ -f .checkpoint/B15_SOURCE.sha256 ]]; then embedded='.checkpoint/B15_SOURCE.sha256'; fi"
        in script
    )
    makefile = (root / "Makefile").read_text()
    block = makefile.split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert block.index("source-transport-clean") < block.index("qualify_b12_development_wsl.sh")


def test_wrong_boot_epoch_cannot_establish_member_identity(tmp_path):
    raw = scope(tmp_path)
    root = Path(raw["cgroup_path"])
    _facts(root, populated=1, pids="234\n")
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("foreign-boot\n")
    (proc / "234").mkdir()
    (proc / "234/stat").write_text("234 (test) " + " ".join(["S"] + ["0"] * 18 + ["900"]))
    assert probe._cgroup_subtree(probe.parse_scope(raw), proc)["state"] == "UNKNOWN"
