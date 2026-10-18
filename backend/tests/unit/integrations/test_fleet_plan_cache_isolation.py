from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan, StepRetryPolicy
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
    RetainedPlanCacheSource,
    StepPlanCacheSource,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.fleet_content_store import FleetContentStore
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness
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
        name="cache-isolation",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=steps,
    )


def _owned_cache(path: Path, repository: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=RepositoryIdentity(path=repository).repository_id,
        root=path,
        path=path,
        label="test retained cache",
    )


def _executor(store: FleetContentStore | None = None) -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(),
        process_isolation_backends=(_UnitProcessIsolation(),),
        content_store=store,
    )


def test_published_cache_is_an_explicit_step_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    producer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="warm",
        argv=("true", "warm"),
        cache=PlanStepCache(publish=True),
    )
    consumer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="use",
        argv=("true", "use"),
        cache=PlanStepCache(source=StepPlanCacheSource(step_id="warm")),
    )
    observed: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cache = Path(kwargs["env"]["XDG_CACHE_HOME"])
        if argv == ("true", "warm"):
            (cache / "tool").mkdir(parents=True)
            (cache / "tool" / "entry").write_text("cached", encoding="utf-8")
        else:
            observed.append((cache / "tool" / "entry").read_text(encoding="utf-8"))
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, _cicd(producer, consumer), cancel_requested=None)

    assert observed == ["cached"]
    assert result.steps[0].attempts[0].cache_output is not None
    assert result.steps[1].attempts[0].cache_input == result.steps[0].attempts[0].cache_output


def test_cache_reference_requires_explicit_publish() -> None:
    with pytest.raises(Exception, match="does not publish its cache"):
        _cicd(
            ResolvedCommandStep(timeout_seconds=3600, step_id="warm", argv=("true", "warm")),
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="use",
                argv=("true", "use"),
                cache=PlanStepCache(source=StepPlanCacheSource(step_id="warm")),
            ),
        )


def test_retry_does_not_inherit_failed_attempt_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="retry-cache",
        argv=("true", "retry-cache"),
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(9,),
            reason="test transient",
            delays_seconds=(0,),
        ),
    )
    calls = 0

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal calls
        calls += 1
        marker = Path(kwargs["env"]["XDG_CACHE_HOME"]) / "failed"
        if calls == 1:
            marker.write_text("dirty", encoding="utf-8")
            return CommandResult(argv=tuple(argv), returncode=9, stdout="", stderr="")
        assert not marker.exists()
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    result = _executor().execute(repository, _cicd(step), cancel_requested=None)

    assert result.steps[0].status == "COMPLETED"
    assert len(result.steps[0].attempts) == 2
    first, second = result.steps[0].attempts
    assert first.retry is not None
    assert first.retry.disposition == "SCHEDULED"
    assert first.retry.policy_reason == "test transient"
    assert first.retry.delay_seconds == 0
    assert first.retry.next_attempt == 2
    assert second.retry is None


def test_retained_cache_can_seed_a_later_run_by_exact_content_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    store = FleetContentStore(tmp_path / "content-store")
    producer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="warm",
        argv=("true", "warm"),
        cache=PlanStepCache(publish=True, retain=True),
    )

    def produce(*argv: str, **kwargs) -> CommandResult:
        cache = Path(kwargs["env"]["XDG_CACHE_HOME"])
        (cache / "tool").mkdir(parents=True)
        (cache / "tool" / "entry").write_text("cross-run", encoding="utf-8")
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", produce)
    first = _executor(store).execute(repository, _cicd(producer), cancel_requested=None)
    retained = first.steps[0].attempts[0].retained_cache
    assert retained is not None
    assert retained.object_id.startswith("tree-sha256-")

    observed: list[str] = []
    consumer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="use",
        argv=("true", "use"),
        cache=PlanStepCache(source=RetainedPlanCacheSource(reference_id=retained.reference_id)),
    )

    def consume(*argv: str, **kwargs) -> CommandResult:
        cache = Path(kwargs["env"]["XDG_CACHE_HOME"])
        observed.append((cache / "tool" / "entry").read_text(encoding="utf-8"))
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", consume)
    second = _executor(store).execute(repository, _cicd(consumer), cancel_requested=None)

    assert observed == ["cross-run"]
    assert second.steps[0].attempts[0].cache_input.sha256 == retained.sha256


def test_missing_cross_run_cache_fails_before_step_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    store = FleetContentStore(tmp_path / "content-store")
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="use",
        argv=("true", "use"),
        cache=PlanStepCache(source=RetainedPlanCacheSource(reference_id="ref-" + "a" * 32)),
    )
    launched = False

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal launched
        launched = True
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    with pytest.raises(Exception, match="content reference is unavailable to repository"):
        _executor(store).execute(repository, _cicd(step), cancel_requested=None)
    assert launched is False


def test_cross_run_cache_is_frozen_into_run_before_step_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    store = FleetContentStore(tmp_path / "content-store")
    source = tmp_path / "cache-source"
    (source / "tool").mkdir(parents=True)
    (source / "tool" / "entry").write_text("frozen", encoding="utf-8")
    retained = store.put_tree(_owned_cache(source, repository))
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="use",
        argv=("true", "use"),
        cache=PlanStepCache(source=RetainedPlanCacheSource(reference_id=retained.reference_id)),
    )
    plan = _cicd(step)
    executor = _executor(store)
    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        plan,
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    assert prepared is not None
    try:
        (retained.path / "tool" / "entry").write_text("tampered-after-prepare", encoding="utf-8")
        observed: list[str] = []

        def consume(*argv: str, **kwargs) -> CommandResult:
            cache = Path(kwargs["env"]["XDG_CACHE_HOME"])
            observed.append((cache / "tool" / "entry").read_text(encoding="utf-8"))
            return authorized_command_result(argv)

        monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", consume)
        result = executor.execute_strict_run(repository, plan, prepared, cancel_requested=None)
    finally:
        prepared.close()

    assert observed == ["frozen"]
    assert result.execution_inputs is not None
    assert result.execution_inputs.content_inputs[0].object_id == retained.object_id
    assert result.execution_inputs.content_inputs[0].sha256 == retained.identity.sha256


def test_retained_cache_object_cannot_cross_repository_ownership_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "repo-a"
    second = tmp_path / "repo-b"
    seed_cicd_repository(first)
    seed_cicd_repository(second)
    store = FleetContentStore(tmp_path / "content-store")
    source = tmp_path / "cache-source-owned-by-a"
    source.mkdir()
    (source / "secret-cache-entry").write_text("repo-a-only", encoding="utf-8")
    retained = store.put_tree(_owned_cache(source, first))
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="consume",
        argv=("true", "consume"),
        cache=PlanStepCache(source=RetainedPlanCacheSource(reference_id=retained.reference_id)),
    )
    launched = False

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal launched
        launched = True
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    with pytest.raises(Exception, match="content reference is unavailable to repository"):
        _executor(store).execute(second, _cicd(step), cancel_requested=None)
    assert launched is False


def test_read_only_retained_cache_avoids_run_and_attempt_tree_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo-readonly"
    seed_cicd_repository(repository)
    store = FleetContentStore(tmp_path / "content-store-readonly")
    source = tmp_path / "cache-source-readonly"
    (source / "tool").mkdir(parents=True)
    (source / "tool" / "entry").write_text("sealed", encoding="utf-8")
    retained = store.put_tree(_owned_cache(source, repository))
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="consume-readonly",
        argv=("true", "consume-readonly"),
        cache=PlanStepCache(
            source=RetainedPlanCacheSource(reference_id=retained.reference_id, access="read_only")
        ),
    )
    executor = _executor(store)
    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        _cicd(step),
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    assert prepared is not None
    try:
        attempt = prepared.create_attempt(step, 1)
        readonly = [
            binding for binding in attempt.sandbox_bindings if str(binding.sandbox_path) == "/cache"
        ]
        assert len(readonly) == 1
        assert readonly[0].snapshot_path == retained.path
        assert readonly[0].identity == retained.identity
        assert list(attempt.paths.cache.iterdir()) == []
        assert not (prepared.root / "content-inputs" / "000").exists()
    finally:
        prepared.close()


def test_read_only_retained_cache_cannot_publish_or_retain() -> None:
    with pytest.raises(ValueError, match="read-only retained cache input"):
        PlanStepCache(
            source=RetainedPlanCacheSource(reference_id="ref-" + "a" * 32, access="read_only"),
            publish=True,
        )


def test_retained_preflight_skips_repository_applicability_when_plan_has_no_retained_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle

    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600,
            step_id="plain",
            argv=("true",),
            cache=PlanStepCache(),
        )
    )
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._preflight_retained_inputs(plan, ())


def test_mixed_retained_cache_access_modes_currently_fail_before_workload_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Characterize the unresolved mixed-mode contract without choosing policy here."""

    repository = tmp_path / "repo-mixed-retained"
    seed_cicd_repository(repository)
    store = FleetContentStore(tmp_path / "content-store-mixed-retained")
    source = tmp_path / "cache-source-mixed-retained"
    (source / "tool").mkdir(parents=True)
    (source / "tool" / "entry").write_text("shared", encoding="utf-8")
    retained = store.put_tree(_owned_cache(source, repository))
    read_only = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="read-only",
        argv=("true", "read-only"),
        cache=PlanStepCache(
            source=RetainedPlanCacheSource(
                reference_id=retained.reference_id,
                access="read_only",
            )
        ),
    )
    private_copy = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="private-copy",
        argv=("true", "private-copy"),
        cache=PlanStepCache(
            source=RetainedPlanCacheSource(
                reference_id=retained.reference_id,
                access="private_copy",
            )
        ),
    )
    plan = _cicd(read_only, private_copy)
    executor = _executor(store)
    launched: list[tuple[str, ...]] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(tuple(argv))
        return authorized_command_result(argv)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        plan,
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    assert prepared is not None
    try:
        assert len(prepared.execution_input_evidence().content_inputs) == 1
        with pytest.raises(
            FleetPlanInvalid,
            match="read-only retained cache was not captured as immutable direct input",
        ):
            executor.execute_strict_run(repository, plan, prepared, cancel_requested=None)
    finally:
        prepared.close()

    assert launched == []


def test_retained_preflight_consumes_admitted_inactive_import_without_reresolving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.integrations.fleet_plan_repository_scope as scope_module
    from oh_goon.core.models.fleet_plan import ResolvedImport
    from oh_goon.core.models.fleet_plan_evidence import PlanImportApplicabilityEvidence
    from oh_goon.core.models.fleet_plan_requirements import RepositoryFileExistsRequirement
    from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
    from tests.factories.run_admission import repository_admission_for

    repository = RepositoryIdentity(path=tmp_path / "repo-admitted-skip")
    step = ResolvedCommandStep(
        timeout_seconds=60,
        step_id="optional.cache",
        argv=("true",),
        cache=PlanStepCache(
            source=RetainedPlanCacheSource(
                reference_id="ref-" + "a" * 32,
                access="read_only",
            )
        ),
    )
    plan = ResolvedFleetPlan.create(
        name="retained-import-skip",
        description="",
        execution=CicdExecutionPolicy(),
        imports=(
            ResolvedImport(
                import_id="optional",
                name="general/optional",
                source_sha256="b" * 64,
                step_ids=(step.step_id,),
                requirements=(RepositoryFileExistsRequirement(path="marker.txt"),),
            ),
        ),
        steps=(step,),
    )
    applicability = (
        PlanImportApplicabilityEvidence(
            import_id="optional",
            status="NOT_APPLICABLE",
            reason="marker missing",
        ),
    )
    admitted = repository_admission_for(
        repository_id=repository.repository_id,
        plan_id=plan.plan_id,
        import_applicability=applicability,
        active_step_ids=(),
    )
    monkeypatch.setattr(
        scope_module,
        "import_applicability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("retained preflight must consume admitted applicability")
        ),
    )
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._content_store = None

    lifecycle._preflight_retained_inputs(plan, (admitted,))
