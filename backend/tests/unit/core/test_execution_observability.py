from datetime import datetime, timedelta, timezone

import pytest

from oh_goon.core.execution_observability import (
    RepositoryRunObservation,
    project_execution_timeline,
)
from oh_goon.core.vocabulary import GameTapeStatus

UTC = timezone.utc


def obs(
    repo: int,
    when: datetime,
    status: GameTapeStatus = GameTapeStatus.PASSED,
    *,
    plan_id: str = "plan-a",
    run: int = 0,
) -> RepositoryRunObservation:
    return RepositoryRunObservation(
        run_id=f"run-{run}-{repo}",
        started_at=when,
        run_status=status,
        repository_id=f"repo-{repo:05d}",
        repository_name=f"Repository {repo:05d}",
        repository_status=status,
        plan_id=plan_id,
        plan_name="Plan A",
        duration_seconds=1.0,
    )


def test_timeline_preserves_canonical_status_counts_without_cell_status() -> None:
    start = datetime(2026, 8, 25, tzinfo=UTC)
    result = project_execution_timeline(
        (
            obs(1, start + timedelta(hours=1), GameTapeStatus.PASSED, run=1),
            obs(1, start + timedelta(hours=2), GameTapeStatus.FAILED, run=2),
            obs(1, start + timedelta(days=1, hours=1), GameTapeStatus.RUNNING, run=3),
        ),
        journal_revision=9,
        from_at=start,
        to_at=start + timedelta(days=3),
        bucket="day",
    )

    assert result.total_repositories == 1
    assert len(result.items[0].buckets) == 2
    first = result.items[0].buckets[0]
    assert first.run_count == 2
    assert [(item.status, item.count) for item in first.statuses] == [
        (GameTapeStatus.PASSED, 1),
        (GameTapeStatus.FAILED, 1),
    ]
    assert result.items[0].buckets[1].statuses[0].status is GameTapeStatus.RUNNING


def test_timeline_empty_bucket_is_absence_not_a_status() -> None:
    start = datetime(2026, 8, 25, tzinfo=UTC)
    result = project_execution_timeline(
        (obs(1, start + timedelta(hours=1)), obs(1, start + timedelta(days=2, hours=1))),
        journal_revision=2,
        from_at=start,
        to_at=start + timedelta(days=4),
        bucket="day",
    )

    starts = [item.start_at for item in result.items[0].buckets]
    assert starts == [start, start + timedelta(days=2)]
    assert all(
        status.status is not GameTapeStatus.NOT_RUN
        for bucket in result.items[0].buckets
        for status in bucket.statuses
    )


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [
        ("day", datetime(2026, 8, 26, tzinfo=UTC)),
        ("week", datetime(2026, 8, 24, tzinfo=UTC)),
        ("month", datetime(2026, 8, 1, tzinfo=UTC)),
    ],
)
def test_timeline_uses_one_bucket_projection(bucket: str, expected: datetime) -> None:
    when = datetime(2026, 8, 26, 17, 30, tzinfo=UTC)
    result = project_execution_timeline(
        (obs(1, when),),
        journal_revision=1,
        from_at=datetime(2026, 8, 1, tzinfo=UTC),
        to_at=datetime(2026, 9, 2, tzinfo=UTC),
        bucket=bucket,  # type: ignore[arg-type]
    )
    assert result.items[0].buckets[0].start_at == expected


def test_timeline_filters_plan_and_repository_before_paging() -> None:
    start = datetime(2026, 8, 25, tzinfo=UTC)
    observations = (
        obs(1, start, plan_id="plan-a"),
        obs(2, start, plan_id="plan-b"),
        obs(12, start, plan_id="plan-a"),
    )
    result = project_execution_timeline(
        observations,
        journal_revision=3,
        from_at=start - timedelta(minutes=1),
        to_at=start + timedelta(days=1),
        bucket="day",
        plan_query="plan-a",
        repository_query="00012",
    )
    assert result.total_repositories == 1
    assert result.items[0].repository_id == "repo-00012"


def test_timeline_is_bounded_with_5000_repositories() -> None:
    start = datetime(2026, 8, 25, tzinfo=UTC)
    observations = tuple(obs(repo, start, run=repo) for repo in range(5000))

    first = project_execution_timeline(
        observations,
        journal_revision=5000,
        from_at=start - timedelta(minutes=1),
        to_at=start + timedelta(days=1),
        bucket="day",
        limit=100,
    )
    last = project_execution_timeline(
        observations,
        journal_revision=5000,
        from_at=start - timedelta(minutes=1),
        to_at=start + timedelta(days=1),
        bucket="day",
        offset=4900,
        limit=100,
    )

    assert first.total_repositories == 5000
    assert len(first.items) == 100
    assert first.items[0].repository_id == "repo-00000"
    assert last.total_repositories == 5000
    assert len(last.items) == 100
    assert last.items[-1].repository_id == "repo-04999"
