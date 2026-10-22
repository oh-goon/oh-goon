from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid, StateConflict, TargetSelectionError
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    MissingConfigPolicy,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedImport,
    ResolvedLocalSearchAgentStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ExecutionPurpose,
    MaintenanceExecutionPolicy,
    VerificationExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
)
from oh_goon.core.models.fleet_plan_requirements import (
    PyprojectValueRequirement,
    RepositoryFileExistsRequirement,
    RepositoryFolderExistsRequirement,
    RepositoryPathExistsRequirement,
    RepositoryRequirement,
    RepositoryValueType,
    UvProjectRequirement,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
    PlanStepCapabilities,
    RetainedPlanCacheSource,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.vocabulary import FleetScope
from oh_goon.integrations import fleet_plan_preview, fleet_plan_repository_scope
from oh_goon.integrations.fleet_plan_admission import (
    RejectedCicdRepositoryAdmission,
    inspect_cicd_repository,
)
from oh_goon.integrations.fleet_plan_preview import (
    repository_plan_admission_evidence,
    require_repository_plan_admission_current,
)
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import TEST_ISOLATION_BACKEND, prepare_physical_test_run
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY
from tests.factories.run_admission import repository_admission_for


def _plan(
    *,
    trust_repository_code: bool = True,
    requirements: tuple[RepositoryRequirement, ...] = (),
) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="strict",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=trust_repository_code,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=tuple(requirements),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )


def _entry(path: Path) -> RepositoryCatalogEntry:
    return IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository={"path": path})


def test_verification_plan_repository_preflight_rejects_before_run_admitted(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    plan = ResolvedFleetPlan.create(
        name="verification",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                working_directory="missing",
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(
        TargetSelectionError, match="Fleet Plan admission rejected before RUN_ADMITTED"
    ) as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "missing" in str(exc.value)


def test_strict_native_rejects_before_source_capture_and_run_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="selected",
        definition=NativeAgentDefinition(executable="/selected/pi-custom"),
        profile=NativeAgentProfile(),
        executable_sha256="a" * 64,
    )
    native = ResolvedLocalSearchAgentStep(
        step_id="native-agent",
        argv=("python",),
        timeout_seconds=30,
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="native-agent",
        harness="native",
        native_agent=selected,
    )
    plan = ResolvedFleetPlan.create(
        name="unsupported-strict-native",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(native,),
        native_agent=selected,
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=native.step_id,
                handoff_identity=native.handoff_identity,
                delivery_sha256=native.delivery_sha256,
                reference_id="retained-native-delivery",
            ),
        ),
    )

    def forbidden_source_capture(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsupported native execution must fail before source capture")

    monkeypatch.setattr(fleet_plan_preview, "source_snapshot_identity", forbidden_source_capture)
    planner = object.__new__(FleetAdmissionPlanner)
    with pytest.raises(
        TargetSelectionError, match="before RUN_ADMITTED.*strict CI/CD is unsupported"
    ):
        planner._validate_plan_repository_admission(target, [_entry(repository)])

    # Historical immutable Plans remain decodable, but cannot gain a new admission.
    restored = ResolvedFleetPlan.model_validate_json(
        plan.model_dump_json(exclude_computed_fields=True)
    )
    assert restored == plan


@pytest.mark.parametrize(
    "argv",
    [
        ("backend/.venv/bin/private-tool",),
        ("frontend/node_modules/.bin/private-tool",),
    ],
)
def test_strict_source_only_step_rejects_raw_materialized_executable_before_run_admitted(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = ResolvedFleetPlan.create(
        name="strict-materialized-path",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=argv,
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "live package-manager materialization paths" in str(exc.value)


@pytest.mark.parametrize(
    ("working_directory", "argv", "expected"),
    [
        ("backend", (".venv/bin/private-tool",), "backend/.venv/bin/private-tool"),
        (
            "frontend",
            ("node_modules/.bin/private-tool",),
            "frontend/node_modules/.bin/private-tool",
        ),
        ("tools", (".venv/bin/private-tool",), "tools/.venv/bin/private-tool"),
        (
            "packages/web",
            ("node_modules/.bin/private-tool",),
            "packages/web/node_modules/.bin/private-tool",
        ),
    ],
)
def test_strict_source_only_step_rejects_canonical_materialized_cwd_alias_before_run_admitted(
    tmp_path: Path,
    working_directory: str,
    argv: tuple[str, ...],
    expected: str,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / working_directory).mkdir(parents=True, exist_ok=True)
    plan = ResolvedFleetPlan.create(
        name="strict-materialized-cwd-alias",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=argv,
                working_directory=working_directory,
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "argv",
    [
        ("sh", "-c", "cd backend && .venv/bin/private-tool"),
        ("sh", "-c", "(cd backend && .venv/bin/private-tool)"),
        ("sh", "-c", "cd packages/web && node_modules/.bin/private-tool"),
    ],
)
def test_strict_source_only_shell_rejects_materialized_cwd_alias_before_run_admitted(
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "packages/web").mkdir(parents=True, exist_ok=True)
    plan = ResolvedFleetPlan.create(
        name="strict-shell-materialized-cwd-alias",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=argv,
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "live package-manager materialization paths" in str(exc.value)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ("sh", "-c", "/workspace/backend/.venv/bin/private-tool"),
            "backend/.venv/bin/private-tool",
        ),
        (
            ("sh", "-c", "/workspace/frontend/node_modules/.bin/private-tool"),
            "frontend/node_modules/.bin/private-tool",
        ),
    ],
)
def test_strict_source_only_step_rejects_canonical_sandbox_materialized_alias_before_run_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    expected: str,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = ResolvedFleetPlan.create(
        name="strict-sandbox-materialized-alias",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=argv,
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)
    monkeypatch.setattr(
        fleet_plan_preview,
        "source_snapshot_identity",
        lambda _root: SimpleNamespace(sha256="c" * 64, byte_count=1, entry_count=1),
    )

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "live package-manager materialization paths" in str(exc.value)
    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "fragment",
    [
        'p=backend/.venv/bin/private-tool; "$p"',
        'command "$tool"',
        'env "$tool"',
        '"${TOOL}"',
        "$(printf tool)",
        'eval "$tool"',
        "`printf tool`",
    ],
)
def test_strict_shell_rejects_unresolved_executable_authority_before_run_admitted(
    tmp_path: Path,
    fragment: str,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = ResolvedFleetPlan.create(
        name="strict-unresolved-shell-authority",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("sh", "-c", fragment),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "execution authority for explicit shell command is unproven" in str(exc.value)
    assert "unresolved" in str(exc.value) or "unprovable" in str(exc.value)


def test_strict_make_owner_rejects_hidden_venv_dependency_before_run_admitted(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "Makefile").write_text(
        "package:\n\tbackend/.venv/bin/private-tool\n",
        encoding="utf-8",
    )
    plan = ResolvedFleetPlan.create(
        name="strict-hidden-materialized-path",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="package",
                argv=("make", "package"),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "backend/.venv/bin/private-tool" in str(exc.value)


@pytest.mark.parametrize(
    "recipe",
    [
        "cd backend && .venv/bin/private-tool",
        "(cd backend && .venv/bin/private-tool)",
        "sh -c 'cd backend && .venv/bin/private-tool'",
        "cd packages/web && node_modules/.bin/private-tool",
    ],
)
def test_strict_make_owner_rejects_materialized_cwd_alias_before_run_admitted(
    tmp_path: Path,
    recipe: str,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "Makefile").write_text(
        f"package:\n\t{recipe}\n",
        encoding="utf-8",
    )
    plan = ResolvedFleetPlan.create(
        name="strict-hidden-materialized-cwd-alias",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="package",
                argv=("make", "package"),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "live package-manager materialization paths" in str(exc.value)


def test_verification_plan_keeps_materialized_cwd_alias_outside_strict_cicd_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    wrapper = repository / "backend/.venv/bin/private-tool"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    plan = ResolvedFleetPlan.create(
        name="verification-materialized-cwd-alias",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=(".venv/bin/private-tool",),
                working_directory="backend",
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)
    monkeypatch.setattr(
        fleet_plan_preview,
        "source_snapshot_identity",
        lambda _root: SimpleNamespace(sha256="c" * 64, byte_count=1, entry_count=1),
    )

    planner._validate_plan_repository_admission(
        structural_fleet_plan_target(plan),
        [_entry(repository)],
    )


@pytest.mark.parametrize(
    ("name", "execution"),
    (
        ("verification-a", VerificationExecutionPolicy()),
        ("verification-b", VerificationExecutionPolicy()),
        ("strict-cicd", CicdExecutionPolicy()),
    ),
)
def test_common_repository_invalidity_rejects_every_purpose_before_run_admitted(
    tmp_path: Path,
    name: str,
    execution,
) -> None:
    repository = tmp_path / name
    seed_cicd_repository(repository)
    plan = ResolvedFleetPlan.create(
        name=name,
        description="",
        execution=execution,
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                working_directory="missing",
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "missing" in str(exc.value)


def test_valid_direct_plan_does_not_inherit_strict_repository_isolation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "plain-directory"
    repository.mkdir()
    verification = ResolvedFleetPlan.create(
        name="verification",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    strict = ResolvedFleetPlan.create(
        name="strict",
        description="",
        execution=CicdExecutionPolicy(),
        steps=verification.steps,
    )
    planner = object.__new__(FleetAdmissionPlanner)

    admitted = planner._validate_plan_repository_admission(
        structural_fleet_plan_target(verification),
        [_entry(repository)],
    )
    assert len(admitted) == 1

    strict_admitted = planner._validate_plan_repository_admission(
        structural_fleet_plan_target(strict),
        [_entry(repository)],
    )
    assert len(strict_admitted) == 1


def test_required_precommit_config_rejects_before_run_admitted(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    plan = ResolvedFleetPlan.create(
        name="verification-precommit",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedPreCommitStep(
                step_id="hooks",
                if_missing=MissingConfigPolicy.FAIL,
                timeout_seconds=30,
                runtime_id="tool-precommit",
                runtime_pyproject_sha256="1" * 64,
                runtime_uv_lock_sha256="2" * 64,
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert ".pre-commit-config.yaml" in str(exc.value)


def test_retained_cross_run_cache_is_strict_cicd_only() -> None:
    step = ResolvedCommandStep(
        timeout_seconds=60,
        step_id="consume",
        argv=("true",),
        cache=PlanStepCache(source=RetainedPlanCacheSource(reference_id="ref-" + "a" * 32)),
        capabilities=PlanStepCapabilities(),
    )

    with pytest.raises(ValueError, match="retained cache source requires execution.purpose=cicd"):
        ResolvedFleetPlan.create(
            name="maintenance-retained",
            description="",
            execution=MaintenanceExecutionPolicy(),
            steps=(step,),
        )

    strict = ResolvedFleetPlan.create(
        name="strict-retained",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(step,),
    )
    assert isinstance(strict.steps[0].cache.source, RetainedPlanCacheSource)


def test_repository_plan_admission_receipt_turns_later_invalidity_into_staleness(
    tmp_path: Path, monkeypatch
) -> None:
    repository_path = tmp_path / "repo"
    work = repository_path / "work"
    work.mkdir(parents=True)
    repository = RepositoryIdentity(path=repository_path)
    plan = ResolvedFleetPlan.create(
        name="verification",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                working_directory="work",
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    monkeypatch.setattr(
        fleet_plan_preview,
        "source_snapshot_identity",
        lambda _root: SimpleNamespace(sha256="a" * 64, byte_count=1, entry_count=1),
    )
    admitted = repository_plan_admission_evidence(repository, plan)
    work.rmdir()

    with pytest.raises(StateConflict, match="became stale after RUN_ADMITTED"):
        require_repository_plan_admission_current(repository, plan, admitted)


def test_verification_admission_binds_filtered_source_identity(tmp_path: Path, monkeypatch) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    authored = repository_path / "owned.txt"
    authored.write_text("one\n", encoding="utf-8")
    repository = RepositoryIdentity(path=repository_path)
    plan = ResolvedFleetPlan.create(
        name="verification",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )

    def identity(root: Path):
        data = (root / "owned.txt").read_bytes()
        return SimpleNamespace(
            sha256=hashlib.sha256(data).hexdigest(),
            byte_count=len(data),
            entry_count=1,
        )

    monkeypatch.setattr(fleet_plan_preview, "source_snapshot_identity", identity)
    admitted = repository_plan_admission_evidence(repository, plan)
    authored.write_text("two\n", encoding="utf-8")
    current = repository_plan_admission_evidence(repository, plan)

    assert current.semantic_preview_id == admitted.semantic_preview_id
    assert (
        current.source_authority.subject_instance_id
        == admitted.source_authority.subject_instance_id
    )
    assert current.source_authority != admitted.source_authority
    assert current.admission_id != admitted.admission_id
    with pytest.raises(StateConflict, match="became stale after RUN_ADMITTED"):
        require_repository_plan_admission_current(repository, plan, admitted)


def test_repository_plan_admission_receipt_detects_same_path_subject_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    repository = RepositoryIdentity(path=repository_path)
    plan = ResolvedFleetPlan.create(
        name="verification",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    monkeypatch.setattr(
        fleet_plan_preview,
        "source_snapshot_identity",
        lambda _root: SimpleNamespace(sha256="b" * 64, byte_count=1, entry_count=1),
    )
    admitted = repository_plan_admission_evidence(repository, plan)

    repository_path.rmdir()
    repository_path.mkdir()

    with pytest.raises(StateConflict, match="became stale after RUN_ADMITTED"):
        require_repository_plan_admission_current(repository, plan, admitted)


def test_rejected_cicd_admission_requires_an_issue() -> None:
    with pytest.raises(ValueError, match="at least one issue"):
        RejectedCicdRepositoryAdmission(issues=())


def test_cicd_plan_does_not_author_repository_identity_requirement(tmp_path: Path) -> None:
    source = tmp_path / "goon.yml"
    source.write_text(
        """version: 1
steps:
  - run: "true"
""",
        encoding="utf-8",
    )
    resolved = FleetPlanResolver().resolve(source)
    assert resolved.requirements == ()


def test_explicit_cicd_root_goon_inherits_executor_capacity(tmp_path: Path) -> None:
    source = tmp_path / "goon.yml"
    source.write_text(
        """version: 1
execution: {purpose: cicd}
steps:
  - id: check
    run: "true"
""",
        encoding="utf-8",
    )

    resolved = FleetPlanResolver().resolve(source)

    assert resolved.execution.purpose is ExecutionPurpose.CICD
    assert resolved.execution.trust_repository_code is True
    assert resolved.execution.resources.cpu_millis is None
    assert resolved.execution.resources.memory_mib is None
    assert resolved.execution.resources.max_processes is None


def test_explicit_file_and_folder_requirements_are_repository_admission_ssot(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8"
    )
    (repository / "src").mkdir()

    inspection = inspect_cicd_repository(
        repository,
        (
            RepositoryFileExistsRequirement(path="pyproject.toml"),
            RepositoryFolderExistsRequirement(path="src"),
            RepositoryFileExistsRequirement(path="uv.lock"),
        ),
    )

    assert isinstance(inspection, RejectedCicdRepositoryAdmission)
    assert inspection.issues == ("repository requirement file is missing: uv.lock",)


def test_strict_plan_admission_accepts_non_git_source_when_requirements_pass(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    target = structural_fleet_plan_target(_plan())
    planner = object.__new__(FleetAdmissionPlanner)

    admitted = planner._validate_plan_repository_admission(target, [_entry(repository)])

    assert len(admitted) == 1
    assert admitted[0].repository_id == RepositoryIdentity(path=repository).repository_id


def test_repository_admission_owns_validation_not_runtime_backend_selection(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    target = structural_fleet_plan_target(_plan(trust_repository_code=False))
    planner = object.__new__(FleetAdmissionPlanner)

    # Repository eligibility is proven first. Physical executor capability matching is
    # the later FleetExecutorPool admission boundary, not another planner side effect.
    admitted = planner._validate_plan_repository_admission(target, [_entry(repository)])
    assert len(admitted) == 1
    assert admitted[0].repository_id == RepositoryIdentity(path=repository).repository_id
    assert admitted[0].plan_id == target.plan.plan_id


def test_frozen_repository_snapshot_is_revalidated_and_evidenced(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _plan()

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        evidence = run.execution_input_evidence()
        assert evidence.repository_admission is not None
        assert evidence.repository_admission.kind == "requirements"
        assert evidence.repository_admission.requirement_count == 0


def test_frozen_snapshot_validation_happens_before_tool_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    captured = False

    def fail_if_called(*args, **kwargs):
        nonlocal captured
        captured = True
        raise AssertionError("host tool capture must not run for invalid repository admission")

    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_host_resources.capture_host_tools",
        fail_if_called,
    )

    with pytest.raises(FleetPlanInvalid, match="repository requirements failed"):
        prepare_physical_test_run(
            RepositoryIdentity(path=repository),
            _plan(requirements=(RepositoryFileExistsRequirement(path="required.txt"),)),
            isolation_backend=TEST_ISOLATION_BACKEND,
            path_authority=TEST_PATH_AUTHORITY,
        )
    assert captured is False


def test_strict_cicd_rejects_overlapping_repository_roots_before_run_admitted(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    seed_cicd_repository(parent)
    seed_cicd_repository(child)
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="must not overlap"):
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(_plan()),
            [_entry(parent), _entry(child)],
        )


def test_strict_cicd_rejects_duplicate_repository_root_ownership_before_run_admitted(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="must not overlap"):
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(_plan()),
            [_entry(repository), _entry(repository)],
        )


def test_strict_cicd_rechecks_external_hard_link_before_snapshot(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    outside = tmp_path / "foreign-secret"
    outside.write_text("secret", encoding="utf-8")
    try:
        (repository / "linked-secret").hardlink_to(outside)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")

    with pytest.raises(FleetPlanInvalid, match="hard-link ownership"):
        prepare_physical_test_run(
            RepositoryIdentity(path=repository),
            _plan(),
            isolation_backend=TEST_ISOLATION_BACKEND,
            path_authority=TEST_PATH_AUTHORITY,
        )


def test_repository_requirement_non_empty_is_string_only() -> None:
    with pytest.raises(ValueError, match="non_empty is valid only for string"):
        PyprojectValueRequirement(
            field="project.count",
            value_type=RepositoryValueType.INTEGER,
            non_empty=True,
        )


def test_plan_repository_requirements_fail_together_before_run_admitted(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _plan(
        requirements=(
            RepositoryPathExistsRequirement(path="uv.lock"),
            PyprojectValueRequirement(
                field="project.imagename",
                value_type=RepositoryValueType.STRING,
                non_empty=True,
            ),
            UvProjectRequirement(group="dev"),
        )
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    message = str(exc.value)
    assert "repository requirement path is missing: uv.lock" in message
    assert "repository requirement pyproject field is missing: project.imagename" in message
    assert "locked UV project requires uv.lock: ." in message


def test_repository_requirements_report_all_typed_failures_in_one_pass(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    pyproject = repository / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'version = "0.0.0"',
            'version = 7\nimagename = "   "\ncount = 3\nflag = true',
        )
        + '\n[dependency-groups]\ndev = [123, "!!!"]\n',
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text(
        'version = 1\nrevision = 1\n[[package]]\nname = "pytest"\nversion = "1"\n',
        encoding="utf-8",
    )

    inspection = inspect_cicd_repository(
        repository,
        (
            RepositoryPathExistsRequirement(path="../outside"),
            PyprojectValueRequirement(
                field="project.version", value_type=RepositoryValueType.STRING
            ),
            PyprojectValueRequirement(
                field="project.imagename",
                value_type=RepositoryValueType.STRING,
                non_empty=True,
            ),
            PyprojectValueRequirement(
                field="project.count", value_type=RepositoryValueType.INTEGER
            ),
            PyprojectValueRequirement(field="project.flag", value_type=RepositoryValueType.BOOLEAN),
            UvProjectRequirement(group="missing"),
            UvProjectRequirement(group="dev"),
        ),
    )

    assert isinstance(inspection, RejectedCicdRepositoryAdmission)
    rendered = "; ".join(inspection.issues)
    assert "must not contain '..' traversal" in rendered
    assert "project.version must be string" in rendered
    assert "project.imagename" in rendered and "must be non-empty" in rendered


def test_repository_requirements_skip_typed_checks_when_pyproject_is_missing(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")

    inspection = inspect_cicd_repository(
        repository,
        (
            PyprojectValueRequirement(
                field="project.version", value_type=RepositoryValueType.STRING
            ),
            UvProjectRequirement(group="dev"),
        ),
    )

    assert isinstance(inspection, RejectedCicdRepositoryAdmission)
    assert "missing pyproject.toml" in inspection.issues


@pytest.mark.parametrize("uv_lock", ["not = [valid", "version = 1\nrevision = 1\n"])
def test_uv_lock_structure_is_owned_by_native_uv_not_static_admission(
    tmp_path: Path, uv_lock: str
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "uv.lock").write_text(uv_lock, encoding="utf-8")

    pyproject = repository / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + "\n[dependency-groups]\ndev = []\n",
        encoding="utf-8",
    )
    inspection = inspect_cicd_repository(repository, (UvProjectRequirement(group="dev"),))

    assert not isinstance(inspection, RejectedCicdRepositoryAdmission)


def test_uv_project_requirement_transports_group_without_shadow_validation(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    pyproject = repository / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + '\n[dependency-groups]\ndev = ["pytest-cov>=6"]\n',
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text(
        "version = 1\nrevision = 1\npackage = []\n", encoding="utf-8"
    )

    accepted = inspect_cicd_repository(repository, (UvProjectRequirement(group="dev"),))
    assert not isinstance(accepted, RejectedCicdRepositoryAdmission)

    deferred = inspect_cicd_repository(repository, (UvProjectRequirement(group="missing"),))
    assert not isinstance(deferred, RejectedCicdRepositoryAdmission)


def test_plan_repository_requirements_are_rechecked_on_frozen_snapshot(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    pyproject = repository / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'version = "0.0.0"', 'version = "0.0.0"\nimagename = "demo/image"'
        ),
        encoding="utf-8",
    )
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + "\n[dependency-groups]\ndev = []\n",
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text(
        "version = 1\nrevision = 1\npackage = []\n",
        encoding="utf-8",
    )
    plan = _plan(
        requirements=(
            RepositoryPathExistsRequirement(path="uv.lock"),
            PyprojectValueRequirement(
                field="project.imagename",
                value_type=RepositoryValueType.STRING,
                non_empty=True,
            ),
            UvProjectRequirement(group="dev"),
        )
    )
    planner = object.__new__(FleetAdmissionPlanner)
    target = structural_fleet_plan_target(plan)

    admitted = planner._validate_plan_repository_admission(target, [_entry(repository)])
    assert len(admitted) == 1
    assert admitted[0].repository_id == RepositoryIdentity(path=repository).repository_id
    assert admitted[0].plan_id == target.plan.plan_id

    # The run boundary evaluates the same requirements against the private frozen input.
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        evidence = run.execution_input_evidence()
        assert evidence.repository_admission is not None


def test_requirements_admission_has_no_hidden_python_baseline_and_checks_explicit_files(
    tmp_path: Path,
) -> None:
    from oh_goon.integrations.fleet_plan_admission import validate_cicd_repository

    (tmp_path / ".git").mkdir()
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    evidence = validate_cicd_repository(
        tmp_path,
        (RepositoryFileExistsRequirement(path="package.json"),),
    )

    assert evidence.kind == "requirements"
    assert evidence.requirement_count == 1
    assert evidence.requirements_id


def test_uv_project_requirement_is_derived_and_valid_without_package_shadow_model(
    tmp_path: Path,
) -> None:
    from oh_goon.integrations.fleet_plan_admission import validate_cicd_repository

    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[dependency-groups]\ndev = []\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\nrevision = 1\npackage = []\n", encoding="utf-8")
    evidence = validate_cicd_repository(tmp_path, (UvProjectRequirement(group="dev"),))

    assert evidence.kind == "requirements"
    assert evidence.requirement_count == 1


def test_sanitized_execution_snapshot_validates_requirements_without_git_metadata(
    tmp_path: Path,
) -> None:
    from oh_goon.integrations.fleet_plan_admission import (
        validate_cicd_repository,
        validate_cicd_snapshot_requirements,
    )

    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    requirement = RepositoryFileExistsRequirement(path="package.json")

    source_evidence = validate_cicd_repository(tmp_path, (requirement,))
    assert source_evidence.kind == "requirements"
    assert source_evidence.requirement_count == 1

    evidence = validate_cicd_snapshot_requirements(tmp_path, (requirement,))
    assert evidence.kind == "requirements"
    assert evidence.requirement_count == 1


def test_repository_requirements_cannot_target_source_excluded_generated_state() -> None:
    from pydantic import ValidationError

    for requirement_type, path in (
        (RepositoryFolderExistsRequirement, ".git"),
        (RepositoryFolderExistsRequirement, "backend/.venv"),
        (RepositoryPathExistsRequirement, "frontend/node_modules/react"),
    ):
        with pytest.raises(ValidationError, match="source-excluded paths"):
            requirement_type(path=path)


def test_strict_cicd_selected_root_set_is_proven_once_before_tree_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = tuple(tmp_path / f"repo-{index}" for index in range(4))
    for root in roots:
        root.mkdir()

    selected_root_sets: list[tuple[Path, ...]] = []
    tree_scans: list[tuple[Path, tuple[Path, ...]]] = []

    def observe_selected_roots(repository_roots: tuple[Path, ...]) -> tuple[Path, ...]:
        selected_root_sets.append(repository_roots)
        return tuple(root.resolve() for root in repository_roots)

    def observe_isolation(self: object, *, other_repository_roots=()) -> None:
        tree_scans.append((self.root, tuple(other_repository_roots)))

    monkeypatch.setattr(
        "oh_goon.services.fleet_admission.RepositoryPathSpace.require_disjoint_sources",
        observe_selected_roots,
    )
    monkeypatch.setattr(
        "oh_goon.services.fleet_admission.RepositoryPathSpace.require_isolated_source",
        observe_isolation,
    )
    monkeypatch.setattr(
        "oh_goon.services.fleet_admission.repository_plan_admission_evidence",
        lambda repository, plan: repository_admission_for(
            repository_id=repository.repository_id,
            plan_id=plan.plan_id,
            active_step_ids=tuple(step.step_id for step in plan.steps),
        ),
    )

    planner = object.__new__(FleetAdmissionPlanner)
    planner._validate_plan_repository_admission(
        structural_fleet_plan_target(_plan()),
        [_entry(root) for root in roots],
    )

    assert selected_root_sets == [roots]
    assert tree_scans == [(root.resolve(), ()) for root in roots]


def test_strict_cicd_duplicate_entries_reach_selected_root_set_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    observed: list[tuple[Path, ...]] = []

    def reject_duplicate(repository_roots: tuple[Path, ...]) -> tuple[Path, ...]:
        observed.append(repository_roots)
        raise FleetPlanInvalid("duplicate selected root")

    monkeypatch.setattr(
        "oh_goon.services.fleet_admission.RepositoryPathSpace.require_disjoint_sources",
        reject_duplicate,
    )

    planner = object.__new__(FleetAdmissionPlanner)
    with pytest.raises(TargetSelectionError, match="duplicate selected root"):
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(_plan()),
            [_entry(repository), _entry(repository)],
        )

    assert observed == [(repository, repository)]


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("xargs backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("printf x | xargs backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        (r"find . -exec backend/.venv/bin/private-tool {} \;", "backend/.venv/bin/private-tool"),
        (
            r"find . -execdir frontend/node_modules/.bin/private-tool {} \;",
            "frontend/node_modules/.bin/private-tool",
        ),
        ("timeout 5 backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("nice backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("nohup backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("stdbuf -oL backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("setsid backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
    ],
)
def test_strict_shell_child_launcher_rejects_materialized_executable_before_run_admitted(
    tmp_path: Path,
    fragment: str,
    expected: str,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = ResolvedFleetPlan.create(
        name="strict-child-launcher-materialization",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("sh", "-c", fragment),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "live package-manager materialization paths" in str(exc.value)
    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "recipe",
    [
        "printf x | xargs backend/.venv/bin/private-tool",
        r"find . -exec backend/.venv/bin/private-tool {} \;",
        "timeout 5 backend/.venv/bin/private-tool",
    ],
)
def test_strict_make_child_launcher_rejects_materialized_executable_before_run_admitted(
    tmp_path: Path,
    recipe: str,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "Makefile").write_text(
        f"package:\n\t{recipe}\n",
        encoding="utf-8",
    )
    plan = ResolvedFleetPlan.create(
        name="strict-make-child-launcher-materialization",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="package",
                argv=("make", "package"),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    planner = object.__new__(FleetAdmissionPlanner)

    with pytest.raises(TargetSelectionError, match="before RUN_ADMITTED") as exc:
        planner._validate_plan_repository_admission(
            structural_fleet_plan_target(plan),
            [_entry(repository)],
        )

    assert "live package-manager materialization paths" in str(exc.value)
    assert "backend/.venv/bin/private-tool" in str(exc.value)


def test_repository_plan_admission_resolves_import_applicability_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_path = tmp_path / "repo-once"
    repository_path.mkdir()
    step = ResolvedCommandStep(
        timeout_seconds=60,
        step_id="optional.check",
        argv=("true",),
        capabilities=PlanStepCapabilities(),
    )
    plan = ResolvedFleetPlan.create(
        name="verification-once",
        description="",
        execution=CicdExecutionPolicy(),
        imports=(
            ResolvedImport(
                import_id="optional",
                name="general/optional",
                source_sha256="a" * 64,
                step_ids=(step.step_id,),
                requirements=(RepositoryFileExistsRequirement(path="marker.txt"),),
            ),
        ),
        steps=(step,),
    )
    real = fleet_plan_repository_scope.import_applicability
    calls: list[Path] = []

    def once(repository: Path, resolved: ResolvedFleetPlan):
        calls.append(repository)
        if len(calls) != 1:
            raise AssertionError("repository applicability was resolved more than once")
        return real(repository, resolved)

    monkeypatch.setattr(fleet_plan_repository_scope, "import_applicability", once)
    monkeypatch.setattr(
        fleet_plan_preview,
        "source_snapshot_identity",
        lambda _root: SimpleNamespace(sha256="d" * 64, byte_count=0, entry_count=0),
    )

    admitted = repository_plan_admission_evidence(RepositoryIdentity(path=repository_path), plan)

    assert calls == [repository_path]
    assert admitted.import_applicability[0].status == "NOT_APPLICABLE"
    assert admitted.active_step_ids == ()


def test_admitted_active_plan_does_not_redecide_repository_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_path = tmp_path / "repo-transport"
    repository_path.mkdir()
    step = ResolvedCommandStep(
        timeout_seconds=60,
        step_id="active",
        argv=("true",),
        capabilities=PlanStepCapabilities(),
    )
    plan = ResolvedFleetPlan.create(
        name="transport",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(step,),
    )
    admitted = repository_admission_for(
        repository_id=RepositoryIdentity(path=repository_path).repository_id,
        plan_id=plan.plan_id,
        active_step_ids=(step.step_id,),
    )

    monkeypatch.setattr(
        fleet_plan_repository_scope,
        "import_applicability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("transport consumption must not resolve repository scope")
        ),
    )

    active = fleet_plan_repository_scope.admitted_active_plan(plan, admitted)
    assert tuple(item.step_id for item in active.steps) == (step.step_id,)
