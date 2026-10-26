from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedFleetPlan,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_evidence import PlanToolEvidence
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.path_authority import (
    ContainedPathSpace,
    RepositoryPathSpace,
    StrictHostPathAuthority,
    ensure_private_directory,
)
from oh_goon.core.path_layouts import ManagedPreparationPathLayout
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt, run_command
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.engine.native_uv_environment import NativeUvAuthority, capture_native_uv_authority
from oh_goon.integrations._fleet_plan_step_local_execution import (
    validate_extension,
    validate_pre_commit_runtime,
)
from oh_goon.integrations.content_snapshot import (
    SnapshotBinding,
    SnapshotIdentity,
    copy_frozen_directory,
    snapshot_identity,
)
from oh_goon.integrations.uv_python_runtime import find_uv_system_python

_UV_PREPARATION_OWNED_ENVIRONMENT = frozenset(
    {
        "UV_CACHE_DIR",
        "UV_PROJECT_ENVIRONMENT",
        "UV_NO_MANAGED_PYTHON",
        "UV_PYTHON_DOWNLOADS",
        "UV_PYTHON",
        "UV_OFFLINE",
    }
)
_UV_PREPARATION_COMMAND_RUNNER = partial(
    run_command,
    require_process_group_quiescence=True,
    max_capture_bytes_per_stream=1024 * 1024,
    retain_output_tail=True,
)


class UvPreparationIncomplete(FleetPlanInvalid):
    """Original native receipt leaves preparation scratch under uncertain process use."""

    def __init__(self, supervisor: CommandSupervisorReceipt | None) -> None:
        super().__init__("Native uv preparation process cleanup is incomplete")
        self.supervisor = supervisor


def uv_preparation_requires_retained_scratch(error: BaseException) -> bool:
    """Transport the original process owner's cleanup fact to its Run resource owner."""
    if isinstance(error, UvPreparationIncomplete):
        return True
    supervisor = getattr(error, "supervisor", None)
    if supervisor is None:
        supervisor = getattr(error.__cause__, "supervisor", None)
    if supervisor is None:
        return hasattr(error, "supervisor") or hasattr(error.__cause__, "supervisor")
    return supervisor.final_process_group_quiescent is not True


@dataclass(frozen=True, slots=True)
class PreCommitManagedRuntime:
    """Exact host runtime bound for one resolved pre-commit step."""

    environment: Path
    executable: Path

    @property
    def readonly_paths(self) -> tuple[Path, ...]:
        return (self.environment,)


@dataclass(frozen=True, slots=True)
class ExtensionManagedRuntime:
    """Exact project/environment pair bound for one resolved extension step."""

    project: Path
    environment: Path

    @property
    def readonly_paths(self) -> tuple[Path, ...]:
        return (self.project, self.environment)


@dataclass(frozen=True, slots=True)
class UvProjectManagedRuntime:
    """Native uv acquisition authority prepared for one source-isolated project Step.

    The live checkout's ``.venv`` is never transported. Preparation validates the
    exact admitted project and warms a run-owned native uv cache for trusted
    code. Untrusted code receives an empty cache and defers native preparation
    to its admitted isolated Step. Each physical attempt owns its environment
    outside immutable source before application work begins.
    """

    project: str
    group: str | None
    cache: Path
    python: Path
    package_source: Path | None = None
    isolated_preparation: bool = False

    @property
    def readonly_paths(self) -> tuple[Path, ...]:
        return (
            (self.cache, self.package_source) if self.package_source is not None else (self.cache,)
        )


ManagedRuntimeSpec = PreCommitManagedRuntime | ExtensionManagedRuntime | UvProjectManagedRuntime


def prepare_managed_runtimes(
    plan: ResolvedFleetPlan,
    *,
    extensions: FleetExtensionStore | None,
    tools: FleetToolStore | None,
    git: GitService,
) -> dict[str, ManagedRuntimeSpec]:
    """Bind Fleet-owned tool/extension runtimes while the durable effect is CLAIMED."""

    result: dict[str, ManagedRuntimeSpec] = {}
    for step in plan.steps:
        if isinstance(step, ResolvedPreCommitStep):
            executable = validate_pre_commit_runtime(tools, step).resolve()
            assert tools is not None
            environment = tools.environment_path(step.runtime_id).resolve()
            result[step.step_id] = PreCommitManagedRuntime(
                environment=environment,
                executable=executable,
            )
        elif isinstance(step, ResolvedExtensionStep):
            project = validate_extension(extensions, git, step).resolve()
            assert extensions is not None
            extensions.check_environment(step.extension_id)
            environment = extensions.environment_path(step.extension_id).resolve()
            result[step.step_id] = ExtensionManagedRuntime(
                project=project,
                environment=environment,
            )
    return result


def prepare_uv_project_runtimes(
    plan: ResolvedFleetPlan,
    *,
    source_root: Path,
    run_root: Path,
    path_authority: StrictHostPathAuthority,
    cancel_requested: Callable[[], bool] | None,
    command_runner: Callable[..., CommandResult] = _UV_PREPARATION_COMMAND_RUNNER,
    python_resolver: Callable[[Path], Path | None] = find_uv_system_python,
    host_environment: Mapping[str, str] | None = None,
) -> dict[str, UvProjectManagedRuntime]:
    """Bind native uv inputs; trusted projects may acquire before Step work begins.

    This preparation intentionally does not transport a resulting virtualenv.
    Only uv's run-owned cache is frozen. Trusted projects warm that cache for
    offline attempt installation; untrusted projects defer every install/build
    to the admitted isolated process. The physical attempt always owns its
    environment outside immutable source and the developer's live environment.
    """

    steps = tuple(
        step for step in plan.steps if isinstance(step, ResolvedCommandStep) and step.uv is not None
    )
    if not steps:
        return {}
    isolated_preparation = (
        isinstance(plan.execution, CicdExecutionPolicy) and not plan.execution.trust_repository_code
    )

    preparation = ManagedPreparationPathLayout.from_root(run_root)
    preparation.ensure()
    uv = path_authority.tool("uv")
    cache = preparation.cache / "uv"
    cache.mkdir(parents=True, exist_ok=True)
    env_root = ensure_private_directory(preparation.root / "uv-environments")
    project_root = ensure_private_directory(preparation.root / "uv-projects")
    python = python_resolver(uv)
    if python is None:
        raise FleetPlanInvalid(
            "Strict native uv preparation could not select one system Python authority"
        )
    prepared: set[tuple[str, str | None, str | None]] = set()
    runtimes: dict[str, UvProjectManagedRuntime] = {}
    native_uv = (
        None
        if isolated_preparation
        else capture_native_uv_authority(
            host_environment, owned_uv_names=_UV_PREPARATION_OWNED_ENVIRONMENT
        )
    )

    owned_directories: set[Path] = set()
    primary_error: BaseException | None = None
    try:
        for step in steps:
            assert step.uv is not None
            package_source = None
            if step.project_source is not None:
                from oh_goon.core.path_layouts import RunPathLayout

                package_source = RunPathLayout.from_root(run_root).package_source(
                    step.project_source.content_sha256
                )
            key = (
                step.uv.project,
                step.uv.group,
                step.project_source.content_sha256 if step.project_source else None,
            )
            if key not in prepared:
                project = package_source or RepositoryPathSpace.from_root(source_root).resolve(
                    step.uv.project,
                    label=f"Fleet uv project for step {step.step_id}",
                    kind="directory",
                    require_exists=True,
                )
                selection_id = stable_id(
                    "fleet-uv-preparation",
                    {"project": step.uv.project, "group": step.uv.group, "package": key[2]},
                ).removeprefix("sha256:")
                environment = env_root / selection_id
                if not isolated_preparation:
                    assert native_uv is not None
                    if package_source is not None:
                        assert step.project_source is not None
                        project = project_root / selection_id
                        owned_directories.add(project)
                        observed = copy_frozen_directory(
                            package_source,
                            project,
                            label=f"Python Goon {step.step_id} native preparation project",
                            cancel_requested=cancel_requested,
                        )
                        expected = step.project_source
                        if observed != SnapshotIdentity(
                            sha256=expected.content_sha256,
                            byte_count=expected.byte_count,
                            entry_count=expected.entry_count,
                        ):
                            raise FleetPlanInvalid(
                                "Python Goon source changed while creating its preparation project"
                            )
                    env = _uv_preparation_environment(
                        preparation,
                        environment=environment,
                        cache=cache,
                        python=python,
                        native_uv=native_uv,
                    )
                    owned_directories.add(environment)
                    result = command_runner(
                        str(uv),
                        "sync",
                        "--project",
                        str(project),
                        "--locked",
                        "--no-default-groups",
                        *(["--group", step.uv.group] if step.uv.group is not None else []),
                        *(["--no-editable"] if package_source is not None else []),
                        cwd=project,
                        timeout=900.0,
                        cancel_requested=cancel_requested,
                        env=env,
                    )
                    if (
                        result.supervisor is None
                        or result.supervisor.final_process_group_quiescent is not True
                    ):
                        raise UvPreparationIncomplete(result.supervisor)
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout or "native uv sync failed").strip()
                        raise FleetPlanInvalid(
                            f"Native uv preparation rejected project {step.uv.project!r} "
                            f"group {step.uv.group!r}: {detail}"
                        )
                prepared.add(key)

            runtimes[step.step_id] = UvProjectManagedRuntime(
                project=step.uv.project,
                group=step.uv.group,
                cache=cache,
                python=python,
                package_source=package_source,
                isolated_preparation=isolated_preparation,
            )

        return runtimes
    except BaseException as error:
        primary_error = error
        raise
    finally:
        failures: list[OSError] = []
        unsettled = primary_error is not None and uv_preparation_requires_retained_scratch(
            primary_error
        )
        if unsettled:
            assert primary_error is not None
            primary_error.add_note(
                f"Native uv preparation scratch retained while process cleanup is incomplete: {preparation.root}"
            )
        for directory in () if unsettled else sorted(owned_directories):
            try:
                if directory.exists() or directory.is_symlink():
                    shutil.rmtree(directory)
            except OSError as error:
                failures.append(error)
        if failures:
            if primary_error is not None:
                for cleanup_error in failures:
                    primary_error.add_note(f"native uv preparation cleanup failed: {cleanup_error}")
            else:
                raise FleetPlanInvalid("Native uv preparation cleanup is incomplete") from failures[
                    0
                ]


def _uv_preparation_environment(
    preparation: ManagedPreparationPathLayout,
    *,
    environment: Path,
    cache: Path,
    python: Path | None,
    native_uv: NativeUvAuthority,
) -> dict[str, str]:
    overrides = {
        "UV_NO_MANAGED_PYTHON": "1",
        "UV_PYTHON_DOWNLOADS": "never",
    }
    if python is not None:
        overrides["UV_PYTHON"] = str(python)
    return native_uv.environment(
        preparation, environment=environment, cache=cache, uv_overrides=overrides
    )


def pre_commit_tool_evidence(
    step: ResolvedPreCommitStep,
    runtime: ManagedRuntimeSpec | None,
    sandbox_bindings: tuple[SnapshotBinding, ...],
) -> PlanToolEvidence:
    """Derive managed pre-commit tool identity from the frozen runtime binding."""
    if not isinstance(runtime, PreCommitManagedRuntime):
        raise FleetPlanInvalid(
            f"Strict pre-commit step has the wrong managed runtime kind: {step.step_id}"
        )
    for binding in sandbox_bindings:
        try:
            relative = ContainedPathSpace.from_root(binding.declared_path).relative(
                runtime.executable, label="Fleet pre-commit managed runtime"
            )
        except FleetPlanInvalid:
            continue
        identity = snapshot_identity(binding.snapshot_path / relative)
        return PlanToolEvidence(
            name=runtime.executable.name,
            origin="managed",
            sha256=identity.sha256,
            byte_count=identity.byte_count,
        )
    raise FleetPlanInvalid(
        f"Fleet pre-commit executable is outside its bound managed runtime: {step.step_id}"
    )
