from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedFleetPlan,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_evidence import PlanToolEvidence
from oh_goon.core.path_authority import (
    ContainedPathSpace,
    RepositoryPathSpace,
    StrictHostPathAuthority,
    ensure_private_directory,
)
from oh_goon.core.path_layouts import ManagedPreparationPathLayout
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.engine.native_uv_environment import NativeUvAuthority, capture_native_uv_authority
from oh_goon.integrations._fleet_plan_step_local_execution import (
    validate_extension,
    validate_pre_commit_runtime,
)
from oh_goon.integrations.content_snapshot import SnapshotBinding, snapshot_identity
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
    exact admitted project and warms a run-owned native uv cache. Each physical
    attempt then materializes its own environment outside immutable source from
    that cache before application work begins.
    """

    project: str
    group: str
    cache: Path
    python: Path

    @property
    def readonly_paths(self) -> tuple[Path, ...]:
        return (self.cache,)


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
    command_runner: Callable[..., CommandResult] = run_command,
    python_resolver: Callable[[Path], Path | None] = find_uv_system_python,
    host_environment: Mapping[str, str] | None = None,
) -> dict[str, UvProjectManagedRuntime]:
    """Let native uv validate/acquire each strict project before Step work begins.

    This preparation intentionally does not transport the resulting virtualenv.
    Only uv's run-owned cache is frozen. A physical attempt creates its own
    attempt-owned environment offline, so editable project paths always bind to
    the exact isolated workspace rather than to the live checkout or preparation
    tree while generated environment bytes stay outside immutable source.
    """

    steps = tuple(
        step for step in plan.steps if isinstance(step, ResolvedCommandStep) and step.uv is not None
    )
    if not steps:
        return {}

    preparation = ManagedPreparationPathLayout.from_root(run_root)
    preparation.ensure()
    uv = path_authority.tool("uv")
    cache = preparation.cache / "uv"
    cache.mkdir(parents=True, exist_ok=True)
    env_root = ensure_private_directory(preparation.root / "uv-environments")
    python = python_resolver(uv)
    if python is None:
        raise FleetPlanInvalid(
            "Strict native uv preparation could not select one system Python authority"
        )
    prepared: dict[tuple[str, str], Path] = {}
    runtimes: dict[str, UvProjectManagedRuntime] = {}
    native_uv = capture_native_uv_authority(
        host_environment, owned_uv_names=_UV_PREPARATION_OWNED_ENVIRONMENT
    )

    for step in steps:
        assert step.uv is not None
        key = (step.uv.project, step.uv.group)
        if key not in prepared:
            project = RepositoryPathSpace.from_root(source_root).resolve(
                step.uv.project,
                label=f"Fleet uv project for step {step.step_id}",
                kind="directory",
                require_exists=True,
            )
            environment = env_root / stable_id(
                "fleet-uv-preparation", {"project": step.uv.project, "group": step.uv.group}
            ).removeprefix("sha256:")
            env = _uv_preparation_environment(
                preparation,
                environment=environment,
                cache=cache,
                python=python,
                native_uv=native_uv,
            )
            result = command_runner(
                str(uv),
                "sync",
                "--project",
                str(project),
                "--locked",
                "--no-default-groups",
                "--group",
                step.uv.group,
                cwd=project,
                timeout=900.0,
                cancel_requested=cancel_requested,
                env=env,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "native uv sync failed").strip()
                raise FleetPlanInvalid(
                    f"Native uv preparation rejected project {step.uv.project!r} "
                    f"group {step.uv.group!r}: {detail}"
                )
            prepared[key] = environment

        runtimes[step.step_id] = UvProjectManagedRuntime(
            project=step.uv.project,
            group=step.uv.group,
            cache=cache,
            python=python,
        )

    for environment in prepared.values():
        shutil.rmtree(environment, ignore_errors=True)
    return runtimes


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
