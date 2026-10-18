from __future__ import annotations

from datetime import datetime

from oh_goon.core.execution_observability import project_execution_timeline, project_repository_run_observations
from oh_goon.core.models.execution_observability import ExecutionTimeBucket, FleetExecutionTimeline
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.runtime.journal_index import JournalIndex


def project_bounded_execution_timeline(
    index: JournalIndex, *, journal_revision: int, from_at: datetime, to_at: datetime,
    bucket: ExecutionTimeBucket, plan_query: str | None, repository_query: str | None,
    offset: int, limit: int,
) -> FleetExecutionTimeline:
    run_ids, repository_names = _candidates(
        index, from_at=from_at, to_at=to_at, plan_query=plan_query, repository_query=repository_query
    )
    repository_ids = sorted(
        repository_names,
        key=lambda repository_id: (repository_names[repository_id].casefold(), repository_id),
    )
    page_ids = set(repository_ids[offset : offset + limit])
    details = tuple(
        index.run_details_by_id[run_id]
        for run_id in run_ids
        if any(target.repository_id in page_ids for target in index.run_details_by_id[run_id].run.targets)
    )
    observations = tuple(
        observation for observation in project_repository_run_observations(details)
        if observation.repository_id in page_ids
    )
    page = project_execution_timeline(
        observations, journal_revision=journal_revision, from_at=from_at, to_at=to_at,
        bucket=bucket, plan_query=plan_query, repository_query=repository_query, offset=0, limit=limit,
    )
    return page.model_copy(update={
        "total_repositories": len(repository_ids), "offset": offset, "limit": limit
    })


def _candidates(
    index: JournalIndex, *, from_at: datetime, to_at: datetime,
    plan_query: str | None, repository_query: str | None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    normalized_plan = plan_query.strip().casefold() if plan_query else ""
    normalized_repository = repository_query.strip().casefold() if repository_query else ""
    run_ids: list[str] = []
    repository_names: dict[str, str] = {}
    for _, run_id in index.admitted_revisions:
        run = index.run_details_by_id[run_id].run
        if not _run_matches(run, from_at=from_at, to_at=to_at, plan_query=normalized_plan):
            continue
        matches = _matching_repositories(run, normalized_repository)
        if matches:
            run_ids.append(run_id)
            repository_names.update(matches)
    return tuple(run_ids), repository_names


def _run_matches(run: FleetRunState, *, from_at: datetime, to_at: datetime, plan_query: str) -> bool:
    if run.accepted_at >= to_at or getattr(run, "finished_at", to_at) < from_at:
        return False
    if not plan_query:
        return True
    target = run.target
    return isinstance(target, FleetPlanTarget) and plan_query in f"{target.plan.name} {target.plan.plan_id}".casefold()


def _matching_repositories(run: FleetRunState, repository_query: str) -> dict[str, str]:
    return {
        repository.repository_id: repository.name
        for repository in run.targets
        if not repository_query or repository_query in f"{repository.name} {repository.repository_id}".casefold()
    }
