from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from oh_goon.core.errors import InvalidRequest, InvalidRunTransition, OperationNotFound, RunNotFound
from oh_goon.core.fleet_plan_projection import atomic_goon_by_step
from oh_goon.core.models.agent_repair import AgentCampaignReadback
from oh_goon.core.models.execution_observability import ExecutionTimeBucket, FleetExecutionTimeline
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.fleet_failures import (
    FleetFailureGroup,
    FleetFailureGroupPage,
    FleetFailureInventory,
    FleetFailureRepositoryPage,
)
from oh_goon.core.models.game_tape import GameTapeRepositoryExecution
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.run import (
    FleetRunDetail,
    FleetRunFailureIdentity,
    FleetRunHistoryItem,
    FleetRunHistoryPage,
    FleetRunOverview,
    FleetRunState,
    fleet_run_outcome,
)
from oh_goon.core.models.run_events import FleetRunEventBatch
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import (
    FleetRunOutcome,
    FleetRunSource,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.runtime._journal_failure_projection import (
    CurrentFleetFailure,
    FailureGroupAccumulator,
    current_failure,
)
from oh_goon.runtime.execution_timeline_read import project_bounded_execution_timeline
from oh_goon.runtime.journal_facts import JournalEntry
from oh_goon.runtime.journal_index import JournalIndex


def _fleet_run_failure_identity(detail: FleetRunDetail | None) -> FleetRunFailureIdentity | None:
    if detail is None or fleet_run_outcome(detail.run) is not FleetRunOutcome.FAILED:
        return None
    # A run-level failure can exist without any repository operation evidence
    # (for example admission/submission/recovery failure).  Do not construct a
    # Game Tape merely to discover that there is no repository/Step identity:
    # history is a bounded canonical read surface, not an observability
    # projection trigger.
    if not detail.operations:
        return FleetRunFailureIdentity()
    focus = next((item for item in detail.game_tape.issue_focus if item.scope == "STEP"), None)
    if focus is None:
        focus = next(
            (item for item in detail.game_tape.issue_focus if item.scope == "REPOSITORY"), None
        )
    if focus is None:
        return FleetRunFailureIdentity()
    goon_name = None
    target = detail.run.target
    if focus.step_id is not None and isinstance(target, FleetPlanTarget):
        goon = atomic_goon_by_step(target.plan).get(focus.step_id)
        goon_name = goon.name if goon is not None else None
    return FleetRunFailureIdentity(
        repository_id=focus.repository_id,
        repository_name=focus.repository_name,
        goon_name=goon_name,
        step_id=focus.step_id,
    )


def _validate_history_range(from_at: datetime | None, to_at: datetime | None) -> None:
    if (from_at is None) != (to_at is None):
        raise InvalidRequest("Fleet run history requires both from_at and to_at")
    if from_at is None or to_at is None:
        return
    if from_at.tzinfo is None or to_at.tzinfo is None:
        raise InvalidRequest("Fleet run history range must use timezone-aware timestamps")
    if not from_at < to_at <= from_at + timedelta(days=366):
        raise InvalidRequest("Fleet run history range must be ordered and cannot exceed 366 days")


@dataclass(frozen=True, slots=True)
class JournalReadView:
    """Immutable indexed read cut over one exact, integrity-verified journal revision."""

    revision: int
    session_id: str
    _index: JournalIndex

    @classmethod
    def from_entries(
        cls,
        entries: tuple[JournalEntry, ...],
        session_id: str,
    ) -> JournalReadView:
        return cls(
            revision=entries[-1].revision if entries else 0,
            session_id=session_id,
            _index=JournalIndex.build(entries),
        )

    def campaigns(self) -> tuple[AgentCampaignReadback, ...]:
        return tuple(self.campaign(campaign_id) for campaign_id in self._index.campaigns)

    def campaign(self, campaign_id: str) -> AgentCampaignReadback:
        return AgentCampaignReadback(
            campaign_id=campaign_id,
            journal_revision=self.revision,
            records=self._index.campaigns.get(campaign_id, ()),
        )

    def operation(self, operation_id: str) -> OperationReport | None:
        report = self._index.operations_by_id.get(operation_id)
        return report.model_copy(deep=True) if report is not None else None

    def latest_effect_reconciliation(
        self, operation_id: str
    ) -> RepositoryEffectReconciliationEvidence | None:
        facts = self._index.reconciliations_by_operation.get(operation_id, ())
        if not facts:
            return None
        return facts[-1].evidence

    def effect_acknowledgement(
        self, operation_id: str
    ) -> RepositoryEffectAcknowledgementEvidence | None:
        fact = self._index.acknowledgements_by_operation.get(operation_id)
        return fact.evidence if fact is not None else None

    def require_operation(self, operation_id: str) -> OperationReport:
        report = self.operation(operation_id)
        if report is None:
            raise OperationNotFound(f"Unknown operation: {operation_id}")
        return report

    def current_capability(
        self,
        repository: Path | str,
        capability_id: str,
        target: OperationTarget | None = None,
    ) -> OperationState | None:
        state = self._index.current_capability(self.session_id, repository, capability_id, target)
        return state.model_copy(deep=True) if state is not None else None

    def current_family(
        self,
        repository: Path | str,
        action: OperationAction,
    ) -> OperationState | None:
        state = self._index.current_family(self.session_id, repository, action)
        return state.model_copy(deep=True) if state is not None else None

    def history(
        self,
        repository: Path | str,
        *,
        action: OperationAction | None = None,
    ) -> list[OperationReport]:
        timeline = self._index.timelines_by_repository.get(repository_id(repository), ())
        return [
            entry.report.model_copy(deep=True)
            for entry in timeline
            if action is None or entry.report.action is action
        ]

    def run_detail(self, fleet_run_id: str) -> FleetRunDetail | None:
        detail = self._index.run_detail(fleet_run_id)
        if detail is None:
            return None
        target = detail.run.target
        campaign = (
            self.campaign(target.agent_repair.repair_id)
            if isinstance(target, FleetPlanTarget) and target.agent_repair is not None
            else None
        )
        return detail.model_copy(
            deep=True,
            update={"journal_revision": self.revision, "campaign_journal": campaign},
        )

    def required_run_detail(self, fleet_run_id: str) -> FleetRunDetail:
        """Return one canonical Fleet run or fail instead of projecting missing data."""
        detail = self.run_detail(fleet_run_id)
        if detail is None:
            raise RunNotFound(f"Unknown fleet run: {fleet_run_id}")
        return detail

    def readmission_child(self, parent_fleet_run_id: str) -> FleetRunDetail | None:
        child_id = self._index.readmission_child_by_parent.get(parent_fleet_run_id)
        return self.run_detail(child_id) if child_id is not None else None

    def active_run(self) -> FleetRunState | None:
        if len(self._index.active_run_ids) > 1:
            raise InvalidRunTransition("Journal invariant violated: multiple active fleet runs")
        if not self._index.active_run_ids:
            return None
        detail = self._index.run_details_by_id[self._index.active_run_ids[0]]
        return detail.run

    def active_run_detail(self) -> FleetRunDetail | None:
        state = self.active_run()
        return self.run_detail(state.fleet_run_id) if state is not None else None

    def latest_run_exact(
        self,
        capability_id: str,
        target: OperationTarget | None = None,
    ) -> FleetRunDetail | None:
        """Return the latest FleetRun for one exact capability/target intent."""
        from oh_goon.core.product_identity import operation_intent_key

        run_id = self._index.latest_run_by_intent.get(operation_intent_key(capability_id, target))
        return self.run_detail(run_id) if run_id is not None else None

    def latest_run_family(self, action: OperationAction) -> FleetRunDetail | None:
        """Return the latest FleetRun across one action family, regardless of capability/target."""
        run_id = self._index.latest_run_by_family.get(action)
        return self.run_detail(run_id) if run_id is not None else None

    def latest_run_details(self) -> list[FleetRunDetail]:
        """Return one latest run per exact durable capability/target intent."""
        return [
            detail
            for run_id in self._index.latest_run_ids
            if (detail := self.run_detail(run_id)) is not None
        ]

    def run_details(self) -> list[FleetRunDetail]:
        """Return every FleetRun detail from this verified cut, oldest first."""
        return [
            detail
            for run_id in self._index.ordered_run_ids
            if (detail := self.run_detail(run_id)) is not None
        ]

    def run_detail_window(
        self, fleet_run_id: str, *, before_limit: int, after_limit: int = 0
    ) -> tuple[FleetRunDetail, tuple[FleetRunDetail, ...], tuple[FleetRunDetail, ...]]:
        """Read one run and bounded neighboring details from this verified cut."""
        if before_limit < 0 or after_limit < 0:
            raise InvalidRequest("Fleet run detail window limits cannot be negative")
        try:
            index = self._index.ordered_run_ids.index(fleet_run_id)
        except ValueError as exc:
            raise RunNotFound(f"Unknown fleet run: {fleet_run_id}") from exc
        ids = self._index.ordered_run_ids
        prior = reversed(ids[max(0, index - before_limit) : index])
        later = ids[index + 1 : index + 1 + after_limit]
        detail = self.required_run_detail
        return (detail(fleet_run_id), tuple(map(detail, prior)), tuple(map(detail, later)))

    def run_overview(self) -> FleetRunOverview:
        counts = {
            "successful": 0,
            "failed": 0,
            "running": 0,
            "queued": 0,
            "cancelling": 0,
            "cancelled": 0,
            "attention_required": 0,
        }
        for run_id in self._index.ordered_run_ids:
            run = self._required_indexed_run_state(run_id)
            outcome = fleet_run_outcome(run)
            if outcome is FleetRunOutcome.SUCCESS:
                counts["successful"] += 1
            elif outcome is FleetRunOutcome.FAILED:
                counts["failed"] += 1
            elif outcome is FleetRunOutcome.CANCELLED:
                counts["cancelled"] += 1
            elif outcome is FleetRunOutcome.ATTENTION_REQUIRED:
                counts["attention_required"] += 1
            elif run.status is FleetRunStatus.QUEUED:
                counts["queued"] += 1
            elif run.status is FleetRunStatus.CANCELLING:
                counts["cancelling"] += 1
            else:
                counts["running"] += 1
        return FleetRunOverview(
            journal_revision=self.revision,
            total=len(self._index.ordered_run_ids),
            **counts,
        )

    def execution_timeline(
        self,
        *,
        from_at: datetime,
        to_at: datetime,
        bucket: ExecutionTimeBucket,
        plan_query: str | None = None,
        repository_query: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> FleetExecutionTimeline:
        if from_at.tzinfo is None or to_at.tzinfo is None:
            raise InvalidRequest("Execution observation range must use timezone-aware timestamps")
        if to_at <= from_at:
            raise InvalidRequest("Execution observation to_at must be after from_at")
        if to_at - from_at > timedelta(days=366):
            raise InvalidRequest("Execution observation range cannot exceed 366 days")
        if offset < 0:
            raise InvalidRequest("Execution observation offset cannot be negative")
        if limit < 1 or limit > 100:
            raise InvalidRequest("Execution observation limit must be between 1 and 100")
        return project_bounded_execution_timeline(
            self._index,
            journal_revision=self.revision,
            from_at=from_at,
            to_at=to_at,
            bucket=bucket,
            plan_query=plan_query,
            repository_query=repository_query,
            offset=offset,
            limit=limit,
        )

    def run_history(
        self,
        *,
        limit: int = 20,
        before_revision: int | None = None,
        outcome: FleetRunOutcome | None = None,
        status: FleetRunStatus | None = None,
        source: FleetRunSource | None = None,
        repository_id: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        plan_query: str | None = None,
    ) -> FleetRunHistoryPage:
        if limit < 1 or limit > 100:
            raise InvalidRequest("Fleet run history limit must be between 1 and 100")
        _validate_history_range(from_at, to_at)
        normalized_plan = plan_query.strip().casefold() if plan_query else ""
        candidates = []
        for revision, run_id in reversed(self._index.admitted_revisions):
            if before_revision is not None and revision >= before_revision:
                continue
            run = self._required_indexed_run_state(run_id)
            if outcome is not None and fleet_run_outcome(run) is not outcome:
                continue
            if status is not None and run.status is not status:
                continue
            if source is not None and run.source is not source:
                continue
            if repository_id is not None and all(
                target.repository_id != repository_id for target in run.targets
            ):
                continue
            if (
                from_at is not None
                and to_at is not None
                and not (from_at <= run.accepted_at < to_at)
            ):
                continue
            if normalized_plan:
                target = run.target
                if not isinstance(target, FleetPlanTarget) or normalized_plan not in (
                    f"{target.plan.name} {target.plan.plan_id}".casefold()
                ):
                    continue
            candidates.append((revision, run_id))
            if len(candidates) == limit + 1:
                break
        page = candidates
        has_more = len(page) > limit
        items = []
        for revision, run_id in page[:limit]:
            detail = self.required_run_detail(run_id)
            items.append(
                FleetRunHistoryItem(
                    admitted_revision=revision,
                    run=detail.run,
                    execution=detail.execution,
                    failure_identity=_fleet_run_failure_identity(detail),
                )
            )
        return FleetRunHistoryPage(
            journal_revision=self.revision,
            items=tuple(items),
            next_before_revision=items[-1].admitted_revision if has_more and items else None,
        )

    def failure_inventory(self) -> FleetFailureInventory:
        repositories: set[str] = set()
        groups: set[str] = set()
        for failure in self._itercurrent_failures():
            repositories.add(failure.item.repository.repository_id)
            groups.add(failure.item.group_id)
        return FleetFailureInventory(
            journal_revision=self.revision,
            failing_repositories=len(repositories),
            failure_groups=len(groups),
        )

    def failure_groups(
        self, *, query: str | None = None, offset: int = 0, limit: int = 50
    ) -> FleetFailureGroupPage:
        _validate_failure_page(offset=offset, limit=limit)
        normalized = query.strip().casefold() if query is not None else ""
        grouped: dict[str, FailureGroupAccumulator] = {}
        for failure in self._itercurrent_failures():
            group_id = failure.item.group_id
            accumulator = grouped.get(group_id)
            if accumulator is None:
                grouped[group_id] = FailureGroupAccumulator.start(failure)
            else:
                accumulator.add(failure)
        groups: list[FleetFailureGroup] = []
        for group_id, accumulator in grouped.items():
            group = accumulator.project(group_id)
            if normalized and normalized not in " ".join(
                value.casefold()
                for value in (group.plan_name, group.plan_id, group.goon_name, group.step_id)
                if value
            ):
                continue
            groups.append(group)
        groups.sort(
            key=lambda group: (
                -group.affected_repositories,
                group.plan_name.casefold(),
                (group.goon_name or "").casefold(),
                group.step_id or "",
                group.group_id,
            )
        )
        return FleetFailureGroupPage(
            journal_revision=self.revision,
            total=len(groups),
            offset=offset,
            limit=limit,
            items=tuple(groups[offset : offset + limit]),
        )

    def failure_repositories(
        self,
        group_id: str,
        *,
        query: str | None = None,
        signature_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> FleetFailureRepositoryPage:
        _validate_failure_page(offset=offset, limit=limit)
        normalized = query.strip().casefold() if query is not None else ""
        group_accumulator: FailureGroupAccumulator | None = None
        candidates: list[CurrentFleetFailure] = []
        total = 0
        page_bound = offset + limit
        for failure in self._itercurrent_failures():
            if failure.item.group_id != group_id:
                continue
            if group_accumulator is None:
                group_accumulator = FailureGroupAccumulator.start(failure)
            else:
                group_accumulator.add(failure)
            if (signature_id is not None and failure.item.signature_id != signature_id) or (
                normalized
                and normalized not in failure.item.repository.name.casefold()
                and normalized not in str(failure.item.repository.path).casefold()
                and normalized not in failure.item.repository.repository_id.casefold()
            ):
                continue
            total += 1
            candidates.append(failure)
            candidates.sort(
                key=lambda item: (
                    item.item.repository.name.casefold(),
                    item.item.repository.repository_id,
                )
            )
            if len(candidates) > page_bound:
                candidates.pop()
        if group_accumulator is None:
            raise InvalidRequest(f"Unknown current failure group: {group_id}")
        group = group_accumulator.project(group_id)
        return FleetFailureRepositoryPage(
            journal_revision=self.revision,
            group=group,
            total=total,
            offset=offset,
            limit=limit,
            items=tuple(failure.item for failure in candidates[offset:]),
        )

    def _itercurrent_failures(self) -> Iterator[CurrentFleetFailure]:
        # Stream directly from the canonical current-operation index.  Failure readers
        # may deliberately stop after satisfying a bounded page; building an
        # operations-by-run mirror here would defeat that bound and duplicate durable
        # state merely to project it.
        repository_indexes: dict[str, dict[str, GameTapeRepositoryExecution]] = {}
        for current in self._index.current_fleet_plan_operations.values():
            fleet_run_id = current.fleet_run_id
            detail = self._index.run_details_by_id.get(fleet_run_id)
            if detail is None:
                continue
            repositories = repository_indexes.get(fleet_run_id)
            if repositories is None:
                repositories = {
                    item.repository.repository_id: item for item in detail.game_tape.repositories
                }
                repository_indexes[fleet_run_id] = repositories
            failure = current_failure(
                fleet_run_id=fleet_run_id,
                report=current.report,
                repository=repositories.get(current.report.repository.repository_id),
            )
            if failure is not None:
                yield failure

    def run_events(
        self, fleet_run_id: str, after: int = 0, *, limit: int | None = None
    ) -> FleetRunEventBatch:
        # The journal read cut alone owns event ordering, paging and cursor facts.
        # Never fetch the complete per-Run history and slice it in a CLI/API caller.
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise InvalidRequest("Fleet event cursor must be a nonnegative integer")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100
        ):
            raise InvalidRequest("Fleet event page limit must be between 1 and 100")
        run_events = self._index.events_by_run.get(fleet_run_id)
        detail = self._index.run_details_by_id.get(fleet_run_id)
        state = detail.run if detail is not None else None
        if run_events is None or state is None:
            raise RunNotFound(f"Unknown fleet run: {fleet_run_id}")
        start = bisect_right(run_events, after, key=lambda event: event.sequence)
        end = len(run_events) if limit is None else min(start + limit, len(run_events))
        page = run_events[start:end]
        has_more = end < len(run_events)
        return FleetRunEventBatch(
            run=state,
            events=page,
            # Keep the historical head cursor for an exhausted page. A bounded
            # nonempty page, however, must resume at its actual final event.
            next_sequence=page[-1].sequence if page else run_events[-1].sequence,
            journal_revision=self.revision,
            has_more=has_more,
        )

    def _required_indexed_run_state(self, fleet_run_id: str) -> FleetRunState:
        """Return immutable indexed state for internal scans without copy-on-read cost."""
        detail = self._index.run_details_by_id.get(fleet_run_id)
        if detail is None:
            raise RunNotFound(f"Unknown fleet run: {fleet_run_id}")
        return detail.run


def _validate_failure_page(*, offset: int, limit: int) -> None:
    if offset < 0:
        raise InvalidRequest("Failure page offset must be zero or greater")
    if limit < 1 or limit > 100:
        raise InvalidRequest("Failure page limit must be between 1 and 100")
