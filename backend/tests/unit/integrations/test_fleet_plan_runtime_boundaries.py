from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    PlanArtifactDeclaration,
    ResolvedCommandStep,
    ResolvedFleetPlan,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    isolation_backend_evidence,
)
from tests.factories.bubblewrap_command import prepared_bubblewrap_command as _prepared
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    PhysicalFleetPlanExecutorHarness,
    prepare_physical_test_run,
    strict_test_plan,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY


class _UnitProcessIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="unit-strict",
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )

    def configuration_identity(self) -> str:
        return "process-isolation-backend-unit-strict"

    def require_available(self, plan) -> None:
        return None

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        import oh_goon.integrations.fleet_plan_step_execution as step_execution

        assert request.attempt.resources is not None
        write_remote_resource_evidence(request.attempt.paths.root, request.attempt.resources)
        return step_execution.run_command(
            *request.argv,
            timeout=timeout,
            cwd=Path(request.cwd),
            env=dict(request.env),
            cancel_requested=cancel_requested,
        )


def _cicd(*steps: ResolvedCommandStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="runtime-boundaries",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=steps,
    )


def _executor(store: FleetContentStore | None = None) -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(),
        process_isolation_backends=(_UnitProcessIsolation(),),
        content_store=store,
    )


def test_explicit_strict_execution_rejects_direct_plan(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="mode", argv=("true",))
    executor = _executor()
    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        _cicd(step),
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    direct_plan = ResolvedFleetPlan.create(
        name="direct-mode",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
    )
    try:
        with pytest.raises(
            FleetPlanInvalid,
            match="Non-CI/CD Fleet plans cannot consume strict execution authority",
        ):
            executor.execute_strict_run(repository, direct_plan, prepared, cancel_requested=None)
    finally:
        prepared.close()


def test_runtime_root_tamper_is_rejected_before_process_launch(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="strict",
        argv=("true",),
        capabilities=PlanStepCapabilities(tools=("sh",)),
    )
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        (attempt.runtime_root.root / "tampered-after-binding").write_text(
            "changed", encoding="utf-8"
        )
        with pytest.raises(FleetPlanInvalid, match="runtime root"):
            _prepared(
                BubblewrapProcessIsolation(executable="/usr/bin/true"),
                IsolatedProcessRequest(
                    attempt=attempt,
                    argv=step.argv,
                    cwd=attempt.paths.workspace,
                    env={},
                ),
            )


def test_kernel_plan_exposes_no_ambient_executable_directories(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="strict", argv=("true",))
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        prepared = _prepared(
            BubblewrapProcessIsolation(executable="/usr/bin/true"),
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={},
            ),
        )

    command = " ".join(prepared.argv)
    assert "--ro-bind /usr /usr" not in command
    assert "--ro-bind /bin /bin" not in command
    assert "--ro-bind /sbin /sbin" not in command
    assert "--ro-bind /lib /lib" not in command
    assert "--ro-bind /usr/lib /usr/lib" not in command
    assert str(attempt.runtime_root.root) in prepared.argv
    runtime_index = prepared.argv.index(str(attempt.runtime_root.root))
    assert prepared.argv[runtime_index + 1] == "/"
    assert prepared.env["PATH"] == "/toolchain/bin"


def test_frozen_runtime_root_owns_mountpoints_for_attempt_etc_and_toolchain_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="strict",
        argv=("true",),
        capabilities=PlanStepCapabilities(tools=("sh",)),
    )
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        runtime_root_path = attempt.runtime_root.root

        for name in ("passwd", "group", "nsswitch.conf", "hosts"):
            assert (runtime_root_path / "etc" / name).is_file()
        assert (runtime_root_path / "toolchain" / "bin" / "true").is_file()
        assert (runtime_root_path / "toolchain" / "bin" / "sh").is_file()
        assert (runtime_root_path / "bin" / "sh").is_file()


def test_step_child_tool_capabilities_reject_paths_duplicates_and_shell_syntax() -> None:
    for payload in (("../git",), ("git", "git"), ("uv", "git"), ("NAME=value",)):
        with pytest.raises(ValidationError, match="tool"):
            PlanStepCapabilities(tools=payload)


def test_cidr_egress_contract_rejects_noncanonical_and_ipv6_destinations() -> None:
    with pytest.raises(ValidationError, match="canonical network form"):
        PlanStepCapabilities.model_validate(
            {
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [{"cidr": "10.20.30.5/24", "protocol": "tcp", "ports": [443]}],
                }
            }
        )
    with pytest.raises(ValidationError, match="IPv4 CIDRs only"):
        PlanStepCapabilities.model_validate(
            {
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [{"cidr": "2001:db8::/32", "protocol": "tcp", "ports": [443]}],
                }
            }
        )


def test_cidr_egress_cannot_add_host_docker_authority() -> None:
    with pytest.raises(ValidationError):
        PlanStepCapabilities.model_validate(
            {
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [{"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]}],
                },
                "docker_socket": "/run/declared-docker.sock",
            }
        )


def test_cidr_egress_builds_kernel_allowlist_and_runs_bwrap_inside_netns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.integrations._fleet_plan_restricted_egress as restricted_egress
    import oh_goon.integrations.fleet_plan_bubblewrap_isolation as isolation

    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="restricted",
        argv=("true",),
        capabilities=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [
                    {"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]},
                    {"cidr": "198.51.100.10/32", "protocol": "udp", "ports": [123]},
                ],
            }
        ),
    )
    captured: list[tuple[str, ...]] = []
    nft_rules: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        captured.append(tuple(argv))
        if "-f" in argv:
            nft_rules.append(Path(argv[argv.index("-f") + 1]).read_text(encoding="utf-8"))
        # B12B requires a fresh, native-shaped *absence* observation after delete.
        # Exit 0 alone is deliberately NOT cleanup proof.
        output = ""
        if "-j" in argv and argv[0] == "/usr/bin/true":
            output = (
                '{"nftables":[{"metainfo":{"version":"1"}}]}'
                if "nft" in argv or "tables" in argv
                else "[]"
            )
        return CommandResult(argv=tuple(argv), returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(isolation, "run_command", fake_run)
    monkeypatch.setattr(isolation, "require_bubblewrap_fd_binding", lambda _executable: None)
    monkeypatch.setattr(restricted_egress, "run_command", fake_run)
    monkeypatch.setattr(
        restricted_egress, "require_independent_native_parent_custody", lambda **_kw: None
    )
    monkeypatch.setattr(restricted_egress, "require_restricted_egress_host", lambda backend: None)
    monkeypatch.setattr(
        isolation.CgroupV2AttemptLease,
        "create",
        lambda *_args, **_kwargs: SimpleNamespace(
            open=lambda: None, command_prefix=lambda: (), close=lambda: None
        ),
    )
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        backend = BubblewrapProcessIsolation(
            executable="/usr/bin/true",
            ip_executable="/usr/bin/true",
            nft_executable="/usr/bin/true",
            egress_pool="10.254.0.0/24",
            egress_lock=tmp_path / "egress.lock",
            egress_state_dir=tmp_path / "egress-state",
        )
        attempt = replace(
            attempt,
            isolation_backend=isolation_backend_evidence(backend),
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        )
        result = backend.execute(
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={},
            ),
            timeout=10.0,
            cancel_requested=None,
        )

    assert result.returncode == 0
    main = next(command for command in captured if "--die-with-parent" in command)
    assert main[1:3] == ("netns", "exec")
    assert "--unshare-net" not in main
    rules = nft_rules[0]
    assert "203.0.113.0/24 tcp dport { 443 } accept" in rules
    assert "198.51.100.10/32 udp dport { 123 } accept" in rules
    assert "chain input" in rules
    assert "chain forward" in rules
    assert "policy accept" in rules
    assert rules.count('iifname "ufh') >= 4
    assert rules.count("drop") >= 2
    assert "masquerade" in rules


def test_cidr_egress_attempt_evidence_is_content_identified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="restricted",
        argv=("true",),
        capabilities=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [{"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]}],
            }
        ),
    )

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr=""),
    )
    result = _executor().execute(repository, _cicd(step), cancel_requested=None)
    egress = result.steps[0].attempts[0].egress

    assert egress.mode == "cidr_allowlist"
    assert egress.rules[0].cidr == "203.0.113.0/24"
    assert egress.policy_id.startswith("plan-egress-")


def test_retained_artifact_is_published_as_immutable_file_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    store = FleetContentStore(tmp_path / "content-store")
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="report",
        argv=("true",),
        artifacts=(PlanArtifactDeclaration(path="dist/report.txt", retention="content_store"),),
    )

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        workspace = Path(kwargs["cwd"])
        path = workspace / "dist" / "report.txt"
        path.parent.mkdir(parents=True)
        path.write_text("portable artifact", encoding="utf-8")
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    executor = PhysicalFleetPlanExecutorHarness(
        GitService(),
        process_isolation_backends=(_UnitProcessIsolation(),),
        content_store=store,
    )
    result = executor.execute(repository, _cicd(step), cancel_requested=None)
    artifact = result.steps[0].artifacts[0]

    assert artifact.status == "PRESENT"
    assert artifact.retained_object is not None
    assert artifact.retained_object.object_id.startswith("file-sha256-")
    stored = store.get(
        artifact.retained_object.reference_id,
        owner_repository_id=RepositoryIdentity(path=repository).repository_id,
        expected_kind="file",
    )
    assert stored.path.read_text(encoding="utf-8") == "portable artifact"


def test_parallel_restricted_egress_allocator_skips_existing_host_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ipaddress import IPv4Network

    import oh_goon.integrations._fleet_plan_restricted_egress as restricted_egress

    routes = ""

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        return CommandResult(argv=tuple(argv), returncode=0, stdout=routes, stderr="")

    monkeypatch.setattr(restricted_egress, "run_command", fake_run)
    pool = IPv4Network("10.253.0.0/29")
    first = restricted_egress._select_free_link_subnet(
        "/usr/bin/ip", pool, seed="same-attempt", cancel_requested=None
    )
    routes = f"{first} dev existing proto kernel scope link\n"
    second = restricted_egress._select_free_link_subnet(
        "/usr/bin/ip", pool, seed="same-attempt", cancel_requested=None
    )

    assert second != first
    assert not second.overlaps(first)


def test_dead_owner_identity_alone_cannot_authorize_network_orphan_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from ipaddress import IPv4Network

    import oh_goon.integrations._fleet_plan_restricted_egress as restricted_egress

    state_dir = tmp_path / "egress-state"
    state_dir.mkdir()
    suffix = "0123456789"
    state = state_dir / f"{suffix}.json"
    state.write_text(
        json.dumps(
            {
                "pid": 4242,
                "process_identity": "old-boot:123",
                "namespace": f"uvf{suffix}",
                "host_veth": f"ufh{suffix}",
                "table": f"uvf_{suffix}",
            }
        ),
        encoding="utf-8",
    )
    commands: list[tuple[str, ...]] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        commands.append(tuple(argv))
        observations = {
            ("-j", "netns", "list"): "[]",
            ("-j", "link", "show"): "[]",
            ("-j", "list", "tables"): '{"nftables":[]}',
        }
        return CommandResult(
            argv=tuple(argv), returncode=0, stdout=observations.get(argv[1:], ""), stderr=""
        )

    retired: list[Path] = []

    def remove_durably(path: Path) -> bool:
        retired.append(path)
        path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(restricted_egress, "run_command", fake_run)
    monkeypatch.setattr(restricted_egress, "_process_identity", lambda pid: None)
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
        rules_file=tmp_path / "rules.nft",
        egress=PlanStepCapabilities(
            egress={
                "mode": "cidr_allowlist",
                "rules": [{"cidr": "203.0.113.0/24", "protocol": "tcp", "ports": [443]}],
            }
        ).egress,
        state_file=state_dir / "aaaaaaaaaa.json",
    )

    before = state.read_bytes()
    with pytest.raises(FleetPlanInvalid, match="lacks independent physical custody"):
        restricted_egress._reconcile_stale_egress_leases(lease)
    assert retired == [] and commands == []
    assert state.read_bytes() == before


def test_hostname_egress_binds_dns_once_into_effective_cidrs_and_private_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import oh_goon.integrations._fleet_plan_egress_binding as egress_binding

    repository = tmp_path / "repo-hostname"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="download",
        argv=("true",),
        capabilities=PlanStepCapabilities(
            egress={
                "mode": "hostname_allowlist",
                "rules": [{"hostname": "packages.example.com", "protocol": "tcp", "ports": [443]}],
            },
        ),
    )
    plan = _cicd(step)
    calls: list[str] = []

    def fake_getaddrinfo(host: str, *_args, **_kwargs):
        calls.append(host)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.20", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.20", 0)),
        ]

    monkeypatch.setattr(egress_binding.socket, "getaddrinfo", fake_getaddrinfo)
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(plan.steps[0], 1)
        evidence = session.execution_input_evidence()

        assert calls == ["packages.example.com"]
        assert attempt.egress.mode == "cidr_allowlist"
        assert [rule.cidr for rule in attempt.egress.rules] == [
            "203.0.113.10/32",
            "203.0.113.20/32",
        ]
        hosts = (attempt.paths.etc / "hosts").read_text(encoding="utf-8")
        assert "203.0.113.10 packages.example.com" in hosts
        assert "203.0.113.20 packages.example.com" in hosts
        assert (attempt.paths.etc / "nsswitch.conf").read_text(encoding="utf-8") == "hosts: files\n"
        assert evidence.hostname_resolutions[0].hostname == "packages.example.com"
        assert evidence.hostname_resolutions[0].addresses == (
            "203.0.113.10",
            "203.0.113.20",
        )


def test_hostname_egress_contract_rejects_noncanonical_names() -> None:
    with pytest.raises(ValidationError, match="canonical lowercase"):
        PlanStepCapabilities.model_validate(
            {
                "egress": {
                    "mode": "hostname_allowlist",
                    "rules": [
                        {"hostname": "Packages.Example.com", "protocol": "tcp", "ports": [443]}
                    ],
                }
            }
        )


def test_hostname_egress_attempt_evidence_records_effective_bound_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import oh_goon.integrations._fleet_plan_egress_binding as egress_binding

    repository = tmp_path / "repo-hostname-evidence"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="networked",
        argv=("true",),
        capabilities=PlanStepCapabilities(
            egress={
                "mode": "hostname_allowlist",
                "rules": [{"hostname": "api.example.com", "protocol": "tcp", "ports": [443]}],
            },
        ),
    )

    monkeypatch.setattr(
        egress_binding.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.9", 0))
        ],
    )
    result = _executor().execute(repository, _cicd(step), cancel_requested=None)
    egress = result.steps[0].attempts[0].egress

    assert egress.mode == "hostname_allowlist"
    assert egress.rules[0].cidr == "198.51.100.9/32"
    assert egress.rules[0].ports == (443,)


def test_hostname_resolution_changes_execution_input_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import oh_goon.integrations._fleet_plan_egress_binding as egress_binding

    repository = tmp_path / "repo-hostname-identity"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="networked",
        argv=("true",),
        capabilities=PlanStepCapabilities(
            egress={
                "mode": "hostname_allowlist",
                "rules": [{"hostname": "api.example.com", "protocol": "tcp", "ports": [443]}],
            },
        ),
    )
    plan = _cicd(step)
    answer = "198.51.100.9"

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, 0))]

    monkeypatch.setattr(egress_binding.socket, "getaddrinfo", fake_getaddrinfo)
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as first:
        first_id = first.execution_input_evidence().input_id
    answer = "198.51.100.10"
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as second:
        second_id = second.execution_input_evidence().input_id

    assert first_id != second_id


def test_sealed_attempt_outputs_rejects_cross_attempt_publication(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.integrations.fleet_plan_run import SealedAttemptOutputs

    first = tmp_path / "a"
    second = tmp_path / "b"
    for root in (first, second):
        for name in ("workspace", "cache"):
            (root / name).mkdir(parents=True, exist_ok=True)
    token = SealedAttemptOutputs(
        attempt_root=first.resolve(),
        workspace=(first / "workspace").resolve(),
        cache=(first / "cache").resolve(),
        _authority=object(),
    )
    other = SimpleNamespace(
        paths=SimpleNamespace(
            root=second,
            workspace=second / "workspace",
            cache=second / "cache",
        ),
        _output_publication_authority=object(),
    )
    with pytest.raises(FleetPlanInvalid, match="does not match physical attempt"):
        token.prove_for(other)


def test_strict_preparation_cancellation_callback_does_not_escape_into_execution(
    tmp_path: Path,
) -> None:
    """Preparation-only observers must never survive into retained strict-run resources.

    Certification binds preparation to a persistent read-only SQLite observer that closes
    before workload execution. Retaining that callback recreates the production failure:
    later source/cache snapshots call into a closed SQLite connection.
    """

    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "payload.txt").write_text("payload", encoding="utf-8")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="strict", argv=("true",))
    executor = _executor()
    preparation_live = True
    preparation_calls = 0
    execution_calls = 0

    def cancel_requested() -> bool:
        nonlocal preparation_calls
        preparation_calls += 1
        if not preparation_live:
            raise RuntimeError("preparation cancellation observer already closed")
        return False

    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        _cicd(step),
        cancel_requested=cancel_requested,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    assert preparation_calls > 0
    preparation_live = False

    def execution_cancel_requested() -> bool:
        nonlocal execution_calls
        execution_calls += 1
        return False

    try:
        outcome = executor.execute_strict_run(
            repository,
            _cicd(step),
            prepared,
            cancel_requested=execution_cancel_requested,
        )
        assert outcome.steps
        assert execution_calls > 0
    finally:
        prepared.close()


def test_callback_lifetime_regression_proof_goes_red_when_boundary_is_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation proof: restoring both historical retention defects must be detected."""
    from oh_goon.integrations._fleet_plan_run_lineage import _RunLineage
    from oh_goon.integrations.fleet_plan_run import PreparedPlanRun

    repository = tmp_path / "repo-mutation"
    seed_cicd_repository(repository)
    (repository / "payload.txt").write_text("payload", encoding="utf-8")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="strict", argv=("true",))
    executor = _executor()
    preparation_live = True

    def cancel_requested() -> bool:
        if not preparation_live:
            raise RuntimeError("mutated boundary retained closed preparation observer")
        return False

    # Reintroduce the two pieces of the historical defect: preparation authority is
    # retained, and execution does not replace it with the live execution callback.
    monkeypatch.setattr(
        _RunLineage, "clear_preparation_cancellation_observation", lambda self: None
    )
    monkeypatch.setattr(
        PreparedPlanRun,
        "bind_execution_cancel_requested",
        lambda self, cancel_requested: None,
    )

    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        _cicd(step),
        cancel_requested=cancel_requested,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    preparation_live = False
    try:
        with pytest.raises(
            RuntimeError, match="mutated boundary retained closed preparation observer"
        ):
            executor.execute_strict_run(
                repository,
                _cicd(step),
                prepared,
                cancel_requested=lambda: False,
            )
    finally:
        prepared.close()
