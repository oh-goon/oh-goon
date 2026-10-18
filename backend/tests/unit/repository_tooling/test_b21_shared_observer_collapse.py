"""B12E-C1: one kernel observation mechanism, no second authority path."""

from __future__ import annotations

import ast
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
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    check_x0_single_execution_path,
)


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


def test_canonical_scope_is_typed_immutable_and_not_an_authority(tmp_path):
    original = scope(tmp_path)
    assert cli.Scope is PhysicalObservationScope
    with pytest.raises(FrozenInstanceError):
        original.run_id = "other"
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


def test_mechanism_and_diagnostic_use_identical_kernel_facts(tmp_path):
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


def test_component_instances_do_not_allow_state_reclassification(tmp_path):
    facts = kernel.observe_kernel(scope(tmp_path), proc_root=tmp_path / "empty-proc")
    original = facts.components[0][1]
    assert isinstance(original, PhysicalKernelFact)
    with pytest.raises(FrozenInstanceError):
        original.state = "PRESENT"
    assert original.state in ("PRESENT", "NOT_OBSERVED", "UNKNOWN")
    extracted = facts.to_mapping()
    extracted["cgroup"]["state"] = "PRESENT"
    assert facts.to_mapping()["cgroup"]["state"] != "PRESENT"


def test_completeness_rejects_missing_probe_and_bad_component(tmp_path):
    original = scope(tmp_path)
    with pytest.raises(ValueError, match="exact diagnostic probe set"):
        kernel.observe_kernel(original, diagnostic_probes={"_cgroup": cli._cgroup})
    methods = {name: getattr(kernel, name) for name in kernel.PROBES}
    methods["_cgroup"] = lambda *_: {"state": "ABSENT", "detail": "unsafe"}
    with pytest.raises(ValueError, match="invalid physical kernel fact"):
        kernel.observe_kernel(original, diagnostic_probes=methods)


def test_second_read_changes_to_unknown_never_absence(tmp_path):
    original = scope(tmp_path)
    methods = {name: getattr(kernel, name) for name in kernel.PROBES}
    states = iter(["PRESENT", "NOT_OBSERVED"])
    methods["_cgroup"] = lambda *_: {"state": next(states), "detail": "same"}
    facts = kernel.observe_kernel(
        original, proc_root=tmp_path / "empty-proc", diagnostic_probes=methods
    )
    assert facts.to_mapping()["cgroup"]["state"] == "UNKNOWN"


def test_only_one_kernel_reader_and_no_control_authority_link():
    assert check_x0_single_execution_path() == []
    cli_source = Path(cli.__file__).read_text()
    kernel_source = Path(kernel.__file__).read_text()
    cli_tree = ast.parse(cli_source)
    assert not any(
        isinstance(n, ast.FunctionDef)
        and n.name
        in (
            "_read_regular",
            "_cgroup",
            "_cgroup_subtree",
            "_process",
            "_mounts",
            "_tracked_namespace_inventory",
            "_namespace_fd_holders",
            "_visible_foreign_mount_targets",
            "_visible_mount_fd_holders",
        )
        for n in cli_tree.body
    )
    assert "def _read_regular(" in kernel_source
    assert "def _visible_mount_fd_holders(" in kernel_source
    assert "from oh_goon.services" not in kernel_source
    assert "from oh_goon.runtime" not in kernel_source
    assert 'QUIESCENCE_PROVEN"' not in kernel_source
    assert 'TEARDOWN_PROVEN"' not in kernel_source


def test_x0_rejects_duplicate_kernel_reader_or_unlinked_cli(tmp_path):
    root = tmp_path / "repo"
    package = root / "backend/src/oh_goon"
    cli_file = package / "repository_tooling/local_lace_physical_preflight.py"
    shared_file = package / "integrations/linux_physical_observer.py"
    contract_file = package / "core/ports/physical_observation.py"
    for path in (cli_file, shared_file, contract_file):
        path.parent.mkdir(parents=True, exist_ok=True)
    (root / "backend/pyproject.toml").write_text("""[project.scripts]
"oh-goon-local-lace-physical-preflight" = "oh_goon.repository_tooling.local_lace_physical_preflight:main"
""")
    shared_file.write_text(Path(kernel.__file__).read_text())
    contract_file.write_text("from dataclasses import dataclass\n")
    cli_file.write_text(Path(cli.__file__).read_text())
    from tests.factories.x0_architecture import copy_x0_architecture_sources

    copy_x0_architecture_sources(root)
    assert check_x0_single_execution_path(root) == []
    cli_file.write_text(cli_file.read_text() + "\ndef _visible_mount_fd_holders():\n    return 1\n")
    assert any(
        "second physical kernel observer" in e.message for e in check_x0_single_execution_path(root)
    )
    cli_file.write_text("def main(): pass\n")
    assert any(
        "must use shared kernel observation" in e.message
        for e in check_x0_single_execution_path(root)
    )


def test_b21_native_wsl_witness_runs_exact_shared_observer_ring():
    root = Path(__file__).resolve().parents[4]
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    recipe = (
        (root / "Makefile").read_text().split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    )
    assert (
        "if [[ -f .checkpoint/B21_SOURCE.sha256 ]]; then embedded='.checkpoint/B21_SOURCE.sha256'; fi"
        in script
    )
    assert script.count("test_b21_shared_observer_collapse") >= 2
    assert "required_modules+=(--require-module test_b21_shared_observer_collapse)" in script
    assert '"${observer_tests[@]}"' in script
    assert script.index("run_ring CORE") < script.index('run_ring "PHYSICAL_OBSERVER${suffix}"')
    assert recipe.index("source-transport-clean") < recipe.index("qualify_b12_development_wsl.sh")
