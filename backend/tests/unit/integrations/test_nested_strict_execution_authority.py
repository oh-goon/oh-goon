from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.integrations import _fleet_plan_cgroup_resources as cgroup_resources
from oh_goon.integrations._fleet_plan_cgroup_resources import CgroupV2AttemptLease
from oh_goon.integrations._fleet_plan_runtime_root import _create_mount_scaffold
from oh_goon.integrations import fleet_plan_bubblewrap_isolation as bubblewrap_isolation
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
    BubblewrapProcessIsolation,
    _bubblewrap_prefix,
)
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    OpenSandboxProcessIsolation,
    OpenSandboxRuntimeKind,
    OpenSandboxSettings,
)


class _Transport:
    def require_available(self, settings) -> None:
        del settings

    def execute(self, *args, **kwargs):
        raise AssertionError("nested strict execution must fail before transport")


def _opensandbox() -> OpenSandboxProcessIsolation:
    fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox"
    return OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain="sandbox.internal",
            api_key="test",
            image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
            cli_executable=sys.executable,
            runtime_attestation_file=str(fixtures / "kata-runtime-attestation.json"),
            runtime_attestation_public_key_file=str(
                fixtures / "runtime-attestation-ed25519.pub.pem"
            ),
            runtime=OpenSandboxRuntimeKind.KATA,
            protocol="https",
        ),
        transport=_Transport(),
    )


def test_nested_attempt_delegates_only_an_attempt_bounded_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Host:
        cgroup_root = tmp_path / "delegated"
        cgroup_lock = tmp_path / "lock"
        cgroup_state_dir = tmp_path / "state"

    # HISTORICAL FIXTURE ONLY: this explicitly simulates a provisioned host
    # to exercise nested argument/limit logic. It is NOT independent Linux
    # custody, native admission proof, or an authorization path in production.
    monkeypatch.setattr(
        cgroup_resources, 'require_independent_native_parent_custody',
        lambda **_kwargs: None,
    )

    Host.cgroup_root.mkdir()
    monkeypatch.setattr(
        cgroup_resources,
        "require_cgroup_v2_host",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cgroup_resources, "_process_identity", lambda _pid: "boot:1")
    monkeypatch.setattr(cgroup_resources, "_reconcile_stale_cgroups", lambda *_args: None)
    original_write = cgroup_resources._write_limit

    def write_limit(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        original_write(path, value)

    enabled: list[tuple[Path, set[str]]] = []

    def enable(root: Path, *, required_controllers: set[str], expected_identity: tuple[int, int] | None = None) -> Path:
        enabled.append((root, required_controllers))
        return root

    # Real cgroupfs materializes controller files as soon as mkdir succeeds.
    native_mkdir = Path.mkdir
    def materialize(group: Path, *args: object, **kwargs: object) -> None:
        native_mkdir(group, *args, **kwargs)
        if group.name.startswith("attempt-"):
            for name in ("cpu.max", "memory.max", "memory.swap.max", "pids.max"):
                (group / name).write_text("", encoding="ascii")
    monkeypatch.setattr(Path, "mkdir", materialize)
    monkeypatch.setattr(cgroup_resources, "_write_limit", write_limit)
    monkeypatch.setattr(cgroup_resources, "enable_delegated_cgroup_v2_controllers", enable)

    attempt = tmp_path / "attempt"
    attempt.mkdir()
    lease = CgroupV2AttemptLease.create(
        Host(),
        attempt,
        CicdResourcePolicy(cpu_millis=1500, memory_mib=1024, max_processes=64),
        nested_strict_execution=True,
    )
    lease.open()

    assert lease.nested_delegation == lease.cgroup / "nested-execution"
    assert lease.nested_delegation.is_dir()
    assert lease.nested_monitor == lease.cgroup / "nested-monitor"
    assert lease.nested_monitor.is_dir()
    assert enabled == [(lease.cgroup, {"cpu", "memory", "pids"})]
    assert lease.command_prefix()[-7:] == (
        str(lease.nested_delegation), str(lease.nested_monitor),
        *(str(v) for v in (*lease.nested_identity, *lease.monitor_identity)), "--",
    )


def test_nested_strict_execution_delegates_controller_authority_without_imposing_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Host:
        cgroup_root = tmp_path / "delegated"
        cgroup_lock = tmp_path / "lock"
        cgroup_state_dir = tmp_path / "state"

    # HISTORICAL FIXTURE ONLY: this explicitly simulates a provisioned host
    # to exercise nested argument/limit logic. It is NOT independent Linux
    # custody, native admission proof, or an authorization path in production.
    monkeypatch.setattr(
        cgroup_resources, 'require_independent_native_parent_custody',
        lambda **_kwargs: None,
    )

    Host.cgroup_root.mkdir()
    required_at_host: list[set[str]] = []
    enabled: list[set[str]] = []
    writes: list[tuple[str, str]] = []

    def require_host(_backend, _policy, *, required_controllers: set[str] | None = None):
        required_at_host.append(set(required_controllers or set()))

    def enable(_root: Path, *, required_controllers: set[str], expected_identity: tuple[int, int] | None = None) -> Path:
        enabled.append(set(required_controllers))
        return _root

    def write_limit(path: Path, value: str) -> None:
        writes.append((path.name, value))

    monkeypatch.setattr(cgroup_resources, "require_cgroup_v2_host", require_host)
    monkeypatch.setattr(cgroup_resources, "enable_delegated_cgroup_v2_controllers", enable)
    monkeypatch.setattr(cgroup_resources, "_write_limit", write_limit)
    monkeypatch.setattr(cgroup_resources, "_process_identity", lambda _pid: "boot:1")
    monkeypatch.setattr(cgroup_resources, "_reconcile_stale_cgroups", lambda *_args: None)

    attempt = tmp_path / "attempt"
    attempt.mkdir()
    lease = CgroupV2AttemptLease.create(
        Host(),
        attempt,
        CicdResourcePolicy(),
        nested_strict_execution=True,
    )
    lease.open()

    assert required_at_host == [{"cpu", "memory", "pids"}]
    assert enabled == [{"cpu", "memory", "pids"}]
    assert writes == []


def test_nested_attempt_cleanup_removes_descendant_cgroups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fixture-only post-custody cleanup simulation; never an actual kernel permit.
    monkeypatch.setattr(cgroup_resources, 'require_independent_native_parent_custody', lambda **_kw: None)
    root = tmp_path / "attempt"
    (root / "nested-execution" / "oh-goon-nested-process").mkdir(parents=True)

    cgroup_resources._remove_cgroup(root)

    assert not root.exists()


def test_runtime_root_scaffolds_only_an_empty_cgroup_mountpoint(tmp_path: Path) -> None:
    _create_mount_scaffold(
        tmp_path,
        directory_mount_targets=(),
        file_mount_targets=(),
    )

    mountpoint = tmp_path / "sys" / "fs" / "cgroup"
    assert mountpoint.is_dir()
    assert tuple(mountpoint.iterdir()) == ()


def test_nested_bubblewrap_requires_a_real_cgroup_namespace() -> None:
    ordinary = _bubblewrap_prefix("/usr/bin/bwrap", egress_mode="none")
    nested = _bubblewrap_prefix(
        "/usr/bin/bwrap", egress_mode="none", require_cgroup_namespace=True
    )

    assert "--unshare-cgroup-try" in ordinary
    assert "--unshare-cgroup" not in ordinary
    assert "--unshare-cgroup" in nested
    assert "--unshare-cgroup-try" not in nested


def test_nested_bubblewrap_projects_only_the_delegated_cgroup_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    (runtime_root / "sys" / "fs" / "cgroup").mkdir(parents=True)
    nested = tmp_path / "delegated" / "nested-execution"
    nested.mkdir(parents=True)
    paths = SimpleNamespace(
        root=tmp_path,
        etc=tmp_path / "etc",
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        tmp=tmp_path / "tmp",
        cache=tmp_path / "cache",
        config=tmp_path / "config",
    )
    for path in (paths.etc, paths.workspace, paths.home, paths.tmp, paths.cache, paths.config):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("passwd", "group", "nsswitch.conf", "hosts"):
        (paths.etc / name).touch()
    request = SimpleNamespace(
        attempt=SimpleNamespace(
            runtime_root=SimpleNamespace(root=runtime_root),
            egress=SimpleNamespace(mode="none"),
            paths=paths,
            sandbox_bindings=(),
            host_tools=(),
        ),
        read_only_workspace=None,
    )
    monkeypatch.setattr(bubblewrap_isolation, "verify_runtime_root", lambda _root: None)
    monkeypatch.setattr(bubblewrap_isolation, "_append_managed_inputs", lambda *_args: None)
    monkeypatch.setattr(bubblewrap_isolation, "_append_toolchain", lambda *_args: None)
    monkeypatch.setattr(bubblewrap_isolation, "_append_atomic_execution_mounts", lambda *_args: None)

    args = bubblewrap_isolation._prepare_mounts(
        "/usr/bin/bwrap",
        request,
        atomic_authority=SimpleNamespace(bubblewrap_bindings=()),
        network_prefix=(),
        nested_cgroup_root=nested,
    )

    mount = ("--bind", str(nested), "/sys/fs/cgroup")
    assert tuple(args[args.index("--bind", args.index("--ro-bind") + 1):][:3]) == mount
    assert "--unshare-cgroup" in args
    assert "--unshare-cgroup-try" not in args


def test_nested_bubblewrap_marks_only_the_inner_delegation_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = BubblewrapProcessIsolation(executable="/usr/bin/bwrap")
    request = SimpleNamespace()
    launch = SimpleNamespace(cwd="/workspace", argv=("make", "check"), env={"BASE": "1"})
    delegated = Path("/delegated/nested-execution")
    monkeypatch.setattr(bubblewrap_isolation, "_prepare_mounts", lambda *_args, **_kwargs: ["bwrap"])

    argv, env = backend._command(
        request,
        launch=launch,
        atomic_authority=SimpleNamespace(),
        network_prefix=(),
        nested_cgroup_root=delegated,
    )

    assert argv[-4:] == ("--chdir", "/workspace", "--", "make", "check")[-4:]
    assert env["BASE"] == "1"
    assert env[cgroup_resources.NESTED_CGROUP_DELEGATED_ENV] == "1"
    assert env[cgroup_resources.NESTED_CGROUP_SUBGROUP_ENV] == (
        cgroup_resources.NESTED_CGROUP_PROCESS_SUBGROUP
    )


def test_opensandbox_rejects_nested_strict_execution_before_transport() -> None:
    plan = ResolvedFleetPlan.create(
        name="nested-strict",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=False,
            resources=CicdResourcePolicy(max_processes=None),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(nested_strict_execution=True),
            ),
        ),
    )

    with pytest.raises(FleetPlanInvalid, match="nested strict execution delegation"):
        _opensandbox().require_available(plan)
