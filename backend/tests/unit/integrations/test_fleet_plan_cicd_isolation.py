from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import required_execution_tool_names
from oh_goon.core.models.fleet_plan import (
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedImport,
    StepFailurePolicy,
    StepLifecycle,
    StepRetryPolicy,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCapabilities,
    PlanStepWorkspace,
    StepWorkspaceSource,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.fleet_plan_requirements import RepositoryFileExistsRequirement
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations._fleet_plan_host_resources import preflight_executor_tool_feasibility
from oh_goon.integrations._fleet_plan_step_dataflow import collect_published_outputs
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.bubblewrap_command import prepared_bubblewrap_command as _prepared
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    PhysicalFleetPlanExecutorHarness,
    prepare_physical_test_run,
    strict_test_plan,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY
from tests.factories.runtime_authority import authorized_command_result


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
        name="sandbox",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=steps,
    )


def _executor() -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_UnitProcessIsolation(),)
    )


def test_cicd_tool_requirement_is_derived_from_resolved_command() -> None:
    plan = ResolvedFleetPlan.create(
        name="derived-tool",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(timeout_seconds=3600, step_id="test", argv=("uv", "run", "pytest")),
        ),
    )

    assert required_execution_tool_names(plan) == ("uv",)




def test_inapplicable_import_does_not_require_its_missing_host_tool(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="general-optional.check",
        argv=("definitely-not-a-real-fleet-tool",),
    )
    plan = ResolvedFleetPlan.create(
        name="optional-import",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(
                cpu_millis=2000, memory_mib=4096, max_processes=256
            ),
        ),
        requirements=(),
        imports=(
            ResolvedImport(
                import_id="general-optional",
                name="general/optional",
                source_sha256="a" * 64,
                step_ids=(step.step_id,),
                requirements=(RepositoryFileExistsRequirement(path="optional.marker"),),
            ),
        ),
        steps=(step,),
    )

    preflight_executor_tool_feasibility(
        (plan.model_copy(update={"steps": ()}),), TEST_PATH_AUTHORITY
    )
    result = _executor().execute(repository, plan, cancel_requested=None)

    assert result.steps[0].status == "SKIPPED"
    assert "optional.marker" in result.steps[0].reason
    assert result.execution_inputs is not None
    assert result.execution_inputs.host_tools == ()

def test_applicable_import_missing_tool_fails_executor_feasibility(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "optional.marker").write_text("present\n", encoding="utf-8")
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="general-optional.check",
        argv=("definitely-not-a-real-fleet-tool",),
    )
    plan = ResolvedFleetPlan.create(
        name="optional-import",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(
                cpu_millis=2000, memory_mib=4096, max_processes=256
            ),
        ),
        requirements=(),
        imports=(
            ResolvedImport(
                import_id="general-optional",
                name="general/optional",
                source_sha256="a" * 64,
                step_ids=(step.step_id,),
                requirements=(RepositoryFileExistsRequirement(path="optional.marker"),),
            ),
        ),
        steps=(step,),
    )

    with pytest.raises(
        FleetPlanInvalid, match="missing required tool.*definitely-not-a-real-fleet-tool"
    ):
        preflight_executor_tool_feasibility((plan,), TEST_PATH_AUTHORITY)


def test_strict_cicd_fails_closed_when_kernel_backend_is_unavailable(tmp_path: Path) -> None:
    plan = _cicd(ResolvedCommandStep(timeout_seconds=3600, step_id="noop", argv=("true",)))
    backend = BubblewrapProcessIsolation(executable="/definitely/not/a/real/bwrap")

    with pytest.raises(FleetPlanInvalid, match="execution is denied rather than downgraded"):
        PhysicalFleetPlanExecutorHarness(
            GitService(), process_isolation_backends=(backend,)
        ).execute(tmp_path, plan, cancel_requested=None)


def test_cicd_steps_do_not_share_undeclared_workspace_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _cicd(
        ResolvedCommandStep(timeout_seconds=3600, step_id="writer", argv=("true", "write")),
        ResolvedCommandStep(timeout_seconds=3600, step_id="reader", argv=("true", "read")),
    )
    seen: list[bool] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cwd = Path(kwargs["cwd"])
        if argv == ("true", "write"):
            (cwd / "undeclared-secret.txt").write_text("cross-step", encoding="utf-8")
        else:
            seen.append((cwd / "undeclared-secret.txt").exists())
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    _executor().execute(repository, plan, cancel_requested=None)

    assert seen == [False]
    assert not (repository / "undeclared-secret.txt").exists()



def test_published_output_evidence_identifies_the_canonical_consumed_value(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    output_path = repository / "value.txt"
    output_path.write_bytes(b"  sha256:abc  \n")
    step = ResolvedCommandStep(
        timeout_seconds=60,
        step_id="producer",
        argv=("true",),
        outputs=(PlanPublishedOutput(name="VALUE", path="value.txt"),),
    )

    values, evidence = collect_published_outputs(repository, step)

    assert values == {"VALUE": "sha256:abc"}
    assert evidence[0].sha256 == hashlib.sha256(b"sha256:abc").hexdigest()
    assert evidence[0].byte_count == len(b"sha256:abc")

def test_published_workspace_is_an_explicit_filesystem_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    producer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="prepare",
        argv=("true", "prepare"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="build",
        argv=("true", "build"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="prepare")),
    )
    plan = _cicd(producer, consumer)
    observed: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cwd = Path(kwargs["cwd"])
        if argv == ("true", "prepare"):
            (cwd / "generated.txt").write_text("typed-workspace", encoding="utf-8")
        else:
            observed.append((cwd / "generated.txt").read_text(encoding="utf-8"))
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    _executor().execute(repository, plan, cancel_requested=None)

    assert observed == ["typed-workspace"]
    assert not (repository / "generated.txt").exists()


def test_failed_workspace_producer_is_not_masked_by_dependent_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    producer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="prepare",
        argv=("true", "prepare"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="build",
        argv=("true", "build"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="prepare")),
    )
    plan = _cicd(producer, consumer)
    calls: list[tuple[str, ...]] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        calls.append(tuple(argv))
        return CommandResult(argv=tuple(argv), returncode=7, stdout="setup failed", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, plan, cancel_requested=None)

    assert calls == [("true", "prepare")]
    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode == 7
    assert result.steps[1].status == "SKIPPED"
    assert "earlier stop-policy failure" in result.steps[1].reason


def test_workspace_reference_requires_explicit_publish() -> None:
    with pytest.raises(Exception, match="does not publish its workspace"):
        _cicd(
            ResolvedCommandStep(timeout_seconds=3600, step_id="prepare", argv=("true", "prepare")),
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="build",
                argv=("true", "build"),
                workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="prepare")),
            ),
        )


def test_stale_repository_file_cannot_satisfy_declared_step_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / ".fleet").mkdir(parents=True)
    (repository / ".fleet" / "value.txt").write_text("stale", encoding="utf-8")
    from oh_goon.core.models.fleet_plan import PlanPublishedOutput

    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="producer",
            argv=("true", "noop"),
            outputs=(PlanPublishedOutput(name="VALUE", path=".fleet/value.txt"),),
        )
    )

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: authorized_command_result(argv),
    )
    result = _executor().execute(repository, plan, cancel_requested=None)

    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode == 125
    assert (repository / ".fleet" / "value.txt").read_text(encoding="utf-8") == "stale"


def test_retry_attempt_starts_from_clean_declared_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="retry",
            argv=("true", "retry"),
            retry=StepRetryPolicy(
                max_attempts=2,
                known_transient_exit_codes=(9,),
                reason="test transient",
                delays_seconds=(0,),
            ),
        )
    )
    calls = 0

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal calls
        calls += 1
        marker = Path(kwargs["cwd"]) / "failed-attempt-state"
        if calls == 1:
            marker.write_text("dirty", encoding="utf-8")
            return CommandResult(argv=tuple(argv), returncode=9, stdout="", stderr="")
        assert not marker.exists()
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, plan, cancel_requested=None)

    assert result.steps[0].status == "COMPLETED"
    assert len(result.steps[0].attempts) == 2
    first, second = result.steps[0].attempts
    assert first.retry is not None
    assert first.retry.disposition == "SCHEDULED"
    assert first.retry.policy_reason == "test transient"
    assert first.retry.delay_seconds == 0
    assert first.retry.next_attempt == 2
    assert second.retry is None



def test_retry_cannot_publish_output_left_by_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="producer",
            argv=("true", "retry-output"),
            outputs=(PlanPublishedOutput(name="VALUE", path="value.txt"),),
            retry=StepRetryPolicy(
                max_attempts=2,
                known_transient_exit_codes=(9,),
                reason="test transient",
                delays_seconds=(0,),
            ),
        )
    )
    calls = 0

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal calls
        calls += 1
        cwd = Path(kwargs["cwd"])
        if calls == 1:
            (cwd / "value.txt").write_text("stale-from-failed-attempt", encoding="utf-8")
            return CommandResult(argv=tuple(argv), returncode=9, stdout="", stderr="")
        assert not (cwd / "value.txt").exists()
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, plan, cancel_requested=None)

    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode == 125
    assert len(result.steps[0].attempts) == 2
    assert result.steps[0].outputs == ()


def test_finalizer_cannot_consume_output_from_failed_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="producer",
            argv=("true", "producer"),
            outputs=(PlanPublishedOutput(name="VALUE", path="value.txt"),),
        ),
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="cleanup",
            argv=("true", "${{ steps.producer.outputs.VALUE }}"),
            lifecycle=StepLifecycle.FINALIZER,
            on_failure=StepFailurePolicy.CONTINUE,
        ),
    )

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cwd = Path(kwargs["cwd"])
        if argv == ("true", "producer"):
            (cwd / "value.txt").write_text("must-not-flow", encoding="utf-8")
            return CommandResult(argv=tuple(argv), returncode=1, stdout="", stderr="")
        raise AssertionError(f"finalizer should not launch with unavailable output: {argv}")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, plan, cancel_requested=None)

    assert result.steps[0].status == "FAILED"
    assert result.steps[1].status == "FAILED"
    assert result.steps[1].returncode == 125
    assert "requires unavailable output producer.VALUE" in result.stderr

def test_step_environment_uses_private_home_tmp_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "host-tmp"))
    plan = _cicd(
        ResolvedCommandStep(timeout_seconds=3600, step_id="inspect", argv=("true", "inspect"))
    )
    observed: dict[str, str] = {}

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        del argv
        observed.update(kwargs["env"])
        return authorized_command_result(("true", "inspect"))

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    _executor().execute(repository, plan, cancel_requested=None)

    assert observed["HOME"] != str(tmp_path / "host-home")
    assert observed["TMPDIR"] != str(tmp_path / "host-tmp")
    assert "XDG_CACHE_HOME" in observed
    assert "XDG_CONFIG_HOME" in observed


def test_escaping_repository_symlink_is_rejected_before_step_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    host_secret = tmp_path / "outside-secret"
    host_secret.write_text("secret", encoding="utf-8")
    (repository / "escape").symlink_to(host_secret)
    plan = _cicd(ResolvedCommandStep(timeout_seconds=3600, step_id="noop", argv=("true", "noop")))
    launched = False

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal launched
        launched = True
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    with pytest.raises(FleetPlanInvalid, match="escaping symlink"):
        _executor().execute(repository, plan, cancel_requested=None)
    assert launched is False


def test_bubblewrap_plan_does_not_mount_ambient_home_and_denies_network_by_default(
    tmp_path: Path,
) -> None:
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
        backend = BubblewrapProcessIsolation(executable="/usr/bin/true")
        prepared = _prepared(
            backend,
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={"PATH": "/usr/bin", "HOME": str(attempt.paths.home)},
            ),
        )

    bind_sources = {
        prepared.argv[index + 1]
        for index, token in enumerate(prepared.argv[:-2])
        if token in {"--bind", "--ro-bind"}
    }
    assert "--unshare-net" in prepared.argv
    assert "/home/fleet" in prepared.argv
    assert str(attempt.paths.home) in bind_sources
    assert str(Path.home()) not in bind_sources
    assert prepared.env["HOME"] == "/home/fleet"


def test_boolean_network_capability_cannot_authorize_egress() -> None:
    with pytest.raises(ValidationError):
        PlanStepCapabilities.model_validate({"network": True})


def test_boolean_unrestricted_network_capability_cannot_authorize_egress() -> None:
    with pytest.raises(ValidationError):
        PlanStepCapabilities.model_validate({"unrestricted_network": True})


def test_network_access_must_be_explicit_in_sandbox_contract(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="networked",
        argv=("true",),
        capabilities=PlanStepCapabilities(egress={"mode": "host_unrestricted"}),
    )
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
                env={"PATH": "/usr/bin"},
            ),
        )

    assert "--unshare-net" not in prepared.argv


def test_stale_repository_artifact_is_not_attributed_to_current_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "dist").mkdir(parents=True)
    (repository / "dist" / "report.json").write_text("stale", encoding="utf-8")
    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="report",
            argv=("true", "noop"),
            artifacts=(PlanArtifactDeclaration(path="dist/report.json"),),
        )
    )
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: authorized_command_result(argv),
    )

    result = _executor().execute(repository, plan, cancel_requested=None)

    assert result.steps[0].status == "COMPLETED"
    assert result.steps[0].artifacts[0].status == "MISSING"
    assert (repository / "dist" / "report.json").read_text(encoding="utf-8") == "stale"


def test_strict_pipeline_cannot_request_host_docker_config(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(ValidationError):
        PlanStepCapabilities.model_validate({"docker_config": "/home/operator/.docker"})


def test_declared_but_unavailable_host_tool_fails_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    rogue_bin = tmp_path / "repo-b-bin"
    rogue_bin.mkdir()
    rogue_tool = rogue_bin / "definitely-not-a-real-fleet-tool"
    rogue_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    rogue_tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{rogue_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="missing-tool",
        argv=("definitely-not-a-real-fleet-tool",),
        capabilities=PlanStepCapabilities(),
    )
    plan = ResolvedFleetPlan.create(
        name="missing-tool",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(step,),
    )

    process_isolation = _UnitProcessIsolation()
    executor = PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(process_isolation,)
    )
    with pytest.raises(
        FleetPlanInvalid, match="missing required tool.*definitely-not-a-real-fleet-tool"
    ):
        executor.prepare_strict_run(
            RepositoryIdentity(path=repository),
            plan,
            cancel_requested=None,
            admitted_isolation_backend=executor.admitted_isolation_backend(),
        )


def test_top_level_executable_identity_is_recorded_in_attempt_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    executable = Path("/usr/bin/true")
    if not executable.exists():
        pytest.skip("/usr/bin/true is unavailable on this platform")
    plan = _cicd(ResolvedCommandStep(timeout_seconds=3600, step_id="identity", argv=("true",)))

    process_isolation = _UnitProcessIsolation()
    result = PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(process_isolation,)
    ).execute(repository, plan, cancel_requested=None)

    tool = result.steps[0].attempts[0].tool
    assert tool is not None
    assert tool.name == "true"
    assert tool.origin == "host"
    assert len(tool.sha256) == 64
    assert tool.byte_count > 0


def test_strict_pipeline_cannot_request_host_docker_socket() -> None:
    with pytest.raises(ValidationError):
        PlanStepCapabilities.model_validate({"docker_socket": "/var/run/docker.sock"})


def test_strict_workspace_script_requires_explicit_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    script = repository / "ci" / "check.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    plan = _cicd(
        ResolvedCommandStep(timeout_seconds=3600, step_id="script", argv=("./ci/check.sh",))
    )
    launched = False

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal launched
        launched = True
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    with pytest.raises(FleetPlanInvalid, match="implicit shebang interpreter"):
        _executor().execute(repository, plan, cancel_requested=None)
    assert launched is False


def test_explicit_script_interpreter_is_a_declared_host_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    script = repository / "ci" / "check.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("echo ok\n", encoding="utf-8")
    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="script",
            argv=("sh", "./ci/check.sh"),
            capabilities=PlanStepCapabilities(),
        )
    )
    observed: list[tuple[str, ...]] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        observed.append(tuple(argv))
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, plan, cancel_requested=None)
    assert result.steps[0].status == "COMPLETED"
    assert observed == [("sh", "./ci/check.sh")]
