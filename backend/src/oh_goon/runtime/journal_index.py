from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from oh_goon.core.errors import InvalidRunTransition, PiSessionBusy
from oh_goon.core.execution_lifecycle_validation import pi_writer_cleanup_unverified
from oh_goon.core.fleet_plan_projection import fleet_plan_logical_slot
from oh_goon.core.models.agent_providers import (
    NoPersistentPiSession,
    PiNativeConfigurationMismatch,
    PiSessionCleanupUnverified,
    PiSessionHistoryUnqualified,
    PiSessionIdentityMismatch,
    PiSessionSettlementUncertain,
    PiSessionUnavailable,
)
from oh_goon.core.models.agent_repair import AgentCampaignRecord
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import fleet_readmission_parent
from oh_goon.core.models.run_events import (
    FleetRunEvent,
    PiSessionContinuationOrigin,
    PiSessionRetirementRequestedEvent,
    PiSessionWriterObservedEvent,
    PiSessionWriterReservedEvent,
    RepositoryCompletedEvent,
    RunAdmittedEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    StepSucceededEvent,
)
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.product_identity import operation_intent_key, repository_id
from oh_goon.core.result_lifecycle import result_freshness
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.vocabulary import FleetRunEventType, OperationAction, ResultFreshness
from oh_goon.runtime.journal_facts import (
    AgentCampaignCommitted,
    EffectAcknowledgementCommitted,
    EffectReconciliationCommitted,
    FleetOperationCommitted,
    JournalEntry,
    OperationCommitted,
    OperationFact,
    RunEventCommitted,
)


def _operation_facts(entries: tuple[JournalEntry, ...]) -> tuple[OperationFact, ...]:
    return tuple(
        entry
        for entry in entries
        if isinstance(entry, (OperationCommitted, FleetOperationCommitted))
    )


@dataclass(frozen=True, slots=True)
class _RepositoryIndexes:
    timelines: Mapping[str, tuple[OperationFact, ...]]
    reconciliations: Mapping[str, tuple[EffectReconciliationCommitted, ...]]
    exact_positions: Mapping[tuple[str, tuple[str, str]], int]
    family_positions: Mapping[tuple[str, OperationAction], int]


def _build_repository_indexes(
    entries: tuple[JournalEntry, ...],
    operation_facts: tuple[OperationFact, ...],
) -> _RepositoryIndexes:
    timelines: dict[str, list[OperationFact]] = {}
    reconciliations: dict[str, list[EffectReconciliationCommitted]] = {}
    for operation_entry in operation_facts:
        timelines.setdefault(operation_entry.report.repository.repository_id, []).append(
            operation_entry
        )
    for entry in entries:
        if isinstance(entry, EffectReconciliationCommitted):
            reconciliations.setdefault(entry.evidence.effect.repository.repository_id, []).append(
                entry
            )

    timeline_index = {key: tuple(value) for key, value in timelines.items()}
    reconciliation_index = {key: tuple(value) for key, value in reconciliations.items()}
    exact_positions: dict[tuple[str, tuple[str, str]], int] = {}
    family_positions: dict[tuple[str, OperationAction], int] = {}
    for repo_id, timeline in timeline_index.items():
        for index, entry in enumerate(timeline):
            exact_positions[
                (
                    repo_id,
                    operation_intent_key(
                        entry.report.capability.capability_id, entry.report.target
                    ),
                )
            ] = index
            family_positions[(repo_id, entry.report.action)] = index

    return _RepositoryIndexes(
        timelines=MappingProxyType(timeline_index),
        reconciliations=MappingProxyType(reconciliation_index),
        exact_positions=MappingProxyType(exact_positions),
        family_positions=MappingProxyType(family_positions),
    )


def _build_readmission_index(
    run_details_by_id: Mapping[str, FleetRunDetail],
) -> Mapping[str, str]:
    child_by_parent: dict[str, str] = {}
    for run_id, detail in run_details_by_id.items():
        parent = fleet_readmission_parent(detail.execution)
        if parent is None:
            continue
        existing_child = child_by_parent.get(parent)
        if existing_child is not None and existing_child != run_id:
            raise InvalidRunTransition(
                "Journal invariant violated: one Fleet run has multiple re-admission children"
            )
        child_by_parent[parent] = run_id
    return MappingProxyType(child_by_parent)


_TERMINAL_RUN_EVENTS = (RunCompletedEvent, RunCancelledEvent, RunFailedEvent)


def pi_session_recorded_writer(events: Sequence[FleetRunEvent]) -> PiSessionWriterReservedEvent:
    writer = next((item for item in events if isinstance(item, PiSessionWriterReservedEvent)), None)
    if writer is None:
        raise NoPersistentPiSession("Selected prior Run has no persistent Pi writer")
    return writer


def require_latest_pi_writer(
    writer: PiSessionWriterReservedEvent,
    events_by_run: Mapping[str, Sequence[FleetRunEvent]],
    writer_heads: Mapping[str, PiSessionWriterReservedEvent],
) -> None:
    head = writer_heads.get(writer.storage_id)
    if head is None:
        raise InvalidRunTransition("Missing original Pi writer lineage head")
    if not any(isinstance(item, _TERMINAL_RUN_EVENTS) for item in events_by_run[head.fleet_run_id]):
        raise PiSessionBusy("Selected Pi session still has an active writer")
    if head != writer:
        raise PiSessionUnavailable("Selected Pi session has a newer writer reservation")


def pi_session_predecessor(
    prior_run_id: str,
    admission: RunAdmittedEvent,
    events_by_run: Mapping[str, Sequence[FleetRunEvent]],
    writer_heads: Mapping[str, PiSessionWriterReservedEvent],
) -> tuple[PiSessionWriterReservedEvent, PiSessionWriterObservedEvent]:
    """Resolve one accepted predecessor at the exact existing Journal cut."""
    events = events_by_run.get(prior_run_id, ())
    if prior_run_id == admission.fleet_run_id:
        raise NoPersistentPiSession("Selected prior Run has no persistent Pi writer")
    prior = pi_session_recorded_writer(events)
    previous = events[0]
    if (
        not isinstance(previous, RunAdmittedEvent)
        or len(previous.targets) != 1
        or len(previous.repository_admissions) != 1
    ):
        raise InvalidRunTransition("Missing original Pi repository admission")
    if (
        len(admission.targets) != 1
        or len(admission.repository_admissions) != 1
        or admission.targets[0].repository_id != previous.targets[0].repository_id
        or admission.targets[0].path != previous.targets[0].path
        or admission.repository_admissions[0].source_authority.subject_instance_id
        != previous.repository_admissions[0].source_authority.subject_instance_id
    ):
        raise PiSessionIdentityMismatch(
            "Selected Pi session belongs to a different repository subject"
        )
    require_latest_pi_writer(prior, events_by_run, writer_heads)
    if any(isinstance(item, PiSessionRetirementRequestedEvent) for item in events):
        raise PiSessionUnavailable("Selected Pi session is retired or pending retirement")
    old_target, new_target = previous.target, admission.target
    if (
        not isinstance(old_target, FleetPlanTarget)
        or not isinstance(new_target, FleetPlanTarget)
        or old_target.plan.native_agent is None
        or new_target.plan.native_agent is None
        or old_target.plan.native_agent.executable_sha256
        != new_target.plan.native_agent.executable_sha256
    ):
        raise PiNativeConfigurationMismatch(
            "Selected Pi session has a different native implementation"
        )
    return prior, pi_session_qualified_observation(prior, events)


def pi_session_qualified_observation(
    prior: PiSessionWriterReservedEvent, events: Sequence[FleetRunEvent]
) -> PiSessionWriterObservedEvent:
    observed = next(
        (
            item
            for item in events[prior.sequence :]
            if isinstance(item, PiSessionWriterObservedEvent)
            and item.reservation_sequence == prior.sequence
        ),
        None,
    )
    if observed is None and pi_writer_cleanup_unverified(prior, events):
        raise PiSessionCleanupUnverified("Original Pi writer cleanup is unverified")
    if observed is None or observed.observation.persistence != "observed":
        raise PiSessionSettlementUncertain(
            "Selected Pi session has no qualified persistence observation"
        )
    if observed.observation.file is None or observed.observation.file.sha256 is None:
        raise PiSessionHistoryUnqualified(
            "Selected Pi session has no qualified persistence observation"
        )
    accepted = next(
        (
            item
            for item in events[observed.sequence :]
            if isinstance(item, StepSucceededEvent)
            and (item.repository_id, item.operation_id, item.step_id)
            == (prior.repository_id, prior.operation_id, prior.step_id)
        ),
        None,
    )
    if accepted is None or not any(
        isinstance(item, RepositoryCompletedEvent) and item.operation_id == prior.operation_id
        for item in events[accepted.sequence :]
    ):
        raise PiSessionSettlementUncertain(
            "Selected Pi session has no accepted original Agent result"
        )
    return observed


def pi_session_origin_observation(
    origin: PiSessionContinuationOrigin,
    events_by_run: Mapping[str, Sequence[FleetRunEvent]],
) -> PiSessionWriterObservedEvent:
    events = events_by_run.get(origin.prior_run_id, ())
    if 1 <= origin.observation_sequence <= len(events):
        event = events[origin.observation_sequence - 1]
        if (
            isinstance(event, PiSessionWriterObservedEvent)
            and event.sequence == origin.observation_sequence
            and event.reservation_sequence == origin.reservation_sequence
        ):
            return event
    raise InvalidRunTransition("Missing original Pi continuation observation")


def require_pi_continuation_observation(
    event: PiSessionWriterObservedEvent, predecessor: PiSessionWriterObservedEvent
) -> None:
    """An observed successor may advance a leaf within the original file and session."""
    result, previous = event.observation, predecessor.observation
    if result.persistence == "observed" and (
        result.session_id != previous.session_id
        or result.file is None
        or result.file.sha256 is None
        or previous.file is None
        or (result.file.device, result.file.inode) != (previous.file.device, previous.file.inode)
    ):
        raise InvalidRunTransition("Pi continuation observation changed original session identity")


def _index_pi_session_writer(
    event: PiSessionWriterReservedEvent,
    history: Sequence[FleetRunEvent],
    events_by_run: Mapping[str, Sequence[FleetRunEvent]],
    writer_heads: dict[str, PiSessionWriterReservedEvent],
) -> None:
    origin = event.continuation
    if origin is None:
        if event.storage_id in writer_heads:
            raise InvalidRunTransition("Journal invariant violated: duplicate Pi writer origin")
    else:
        admission = history[0]
        if not isinstance(admission, RunAdmittedEvent):
            raise InvalidRunTransition("Journal invariant violated: missing Pi Run admission")
        try:
            prior, observed = pi_session_predecessor(
                origin.prior_run_id, admission, events_by_run, writer_heads
            )
        except (PiSessionBusy, PiSessionUnavailable) as exc:
            raise InvalidRunTransition(
                "Journal invariant violated: unqualified Pi predecessor"
            ) from exc
        if (
            prior.storage_id != event.storage_id
            or prior.sequence != origin.reservation_sequence
            or observed.sequence != origin.observation_sequence
        ):
            raise InvalidRunTransition("Journal invariant violated: Pi predecessor changed")
    writer_heads[event.storage_id] = event


@dataclass(frozen=True, slots=True)
class CurrentFleetPlanOperation:
    revision: int
    fleet_run_id: str
    report: OperationReport


@dataclass(frozen=True, slots=True)
class JournalIndex:
    """Private disposable indexes for one already-verified Journal read cut."""

    campaigns: Mapping[str, tuple[AgentCampaignRecord, ...]]
    operation_facts: tuple[OperationFact, ...]
    operations_by_id: Mapping[str, OperationReport]
    timelines_by_repository: Mapping[str, tuple[OperationFact, ...]]
    reconciliations_by_repository: Mapping[str, tuple[EffectReconciliationCommitted, ...]]
    reconciliations_by_operation: Mapping[str, tuple[EffectReconciliationCommitted, ...]]
    acknowledgements_by_operation: Mapping[str, EffectAcknowledgementCommitted]
    current_exact_positions: Mapping[tuple[str, tuple[str, str]], int]
    current_family_positions: Mapping[tuple[str, OperationAction], int]
    events_by_run: Mapping[str, tuple[FleetRunEvent, ...]]
    pi_session_writer_heads: Mapping[str, PiSessionWriterReservedEvent]
    run_details_by_id: Mapping[str, FleetRunDetail]
    latest_run_by_intent: Mapping[tuple[str, str], str]
    latest_run_by_family: Mapping[OperationAction, str]
    latest_run_ids: tuple[str, ...]
    ordered_run_ids: tuple[str, ...]
    active_run_ids: tuple[str, ...]
    admitted_revisions: tuple[tuple[int, str], ...]
    admitting_sessions_by_operation: Mapping[str, str]
    readmission_child_by_parent: Mapping[str, str]
    current_fleet_plan_operations: Mapping[tuple[str, tuple[str, str]], CurrentFleetPlanOperation]

    @classmethod
    def build(
        cls, entries: tuple[JournalEntry, ...], *, previous: JournalIndex | None = None
    ) -> JournalIndex:
        operation_facts = _operation_facts(entries)
        operations_by_id = {
            entry.report.provenance.operation_id: entry.report for entry in operation_facts
        }
        repository_indexes = _build_repository_indexes(entries, operation_facts)
        reconciliations_by_operation: dict[str, list[EffectReconciliationCommitted]] = {}
        acknowledgements_by_operation: dict[str, EffectAcknowledgementCommitted] = {}
        run_events: dict[str, list[FleetRunEvent]] = {}
        pi_session_writer_heads: dict[str, PiSessionWriterReservedEvent] = {}
        admitted: list[tuple[int, str]] = []
        admission_sessions: dict[str, str] = {}
        current_fleet_plan_operations: dict[
            tuple[str, tuple[str, str]], CurrentFleetPlanOperation
        ] = {}
        campaigns: dict[str, list[AgentCampaignRecord]] = {}
        for entry in entries:
            if isinstance(entry, AgentCampaignCommitted):
                history = campaigns.setdefault(entry.fact.campaign_id, [])
                previous_revision = history[-1].revision if history else 0
                if entry.fact.expected_revision != previous_revision:
                    raise InvalidRunTransition(
                        "Journal invariant violated: campaign predecessor mismatch"
                    )
                history.append(
                    AgentCampaignRecord(
                        session_id=entry.session_id, revision=entry.revision, fact=entry.fact
                    )
                )
                continue
            if isinstance(entry, EffectReconciliationCommitted):
                reconciliations_by_operation.setdefault(
                    entry.evidence.effect.operation_id, []
                ).append(entry)
                continue
            if isinstance(entry, EffectAcknowledgementCommitted):
                acknowledgements_by_operation[entry.evidence.effect.operation_id] = entry
                continue
            if not isinstance(entry, (RunEventCommitted, FleetOperationCommitted)):
                continue
            run_history = run_events.setdefault(entry.event.fleet_run_id, [])
            if isinstance(entry.event, PiSessionWriterReservedEvent):
                _index_pi_session_writer(
                    entry.event, run_history, run_events, pi_session_writer_heads
                )
            elif isinstance(entry.event, PiSessionRetirementRequestedEvent):
                try:
                    writer = pi_session_recorded_writer(run_history)
                    require_latest_pi_writer(writer, run_events, pi_session_writer_heads)
                except (PiSessionBusy, PiSessionUnavailable) as exc:
                    raise InvalidRunTransition(
                        "Pi retirement does not own the latest writer"
                    ) from exc
            elif isinstance(entry.event, PiSessionWriterObservedEvent):
                seq = entry.event.reservation_sequence
                if 1 <= seq <= len(run_history):
                    reservation = run_history[seq - 1]
                    if (
                        isinstance(reservation, PiSessionWriterReservedEvent)
                        and reservation.continuation is not None
                    ):
                        predecessor = pi_session_origin_observation(
                            reservation.continuation, run_events
                        )
                        require_pi_continuation_observation(entry.event, predecessor)
            run_history.append(entry.event)
            if isinstance(entry, FleetOperationCommitted) and isinstance(
                entry.report.target, FleetPlanTarget
            ):
                current_fleet_plan_operations[
                    (
                        entry.report.repository.repository_id,
                        fleet_plan_logical_slot(entry.report.target),
                    )
                ] = CurrentFleetPlanOperation(
                    revision=entry.revision,
                    fleet_run_id=entry.event.fleet_run_id,
                    report=entry.report,
                )
            if entry.event.event_type is FleetRunEventType.RUN_ADMITTED:
                admitted.append((entry.revision, entry.event.fleet_run_id))
                if isinstance(entry, RunEventCommitted):
                    admission_sessions[entry.event.fleet_run_id] = entry.session_id

        events_by_run = {run_id: tuple(items) for run_id, items in run_events.items()}
        run_details_by_id: dict[str, FleetRunDetail] = {}
        for run_id, events_for_run in events_by_run.items():
            cached = previous.run_details_by_id.get(run_id) if previous is not None else None
            # A successful fold records every resolved operation in detail.operations.
            # Reuse requires the same validated events and every resolver dependency;
            # outer indexes still derive from this complete, integrity-verified cut.
            if (
                cached is not None
                and previous is not None
                and previous.events_by_run.get(run_id) == events_for_run
                and all(
                    operations_by_id.get(report.provenance.operation_id) == report
                    for report in cached.operations
                )
            ):
                run_details_by_id[run_id] = cached
            else:
                run_details_by_id[run_id] = project_fleet_run_detail(
                    events_for_run, operations_by_id.get
                )
        # The validated Run fold above owns report/Run/capability/repository
        # binding. An accepted worker report is fresh for its original control
        # session as well as its actual writer; opening a new runtime still
        # invalidates both contexts. Never rewrite the worker's fact provenance.
        admitting_sessions_by_operation = {
            entry.report.provenance.operation_id: admission_sessions[entry.event.fleet_run_id]
            for entry in operation_facts
            if isinstance(entry, FleetOperationCommitted)
            and isinstance(entry.event, RepositoryCompletedEvent)
            and entry.event.fleet_run_id in admission_sessions
            and entry.event.operation_id == entry.report.provenance.operation_id
            and operations_by_id.get(entry.event.operation_id) == entry.report
        }
        ordered_states = sorted(
            (detail.run for detail in run_details_by_id.values()),
            key=lambda state: (state.accepted_at, state.fleet_run_id),
        )
        readmission_child_by_parent = _build_readmission_index(run_details_by_id)
        latest_run_by_intent: dict[tuple[str, str], str] = {}
        latest_run_by_family: dict[OperationAction, str] = {}
        for state in ordered_states:
            latest_run_by_intent[
                operation_intent_key(state.capability.capability_id, state.target)
            ] = state.fleet_run_id
            latest_run_by_family[state.action] = state.fleet_run_id

        return cls(
            campaigns=MappingProxyType({key: tuple(value) for key, value in campaigns.items()}),
            operation_facts=operation_facts,
            operations_by_id=MappingProxyType(operations_by_id),
            timelines_by_repository=repository_indexes.timelines,
            reconciliations_by_repository=repository_indexes.reconciliations,
            reconciliations_by_operation=MappingProxyType(
                {key: tuple(value) for key, value in reconciliations_by_operation.items()}
            ),
            acknowledgements_by_operation=MappingProxyType(acknowledgements_by_operation),
            current_exact_positions=repository_indexes.exact_positions,
            current_family_positions=repository_indexes.family_positions,
            events_by_run=MappingProxyType(events_by_run),
            pi_session_writer_heads=MappingProxyType(pi_session_writer_heads),
            run_details_by_id=MappingProxyType(run_details_by_id),
            latest_run_by_intent=MappingProxyType(latest_run_by_intent),
            latest_run_by_family=MappingProxyType(latest_run_by_family),
            latest_run_ids=tuple(latest_run_by_intent[key] for key in sorted(latest_run_by_intent)),
            ordered_run_ids=tuple(state.fleet_run_id for state in ordered_states),
            active_run_ids=tuple(
                state.fleet_run_id for state in ordered_states if not state.status.terminal
            ),
            admitted_revisions=tuple(admitted),
            admitting_sessions_by_operation=MappingProxyType(admitting_sessions_by_operation),
            readmission_child_by_parent=readmission_child_by_parent,
            current_fleet_plan_operations=MappingProxyType(current_fleet_plan_operations),
        )

    def current_capability(
        self,
        session_id: str,
        repository: Path | str,
        capability_id: str,
        target: OperationTarget | None,
    ) -> OperationState | None:
        """Return the latest exact capability/target state."""
        repo_id = repository_id(repository)
        timeline = self.timelines_by_repository.get(repo_id, ())
        index = self.current_exact_positions.get(
            (repo_id, operation_intent_key(capability_id, target))
        )
        return self._current_at(session_id, repo_id, timeline, index)

    def current_family(
        self,
        session_id: str,
        repository: Path | str,
        action: OperationAction,
    ) -> OperationState | None:
        """Return the latest state in one action family, regardless of target."""
        repo_id = repository_id(repository)
        timeline = self.timelines_by_repository.get(repo_id, ())
        index = self.current_family_positions.get((repo_id, action))
        return self._current_at(session_id, repo_id, timeline, index)

    def _current_at(
        self,
        session_id: str,
        repo_id: str,
        timeline: tuple[OperationFact, ...],
        index: int | None,
    ) -> OperationState | None:
        if index is None:
            return None
        entry = timeline[index]
        later_invalidators = [item.report.capability for item in timeline[index + 1 :]]
        later_invalidators.extend(
            item.evidence.effect.capability
            for item in self.reconciliations_by_repository.get(repo_id, ())
            if item.revision > entry.revision and item.evidence.invalidates_prior_results
        )
        freshness = result_freshness(entry.report, later_invalidators)
        if (
            entry.session_id != session_id
            and self.admitting_sessions_by_operation.get(entry.report.provenance.operation_id)
            != session_id
        ):
            freshness = ResultFreshness.STALE
        return OperationState(report=entry.report, freshness=freshness)

    def run_detail(self, fleet_run_id: str) -> FleetRunDetail | None:
        return self.run_details_by_id.get(fleet_run_id)
