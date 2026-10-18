from __future__ import annotations

from collections.abc import Iterable

from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.operator_diagnostic import OperatorWhyNotDiagnostic
from oh_goon.core.models.shared_plan import ReadySharedPlanRepositoryPreview



def blocked_preview_diagnostic(exc: Exception) -> OperatorWhyNotDiagnostic:
    if isinstance(exc, FleetDomainError):
        reason_code = exc.code
        retryable = exc.retryable
    else:
        reason_code = "PLAN_PREVIEW_VALUE_INVALID"
        retryable = False
    return OperatorWhyNotDiagnostic(
        authority_owner="fleet_plan_preview",
        state="BLOCKED",
        reason_code=reason_code,
        current_state=str(exc) or exc.__class__.__name__,
        required_state="repository-resolvable Plan and Step policy must validate without runtime effects",
        safe_next_action="fix the declared repository/Plan input or policy, then refresh the execution preview",
        evidence_hint="Shared Plan preview Team evidence",
        retryable=retryable,
    )


def preview_readiness(
    teams: Iterable[ReadySharedPlanRepositoryPreview],
) -> tuple[OperatorWhyNotDiagnostic, ...]:
    ready_teams = tuple(teams)
    cache_sources = tuple(
        sorted(
            {
                step.cache_source
                for team in ready_teams
                for step in team.preview.steps
                if step.cache_source != "empty"
            }
        )
    )
    return (
        _host_storage_fact(),
        _cache_reuse_fact(cache_sources),
    )



def _host_storage_fact() -> OperatorWhyNotDiagnostic:
    return OperatorWhyNotDiagnostic(
        authority_owner="storage_health",
        state="NOT_PROBED",
        reason_code="HOST_STORAGE_NOT_PROBED_IN_PREVIEW",
        current_state="read-only execution preview deliberately performs no write/SQLite storage probe",
        required_state="the owning admission/prelaunch boundary must prove required write, lock, and filesystem semantics",
        safe_next_action="use make init-doctor for diagnostics; certification uses make local-state-prelaunch-check",
        evidence_hint="storage_health prelaunch owner",
    )


def _cache_reuse_fact(cache_sources: tuple[str, ...]) -> OperatorWhyNotDiagnostic:
    if not cache_sources:
        return OperatorWhyNotDiagnostic(
            authority_owner="fleet_plan_step_cache_policy",
            state="NOT_APPLICABLE",
            reason_code="CACHE_REUSE_NOT_REQUESTED",
            current_state="all resolved Steps use an empty cache source",
            required_state="no reusable cache authority is required",
            evidence_hint="resolved Step cache policy",
        )
    return OperatorWhyNotDiagnostic(
        authority_owner="fleet_plan_step_cache_policy",
        state="RUNTIME_DEPENDENT",
        reason_code="CACHE_REUSE_RUNTIME_BOUND",
        current_state="resolved cache sources: " + ", ".join(cache_sources),
        required_state="exact admitted cache source/protection identity must still hold when each Step executes",
        evidence_hint="resolved Step cache source/publish/retain policy",
    )
