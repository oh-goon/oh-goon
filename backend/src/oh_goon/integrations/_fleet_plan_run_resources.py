from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import step_runtime_tool_names
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedFleetPlan,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    PlanContentObjectEvidence,
    PlanExecutionInputEvidence,
    PlanIsolationBackendEvidence,
    PlanSnapshotEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepEgress
from oh_goon.core.models.fleet_plan_tool_authority import PlanHostToolAuthorityEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.path_authority import (
    RepositoryPathSpace,
    StrictHostPathAuthority,
    canonical_path,
)
from oh_goon.core.path_layouts import (
    SANDBOX_PATHS,
    RunPathLayout,
)
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.integrations import _fleet_plan_host_resources as host_resources
from oh_goon.integrations._fleet_plan_egress_binding import (
    capture_hostname_egress,
    effective_egress_for,
    hosts_for,
)
from oh_goon.integrations._fleet_plan_runtime_root import capture_runtime_root
from oh_goon.integrations.content_snapshot import (
    SnapshotBinding,
    SnapshotIdentity,
    snapshot_host_path,
)
from oh_goon.integrations.content_snapshot_dereferenced import snapshot_directory_dereferenced
from oh_goon.integrations.executable_materialization_authority import (
    LogicalStepExecutionAuthority,
    logical_step_execution_authority,
)
from oh_goon.integrations.fleet_plan_environment import step_host_environment_names
from oh_goon.integrations.fleet_plan_managed_runtime import (
    ExtensionManagedRuntime,
    ManagedRuntimeSpec,
    PreCommitManagedRuntime,
    UvProjectManagedRuntime,
    prepare_uv_project_runtimes,
)
from oh_goon.integrations.fleet_plan_repository_scope import bind_repository_scope
from oh_goon.integrations.source_snapshot import snapshot_source_directory


@dataclass(frozen=True, slots=True)
class StepInputBinding:
    """All immutable step-specific run inputs resolved once during strict preparation."""

    egress: PlanStepEgress
    hosts: tuple[tuple[str, str], ...]
    sandbox_bindings: tuple[SnapshotBinding, ...]
    managed_runtime: ManagedRuntimeSpec | None
    execution_authority: LogicalStepExecutionAuthority


class _FleetPlanRunInputs:
    """Freeze all non-step-local inputs before a strict plan crosses EFFECT_STARTED."""

    def __init__(
        self,
        repository: RepositoryIdentity,
        root: Path,
        plan: ResolvedFleetPlan,
        *,
        source_authority: RepositorySourceAuthority,
        isolation_backend: PlanIsolationBackendEvidence,
        path_authority: StrictHostPathAuthority,
        managed_runtimes: Mapping[str, ManagedRuntimeSpec] | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        # ``cancel_requested`` is consumed only while freezing immutable inputs below;
        # it must not be retained by the prepared resource graph after preparation.
        self._layout = RunPathLayout.from_root(root)
        self.isolation_backend = isolation_backend
        if not isinstance(plan.execution, CicdExecutionPolicy):
            raise FleetPlanInvalid("PreparedPlanRun requires a resolved CI/CD plan")
        self.plan_id = plan.plan_id
        self.resources = plan.execution.resources
        self.base_workspace = self._layout.base_workspace
        self.repository_id = repository.repository_id
        self.source_authority_id = source_authority.authority_id
        _require_admitted_repository_subject(repository, source_authority)
        RepositoryPathSpace.from_root(repository.path).require_isolated_source()
        self.base_identity = snapshot_source_directory(
            repository.path,
            self.base_workspace,
            label="Fleet admitted repository workspace",
            cancel_requested=cancel_requested,
        )
        # Equal source bytes cannot authorize a replacement filesystem subject.
        # Fence the transfer into the private snapshot without another source walk.
        _require_admitted_repository_subject(repository, source_authority)
        admitted_snapshot = source_authority.source_snapshot
        if (
            self.base_identity.sha256,
            self.base_identity.byte_count,
            self.base_identity.entry_count,
        ) != (
            admitted_snapshot.sha256,
            admitted_snapshot.byte_count,
            admitted_snapshot.entry_count,
        ):
            raise FleetPlanInvalid(
                "Strict execution source snapshot does not match admitted repository source authority"
            )
        repository_scope = bind_repository_scope(self.base_workspace, plan)
        self.repository_admission = repository_scope.admission
        self.import_applicability = repository_scope.import_applicability
        self.import_skips = repository_scope.import_skips
        active_steps = tuple(step for step in plan.steps if step.step_id not in self.import_skips)
        active_plan = plan.model_copy(update={"steps": active_steps})
        combined_managed = dict(managed_runtimes or {})
        from oh_goon.integrations.python_goon_projects import materialize_plan_projects

        project_sources = materialize_plan_projects(active_plan, self._layout)
        self._captured_projects = MappingProxyType(
            {
                step.project_source.content_sha256: (
                    project_sources[step.project_source.content_sha256],
                    SnapshotIdentity(
                        sha256=step.project_source.content_sha256,
                        byte_count=step.project_source.byte_count,
                        entry_count=step.project_source.entry_count,
                    ),
                )
                for step in active_steps
                if isinstance(step, ResolvedCommandStep) and step.project_source is not None
            }
        )
        uv_managed = prepare_uv_project_runtimes(
            active_plan,
            source_root=self.base_workspace,
            run_root=self._layout.root,
            path_authority=path_authority,
            cancel_requested=cancel_requested,
        )
        overlap = sorted(set(combined_managed) & set(uv_managed))
        if overlap:
            raise FleetPlanInvalid(
                "Fleet step cannot own two managed runtimes: " + ", ".join(overlap)
            )
        combined_managed.update(uv_managed)
        self.host_environment = MappingProxyType(
            host_resources.capture_host_environment(active_plan)
        )
        self._hostname_egress, self._hostname_evidence = capture_hostname_egress(active_plan)
        self._host_tools = MappingProxyType(
            host_resources.capture_host_tools(
                active_plan, self._layout.toolchain, path_authority, cancel_requested
            )
        )
        self._managed_specs = MappingProxyType(combined_managed)
        self._managed = _capture_managed_paths(self._layout, self._managed_specs, cancel_requested)
        self.runtime_root = capture_runtime_root(
            self._host_tools.values(),
            self._managed_specs,
            self._layout.runtime_root,
            runtime_inspector=path_authority.runtime_inspector,
            network_enabled=bool(
                any(step.capabilities.egress.mode != "none" for step in active_steps)
            ),
            managed_mount_targets=_runtime_mount_targets(self._managed_specs),
            cancel_requested=cancel_requested,
        )
        self._step_bindings = MappingProxyType(
            {step.step_id: self._bind_step(step) for step in active_steps}
        )

    @property
    def host_tools(self) -> tuple[host_resources.HostToolBinding, ...]:
        """Return the run-wide frozen host-tool inventory used for evidence/reuse."""

        return tuple(self._host_tools.values())

    def host_tools_for(self, step: ResolvedPlanStep) -> tuple[host_resources.HostToolBinding, ...]:
        """Return only the host-tool authority declared/derived for this Step."""

        self.step_binding(step)
        return tuple(self._host_tools[name] for name in step_runtime_tool_names(step))

    def host_environment_for(self, step: ResolvedPlanStep) -> Mapping[str, str]:
        """Return only imported host environment values owned by this Step."""

        self.step_binding(step)
        return MappingProxyType(
            {name: self.host_environment[name] for name in step_host_environment_names(step)}
        )

    def step_binding(self, step: ResolvedPlanStep) -> StepInputBinding:
        try:
            return self._step_bindings[step.step_id]
        except KeyError as exc:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} was not bound during strict run preparation"
            ) from exc

    def captured_project_source(self, step: ResolvedCommandStep) -> tuple[Path, SnapshotIdentity]:
        assert step.project_source is not None
        try:
            return self._captured_projects[step.project_source.content_sha256]
        except KeyError as exc:
            raise FleetPlanInvalid(
                "Captured project is outside original Run input custody"
            ) from exc

    def _bind_step(self, step: ResolvedPlanStep) -> StepInputBinding:
        managed = self._managed.get(step.step_id, ())
        runtime = self._managed_specs.get(step.step_id)
        _validate_managed_runtime_binding(step, runtime)
        source = (
            self.captured_project_source(step)[0]
            if isinstance(step, ResolvedCommandStep)
            and step.project_source is not None
            and step.project_source.application is not None
            else self.base_workspace
        )
        return StepInputBinding(
            egress=effective_egress_for(step, self._hostname_egress),
            hosts=hosts_for(step, self._hostname_egress),
            sandbox_bindings=managed,
            managed_runtime=runtime,
            execution_authority=logical_step_execution_authority(source, step),
        )

    def evidence(
        self,
        *,
        content_inputs: tuple[PlanContentObjectEvidence, ...] = (),
    ) -> PlanExecutionInputEvidence:
        managed_identities = {
            (
                binding.identity.sha256,
                binding.identity.byte_count,
                binding.identity.entry_count,
            ): binding.identity
            for bindings in self._managed.values()
            for binding in bindings
        }
        managed_identities.update(
            {
                (identity.sha256, identity.byte_count, identity.entry_count): identity
                for _path, identity in self._captured_projects.values()
            }
        )
        return PlanExecutionInputEvidence(
            repository_id=self.repository_id,
            source_authority_id=self.source_authority_id,
            base_workspace=_snapshot_evidence(self.base_identity),
            repository_admission=self.repository_admission,
            import_applicability=self.import_applicability,
            runtime_root=_snapshot_evidence(self.runtime_root.identity),
            isolation_backend=self.isolation_backend,
            host_environment_names=tuple(self.host_environment),
            host_tools=tuple(
                PlanHostToolAuthorityEvidence(
                    name=binding.name,
                    sha256=binding.identity.sha256,
                    byte_count=binding.identity.byte_count,
                )
                for binding in self._host_tools.values()
            ),
            managed_inputs=tuple(
                _snapshot_evidence(identity)
                for identity in sorted(
                    managed_identities.values(),
                    key=lambda item: (item.sha256, item.byte_count, item.entry_count),
                )
            ),
            content_inputs=content_inputs,
            hostname_resolutions=self._hostname_evidence,
        )


def _require_admitted_repository_subject(
    repository: RepositoryIdentity, source_authority: RepositorySourceAuthority
) -> None:
    try:
        subject = repository_subject_instance_id(repository.path)
    except ValueError as exc:
        raise FleetPlanInvalid("Strict execution lost its admitted repository subject") from exc
    if subject != source_authority.subject_instance_id:
        raise FleetPlanInvalid("Strict execution source does not match admitted repository subject")


def _validate_managed_runtime_binding(
    step: ResolvedPlanStep, runtime: ManagedRuntimeSpec | None
) -> None:
    if runtime is None:
        return
    if isinstance(step, ResolvedPreCommitStep) and isinstance(runtime, PreCommitManagedRuntime):
        return
    if isinstance(step, ResolvedExtensionStep) and isinstance(runtime, ExtensionManagedRuntime):
        return
    if (
        isinstance(step, ResolvedCommandStep)
        and step.uv is not None
        and isinstance(runtime, UvProjectManagedRuntime)
    ):
        return
    raise FleetPlanInvalid(
        f"Fleet managed runtime does not match resolved step kind: {step.step_id}"
    )


def _runtime_mount_targets(
    specs: Mapping[str, ManagedRuntimeSpec],
) -> tuple[PurePosixPath, ...]:
    declared = sorted(
        {str(canonical_path(path)) for spec in specs.values() for path in spec.readonly_paths}
    )
    return tuple(SANDBOX_PATHS.managed_path(index) for index, _value in enumerate(declared))


def _capture_managed_paths(
    layout: RunPathLayout,
    specs: Mapping[str, ManagedRuntimeSpec],
    cancel_requested: Callable[[], bool] | None,
) -> dict[str, tuple[SnapshotBinding, ...]]:
    declared = sorted(
        {str(canonical_path(path)) for spec in specs.values() for path in spec.readonly_paths}
    )
    by_path = {
        value: _snapshot_managed_path(
            Path(value),
            layout.managed_input(index),
            sandbox_path=SANDBOX_PATHS.managed_path(index),
            cancel_requested=cancel_requested,
        )
        for index, value in enumerate(declared)
    }
    return {
        step_id: tuple(by_path[str(canonical_path(path))] for path in spec.readonly_paths)
        for step_id, spec in specs.items()
    }


def _snapshot_managed_path(
    source: Path,
    destination: Path,
    *,
    sandbox_path: PurePosixPath,
    cancel_requested: Callable[[], bool] | None,
) -> SnapshotBinding:
    source = canonical_path(source)
    if not source.exists():
        raise FleetPlanInvalid(f"Fleet managed runtime input is unavailable: {source}")
    if source.is_dir():
        identity = snapshot_directory_dereferenced(
            source,
            destination,
            label="Fleet managed runtime input",
            cancel_requested=cancel_requested,
        )
        return SnapshotBinding(
            declared_path=source,
            snapshot_path=canonical_path(destination),
            identity=identity,
            sandbox_path=sandbox_path,
        )
    binding = snapshot_host_path(
        source,
        destination,
        label="Fleet managed runtime input",
        cancel_requested=cancel_requested,
    )
    return SnapshotBinding(
        declared_path=binding.declared_path,
        snapshot_path=binding.snapshot_path,
        identity=binding.identity,
        sandbox_path=sandbox_path,
    )


def _snapshot_evidence(identity: SnapshotIdentity) -> PlanSnapshotEvidence:
    return PlanSnapshotEvidence(
        sha256=identity.sha256,
        byte_count=identity.byte_count,
        entry_count=identity.entry_count,
    )
