from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import cast

from oh_goon.core.models.game_tape import GameTapeIssueFocus, GameTapeRepositoryExecution
from oh_goon.core.models.game_tape_team import (
    GameTapeRepositoryStatusCount,
    GameTapeTeamComposition,
    GameTapeTeamFailureFocus,
)
from oh_goon.core.vocabulary import GameTapeStatus


def project_team_composition(
    repositories: tuple[GameTapeRepositoryExecution, ...],
    repository_failures: tuple[GameTapeIssueFocus, ...],
) -> GameTapeTeamComposition:
    timed = tuple(
        repository
        for repository in repositories
        if repository.started_at is not None
        and repository.finished_at is not None
        and repository.finished_at >= repository.started_at
    )
    started_at = min((cast(datetime, item.started_at) for item in timed), default=None)
    finished_at = max((cast(datetime, item.finished_at) for item in timed), default=None)
    overlap_ids = _overlap_ids(timed)
    counts = Counter(item.status for item in repositories)
    return GameTapeTeamComposition(
        repository_count=len(repositories),
        timed_repository_count=len(timed),
        started_at=started_at,
        finished_at=finished_at,
        observed_peak_concurrency=_peak_concurrency(timed),
        overlapping_repository_execution_ids=overlap_ids,
        status_counts=tuple(
            GameTapeRepositoryStatusCount(status=status, count=counts[status])
            for status in GameTapeStatus
            if counts[status] > 0
        ),
        first_failure=_first_failure(repository_failures),
    )


def _peak_concurrency(repositories: tuple[GameTapeRepositoryExecution, ...]) -> int:
    intervals = tuple(
        (item.started_at, item.finished_at)
        for item in repositories
        if item.started_at is not None
        and item.finished_at is not None
        and item.finished_at > item.started_at
    )
    return max(
        (sum(start <= point < finish for start, finish in intervals) for point, _ in intervals),
        default=0,
    )


def _overlap_ids(
    repositories: tuple[GameTapeRepositoryExecution, ...],
) -> tuple[str, ...]:
    overlapping: set[str] = set()
    for index, left in enumerate(repositories):
        for right in repositories[index + 1 :]:
            if _overlap(left, right):
                overlapping.add(left.repository_execution_id)
                overlapping.add(right.repository_execution_id)
    return tuple(
        item.repository_execution_id
        for item in repositories
        if item.repository_execution_id in overlapping
    )


def _overlap(left: GameTapeRepositoryExecution, right: GameTapeRepositoryExecution) -> bool:
    if (
        left.started_at is None
        or left.finished_at is None
        or right.started_at is None
        or right.finished_at is None
    ):
        return False
    return max(left.started_at, right.started_at) < min(left.finished_at, right.finished_at)


def _first_failure(
    repository_failures: tuple[GameTapeIssueFocus, ...],
) -> GameTapeTeamFailureFocus | None:
    if not repository_failures or any(item.occurred_at is None for item in repository_failures):
        return None
    failure = min(
        enumerate(repository_failures),
        key=lambda item: (item[1].occurred_at, item[0]),
    )[1]
    if (
        failure.repository_execution_id is None
        or failure.repository_id is None
        or failure.repository_name is None
    ):
        raise ValueError("Repository failure focus must carry exact repository identity")
    return GameTapeTeamFailureFocus(
        repository_execution_id=failure.repository_execution_id,
        repository_id=failure.repository_id,
        repository_name=failure.repository_name,
        step_execution_id=failure.step_execution_id,
        step_id=failure.step_id,
        occurred_at=failure.occurred_at,
    )
