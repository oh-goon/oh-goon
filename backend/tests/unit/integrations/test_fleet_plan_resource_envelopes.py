from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import PlanResourceEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
    isolation_requirements,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroup_resources
from oh_goon.integrations._fleet_plan_cgroup_resources import CgroupV2AttemptLease
from oh_goon.integrations._fleet_plan_opensandbox_transport import _opensandbox_resource
from oh_goon.integrations._fleet_plan_resource_evidence import (
    read_attempt_resource_evidence,
    write_remote_resource_evidence,
)
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    OpenSandboxProcessIsolation,
    OpenSandboxRuntimeKind,
    OpenSandboxSettings,
)


class _Transport:
    def require_available(self, settings) -> None:
        del settings

    def execute(self, settings, request, *, expected_runtime_authority, timeout, cancel_requested):
        raise AssertionError("selection-only transport")


def _plan(resources: CicdResourcePolicy) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="resource-envelope",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=resources,
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )


def _capabilities(
    *,
    cpu: bool = True,
    memory: bool = True,
    processes: bool = True,
) -> IsolationBackendCapabilities:
    return IsolationBackendCapabilities(
        backend_id="resource-test",
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=cpu,
        enforced_memory_limit=memory,
        enforced_process_limit=processes,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )


def test_backend_without_resource_guarantees_cannot_satisfy_resource_plan() -> None:
    resources = CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256)

    assert not _capabilities(cpu=False, memory=False).satisfies(
        isolation_requirements(True, resources)
    )


def test_backend_without_pid_guarantee_cannot_satisfy_pid_limited_plan() -> None:
    resources = CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=128)

    assert not _capabilities(processes=False).satisfies(isolation_requirements(True, resources))


def test_opensandbox_resource_translation_is_exact_and_pid_limit_is_not_claimed() -> None:
    policy = CicdResourcePolicy(cpu_millis=1500, memory_mib=3072, max_processes=None)
    assert _opensandbox_resource(policy) == {"cpu": "1500m", "memory": "3072Mi"}
    assert _opensandbox_resource(
        CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=None)
    ) == {"cpu": "2", "memory": "4096Mi"}

    backend = OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain="sandbox.internal",
            api_key="test",
            image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
            cli_executable=sys.executable,
            runtime_attestation_file=str(Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox" / "kata-runtime-attestation.json"),
            runtime_attestation_public_key_file=str(Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox" / "runtime-attestation-ed25519.pub.pem"),
            runtime=OpenSandboxRuntimeKind.KATA,
            protocol="https",
        ),
        transport=_Transport(),
    )
    assert backend.capabilities.enforced_cpu_limit is True
    assert backend.capabilities.enforced_memory_limit is True
    assert backend.capabilities.enforced_process_limit is False


def test_opensandbox_cannot_satisfy_requested_pid_limit() -> None:
    backend = OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain="sandbox.internal",
            api_key="test",
            image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
            cli_executable=sys.executable,
            runtime_attestation_file=str(Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox" / "kata-runtime-attestation.json"),
            runtime_attestation_public_key_file=str(Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox" / "runtime-attestation-ed25519.pub.pem"),
            runtime=OpenSandboxRuntimeKind.KATA,
            protocol="https",
        ),
        transport=_Transport(),
    )
    plan = ResolvedFleetPlan.create(
        name="remote-resource-envelope",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=False,
            resources=CicdResourcePolicy(
                cpu_millis=2000,
                memory_mib=4096,
                max_processes=128,
            ),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )

    execution = plan.execution
    assert isinstance(execution, CicdExecutionPolicy)
    assert not backend.capabilities.satisfies(
        isolation_requirements(execution.trust_repository_code, execution.resources)
    )


def test_cgroup_bootstrap_refuses_unqualified_process_attachment(tmp_path: Path) -> None:
    procs = tmp_path / "cgroup.procs"
    procs.write_text("", encoding="ascii")
    command = (
        sys.executable,
        "-S",
        "-c",
        cgroup_resources._CGROUP_EXEC,
        str(tmp_path),
        str(tmp_path.stat().st_dev), str(tmp_path.stat().st_ino),
        sys.executable,
        "-S",
        "-c",
        "print('ran')",
    )

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode != 0
    assert "independent kernel custody NOT PROVEN" in result.stderr
    assert result.stdout == ""
    assert procs.read_text(encoding="ascii") == ""


def test_resource_evidence_binds_limits_not_usage(tmp_path: Path) -> None:
    policy = CicdResourcePolicy(cpu_millis=1750, memory_mib=2048, max_processes=96)
    first = PlanResourceEvidence(
        cpu_millis=policy.cpu_millis,
        memory_bytes=policy.memory_bytes,
        max_processes=policy.max_processes,
        cpu_usage_usec=10,
        memory_peak_bytes=20,
        pids_peak=3,
        oom_kill_count=0,
    )
    second = PlanResourceEvidence(
        cpu_millis=policy.cpu_millis,
        memory_bytes=policy.memory_bytes,
        max_processes=policy.max_processes,
        cpu_usage_usec=999,
        memory_peak_bytes=888,
        pids_peak=7,
        oom_kill_count=1,
    )

    assert first.policy_id == second.policy_id
    write_remote_resource_evidence(tmp_path, policy)
    observed = read_attempt_resource_evidence(tmp_path)
    assert observed is not None
    assert observed.policy_id == first.policy_id
    assert observed.cpu_usage_usec is None


def test_resource_evidence_policy_id_is_read_only_and_derived() -> None:
    evidence = PlanResourceEvidence(
        cpu_millis=2000,
        memory_bytes=4096 * 1024 * 1024,
        max_processes=None,
    )
    changed = PlanResourceEvidence(
        cpu_millis=3000,
        memory_bytes=evidence.memory_bytes,
        max_processes=evidence.max_processes,
    )

    assert changed.policy_id != evidence.policy_id
    with pytest.raises(ValidationError):
        PlanResourceEvidence.model_validate(
            evidence.model_dump(mode="python", exclude_computed_fields=True)
            | {"policy_id": evidence.policy_id}
        )


def test_cgroup_stale_owner_reconciliation_is_fail_closed_and_owner_aware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cgroups"
    state = tmp_path / "state"
    root.mkdir()
    state.mkdir()
    name = "attempt-" + "a" * 16
    cgroup = root / name
    cgroup.mkdir()
    (cgroup / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (cgroup / "cgroup.procs").write_text("", encoding="ascii")
    lease = state / "dead.json"
    lease.write_text(
        json.dumps({"pid": 424242, "process_identity": "old", "cgroup": name,
                    "cgroup_dev": cgroup.stat().st_dev, "cgroup_ino": cgroup.stat().st_ino}),
        encoding="utf-8",
    )
    killed: list[Path] = []
    removed: list[Path] = []
    retired: list[Path] = []

    def remove_durably(path: Path) -> bool:
        retired.append(path)
        path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(cgroup_resources, "_process_identity", lambda _pid: None)
    monkeypatch.setattr(cgroup_resources, "_kill_cgroup", lambda path, **_kw: killed.append(path))
    monkeypatch.setattr(cgroup_resources, "_remove_cgroup", lambda path, **_kw: removed.append(path))
    monkeypatch.setattr(cgroup_resources, "remove_file_durably", remove_durably)

    with pytest.raises(FleetPlanInvalid, match='physical custody'):
        cgroup_resources._reconcile_stale_cgroups(root, state)

    assert killed == [] and removed == [] and retired == []
    assert cgroup.exists() and lease.exists()

    lease.unlink()  # Quarantined record would correctly stop reconciliation first.
    corrupt = state / "corrupt.json"
    corrupt.write_text("not-json", encoding="utf-8")
    with pytest.raises(FleetPlanInvalid, match="refusing destructive reconciliation"):
        cgroup_resources._reconcile_stale_cgroups(root, state)


def test_attempt_cgroup_limit_values_and_metrics_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fixture-backed cgroup is not a real native custody grant.
    monkeypatch.setattr(cgroup_resources, "require_independent_native_parent_custody",
                        lambda **_kwargs: None)
    class Host:
        cgroup_root = tmp_path / "delegated"
        cgroup_lock = tmp_path / "lock"
        cgroup_state_dir = tmp_path / "state"

    host = Host()
    host.cgroup_root.mkdir()
    for name in ("cgroup.procs", "cpu.max", "memory.max", "pids.max"):
        (host.cgroup_root / name).write_text("", encoding="ascii")
    monkeypatch.setattr(
        cgroup_resources,
        "require_cgroup_v2_host",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cgroup_resources, "_process_identity", lambda _pid: "boot:1")
    retired: list[Path] = []

    def remove_durably(path: Path) -> bool:
        retired.append(path)
        path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(cgroup_resources, "_kill_cgroup", lambda _path, **_kw: None)
    monkeypatch.setattr(cgroup_resources, "_remove_cgroup", lambda path, **_kw: None)
    monkeypatch.setattr(cgroup_resources, "remove_file_durably", remove_durably)

    original_write = cgroup_resources._write_limit

    def write_limit(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        original_write(path, value)

    monkeypatch.setattr(cgroup_resources, "_write_limit", write_limit)
    native_mkdir = Path.mkdir
    def materialize(group: Path, *args: object, **kwargs: object) -> None:
        native_mkdir(group, *args, **kwargs)
        if group.name.startswith("attempt-"):
            for name in ("cgroup.kill", "cgroup.events", "cgroup.procs", "cpu.max", "memory.max", "pids.max"):
                (group / name).write_text("populated 0\n" if name == "cgroup.events" else "", encoding="ascii")
    monkeypatch.setattr(Path, "mkdir", materialize)
    policy = CicdResourcePolicy(cpu_millis=1500, memory_mib=1024, max_processes=64)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    lease = CgroupV2AttemptLease.create(host, attempt, policy)
    lease.open()

    assert (lease.cgroup / "cpu.max").read_text(encoding="ascii") == "150000 100000"
    assert (lease.cgroup / "memory.max").read_text(encoding="ascii") == str(1024 * 1024 * 1024)
    assert (lease.cgroup / "pids.max").read_text(encoding="ascii") == "64"
    (lease.cgroup / "cpu.stat").write_text("usage_usec 321\n", encoding="ascii")
    (lease.cgroup / "memory.peak").write_text("654321\n", encoding="ascii")
    (lease.cgroup / "pids.peak").write_text("17\n", encoding="ascii")
    (lease.cgroup / "memory.events").write_text("oom_kill 2\n", encoding="ascii")

    (lease.cgroup / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (lease.cgroup / "cgroup.procs").write_text("", encoding="ascii")
    evidence = lease.close()

    assert evidence.cpu_usage_usec == 321
    assert evidence.memory_peak_bytes == 654321
    assert evidence.pids_peak == 17
    assert evidence.oom_kill_count == 2
    assert read_attempt_resource_evidence(attempt) == evidence
    assert retired == [lease.state_file]


def test_cgroup_preflight_requires_controllers_enabled_for_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Host:
        cgroup_root = tmp_path / "delegated"
        cgroup_lock = tmp_path / "lock"
        cgroup_state_dir = tmp_path / "state"

    root = Host.cgroup_root
    root.mkdir()
    for name in (
        "cgroup.procs",
        "cgroup.controllers",
        "cgroup.subtree_control",
        "cpu.max",
        "memory.max",
        "pids.max",
    ):
        (root / name).write_text("", encoding="ascii")
    (root / "cgroup.controllers").write_text("cpu memory pids", encoding="ascii")
    (root / "cgroup.subtree_control").write_text("cpu memory", encoding="ascii")
    monkeypatch.setattr(cgroup_resources.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_resources, "_is_cgroup2_path", lambda _path: True)

    with pytest.raises(FleetPlanInvalid, match="not-enabled-for-children=pids"):
        cgroup_resources.require_cgroup_v2_host(
            Host(),
            CicdResourcePolicy(
                cpu_millis=2000,
                memory_mib=4096,
                max_processes=128,
            ),
        )

    (root / "cgroup.subtree_control").write_text("cpu memory pids", encoding="ascii")
    cgroup_resources.require_cgroup_v2_host(
        Host(),
        CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=128),
    )


def test_delegated_cgroup_validation_does_not_require_global_sys_cgroup_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "delegated"
    root.mkdir()
    for name in (
        "cgroup.procs",
        "cgroup.controllers",
        "cgroup.subtree_control",
        "cpu.max",
        "memory.max",
        "pids.max",
    ):
        (root / name).write_text("", encoding="ascii")
    (root / "cgroup.controllers").write_text("cpu memory pids", encoding="ascii")
    (root / "cgroup.subtree_control").write_text("cpu memory pids", encoding="ascii")
    monkeypatch.setattr(cgroup_resources.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_resources, "_is_cgroup2_path", lambda _path: True)

    real_exists = Path.exists

    def isolated_exists(path: Path) -> bool:
        if path == Path("/sys/fs/cgroup/cgroup.controllers"):
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", isolated_exists)

    assert cgroup_resources.require_delegated_cgroup_v2_root(
        root, required_controllers={"cpu", "memory", "pids"}
    ) == root.resolve()


def test_cgroup_preflight_rejects_fake_non_cgroup_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Host:
        cgroup_root = tmp_path / "fake"
        cgroup_lock = tmp_path / "lock"
        cgroup_state_dir = tmp_path / "state"

    Host.cgroup_root.mkdir()
    monkeypatch.setattr(cgroup_resources, "_is_cgroup2_path", lambda _path: False)

    with pytest.raises(FleetPlanInvalid, match="not backed by a cgroup-v2 filesystem"):
        cgroup_resources.require_cgroup_v2_host(
            Host(), CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256)
        )


def test_restricted_egress_recovery_fails_closed_on_corrupt_metadata(tmp_path: Path) -> None:
    from ipaddress import IPv4Network

    import oh_goon.integrations._fleet_plan_restricted_egress as restricted_egress

    state_dir = tmp_path / "egress-state"
    state_dir.mkdir()
    (state_dir / "0123456789.json").write_text("{broken", encoding="utf-8")
    lease = restricted_egress.RestrictedEgressLease(
        ip="/usr/bin/ip",
        nft="/usr/bin/nft",
        namespace="uvfaaaaaaaaaa",
        host_veth="ufhaaaaaaaaaa",
        peer_veth="ufpaaaaaaaaaa",
        table="uvf_aaaaaaaaaa",
        pool=IPv4Network("10.253.0.0/29"),
        lock_path=tmp_path / "egress.lock",
        rules_file=tmp_path / "rules.nft",
        egress=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [{"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]}],
            }
        ).egress,
        state_file=state_dir / "aaaaaaaaaa.json",
    )

    with pytest.raises(FleetPlanInvalid, match="recovery metadata is unreadable"):
        restricted_egress._reconcile_stale_egress_leases(lease)


def test_restricted_egress_close_durably_retires_recovery_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ipaddress import IPv4Network

    import oh_goon.integrations._fleet_plan_restricted_egress as restricted_egress

    # Simulate a future provisioned host solely to test post-custody retirement.
    monkeypatch.setattr(restricted_egress, "require_independent_native_parent_custody",
                        lambda **_kwargs: None)
    state = tmp_path / "egress-state" / "aaaaaaaaaa.json"
    state.parent.mkdir()
    state.write_text("{}", encoding="utf-8")
    rules = tmp_path / "egress.nft"
    rules.write_text("table inet test {}", encoding="utf-8")
    retired: list[Path] = []

    def remove_durably(path: Path) -> bool:
        retired.append(path)
        path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(restricted_egress, "_cleanup_egress_resources", lambda *_args: None)
    monkeypatch.setattr(restricted_egress, "remove_file_durably", remove_durably)
    lease = restricted_egress.RestrictedEgressLease(
        ip="/usr/bin/ip",
        nft="/usr/bin/nft",
        namespace="uvfaaaaaaaaaa",
        host_veth="ufhaaaaaaaaaa",
        peer_veth="ufpaaaaaaaaaa",
        table="uvf_aaaaaaaaaa",
        pool=IPv4Network("10.253.0.0/29"),
        lock_path=tmp_path / "egress.lock",
        rules_file=rules,
        egress=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [{"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]}],
            }
        ).egress,
        state_file=state,
    )

    lease.close()

    assert not rules.exists()
    assert retired == [state]
    assert not state.exists()


def test_certification_can_enable_missing_controllers_only_inside_a_delegated_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "delegated"
    root.mkdir()
    for name in (
        "cgroup.procs",
        "cgroup.controllers",
        "cgroup.subtree_control",
        "cpu.max",
        "memory.max",
        "pids.max",
    ):
        (root / name).write_text("", encoding="ascii")
    (root / "cgroup.controllers").write_text("cpu memory pids", encoding="ascii")
    (root / "cgroup.subtree_control").write_text("cpu", encoding="ascii")
    monkeypatch.setattr(cgroup_resources.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_resources, "_is_cgroup2_path", lambda _path: True)
    writes: list[set[str]] = []

    def enable(candidate: Path, controllers: set[str]) -> None:
        writes.append(controllers)
        (candidate / "cgroup.subtree_control").write_text(
            "cpu memory pids", encoding="ascii"
        )

    monkeypatch.setattr(cgroup_resources, "_write_subtree_controllers", enable)

    assert cgroup_resources.enable_delegated_cgroup_v2_controllers(
        root, required_controllers={"cpu", "memory", "pids"}
    ) == root.resolve()
    assert writes == [{"memory", "pids"}]


def test_delegated_process_subgroup_moves_only_the_current_process_below_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mocking the kernel gate only for simulated post-custody cgroup mechanics.
    monkeypatch.setattr(cgroup_resources, "require_independent_native_parent_custody",
                        lambda **_kwargs: None)
    root = tmp_path / "delegated"
    root.mkdir()
    for name in (
        "cgroup.procs",
        "cgroup.controllers",
        "cgroup.subtree_control",
        "cpu.max",
        "memory.max",
        "pids.max",
    ):
        (root / name).write_text("", encoding="ascii")
    (root / "cgroup.controllers").write_text("cpu memory pids", encoding="ascii")
    monkeypatch.setattr(cgroup_resources.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_resources, "_is_cgroup2_path", lambda _path: True)
    moved: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        cgroup_resources,
        "_write_process_to_cgroup",
        lambda child, pid, *, expected_identity: moved.append((child, pid)),
    )

    child = cgroup_resources.place_current_process_in_delegated_cgroup_v2_subgroup(
        root,
        subgroup="oh-goon-workflow",
        required_controllers={"cpu", "memory", "pids"},
    )

    assert child == (root / "oh-goon-workflow").resolve()
    assert moved == [(child, cgroup_resources.os.getpid())]
