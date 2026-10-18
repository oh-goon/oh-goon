from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import ExecutorPlacementLost, FleetPlanInvalid
from oh_goon.core.models.fleet_executor import (
    ExecutorCapacityEvidenceInput,
    FleetExecutorIdentity,
    FleetExecutorPlacement,
    PlanExecutorCapacityEvidence,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
    IsolationThreatModel,
    isolation_requirements,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    CidrAllowlistPlanEgress,
    CidrPlanEgressRule,
    PlanStepCapabilities,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.command import CommandCancelledError, CommandResult
from oh_goon.integrations import _fleet_plan_opensandbox_transport as opensandbox_transport
from oh_goon.integrations._fleet_plan_opensandbox_transport import (
    OpenSandboxCliTransport,
    _create_sandbox,
    _osb_environment,
    _run_osb,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    OpenSandboxProcessIsolation,
    OpenSandboxRuntimeKind,
    OpenSandboxSettings,
)
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    isolation_backend_evidence,
    select_bound_process_isolation_backend,
)
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.integrations.fleet_plan_runtime_authority import reconcile_runtime_authority
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import prepare_physical_test_run
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.journal import admit_run
from tests.factories.path_authority import TEST_PATH_AUTHORITY

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox"


class _AvailableBackend:
    def __init__(
        self,
        capabilities: IsolationBackendCapabilities,
        *,
        available: bool = True,
        configuration: str | None = None,
    ):
        self.capabilities = capabilities
        self.available = available
        self.configuration = configuration or capabilities.backend_id

    def configuration_identity(self) -> str:
        return f"process-isolation-backend-{self.configuration}"

    def require_available(self, plan) -> None:
        if not self.available:
            raise FleetPlanInvalid(f"{self.capabilities.backend_id} unavailable")

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        raise AssertionError("selection tests do not execute")


class _OpenSandboxTransport:
    def __init__(self) -> None:
        self.requests = []
        self.availability_checks = 0

    def require_available(self, settings) -> None:
        self.availability_checks += 1

    def execute(self, settings, request, *, expected_runtime_authority, timeout, cancel_requested):
        del settings, timeout, cancel_requested
        self.requests.append(request)
        return CommandResult(argv=request.argv, returncode=0, stdout="", stderr="", runtime_authority=expected_runtime_authority)


def _plan(
    threat_model: IsolationThreatModel,
    *,
    capabilities: PlanStepCapabilities | None = None,
) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="isolation-contract",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=(threat_model is IsolationThreatModel.TRUSTED_REPOSITORY),
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="check",
                argv=("true",),
                capabilities=capabilities or PlanStepCapabilities(),
            ),
        ),
    )


def _capabilities(
    backend_id: str,
    boundary: IsolationKernelBoundary,
    *,
    network: bool = True,
) -> IsolationBackendCapabilities:
    return IsolationBackendCapabilities(
        backend_id=backend_id,
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=network,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
        kernel_boundary=boundary,
    )


def _settings(runtime: str) -> OpenSandboxSettings:
    attestation = "firecracker-runtime-attestation.json" if runtime == OpenSandboxRuntimeKind.FIRECRACKER else "kata-runtime-attestation.json"
    return OpenSandboxSettings(
        domain="sandbox.internal",
        api_key="test-key",
        image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
        cli_executable=sys.executable,
        runtime_attestation_file=str(_FIXTURES / attestation),
        runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
        runtime=runtime,
        protocol="https",
    )


def test_trust_repository_code_compiles_to_backend_requirements() -> None:
    assert (
        isolation_requirements(True).minimum_kernel_boundary
        is IsolationKernelBoundary.SHARED_HOST_KERNEL
    )
    assert (
        isolation_requirements(False).minimum_kernel_boundary
        is IsolationKernelBoundary.APPLICATION_KERNEL
    )


def test_bubblewrap_is_reference_backend_for_trusted_repository_only() -> None:
    capabilities = BubblewrapProcessIsolation.capabilities

    assert capabilities.satisfies(isolation_requirements(True))
    assert not capabilities.satisfies(isolation_requirements(False))


def test_bound_backend_selection_never_falls_back_after_run_admission() -> None:
    plan = _plan(IsolationThreatModel.TRUSTED_REPOSITORY)
    admitted = _AvailableBackend(
        _capabilities("shared", IsolationKernelBoundary.SHARED_HOST_KERNEL),
        configuration="admitted",
    )
    expected = isolation_backend_evidence(admitted)
    drifted = _AvailableBackend(
        _capabilities("shared", IsolationKernelBoundary.SHARED_HOST_KERNEL),
        configuration="changed",
    )
    fallback = _AvailableBackend(
        _capabilities("fallback", IsolationKernelBoundary.SHARED_HOST_KERNEL)
    )

    with pytest.raises(FleetPlanInvalid, match="admitted before RUN_ADMITTED"):
        select_bound_process_isolation_backend(plan, expected, (drifted, fallback))


def test_bound_backend_selection_rechecks_availability_without_reselection() -> None:
    plan = _plan(IsolationThreatModel.TRUSTED_REPOSITORY)
    admitted = _AvailableBackend(
        _capabilities("shared", IsolationKernelBoundary.SHARED_HOST_KERNEL),
        configuration="stable",
    )
    expected = isolation_backend_evidence(admitted)
    admitted.available = False
    fallback = _AvailableBackend(
        _capabilities("fallback", IsolationKernelBoundary.SHARED_HOST_KERNEL)
    )

    with pytest.raises(FleetPlanInvalid, match="shared unavailable"):
        select_bound_process_isolation_backend(plan, expected, (admitted, fallback))


def test_opensandbox_backend_is_vm_only_and_rejects_gvisor_for_deny_all_egress() -> None:
    with pytest.raises(FleetPlanInvalid, match="requires Kata or Firecracker"):
        _settings(OpenSandboxRuntimeKind.GVISOR).validate()
    kata = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.KATA), transport=_OpenSandboxTransport()
    )
    firecracker = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.FIRECRACKER), transport=_OpenSandboxTransport()
    )
    assert kata.capabilities.kernel_boundary is IsolationKernelBoundary.VIRTUAL_MACHINE
    assert firecracker.capabilities.kernel_boundary is IsolationKernelBoundary.VIRTUAL_MACHINE
    assert kata.capabilities.enforced_network_policy is True
    assert kata.capabilities.satisfies(
        isolation_requirements(IsolationThreatModel.HOSTILE_GENERATED_CODE)
    )


def test_experimental_opensandbox_backend_accepts_only_deny_all_egress() -> None:
    backend = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.KATA), transport=_OpenSandboxTransport()
    )
    restricted = PlanStepCapabilities(
        egress=CidrAllowlistPlanEgress(
            rules=(CidrPlanEgressRule(cidr="203.0.113.7/32", protocol="tcp", ports=(443,)),)
        ),
    )

    with pytest.raises(FleetPlanInvalid, match="egress.mode=none only"):
        backend.require_available(
            _plan(IsolationThreatModel.HOSTILE_GENERATED_CODE, capabilities=restricted)
        )


def test_opensandbox_runner_image_must_be_content_pinned() -> None:
    settings = OpenSandboxSettings(
        domain="sandbox.internal",
        api_key="test",
        image="registry.internal/oh-goon-runner:latest",
        cli_executable=sys.executable,
        runtime_attestation_file=str(_FIXTURES / "kata-runtime-attestation.json"),
        runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
        runtime=OpenSandboxRuntimeKind.KATA,
        protocol="https",
    )

    with pytest.raises(FleetPlanInvalid, match="digest-pinned"):
        settings.validate()


def test_backend_configuration_identity_changes_when_bubblewrap_bytes_change(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "bwrap"
    executable.write_bytes(b"first")
    backend = BubblewrapProcessIsolation(executable=str(executable))
    first = isolation_backend_evidence(backend)

    executable.write_bytes(b"second")
    second = isolation_backend_evidence(backend)

    assert first.backend_id == second.backend_id == "bubblewrap"
    assert first.configuration_id != second.configuration_id


def test_opensandbox_configuration_identity_excludes_api_key_but_binds_runner_image() -> None:
    first = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.KATA), transport=_OpenSandboxTransport()
    )
    rotated_key = OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain=first.settings.domain,
            api_key="rotated-secret",
            image=first.settings.image,
            cli_executable=first.settings.cli_executable,
            runtime_attestation_file=first.settings.runtime_attestation_file,
            runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
            runtime=first.settings.runtime,
            protocol=first.settings.protocol,
        ),
        transport=_OpenSandboxTransport(),
    )
    different_image = OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain=first.settings.domain,
            api_key=first.settings.api_key,
            image="registry.internal/oh-goon-runner@sha256:" + "2" * 64,
            cli_executable=first.settings.cli_executable,
            runtime_attestation_file=first.settings.runtime_attestation_file,
            runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
            runtime=first.settings.runtime,
            protocol=first.settings.protocol,
        ),
        transport=_OpenSandboxTransport(),
    )

    assert (
        isolation_backend_evidence(first).configuration_id
        == isolation_backend_evidence(rotated_key).configuration_id
    )
    assert (
        isolation_backend_evidence(first).configuration_id
        != isolation_backend_evidence(different_image).configuration_id
    )


def test_opensandbox_rechecks_server_before_each_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    transport = _OpenSandboxTransport()
    backend = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.KATA), transport=transport
    )
    plan = _plan(IsolationThreatModel.HOSTILE_GENERATED_CODE)
    step = plan.steps[0]
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=isolation_backend_evidence(backend),
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true",),
            cwd=attempt.paths.workspace,
            env={},
        )
        backend.execute(request, timeout=1.0, cancel_requested=None)

    assert transport.availability_checks == 1
    assert len(transport.requests) == 1


def test_opensandbox_loss_after_admission_maps_to_executor_placement_lost(tmp_path: Path) -> None:
    class LostTransport(_OpenSandboxTransport):
        def require_available(self, settings: OpenSandboxSettings) -> None:
            del settings
            raise FleetPlanInvalid("remote executor unavailable")

    backend = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.KATA), transport=LostTransport()
    )
    seed_cicd_repository(tmp_path / "repo")
    plan = _plan(IsolationThreatModel.HOSTILE_GENERATED_CODE)
    step = plan.steps[0]
    with prepare_physical_test_run(
        RepositoryIdentity(path=tmp_path / "repo"),
        plan,
        isolation_backend=isolation_backend_evidence(backend),
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true",),
            cwd=attempt.paths.workspace,
            env={},
        )
        with pytest.raises(ExecutorPlacementLost, match="after RUN_ADMITTED"):
            backend.execute(request, timeout=1.0, cancel_requested=None)


def test_backend_configuration_drift_after_admission_is_placement_loss(tmp_path: Path) -> None:
    backend = OpenSandboxProcessIsolation(
        _settings(OpenSandboxRuntimeKind.KATA), transport=_OpenSandboxTransport()
    )
    seed_cicd_repository(tmp_path / "repo")
    plan = _plan(IsolationThreatModel.HOSTILE_GENERATED_CODE)
    step = plan.steps[0]
    admitted = isolation_backend_evidence(backend)
    drifted = admitted.model_copy(update={"configuration_id": "changed-config"})
    with prepare_physical_test_run(
        RepositoryIdentity(path=tmp_path / "repo"),
        plan,
        isolation_backend=drifted,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=("true",),
            cwd=attempt.paths.workspace,
            env={},
        )
        with pytest.raises(ExecutorPlacementLost, match="changed after RUN_ADMITTED"):
            backend.execute(request, timeout=1.0, cancel_requested=None)


def test_opensandbox_settings_repr_does_not_expose_api_key() -> None:
    settings = _settings(OpenSandboxRuntimeKind.KATA)

    assert settings.api_key not in repr(settings)


def test_opensandbox_cli_environment_carries_secret_but_argv_does_not(monkeypatch) -> None:
    settings = _settings(OpenSandboxRuntimeKind.KATA)
    observed = {}

    def fake_run_command(*args, **kwargs):
        observed["args"] = args
        observed["env"] = kwargs["env"]
        return CommandResult(argv=tuple(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(opensandbox_transport, "run_command", fake_run_command)
    _run_osb(settings, "--version", timeout=1.0)

    assert settings.api_key not in observed["args"]
    assert observed["env"]["OPEN_SANDBOX_API_KEY"] == settings.api_key


def test_opensandbox_runtime_attestation_is_endpoint_and_runtime_bound(tmp_path: Path) -> None:
    attestation = tmp_path / "attestation.json"
    payload = __import__("json").loads((_FIXTURES / "kata-runtime-attestation.json").read_text())
    payload["claims"]["domain"] = "other.internal"
    attestation.write_text(__import__("json").dumps(payload), encoding="utf-8")
    settings = OpenSandboxSettings(
        domain="sandbox.internal",
        api_key="secret",
        image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
        cli_executable=sys.executable,
        runtime_attestation_file=str(attestation),
        runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
        runtime=OpenSandboxRuntimeKind.KATA,
        protocol="https",
    )
    with pytest.raises(FleetPlanInvalid, match="does not bind"):
        settings.validate()


def test_opensandbox_cli_transport_does_not_require_python_sdk() -> None:
    source = Path(opensandbox_transport.__file__).read_text(encoding="utf-8")
    assert "import opensandbox" not in source
    assert "from opensandbox" not in source


def test_opensandbox_configuration_identity_binds_cli_and_attestation_bytes(tmp_path: Path) -> None:
    cli = tmp_path / "osb"
    cli.write_bytes(b"first")
    cli.chmod(0o755)
    attestation = tmp_path / "attestation.json"
    attestation.write_bytes((_FIXTURES / "kata-runtime-attestation.json").read_bytes())
    settings = OpenSandboxSettings(
        domain="sandbox.internal",
        api_key="secret",
        image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
        cli_executable=str(cli),
        runtime_attestation_file=str(attestation),
        runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
        runtime=OpenSandboxRuntimeKind.KATA,
        protocol="https",
    )
    backend = OpenSandboxProcessIsolation(settings, transport=_OpenSandboxTransport())
    first = backend.configuration_identity()
    cli.write_bytes(b"second")
    second = backend.configuration_identity()
    assert first != second
    cli.write_bytes(b"first")
    attestation.write_text(attestation.read_text().replace('"a"', '"a"'), encoding="utf-8")
    payload = __import__("json").loads(attestation.read_text())
    payload["claims"]["server_configuration_sha256"] = "b" * 64
    attestation.write_text(__import__("json").dumps(payload), encoding="utf-8")
    third = backend.configuration_identity()
    assert first != third


def test_opensandbox_runtime_attestation_rejects_forged_signature(tmp_path: Path) -> None:
    payload = __import__("json").loads((_FIXTURES / "kata-runtime-attestation.json").read_text())
    payload["claims"]["server_configuration_sha256"] = "9" * 64
    attestation = tmp_path / "forged.json"
    attestation.write_text(__import__("json").dumps(payload), encoding="utf-8")
    settings = _settings(OpenSandboxRuntimeKind.KATA)
    settings = settings.__class__(
        domain=settings.domain, api_key=settings.api_key, image=settings.image,
        cli_executable=settings.cli_executable, runtime_attestation_file=str(attestation),
        runtime_attestation_public_key_file=settings.runtime_attestation_public_key_file,
        runtime=settings.runtime, protocol=settings.protocol,
    )
    with pytest.raises(FleetPlanInvalid, match="signature verification failed"):
        settings.validate()


def test_opensandbox_runtime_attestation_rejects_wrong_trust_anchor(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    wrong = tmp_path / "wrong.pub.pem"
    wrong.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ))
    settings = _settings(OpenSandboxRuntimeKind.KATA)
    settings = settings.__class__(
        domain=settings.domain, api_key=settings.api_key, image=settings.image,
        cli_executable=settings.cli_executable, runtime_attestation_file=settings.runtime_attestation_file,
        runtime_attestation_public_key_file=str(wrong), runtime=settings.runtime, protocol=settings.protocol,
    )
    with pytest.raises(FleetPlanInvalid, match="signature verification failed"):
        settings.validate()


def test_opensandbox_create_payload_preserves_resource_and_deny_all_policy(monkeypatch) -> None:
    settings = _settings(OpenSandboxRuntimeKind.KATA)
    observed = {}

    class Response:
        status_code = 202
        def json(self):
            return {"id": "sbx-1"}

    class Httpx:
        @staticmethod
        def post(url, **kwargs):
            observed["url"] = url
            observed.update(kwargs)
            return Response()

    monkeypatch.setattr(opensandbox_transport, "_httpx2", lambda: Httpx)
    sandbox_id = _create_sandbox(
        settings,
        resources=CicdResourcePolicy(cpu_millis=1500, memory_mib=3072, max_processes=None),
        timeout=12.0,
    )
    assert sandbox_id == "sbx-1"
    assert observed["json"]["resourceLimits"] == {"cpu": "1500m", "memory": "3072Mi"}
    assert observed["json"]["networkPolicy"] == {"defaultAction": "deny", "egress": []}
    assert observed["json"]["metadata"]["runtime-attestation"] == settings.runtime_attestation_sha256
    assert observed["headers"]["OPEN-SANDBOX-API-KEY"] == settings.api_key


def test_opensandbox_plain_runc_is_rejected_even_with_attestation(tmp_path: Path) -> None:
    attestation = tmp_path / "attestation.json"
    attestation.write_text(
        '{"schema":"oh-goon.opensandbox-runtime-attestation.v1","domain":"sandbox.internal","protocol":"https","secure_runtime":"runc","server_configuration_sha256":"' + 'a' * 64 + '"}',
        encoding="utf-8",
    )
    settings = OpenSandboxSettings(
        domain="sandbox.internal", api_key="secret",
        image="registry/image@sha256:" + "1" * 64,
        cli_executable=sys.executable,
        runtime_attestation_file=str(attestation),
        runtime_attestation_public_key_file=str(_FIXTURES / "runtime-attestation-ed25519.pub.pem"),
        runtime=OpenSandboxRuntimeKind.RUNC,
        protocol="https",
    )
    with pytest.raises(FleetPlanInvalid, match="requires Kata or Firecracker"):
        settings.validate()


def test_strict_cicd_run_admission_persists_exact_backend_evidence(tmp_path: Path) -> None:
    plan = _plan(IsolationThreatModel.TRUSTED_REPOSITORY)
    target = structural_fleet_plan_target(plan)
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    repository.path.mkdir()
    backend = _AvailableBackend(
        _capabilities("shared", IsolationKernelBoundary.SHARED_HOST_KERNEL),
        configuration="admitted-config",
    )
    evidence = isolation_backend_evidence(backend)
    journal = Journal(LocalDatabase(tmp_path / "journal.db"))

    with pytest.raises(ValueError, match="require strict execution admission"):
        FleetRunAdmissionIntent(
            target=intent.target,
            capability=intent.definition.durable_snapshot(),
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=(),
        )

    capacity = PlanExecutorCapacityEvidence.create(
        ExecutorCapacityEvidenceInput(
            fleet_run_id="bound-backend",
            executor=FleetExecutorIdentity(executor_id="test-executor", generation=1),
            source="remote_operator_attested",
            backend_configuration_id=evidence.configuration_id,
            capacity_cpu_millis=4000,
            capacity_memory_bytes=8 * 1024 * 1024 * 1024,
            capacity_max_processes=None,
            requested_parallelism=1,
            admitted_parallelism=1,
            per_attempt_cpu_millis=2000,
            per_attempt_memory_bytes=4 * 1024 * 1024 * 1024,
            per_attempt_max_processes=None,
            pool_id="pool-test",
            placement_policy="priority_rendezvous_fifo_v2",
            placement_rank=0,
            executor_priority=10,
        )
    )
    admitted_intent = FleetRunAdmissionIntent(
        target=intent.target,
        capability=intent.definition.durable_snapshot(),
        execution=StrictFleetExecutionAdmission(
            placement=FleetExecutorPlacement(
                isolation_backend=evidence,
                executor_capacity=capacity,
                executor_trust_epoch=0,
            ),
        ),
        repository_admissions=(repository_plan_admission_evidence(repository, plan),),
    )
    admit_run(
        journal,
        "bound-backend",
        admitted_intent,
        fleet_repository_scope(None),
        [repository],
    )
    event = journal.run_events("bound-backend").events[0]

    detail = journal.read_view().run_detail("bound-backend")
    assert detail.execution == StrictFleetExecutionAdmission(
        placement=FleetExecutorPlacement(
            isolation_backend=evidence,
            executor_capacity=capacity,
            executor_trust_epoch=0,
        ),
    )
    assert event.execution == detail.execution
    payload = event.model_dump(mode="json")
    assert payload["execution"]["kind"] == "strict"
    assert "placement" in payload["execution"]
    assert "isolation_backend" not in payload["execution"]
    assert "executor_capacity" not in payload["execution"]

    legacy = dict(payload)
    execution_payload = legacy["execution"] = dict(legacy["execution"])
    placement_payload = execution_payload.pop("placement")
    execution_payload["isolation_backend"] = placement_payload["isolation_backend"]
    execution_payload["executor_capacity"] = placement_payload["executor_capacity"]
    with pytest.raises(ValidationError):
        type(event).model_validate(legacy)
