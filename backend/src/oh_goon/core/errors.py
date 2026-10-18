from __future__ import annotations

from enum import StrEnum


class ErrorKind(StrEnum):
    """Transport-independent semantic category for expected domain failures."""

    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"


class FleetDomainError(Exception):
    """Base class for expected domain failures that cross service/API boundaries."""

    code = "FLEET_DOMAIN_ERROR"
    kind = ErrorKind.INTERNAL
    retryable = False


class InvalidRequest(FleetDomainError):
    code = "INVALID_REQUEST"
    kind = ErrorKind.INVALID_REQUEST


class ResourceNotFound(FleetDomainError):
    code = "RESOURCE_NOT_FOUND"
    kind = ErrorKind.NOT_FOUND


class StateConflict(FleetDomainError):
    code = "STATE_CONFLICT"
    kind = ErrorKind.CONFLICT


class WorkspaceNotFound(ResourceNotFound):
    code = "WORKSPACE_NOT_FOUND"


class WorkspaceStateStale(StateConflict):
    code = "WORKSPACE_STATE_STALE"
    retryable = True


class WorkspaceTransitionInvalid(StateConflict):
    code = "WORKSPACE_TRANSITION_INVALID"


class RepositoryNotManaged(ResourceNotFound):
    code = "REPOSITORY_NOT_MANAGED"


class DependencyTargetSelectionNotFound(ResourceNotFound):
    code = "DEPENDENCY_TARGET_SELECTION_NOT_FOUND"


class LeagueTeamNotManaged(ResourceNotFound):
    code = "LEAGUE_TEAM_NOT_MANAGED"


class LeagueLineupStale(StateConflict):
    code = "LEAGUE_LINEUP_STALE"
    retryable = True


class TeamPipelineNotConfigured(InvalidRequest):
    code = "TEAM_PIPELINE_NOT_CONFIGURED"


class LeaguePreferencesInvalid(FleetDomainError):
    code = "LEAGUE_PREFERENCES_INVALID"


class LocationNotDirectory(InvalidRequest):
    code = "LOCATION_NOT_DIRECTORY"


class FilesystemBrowseError(InvalidRequest):
    code = "FILESYSTEM_BROWSE_ERROR"


class ScheduleNotFound(ResourceNotFound):
    code = "SCHEDULE_NOT_FOUND"


class ScheduleExecutionActive(StateConflict):
    code = "SCHEDULE_EXECUTION_ACTIVE"


class ScheduleExecutionStale(StateConflict):
    code = "SCHEDULE_EXECUTION_STALE"
    retryable = True


class ScheduleDefinitionStale(StateConflict):
    code = "SCHEDULE_DEFINITION_STALE"
    retryable = True


class SecurityRunNotFound(ResourceNotFound):
    code = "SECURITY_RUN_NOT_FOUND"


class DurableStateBusy(StateConflict):
    code = "DURABLE_STATE_BUSY"


class DatabaseSchemaMismatch(FleetDomainError):
    code = "DATABASE_SCHEMA_MISMATCH"


class StateMetadataMissing(FleetDomainError):
    code = "STATE_METADATA_MISSING"


class JournalCorruption(FleetDomainError):
    code = "JOURNAL_CORRUPTION"


class InvalidRunTransition(StateConflict):
    code = "INVALID_RUN_TRANSITION"


class RunNotFound(ResourceNotFound):
    code = "RUN_NOT_FOUND"


class OperationNotFound(ResourceNotFound):
    code = "OPERATION_NOT_FOUND"


class CapabilityUnavailable(InvalidRequest):
    code = "CAPABILITY_UNAVAILABLE"


class TargetSelectionError(StateConflict):
    code = "TARGET_SELECTION_ERROR"


class MigrationPreconditionFailed(StateConflict):
    code = "MIGRATION_PRECONDITION_FAILED"


class MigrationSourceUnsupported(InvalidRequest):
    code = "MIGRATION_SOURCE_UNSUPPORTED"


class JournalConflict(StateConflict):
    code = "JOURNAL_CONFLICT"


class ExecutionBusy(StateConflict):
    code = "EXECUTION_BUSY"
    retryable = True


class ExecutorPlacementLost(StateConflict):
    """The exact physical executor admitted for a run can no longer be proven."""

    code = "EXECUTOR_PLACEMENT_LOST"
    retryable = False


class ExecutorGenerationSuperseded(ExecutorPlacementLost):
    """A newer durable generation replaced the exact admitted executor generation."""


class ExecutorTrustRevoked(ExecutorPlacementLost):
    """Operator trust for the exact admitted executor was revoked."""


class ExecutionAuthorityDivergence(StateConflict):
    """Attempted runtime executable resolution diverged from admitted authority."""

    code = "EXECUTION_AUTHORITY_DIVERGENCE"
    retryable = False


class ExecutorCapacityBusy(StateConflict):
    """No configured executor can currently reserve one complete CI/CD attempt."""

    code = "EXECUTOR_CAPACITY_BUSY"
    retryable = True


class ExecutorReadmissionNotAllowed(StateConflict):
    """A run is not eligible for explicit executor re-admission."""

    code = "EXECUTOR_READMISSION_NOT_ALLOWED"
    retryable = False


class ExecutorAdmissionCancelled(StateConflict):
    """An operator cancelled a strict CI/CD admission before RUN_ADMITTED."""

    code = "EXECUTOR_ADMISSION_CANCELLED"
    retryable = False


class ExecutorAdmissionBlocked(StateConflict):
    """Operator maintenance/trust state currently forbids new executor placement."""

    code = "EXECUTOR_ADMISSION_BLOCKED"
    retryable = True


class ExecutorQueueEntryNotFound(ResourceNotFound):
    """The requested pre-admission placement ticket no longer exists."""

    code = "EXECUTOR_QUEUE_ENTRY_NOT_FOUND"


class ExecutorNotFound(ResourceNotFound):
    """The requested logical executor is not present in the captured inventory."""

    code = "EXECUTOR_NOT_FOUND"


class RuntimeQuiescing(StateConflict):
    code = "RUNTIME_QUIESCING"
    retryable = True


class RuntimeSafetyBlocked(StateConflict):
    code = "RUNTIME_SAFETY_BLOCKED"
    retryable = False


class RuntimeSafetyStale(StateConflict):
    code = "RUNTIME_SAFETY_STALE"
    retryable = True


class RepositoryEffectQuarantined(StateConflict):
    code = "REPOSITORY_EFFECT_QUARANTINED"
    retryable = False



class CandidateCheckpointNotFound(ResourceNotFound):
    """Candidate checkpoint identity does not exist."""


class CandidateCheckpointStale(StateConflict):
    """Candidate bytes or checkpoint parent changed before advancement."""


class ExecutionAssignmentNotFound(ResourceNotFound):
    """Immutable execution assignment does not exist."""


class ExecutionAssignmentStale(StateConflict):
    """Assignment no longer names the current admitted workspace checkpoint."""


class ExecutionMaterializationNotFound(ResourceNotFound):
    """Immutable execution materialization does not exist."""


class ExecutionMaterializationStale(StateConflict):
    """Materialization no longer joins current assignment/lease/checkpoint authority."""


class ExecutionEvidenceAcceptanceNotFound(ResourceNotFound):
    """Accepted execution-evidence receipt does not exist."""


class ExecutionEvidenceRejected(StateConflict):
    """Raw executor evidence does not match current admitted execution authority."""


class WorkspaceLeaseNotFound(ResourceNotFound):
    """Workspace lease identity does not exist."""


class WorkspaceLeaseBusy(StateConflict):
    """Mutable workspace already has a current possession lease."""


class WorkspaceLeaseLost(StateConflict):
    """Workspace lease is no longer the current mutation authority."""


class WorkspaceLeaseExpired(WorkspaceLeaseLost):
    """Workspace lease deadline elapsed; it is not silently reusable."""


class WorkspaceFenceUnproven(StateConflict):
    """Old physical writer could not be proven quiescent before reuse."""

class LeaseLost(StateConflict):
    code = "LEASE_LOST"
    retryable = True


class OwnershipLost(LeaseLost):
    code = "OWNERSHIP_LOST"


class RepositoryStateChanged(StateConflict):
    code = "REPOSITORY_STATE_CHANGED"
    retryable = True


class OperationSuperseded(StateConflict):
    code = "OPERATION_SUPERSEDED"


class OperationCancelled(StateConflict):
    code = "OPERATION_CANCELLED"


class FleetPlanInvalid(InvalidRequest):
    code = "FLEET_PLAN_INVALID"


class FleetPlanInputRejected(FleetPlanInvalid):
    """A canonical input owner rejected a value or contract.

    Human exception messages retain compatibility, but diagnostic clients use
    *only* the typed code/names. The message may contain untrusted caller data
    and must never be serialized into the safe diagnostic projection.
    """

    def __init__(
        self, message: str, *, diagnostic_code: str, input_names: tuple[str, ...]
    ) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.input_names = input_names


class ExternalEffectOutcomeUnknown(StateConflict):
    """A remote mutation may have committed but no authoritative response was received."""

    code = "EXTERNAL_EFFECT_OUTCOME_UNKNOWN"
    retryable = False


class FleetPlanSourceNotFound(ResourceNotFound):
    code = "FLEET_PLAN_SOURCE_NOT_FOUND"


class FleetPlanSourceStale(StateConflict):
    code = "FLEET_PLAN_SOURCE_STALE"
    retryable = False


class FleetExtensionInvalid(InvalidRequest):
    code = "FLEET_EXTENSION_INVALID"
