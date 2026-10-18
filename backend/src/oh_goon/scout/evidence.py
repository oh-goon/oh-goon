from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.execution_observability import (
    RepositoryRunObservation,
    project_repository_run_observations,
)
from oh_goon.core.fleet_plan_projection import atomic_goon_by_step
from oh_goon.scout.models import ScoutComparisonEnvironment, ScoutResourceEnvelope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import GameTapeStatus

_EXECUTED_STEP_STATUSES = frozenset(
    {
        GameTapeStatus.PASSED,
        GameTapeStatus.ATTENTION_REQUIRED,
        GameTapeStatus.FAILED,
        GameTapeStatus.CANCELLED,
        GameTapeStatus.INTERRUPTED,
    }
)


@dataclass(frozen=True, slots=True)
class ScoutStepObservation:
    run_id: str
    started_at: datetime
    repository_id: str
    repository_name: str
    repository_status: GameTapeStatus
    plan_id: str | None
    plan_name: str | None
    goon_name: str
    goon_source_sha256: str
    step_id: str
    step_status: GameTapeStatus
    attempt_count: int
    eligible_attempts: int
    cpu_observed_attempts: int
    memory_observed_attempts: int
    observed_cpu_seconds: float
    complete_cpu_seconds: float | None
    observed_peak_memory_bytes: int | None
    complete_peak_memory_bytes: int | None
    environment_key: tuple[object, ...]
    duration_seconds: float | None = None


def comparison_environment(item: ScoutStepObservation) -> ScoutComparisonEnvironment:
    backend_id, configuration_id, runtime_root_sha256, host_toolchain_id, envelopes = (
        item.environment_key
    )
    return ScoutComparisonEnvironment(
        isolation_backend_id=_optional_string(backend_id),
        isolation_configuration_id=_optional_string(configuration_id),
        runtime_root_sha256=_optional_string(runtime_root_sha256),
        host_toolchain_id=_optional_string(host_toolchain_id),
        resource_envelopes=tuple(
            ScoutResourceEnvelope(cpu_millis=int(cpu), memory_bytes=int(memory))
            for cpu, memory in envelopes
        ),
    )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class ScoutEvidenceSnapshot:
    journal_revision: int
    runs: tuple[RepositoryRunObservation, ...]
    steps: tuple[ScoutStepObservation, ...]


@dataclass(frozen=True, slots=True)
class _AttemptEvidence:
    eligible_attempts: int
    attempt_count: int
    cpu_observed: int
    memory_observed: int
    cpu_seconds: float
    observed_peak_memory_bytes: int | None
    environment_envelopes: tuple[tuple[int, int], ...]

    @property
    def complete_cpu_seconds(self) -> float | None:
        if self.cpu_observed != self.eligible_attempts:
            return None
        return self.cpu_seconds

    @property
    def complete_peak_memory_bytes(self) -> int | None:
        if self.memory_observed != self.eligible_attempts:
            return None
        return self.observed_peak_memory_bytes


def project_scout_evidence(
    details: Iterable[FleetRunDetail], *, journal_revision: int
) -> ScoutEvidenceSnapshot:
    detail_tuple = tuple(details)
    runs = project_repository_run_observations(detail_tuple)
    steps: list[ScoutStepObservation] = []
    for detail in detail_tuple:
        tape = detail.game_tape
        started_at = tape.receipt.started_at
        if started_at is None:
            continue
        target = detail.run.target
        plan = target.plan if isinstance(target, FleetPlanTarget) else None
        step_goons = atomic_goon_by_step(plan) if plan is not None else {}
        for repository in tape.repositories:
            steps.extend(_step_observations(detail, repository, started_at, step_goons))
    return ScoutEvidenceSnapshot(
        journal_revision=journal_revision,
        runs=runs,
        steps=tuple(steps),
    )


def _step_observations(detail, repository, started_at: datetime, step_goons):
    tape = detail.game_tape
    fingerprint = repository.fingerprint
    result: list[ScoutStepObservation] = []
    for step in repository.steps:
        if step.status not in _EXECUTED_STEP_STATUSES:
            continue
        goon = step_goons.get(step.step_id)
        if goon is None:
            continue
        evidence = _attempt_evidence(step.attempts)
        result.append(
            ScoutStepObservation(
                run_id=tape.fleet_run_id,
                started_at=started_at,
                repository_id=repository.repository.repository_id,
                repository_name=repository.repository.name,
                repository_status=repository.status,
                plan_id=tape.plan_id,
                plan_name=tape.plan_name,
                goon_name=goon.name,
                goon_source_sha256=goon.source_sha256,
                step_id=step.step_id,
                step_status=step.status,
                attempt_count=evidence.attempt_count,
                eligible_attempts=evidence.eligible_attempts,
                cpu_observed_attempts=evidence.cpu_observed,
                memory_observed_attempts=evidence.memory_observed,
                observed_cpu_seconds=evidence.cpu_seconds,
                complete_cpu_seconds=evidence.complete_cpu_seconds,
                observed_peak_memory_bytes=evidence.observed_peak_memory_bytes,
                complete_peak_memory_bytes=evidence.complete_peak_memory_bytes,
                duration_seconds=(
                    step.duration_ms / 1000 if step.duration_ms is not None else None
                ),
                environment_key=(
                    fingerprint.isolation_backend_id,
                    fingerprint.isolation_configuration_id,
                    fingerprint.runtime_root_sha256,
                    fingerprint.host_toolchain_id,
                    evidence.environment_envelopes,
                ),
            )
        )
    return result


def _attempt_evidence(attempts) -> _AttemptEvidence:
    eligible_attempts = len(attempts) if attempts else 1
    cpu_observed = 0
    memory_observed = 0
    cpu_seconds = 0.0
    memory_peaks: list[int] = []
    resource_envelopes: set[tuple[int, int]] = set()
    for attempt in attempts:
        resource = attempt.resources
        if resource is None:
            continue
        resource_envelopes.add((resource.cpu_millis, resource.memory_bytes))
        if resource.cpu_usage_usec is not None:
            cpu_observed += 1
            cpu_seconds += resource.cpu_usage_usec / 1_000_000
        if resource.memory_peak_bytes is not None:
            memory_observed += 1
            memory_peaks.append(resource.memory_peak_bytes)
    return _AttemptEvidence(
        eligible_attempts=eligible_attempts,
        attempt_count=len(attempts),
        cpu_observed=cpu_observed,
        memory_observed=memory_observed,
        cpu_seconds=cpu_seconds,
        observed_peak_memory_bytes=max(memory_peaks) if memory_peaks else None,
        environment_envelopes=tuple(sorted(resource_envelopes)),
    )
