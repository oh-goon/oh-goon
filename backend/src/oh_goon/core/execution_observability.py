from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from oh_goon.core.models.execution_observability import (
    ExecutionBucketWindow,
    ExecutionStatusCount,
    ExecutionTimeBucket,
    FleetExecutionTimeline,
    RepositoryExecutionBucket,
    RepositoryExecutionTimelineRow,
)
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.vocabulary import GameTapeStatus


@dataclass(frozen=True, slots=True)
class RepositoryRunObservation:
    """Canonical read-only repository execution fact projected from FleetRunDetail."""

    run_id: str
    started_at: datetime
    run_status: GameTapeStatus
    repository_id: str
    repository_name: str
    repository_status: GameTapeStatus
    plan_id: str | None
    plan_name: str | None
    duration_seconds: float | None = None


def project_repository_run_observations(
    details: tuple[FleetRunDetail, ...],
) -> tuple[RepositoryRunObservation, ...]:
    observations: list[RepositoryRunObservation] = []
    for detail in details:
        tape = detail.game_tape
        run_started_at = tape.receipt.started_at
        for repository in tape.repositories:
            started_at = repository.started_at or run_started_at
            if started_at is None:
                continue
            observations.append(
                RepositoryRunObservation(
                    run_id=tape.fleet_run_id,
                    started_at=started_at,
                    run_status=tape.status,
                    repository_id=repository.repository.repository_id,
                    repository_name=repository.repository.name,
                    repository_status=repository.status,
                    plan_id=tape.plan_id,
                    plan_name=tape.plan_name,
                    duration_seconds=(
                        repository.duration_ms / 1000
                        if repository.duration_ms is not None
                        else None
                    ),
                )
            )
    return tuple(observations)


def project_execution_timeline(
    observations: tuple[RepositoryRunObservation, ...],
    *,
    journal_revision: int,
    from_at: datetime,
    to_at: datetime,
    bucket: ExecutionTimeBucket,
    plan_query: str | None = None,
    repository_query: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> FleetExecutionTimeline:
    if to_at <= from_at:
        raise ValueError("Execution observation to_at must be after from_at")
    normalized_query = repository_query.strip().casefold() if repository_query else ""
    normalized_plan = plan_query.strip().casefold() if plan_query else ""
    grouped: dict[str, dict[datetime, list[RepositoryRunObservation]]] = defaultdict(
        lambda: defaultdict(list)
    )
    repository_names: dict[str, str] = {}
    for observation in observations:
        if observation.started_at < from_at or observation.started_at >= to_at:
            continue
        if normalized_plan and normalized_plan not in (
            f"{observation.plan_name or ''} {observation.plan_id or ''}".casefold()
        ):
            continue
        if normalized_query and normalized_query not in (
            f"{observation.repository_name} {observation.repository_id}".casefold()
        ):
            continue
        repository_names[observation.repository_id] = observation.repository_name
        bucket_start = _bucket_start(observation.started_at, bucket)
        grouped[observation.repository_id][bucket_start].append(observation)

    repository_ids = sorted(
        repository_names,
        key=lambda repository_id: (repository_names[repository_id].casefold(), repository_id),
    )
    page_ids = repository_ids[offset : offset + limit]
    rows: list[RepositoryExecutionTimelineRow] = []
    for repository_id in page_ids:
        buckets = []
        repository_buckets = grouped[repository_id]
        for start in sorted(repository_buckets):
            items = repository_buckets[start]
            counts = Counter(item.repository_status for item in items)
            buckets.append(
                RepositoryExecutionBucket(
                    start_at=start,
                    end_at=_bucket_end(start, bucket),
                    run_count=len(items),
                    statuses=tuple(
                        ExecutionStatusCount(status=status, count=counts[status])
                        for status in GameTapeStatus
                        if counts[status]
                    ),
                )
            )
        rows.append(
            RepositoryExecutionTimelineRow(
                repository_id=repository_id,
                repository_name=repository_names[repository_id],
                buckets=tuple(buckets),
            )
        )
    return FleetExecutionTimeline(
        journal_revision=journal_revision,
        from_at=from_at,
        to_at=to_at,
        bucket=bucket,
        total_repositories=len(repository_ids),
        windows=_bucket_windows(from_at, to_at, bucket),
        offset=offset,
        limit=limit,
        items=tuple(rows),
    )


def _bucket_start(value: datetime, bucket: ExecutionTimeBucket) -> datetime:
    value = value.astimezone(timezone.utc)
    if bucket == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        day = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _bucket_end(start: datetime, bucket: ExecutionTimeBucket) -> datetime:
    if bucket == "day":
        return start + timedelta(days=1)
    if bucket == "week":
        return start + timedelta(days=7)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def _bucket_windows(
    from_at: datetime, to_at: datetime, bucket: ExecutionTimeBucket
) -> tuple[ExecutionBucketWindow, ...]:
    windows: list[ExecutionBucketWindow] = []
    start = _bucket_start(from_at, bucket)
    while start < to_at:
        end = _bucket_end(start, bucket)
        windows.append(ExecutionBucketWindow(start_at=start, end_at=end))
        start = end
    return tuple(windows)
