from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement
from oh_goon.core.models.fleet_plan_evidence import PlanImportApplicabilityEvidence
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.goon_plan_source import (
    TeamGoonSource,
    validate_plan_source_repository_count,
)
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_launch import (
    ApplicationLaunchReceipt,
    UnmanagedApplicationLaunchReceipt,
)
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.models.scheduling import SchedulingEnvelope
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import FleetRunSource


class InvalidRepositoryScopeRequest(ValueError):
    pass


class AllRepositoryScope(ImmutableModel):
    kind: Literal["ALL"] = "ALL"


class ExplicitRepositoryScope(ImmutableModel):
    kind: Literal["EXPLICIT"] = "EXPLICIT"
    repository_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_repository_ids(self) -> ExplicitRepositoryScope:
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("Explicit Fleet repository scope must contain unique repository IDs")
        if self.repository_ids != tuple(sorted(self.repository_ids)):
            raise ValueError("Explicit Fleet repository scope must be canonically sorted")
        return self


FleetRepositoryScope = Annotated[
    AllRepositoryScope | ExplicitRepositoryScope,
    Field(discriminator="kind"),
]


def fleet_repository_scope(repository_ids: set[str] | None) -> FleetRepositoryScope:
    if repository_ids is None:
        return AllRepositoryScope()
    return ExplicitRepositoryScope(repository_ids=tuple(sorted(repository_ids)))


def requested_fleet_repository_scope(
    target: OperationTarget | None,
    *,
    repository_ids: set[str] | None,
    all_repositories: bool,
) -> FleetRepositoryScope:
    if getattr(target, "kind", None) == "fleet_plan":
        if repository_ids is not None and all_repositories:
            raise InvalidRepositoryScopeRequest(
                "Fleet plan run cannot combine repository_ids with all_repositories=true"
            )
        if repository_ids is None and not all_repositories:
            raise InvalidRepositoryScopeRequest(
                "Fleet plan run requires explicit repository_ids or all_repositories=true"
            )
    elif all_repositories:
        raise InvalidRepositoryScopeRequest("all_repositories is only valid for Fleet plan runs")
    return fleet_repository_scope(repository_ids)


def validate_fleet_repository_capture(
    repository_scope: FleetRepositoryScope,
    targets: tuple[RepositoryIdentity, ...],
    *,
    target: OperationTarget | None = None,
) -> None:
    repository_ids = tuple(target.repository_id for target in targets)
    if len(repository_ids) != len(set(repository_ids)):
        raise ValueError("Fleet run targets must contain unique repository identities")
    if isinstance(repository_scope, ExplicitRepositoryScope):
        if repository_scope.repository_ids != tuple(sorted(repository_ids)):
            raise ValueError("Explicit Fleet repository scope must match captured targets exactly")
    if isinstance(target, FleetPlanTarget):
        validate_plan_source_repository_count(target.source, len(targets))
        if isinstance(target.source, TeamGoonSource) and not isinstance(
            repository_scope, ExplicitRepositoryScope
        ):
            raise ValueError("Team Goon Fleet admission requires explicit repository scope")


def _is_strict_cicd_target(target: OperationTarget | None) -> bool:
    return isinstance(target, FleetPlanTarget) and isinstance(
        target.plan.execution, CicdExecutionPolicy
    )


class PlanRepositoryAdmissionEvidence(ImmutableModel):
    """Durable repository, Plan semantics, and source authority admitted before effects."""

    repository_id: str = Field(pattern=r"^repo-[0-9a-f]{20}$")
    plan_id: str = Field(min_length=1)
    semantic_preview_id: str = Field(pattern=r"^fleet-plan-repository-preview-[0-9a-f]{64}$")
    import_applicability: tuple[PlanImportApplicabilityEvidence, ...]
    active_step_ids: tuple[str, ...]
    source_authority: RepositorySourceAuthority
    admission_id: str = Field(pattern=r"^fleet-plan-repository-admission-[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        repository_id: str,
        plan_id: str,
        semantic_preview_id: str,
        import_applicability: tuple[PlanImportApplicabilityEvidence, ...] = (),
        active_step_ids: tuple[str, ...] = (),
        source_authority: RepositorySourceAuthority,
    ) -> PlanRepositoryAdmissionEvidence:
        admission_id = stable_id(
            "fleet-plan-repository-admission",
            {
                "repository_id": repository_id,
                "plan_id": plan_id,
                "semantic_preview_id": semantic_preview_id,
                "import_applicability": [
                    item.model_dump(mode="json") for item in import_applicability
                ],
                "active_step_ids": list(active_step_ids),
                "source_authority_id": source_authority.authority_id,
            },
        )
        return cls(
            repository_id=repository_id,
            plan_id=plan_id,
            semantic_preview_id=semantic_preview_id,
            import_applicability=import_applicability,
            active_step_ids=active_step_ids,
            source_authority=source_authority,
            admission_id=admission_id,
        )

    @model_validator(mode="after")
    def validate_admission_id(self) -> PlanRepositoryAdmissionEvidence:
        expected = stable_id(
            "fleet-plan-repository-admission",
            {
                "repository_id": self.repository_id,
                "plan_id": self.plan_id,
                "semantic_preview_id": self.semantic_preview_id,
                "import_applicability": [
                    item.model_dump(mode="json") for item in self.import_applicability
                ],
                "active_step_ids": list(self.active_step_ids),
                "source_authority_id": self.source_authority.authority_id,
            },
        )
        if self.admission_id != expected:
            raise ValueError("Repository/Plan admission_id must match its admitted authority")
        return self


class DirectFleetExecutionAdmission(ImmutableModel):
    """Fleet execution admitted without strict executor placement."""

    kind: Literal["direct"] = "direct"
    rerun_from_run_id: str | None = Field(default=None, min_length=1)
    resource_envelope: RunResourceEnvelopeEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class StrictFleetExecutionAdmission(ImmutableModel):
    """Exact strict placement authority and optional re-admission lineage."""

    kind: Literal["strict"] = "strict"
    placement: FleetExecutorPlacement
    trusted_local_consent: TrustedLocalRunConsent | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    readmitted_from_run_id: str | None = Field(default=None, min_length=1)
    rerun_from_run_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_exact_tl1_placement(self) -> StrictFleetExecutionAdmission:
        """TL-1 cannot enter the durable Run as an unconsented strict fallback."""
        backend = self.placement.isolation_backend
        capacity = self.placement.executor_capacity
        consent = self.trusted_local_consent
        if backend.backend_id == "trusted-local":
            if consent is None:
                raise ValueError("trusted-local placement requires explicit per-Run consent")
            if (
                backend.remote_execution
                or backend.kernel_boundary != "shared_host_kernel"
                or capacity.source != "local_verified"
                or capacity.per_attempt_cpu_millis is not None
                or capacity.per_attempt_memory_bytes is not None
                or capacity.per_attempt_max_processes is not None
            ):
                raise ValueError(
                    "trusted-local admission cannot claim remote or kernel-limited execution"
                )
            if (
                consent.backend_id != backend.backend_id
                or consent.backend_configuration_id != backend.configuration_id
                or consent.executor_id != capacity.executor_id
                or consent.executor_generation != capacity.executor_generation
                or consent.fleet_run_id != capacity.fleet_run_id
            ):
                raise ValueError(
                    "trusted-local consent must match exact admitted Run and executor placement"
                )
        elif consent is not None:
            raise ValueError("trusted-local consent cannot authorize a strict isolation backend")
        return self


FleetExecutionAdmission = Annotated[
    DirectFleetExecutionAdmission | StrictFleetExecutionAdmission,
    Field(discriminator="kind"),
]


def fleet_readmission_parent(execution: FleetExecutionAdmission) -> str | None:
    return (
        execution.readmitted_from_run_id
        if isinstance(execution, StrictFleetExecutionAdmission)
        else None
    )


def fleet_rerun_parent(execution: FleetExecutionAdmission) -> str | None:
    return execution.rerun_from_run_id


def _validate_execution_admission(
    target: OperationTarget | None, execution: FleetExecutionAdmission
) -> None:
    if (
        isinstance(execution, DirectFleetExecutionAdmission)
        and execution.resource_envelope is not None
        and not isinstance(target, FleetPlanTarget)
    ):
        raise ValueError("Whole-Run resources require original Fleet Plan execution")
    if _is_strict_cicd_target(target) != isinstance(execution, StrictFleetExecutionAdmission):
        raise ValueError(
            "Strict Fleet CI/CD targets require strict execution admission and other targets require direct admission"
        )


def _validate_trusted_local_admission(
    *,
    target: OperationTarget | None,
    execution: FleetExecutionAdmission,
    fleet_run_id: str,
    source: FleetRunSource,
    repository_scope: FleetRepositoryScope,
    repository_count: int,
) -> None:
    """Original RUN_ADMITTED/RunDetail invariant; never an effect authorization.

    The same check is applied when the journal decodes an admitted event and
    when an admitted projection is reconstructed after process restart.
    """
    if not isinstance(execution, StrictFleetExecutionAdmission):
        return
    consent = execution.trusted_local_consent
    admitted_backend = execution.placement.isolation_backend.backend_id
    # Reassert even for a prebuilt Pydantic instance: model_copy(update=...) does
    # not re-run nested validators. Journal ingress must not trust its caller.
    if admitted_backend == "trusted-local" and consent is None:
        raise ValueError("trusted-local admission requires explicit per-Run consent")
    if admitted_backend != "trusted-local" and consent is not None:
        raise ValueError("trusted-local consent cannot authorize a strict isolation backend")
    if consent is None:
        return
    if not isinstance(target, FleetPlanTarget) or not isinstance(
        target.plan.execution, CicdExecutionPolicy
    ):
        raise ValueError("trusted-local consent requires a resolved CI/CD Plan")
    if not isinstance(repository_scope, ExplicitRepositoryScope) or repository_count != 1:
        raise ValueError(
            "trusted-local consent requires exactly one explicitly selected repository"
        )
    if source is not FleetRunSource.MANUAL:
        raise ValueError("trusted-local consent forbids scheduled, rerun or unattended admission")
    if any(
        step.capabilities.egress.mode != "host_unrestricted"
        or step.capabilities.nested_strict_execution
        for step in target.plan.steps
    ):
        raise ValueError("trusted-local cannot claim restricted network or nested strict execution")
    if execution.readmitted_from_run_id is not None or execution.rerun_from_run_id is not None:
        raise ValueError("trusted-local consent cannot be inherited by rerun or readmission")
    try:
        consent.require_exact_authority(
            fleet_run_id=fleet_run_id,
            execution_digest=target.execution_digest,
            backend_id=execution.placement.isolation_backend.backend_id,
            backend_configuration_id=execution.placement.isolation_backend.configuration_id,
            executor_id=execution.placement.executor_capacity.executor_id,
            executor_generation=execution.placement.executor_capacity.executor_generation,
            execution=target.plan.execution,
            source=source.value.lower(),
            all_repositories=False,
            repository_count=repository_count,
            unattended=False,
        )
    except (ValueError, FleetPlanInvalid) as error:
        raise ValueError("trusted-local durable Run admission rejected: " + str(error)) from error


def _validate_execution_admission_run(
    execution: FleetExecutionAdmission, fleet_run_id: str
) -> None:
    if execution.rerun_from_run_id == fleet_run_id:
        raise ValueError("Fleet rerun provenance cannot reference the same run")
    if (
        isinstance(execution, DirectFleetExecutionAdmission)
        and execution.resource_envelope is not None
        and execution.resource_envelope.fleet_run_id != fleet_run_id
    ):
        raise ValueError("Run resource envelope must belong to the same Fleet run")
    if isinstance(execution, StrictFleetExecutionAdmission):
        if execution.placement.executor_capacity.fleet_run_id != fleet_run_id:
            raise ValueError(
                "Fleet executor-capacity reservation must belong to the same Fleet run"
            )
        if execution.readmitted_from_run_id == fleet_run_id:
            raise ValueError("Fleet re-admission provenance cannot reference the same run")
        if execution.readmitted_from_run_id is not None and execution.rerun_from_run_id is not None:
            raise ValueError(
                "Fleet execution cannot be both a re-admission and a Run-history rerun"
            )


def _validate_plan_repository_admissions(
    target: OperationTarget | None,
    evidence: tuple[PlanRepositoryAdmissionEvidence, ...],
) -> None:
    """Validate the sole current durable repository/Plan admission evidence shape."""

    if not isinstance(target, FleetPlanTarget):
        if evidence:
            raise ValueError(
                "Repository/Plan admission evidence is valid only for Fleet-plan targets"
            )
        return
    if not evidence:
        raise ValueError("Fleet Plan admission requires repository/Plan admission evidence")
    repository_ids = tuple(item.repository_id for item in evidence)
    if len(repository_ids) != len(set(repository_ids)):
        raise ValueError("Repository/Plan admission evidence must contain unique repositories")
    if repository_ids != tuple(sorted(repository_ids)):
        raise ValueError("Repository/Plan admission evidence must use canonical repository order")
    if any(item.plan_id != target.plan.plan_id for item in evidence):
        raise ValueError("Repository/Plan admission evidence must match the admitted plan_id")


class FleetRunAdmissionIntent(ImmutableModel):
    """One immutable durable admission intent captured before RUN_ADMITTED."""

    source: FleetRunSource = FleetRunSource.MANUAL
    target: OperationTarget | None = None
    capability: CapabilitySnapshot
    execution: FleetExecutionAdmission
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]
    application_launch: ApplicationLaunchReceipt = Field(
        default_factory=UnmanagedApplicationLaunchReceipt
    )
    scheduling: SchedulingEnvelope | None = None
    # Transaction-local input to the ORIGINAL Journal admission cut. The Schedule
    # store persists the joined child identity atomically with RUN_ADMITTED.
    schedule_run_id: str | None = None

    @model_validator(mode="after")
    def validate_admission(self) -> FleetRunAdmissionIntent:
        if not self.capability.matches_intent(self.capability.action, self.target):
            raise ValueError("Fleet run admission capability snapshot must match target kind")
        _validate_execution_admission(self.target, self.execution)
        _validate_plan_repository_admissions(self.target, self.repository_admissions)
        if (self.source is FleetRunSource.SCHEDULED) != (self.schedule_run_id is not None):
            raise ValueError("Scheduled Fleet admission needs one occurrence identity")
        return self
