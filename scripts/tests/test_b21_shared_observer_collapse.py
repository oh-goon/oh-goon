"""B12E-C1: one kernel observation mechanism, no second authority path."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from oh_goon.core.ports.physical_observation import (
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
    PhysicalObservationScope,
)
from oh_goon.integrations import linux_physical_observer as kernel
from oh_goon.repository_tooling import local_lace_physical_preflight as cli


def scope(tmp_path: Path) -> PhysicalObservationScope:
    cgroup = tmp_path / "absent-cgroup"
    return PhysicalObservationScope(
        "example-run",
        "sha256:" + "a" * 64,
        str(cgroup),
        123,
        789,
        9876543,
        "some-boot:3",
        ("/example/target",),
        None,
        None,
        None,
    )


def test_canonical_scope_is_typed_immutable_and_not_an_authority(
    tmp_path: Path,
) -> None:
    original = scope(tmp_path)
    assert getattr(cli, "Scope") is PhysicalObservationScope
    with pytest.raises(FrozenInstanceError):
        setattr(original, "run_id", "other")
    assert not any(
        f.name
        in (
            "effect_permission",
            "quiescence_proven",
            "teardown_proven",
            "signer_epoch",
            "admission_grant",
        )
        for f in fields(PhysicalObservationScope)
    )


def test_mechanism_and_diagnostic_use_identical_kernel_facts(tmp_path: Path) -> None:
    original = scope(tmp_path)
    # No magic default for missing tracked process, cgroup or network lease.
    facts = kernel.observe_kernel(original, proc_root=tmp_path / "empty-proc")
    report = cli.observe(original, proc_root=tmp_path / "empty-proc")
    assert isinstance(facts, PhysicalKernelSnapshot)
    assert facts.to_mapping() == report["observations"]
    assert report["classification"] == "DEVELOPMENT_DIAGNOSTIC_ONLY"
    assert report["physical_absence_proven"] is False
    assert report["quiescence_proven"] is report["teardown_proven"] is False
    assert report["gate_b"] == "BLOCKED"
    assert report["effect_permission"] == "NONE"
    assert report["proof_obligations"]
    assert all(value == "UNVERIFIED" for value in report["proof_obligations"].values())
    assert not hasattr(facts, "gate_b")
    assert not hasattr(facts, "physical_absence_proven")
    assert not hasattr(facts, "run_id")


def test_component_instances_do_not_allow_state_reclassification(
    tmp_path: Path,
) -> None:
    facts = kernel.observe_kernel(scope(tmp_path), proc_root=tmp_path / "empty-proc")
    original = facts.components[0][1]
    assert isinstance(original, PhysicalKernelFact)
    with pytest.raises(FrozenInstanceError):
        setattr(original, "state", "PRESENT")
    assert original.state in ("PRESENT", "NOT_OBSERVED", "UNKNOWN")
    extracted = facts.to_mapping()
    extracted["cgroup"]["state"] = "PRESENT"
    assert facts.to_mapping()["cgroup"]["state"] != "PRESENT"


def test_completeness_rejects_missing_probe_and_bad_component(tmp_path: Path) -> None:
    original = scope(tmp_path)
    with pytest.raises(ValueError, match="exact diagnostic probe set"):
        kernel.observe_kernel(
            original, diagnostic_probes={"_cgroup": getattr(cli, "_cgroup")}
        )
    methods = {name: getattr(kernel, name) for name in kernel.PROBES}
    methods["_cgroup"] = lambda *_: {"state": "ABSENT", "detail": "unsafe"}
    with pytest.raises(ValueError, match="invalid physical kernel fact"):
        kernel.observe_kernel(original, diagnostic_probes=methods)


def test_second_read_changes_to_unknown_never_absence(tmp_path: Path) -> None:
    original = scope(tmp_path)
    methods = {name: getattr(kernel, name) for name in kernel.PROBES}
    states = iter(["PRESENT", "NOT_OBSERVED"])
    methods["_cgroup"] = lambda *_: {"state": next(states), "detail": "same"}
    facts = kernel.observe_kernel(
        original, proc_root=tmp_path / "empty-proc", diagnostic_probes=methods
    )
    assert facts.to_mapping()["cgroup"]["state"] == "UNKNOWN"
