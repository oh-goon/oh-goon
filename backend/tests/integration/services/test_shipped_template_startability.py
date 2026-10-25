from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.path_layouts import ManagedPreparationPathLayout
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.engine.native_uv_environment import capture_native_uv_authority
from oh_goon.integrations import fleet_plan_preview
from oh_goon.integrations.uv_python_runtime import find_uv_system_python
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import prepare_reviewed_execution_target
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.league import LeagueService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.local_state import local_state
from tests.factories.path_authority import TEST_PATH_AUTHORITY
from tests.factories.path_authority import (
    test_path_authority_for_plan as _test_path_authority_for_plan,
)

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = ROOT / "templates"
IRIS_CERTIFICATION_FIXTURE = ROOT / "template-certification" / "fixtures" / "ml" / "iris"


def _repository_bytes(repository: Path) -> tuple[tuple[str, bytes], ...]:
    """Test evidence that shipped-template startability never mutates workload bytes."""

    return tuple(
        (path.relative_to(repository).as_posix(), path.read_bytes())
        for path in sorted(repository.rglob("*"))
        if path.is_file()
    )


def _install_source_identity_observer(monkeypatch, repository: Path) -> None:
    """Use real Hashmarks when installed; otherwise replace only that native observation."""

    if importlib.util.find_spec("hashmarks") is not None:
        return

    def observe(root: Path):
        assert root == repository
        members = _repository_bytes(root)
        digest = hashlib.sha256()
        byte_count = 0
        for name, payload in members:
            encoded_name = name.encode("utf-8")
            digest.update(len(encoded_name).to_bytes(8, "big"))
            digest.update(encoded_name)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
            byte_count += len(payload)
        return SimpleNamespace(
            sha256=digest.hexdigest(),
            byte_count=byte_count,
            entry_count=len(members),
        )

    monkeypatch.setattr(fleet_plan_preview, "source_snapshot_identity", observe)


class _StartabilityBackend:
    """Capability-only strict backend. Startability must never execute it."""

    capabilities = IsolationBackendCapabilities(
        backend_id="template-startability-test",
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
        kernel_boundary=IsolationKernelBoundary.VIRTUAL_MACHINE,
        remote_execution=True,
    )

    def __init__(self) -> None:
        self.availability_checks = 0
        self.execute_calls = 0

    def configuration_identity(self) -> str:
        return "template-startability-test-v1"

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        del plan
        self.availability_checks += 1

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        del request, timeout, cancel_requested, process_observer
        self.execute_calls += 1
        raise AssertionError("template startability must stop before Step work")


def _admission_stack(tmp_path: Path, repository: Path, *, path_authority=TEST_PATH_AUTHORITY):
    database = LocalDatabase(tmp_path / "runtime.db")
    state = local_state(database)
    state.add_location(repository.parent)

    discovery = RepositoryDiscoveryService()
    repositories = RepositoryService(state, discovery)
    migration = MigrationService(state, discovery)
    league = LeagueService(state, discovery)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, state.journal, execution, attempts, schedules)
    safety = RuntimeSafetyService(auditor, RuntimeSafetyGate())
    safety.refresh()
    planner = FleetAdmissionPlanner(
        state,
        FleetTargetSelector(repositories, migration, league),
        auditor,
        safety,
    )

    backend = _StartabilityBackend()
    pool = FleetExecutorPool(
        database,
        ExecutorPoolConfiguration(
            placement_wait_seconds=0,
            placement_queue_limit=64,
            path_authority=path_authority,
            executors=(
                ConfiguredFleetExecutor(
                    priority=0,
                    backend=backend,
                    capacity=ExecutorCapacitySettings(
                        executor=FleetExecutorIdentity(
                            executor_id="template-startability-test",
                            generation=1,
                        ),
                        cpu_millis=16_000,
                        memory_mib=16_384,
                        max_processes=4096,
                    ),
                ),
            ),
        ),
    )
    lifecycle = FleetRunLifecycle(database, state.journal, execution, pool)
    return database, state, attempts, planner, lifecycle, backend


def _reviewed_shipped_target(template_key: str, *, inputs: dict[str, str] | None = None):
    plans = PlanTemplateStore(TEMPLATES / "plans")
    goons = GoonTemplateStore(TEMPLATES / "goons")

    catalog_entry = next(
        item for item in plans.catalog().templates if item.template_key == template_key
    )
    source = plans.source(catalog_entry.template_key)
    assert catalog_entry.template_path == f"templates/plans/{template_key}/plan.yml"
    assert catalog_entry.sha256 == source.sha256
    assert source.compiled.name == template_key.rsplit("/", 1)[1]

    resolver = FleetPlanResolver(goon_templates=goons)
    reviewed_plan = resolver.resolve(
        source.path,
        inputs=inputs,
        root_expected_name=source.compiled.name,
    )
    reviewed_digest = build_execution_lock(reviewed_plan).digest
    target = prepare_reviewed_execution_target(
        resolver,
        source.path,
        expected_execution_digest=reviewed_digest,
        inputs=inputs,
        expected_plan_id=reviewed_plan.plan_id,
        root_expected_name=source.compiled.name,
    )
    assert target.plan == reviewed_plan
    return target


def test_representative_shipped_plan_reaches_run_admitted_without_step_work(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repositories" / "fixture"
    seed_cicd_repository(repository)
    target = _reviewed_shipped_target("python/dependency-inventory")
    database, state, attempts, planner, lifecycle, backend = _admission_stack(tmp_path, repository)
    repository_identity = RepositoryIdentity(path=repository)
    repository_scope = fleet_repository_scope({repository_identity.repository_id})
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    source_before = _repository_bytes(repository)
    _install_source_identity_observer(monkeypatch, repository)

    admission = planner.plan(intent, repository_scope=repository_scope)
    _ticket, run = lifecycle.admit(
        "template-startability-positive",
        intent,
        repository_scope,
        [entry.repository for entry in admission.targets],
        expected_durable_revision=admission.durable_revision,
        repository_admissions=admission.repository_admissions,
        source=FleetRunSource.MANUAL,
    )

    events = state.journal.run_events("template-startability-positive").events
    detail = state.journal.read_view().required_run_detail("template-startability-positive")
    assert run.status is FleetRunStatus.QUEUED
    assert [event.event_type for event in events] == [FleetRunEventType.RUN_ADMITTED]
    assert isinstance(detail.execution, DirectFleetExecutionAdmission)
    assert detail.run.target == target
    assert backend.availability_checks == 0
    assert backend.execute_calls == 0
    assert attempts.all() == []
    assert _repository_bytes(repository) == source_before
    assert database.durable_revision() > admission.durable_revision


def test_invalid_shipped_template_inputs_fail_before_state_or_work_mutation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repositories" / "fixture"
    seed_cicd_repository(repository)
    database, state, attempts, _planner, _lifecycle, backend = _admission_stack(
        tmp_path, repository
    )
    revision_before = database.durable_revision()
    source_before = _repository_bytes(repository)

    with pytest.raises(FleetPlanInvalid, match="received unknown inputs: unexpected"):
        _reviewed_shipped_target(
            "python/dependency-inventory",
            inputs={"unexpected": "value"},
        )

    assert state.journal.read_view().run_detail("template-startability-negative") is None
    assert attempts.all() == []
    assert backend.availability_checks == 0
    assert backend.execute_calls == 0
    assert database.durable_revision() == revision_before
    assert _repository_bytes(repository) == source_before


def _copy_iris_certification_fixture(repository: Path) -> None:
    shutil.copytree(IRIS_CERTIFICATION_FIXTURE, repository)
    (repository / ".git").mkdir()


def _iris_uv_requirement(plan: ResolvedFleetPlan) -> tuple[ResolvedCommandStep, str]:
    uv_steps = tuple(
        step for step in plan.steps if isinstance(step, ResolvedCommandStep) and step.uv is not None
    )
    assert len(uv_steps) == 1
    step = uv_steps[0]
    assert step.uv is not None
    prefix = (
        "uv",
        "run",
        "--frozen",
        "--no-default-groups",
        "--project",
        step.uv.project,
        "--group",
        step.uv.group,
    )
    assert step.argv[: len(prefix)] == prefix
    assert len(step.argv) > len(prefix)
    return step, step.argv[len(prefix)]


def _materialized_executable(environment: Path, name: str) -> Path | None:
    candidates = (
        environment / "bin" / name,
        environment / "Scripts" / name,
        environment / "Scripts" / f"{name}.exe",
        environment / "Scripts" / f"{name}.cmd",
    )
    return next((path for path in candidates if path.is_file()), None)


def _prove_iris_project_executable(
    plan: ResolvedFleetPlan,
    *,
    repository: Path,
    preparation_root: Path,
) -> Path:
    step, required_executable = _iris_uv_requirement(plan)
    assert step.uv is not None
    authority = _test_path_authority_for_plan(plan)
    uv = authority.tool("uv")
    python = find_uv_system_python(uv)
    if python is None:
        raise FleetPlanInvalid(
            "Iris certification fixture could not select native uv system Python authority"
        )

    preparation = ManagedPreparationPathLayout.from_root(preparation_root)
    environment = preparation.root / "iris-environment"
    native_uv = capture_native_uv_authority()
    env = native_uv.environment(
        preparation,
        environment=environment,
        cache=preparation.cache / "uv",
        uv_overrides={
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PYTHON": str(python),
        },
    )
    argv = (
        str(uv),
        "sync",
        "--project",
        str(repository),
        "--locked",
        "--no-default-groups",
        "--group",
        step.uv.group,
    )
    lock = repository / "uv.lock"
    lock_before = lock.read_bytes()
    # This copied fixture is a new execution subject. Materialize its native
    # lock once, then verify that same interpreter/environment/cache offline.
    # Caller-selected offline mode still applies to the preparation request.
    for command, command_env in (
        (argv, env),
        ((*argv, "--check", "--offline"), {**env, "UV_OFFLINE": "1"}),
    ):
        result = subprocess.run(
            command,
            cwd=repository,
            env=command_env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "native uv sync failed").strip()
            raise FleetPlanInvalid(f"Native uv rejected Iris certification fixture: {detail}")
        if lock.read_bytes() != lock_before:
            raise FleetPlanInvalid("Native uv rewrote the selected Iris certification lock")

    executable = _materialized_executable(environment, required_executable)
    if executable is None:
        raise FleetPlanInvalid(
            f"required project executable `{required_executable}` is not resolvable"
        )

    metadata = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    script_target = metadata.get("project", {}).get("scripts", {}).get(required_executable)
    if script_target != "oh_goon_examples.iris:main":
        raise FleetPlanInvalid(
            f"required project executable `{required_executable}` is not resolvable"
        )
    if not (repository / "src" / "oh_goon_examples" / "iris.py").is_file():
        raise FleetPlanInvalid(
            f"required project executable `{required_executable}` target is missing"
        )
    return executable


def test_iris_certification_fixture_resolves_entrypoint_and_reaches_admission_without_work(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repositories" / "iris"
    _copy_iris_certification_fixture(repository)
    target = _reviewed_shipped_target("ml/iris-training")
    database, state, attempts, planner, lifecycle, backend = _admission_stack(tmp_path, repository)
    repository_identity = RepositoryIdentity(path=repository)
    repository_scope = fleet_repository_scope({repository_identity.repository_id})
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    source_before = _repository_bytes(repository)

    executable = _prove_iris_project_executable(
        target.plan,
        repository=repository,
        preparation_root=tmp_path / "iris-certification",
    )
    assert executable.name == "iris-train"
    assert _repository_bytes(repository) == source_before
    _install_source_identity_observer(monkeypatch, repository)

    admission = planner.plan(intent, repository_scope=repository_scope)
    _ticket, run = lifecycle.admit(
        "template-iris-startability-positive",
        intent,
        repository_scope,
        [entry.repository for entry in admission.targets],
        expected_durable_revision=admission.durable_revision,
        repository_admissions=admission.repository_admissions,
        source=FleetRunSource.MANUAL,
    )

    events = state.journal.run_events("template-iris-startability-positive").events
    assert run.status is FleetRunStatus.QUEUED
    assert [event.event_type for event in events] == [FleetRunEventType.RUN_ADMITTED]
    assert backend.availability_checks > 0
    assert backend.execute_calls == 0
    assert attempts.all() == []
    assert _repository_bytes(repository) == source_before
    assert database.durable_revision() > admission.durable_revision


@pytest.mark.parametrize("entrypoint_mutation", ["missing", "renamed"])
def test_iris_certification_fixture_rejects_unresolvable_required_entrypoint_before_admission(
    tmp_path: Path,
    entrypoint_mutation: str,
) -> None:
    repository = tmp_path / "repositories" / "iris"
    _copy_iris_certification_fixture(repository)
    pyproject = repository / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    entrypoint = 'iris-train = "oh_goon_examples.iris:main"'
    assert entrypoint in source
    if entrypoint_mutation == "missing":
        source = source.replace(f"{entrypoint}\n", "")
    else:
        source = source.replace(entrypoint, 'iris-fit = "oh_goon_examples.iris:main"')
    pyproject.write_text(source, encoding="utf-8")

    target = _reviewed_shipped_target("ml/iris-training")
    database, state, attempts, _planner, _lifecycle, backend = _admission_stack(
        tmp_path, repository
    )
    revision_before = database.durable_revision()
    source_before = _repository_bytes(repository)

    with pytest.raises(
        FleetPlanInvalid,
        match=r"required project executable `iris-train` is not resolvable",
    ):
        _prove_iris_project_executable(
            target.plan,
            repository=repository,
            preparation_root=tmp_path / "iris-certification-negative",
        )

    assert state.journal.read_view().run_detail("template-iris-startability-negative") is None
    assert attempts.all() == []
    assert backend.availability_checks == 0
    assert backend.execute_calls == 0
    assert database.durable_revision() == revision_before
    assert _repository_bytes(repository) == source_before
