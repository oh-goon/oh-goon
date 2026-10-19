"""B13 physical-negative-evidence regression ring; no mocked proof or gate seal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_lace_physical_preflight as b13


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


def test_exact_schema_rejects_missing_extra_and_invalid_id(tmp_path):
    raw = scope(tmp_path)
    assert b13.parse_scope(raw).subject_identity == raw["subject_identity"]
    for invalid in (
        {**raw, "effect_permission": "GRANTED"},
        {k: v for k, v in raw.items() if k != "tracked_birth"},
        {**raw, "subject_identity": "fake"},
        {**raw, "tracked_pid": True},
        {**raw, "mount_targets": []},
        {**raw, "mount_targets": ["/good", "../bad"]},
        {**raw, "network_namespace": None},
    ):
        with pytest.raises(b13.PreflightError):
            b13.parse_scope(invalid)


def test_input_cannot_overwrite_output_or_follow_scope_symlink(tmp_path):
    raw = scope(tmp_path)
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(raw))
    output = tmp_path / "report.json"
    assert b13.main(["--scope", str(path), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["gate_b"] == "BLOCKED"
    with pytest.raises(SystemExit) as error:
        b13.main(["--scope", str(path), "--output", str(output)])
    assert error.value.code == 2
    path2 = tmp_path / "linked.json"
    path2.symlink_to(path)
    with pytest.raises(SystemExit) as error:
        b13.main(["--scope", str(path2), "--output", str(tmp_path / "new.json")])
    assert error.value.code == 2


def test_cgroup_present_is_not_absence_even_when_empty(tmp_path):
    raw = scope(tmp_path, network_namespace=None, host_veth=None, nft_table=None)
    root = Path(raw["cgroup_path"])
    (root / "cgroup.events").write_text("populated 0\n")
    (root / "cgroup.procs").write_text("")
    result = b13._cgroup(b13.parse_scope(raw))
    assert result["state"] == "PRESENT"


def test_cgroup_missing_is_not_physical_absence(tmp_path):
    raw = scope(tmp_path)
    Path(raw["cgroup_path"]).rmdir()
    result = b13._cgroup(b13.parse_scope(raw))
    assert result["state"] == "NOT_OBSERVED"


def test_existing_cgroup_with_missing_kernel_facts_is_unknown(tmp_path):
    raw = scope(tmp_path)
    result = b13._cgroup(b13.parse_scope(raw))
    assert result["state"] == "UNKNOWN"


def test_reused_cgroup_inode_remains_unknown(tmp_path):
    raw = scope(tmp_path, cgroup_inode=1)
    result = b13._cgroup(b13.parse_scope(raw))
    assert result["state"] == "UNKNOWN"


def test_pid_birth_requires_boot_identity(tmp_path):
    raw = scope(tmp_path, tracked_pid=123)
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot-test\n")
    # /proc/<pid>/stat fields 3..22; starttime = zero-based tail[19].
    stats = ["S"] + ["0"] * 18 + ["123"]
    (proc / "123/stat").write_text("123 (test process) " + " ".join(stats))
    assert b13._process(b13.parse_scope(raw), proc)["state"] == "PRESENT"
    raw["tracked_birth"] = "previous-boot:123"
    assert b13._process(b13.parse_scope(raw), proc)["state"] == "UNKNOWN"


def test_missing_pid_does_not_prove_descendants_absent(tmp_path):
    raw = scope(tmp_path)
    proc = tmp_path / "proc"
    proc.mkdir()
    assert b13._process(b13.parse_scope(raw), proc)["state"] == "NOT_OBSERVED"


def test_mounts_in_observer_namespace_only(tmp_path):
    raw = scope(tmp_path)
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/mountinfo").write_text(
        "42 39 0:42 / /private/original-mount rw - tmpfs tmpfs rw\n"
    )
    assert b13._mounts(b13.parse_scope(raw), proc)["state"] == "PRESENT"
    (proc / "self/mountinfo").write_text("42 39 0:42 / /somewhere rw - tmpfs tmpfs rw\n")
    assert b13._mounts(b13.parse_scope(raw), proc)["state"] == "NOT_OBSERVED"


def test_network_unavailable_is_unknown_never_absent(tmp_path, monkeypatch):
    raw = scope(tmp_path)
    monkeypatch.setattr(b13, "_command", lambda *_a: (_ for _ in ()).throw(OSError("ip missing")))
    assert b13._network(b13.parse_scope(raw))["state"] == "UNKNOWN"


def test_network_any_one_remaining_object_is_present(tmp_path, monkeypatch):
    raw = scope(tmp_path)

    def command(argv):
        if "netns" in argv:
            return []
        if "link" in argv:
            return [{"ifname": "testveth@if12"}]
        return {"nftables": []}

    monkeypatch.setattr(b13, "_command", command)
    assert b13._network(b13.parse_scope(raw))["state"] == "PRESENT"


def test_double_scan_race_is_unknown_and_no_seal(tmp_path, monkeypatch):
    spec = b13.parse_scope(scope(tmp_path))
    sequence = iter([b13._component("PRESENT", "found"), b13._component("NOT_OBSERVED", "missing")])
    monkeypatch.setattr(b13, "_cgroup", lambda *_a: next(sequence))
    monkeypatch.setattr(b13, "_process", lambda *_a: b13._component("NOT_OBSERVED", "pid missing"))
    monkeypatch.setattr(b13, "_mounts", lambda *_a: b13._component("NOT_OBSERVED", "not here"))
    monkeypatch.setattr(b13, "_network", lambda *_a: b13._component("NOT_OBSERVED", "not here"))
    receipt = b13.observe(spec)
    assert receipt["observations"]["cgroup"]["state"] == "UNKNOWN"
    assert receipt["gate_b"] == "BLOCKED" and receipt["effect_permission"] == "NONE"
    assert receipt["teardown_proven"] is False and receipt["quiescence_proven"] is False
    assert "INDEPENDENT_PROCESS_TREE" in receipt["unresolved"]


def test_all_no_matches_still_cannot_upgrade_into_proof(tmp_path, monkeypatch):
    spec = b13.parse_scope(scope(tmp_path))
    for name in (
        "_cgroup",
        "_cgroup_subtree",
        "_process",
        "_mounts",
        "_network",
        "_tracked_namespace_inventory",
        "_namespace_fd_holders",
        "_visible_foreign_mount_targets",
        "_visible_mount_fd_holders",
    ):
        monkeypatch.setattr(
            b13,
            name,
            (
                lambda *_a: {
                    "tracked_mount_namespace": b13._component("NOT_OBSERVED", "local-only"),
                    "tracked_network_namespace": b13._component("NOT_OBSERVED", "local-only"),
                }
            )
            if name == "_tracked_namespace_inventory"
            else (lambda *_a: b13._component("NOT_OBSERVED", "local-only")),
        )
    receipt = b13.observe(spec)
    assert all(x["state"] == "NOT_OBSERVED" for x in receipt["observations"].values())
    assert receipt["physical_absence_proven"] is receipt["teardown_proven"] is False
    assert receipt["gate_b"] == "BLOCKED"


def test_module_does_not_import_effect_issuer_or_journal(tmp_path):
    path = Path(b13.__file__)
    source = path.read_text()
    assert "from oh_goon.runtime" not in source
    assert "from oh_goon.services" not in source
    assert 'QUIESCENCE_PROVEN"' not in source
    assert 'TEARDOWN_PROVEN"' not in source


def test_b13_source_identity_is_selected_before_b12_in_development_witness():
    source = Path(__file__).resolve().parents[4] / "scripts" / "qualify_b12_development_wsl.sh"
    content = source.read_text()
    assert "embedded='.checkpoint/B12_SOURCE.sha256'" in content
    assert (
        "if [[ -f .checkpoint/B13_SOURCE.sha256 ]]; then embedded='.checkpoint/B13_SOURCE.sha256'; fi"
        in content
    )
    assert 'source_transport_audit --baseline "$embedded"' in content
    assert 'source_inventory | grep -v -F "  ./$embedded"' in content
