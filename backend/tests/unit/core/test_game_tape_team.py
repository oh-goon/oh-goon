from datetime import timedelta
from pathlib import Path

from oh_goon.core.game_tape_observability import project_observability_receipt
from oh_goon.core.game_tape_policy import (
    GAME_TAPE_MAX_CHUNK_CHARS,
    GAME_TAPE_MAX_CHUNKS_PER_RUN,
    GAME_TAPE_TERMINAL_RETENTION_SECONDS,
)
from oh_goon.core.game_tape_run_facts import project_repository_failure_focus
from oh_goon.core.game_tape_team import project_team_composition
from oh_goon.core.models.game_tape import (
    GameTapeEnvironmentFingerprint,
    GameTapeRepositoryExecution,
    GameTapeStepExecution,
    GameTapeWhy,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus


def _step(repository_id: str, step_id: str, order: int, status: GameTapeStatus, start, finish):
    return GameTapeStepExecution(
        step_execution_id=f"step-{repository_id}-{step_id}",
        step_id=step_id,
        kind="command",
        order=order,
        status=status,
        started_at=start,
        finished_at=finish,
        why=GameTapeWhy(
            declaration="goon.yml:1",
            reached=True,
            explanation="Execution reached this declared step in plan order.",
        ),
    )


def _repository(tmp_path: Path, name: str, status: GameTapeStatus, start, finish, steps=()):
    repository = RepositoryIdentity(path=tmp_path / name)
    return GameTapeRepositoryExecution(
        repository_execution_id=f"repo-exec-{name}",
        repository=repository,
        status=status,
        started_at=start,
        finished_at=finish,
        fingerprint=GameTapeEnvironmentFingerprint(),
        steps=steps,
    )


def test_team_composition_reports_observed_overlap_and_first_failure(tmp_path: Path) -> None:
    now = utc_now()
    repo_a = _repository(
        tmp_path,
        "repo-a",
        GameTapeStatus.PASSED,
        now,
        now + timedelta(seconds=6),
        (_step("a", "test", 1, GameTapeStatus.PASSED, now, now + timedelta(seconds=5)),),
    )
    repo_b = _repository(
        tmp_path,
        "repo-b",
        GameTapeStatus.FAILED,
        now + timedelta(seconds=2),
        now + timedelta(seconds=8),
        (
            _step(
                "b",
                "build",
                1,
                GameTapeStatus.FAILED,
                now + timedelta(seconds=3),
                now + timedelta(seconds=4),
            ),
        ),
    )
    repo_c = _repository(
        tmp_path,
        "repo-c",
        GameTapeStatus.PASSED,
        now + timedelta(seconds=8),
        now + timedelta(seconds=10),
    )

    repositories = (repo_a, repo_b, repo_c)
    team = project_team_composition(repositories, project_repository_failure_focus(repositories))

    assert team.repository_count == 3
    assert team.timed_repository_count == 3
    assert team.duration_ms == 10_000
    assert team.observed_peak_concurrency == 2
    assert team.overlap_observed is True
    assert team.overlapping_repository_execution_ids == (
        "repo-exec-repo-a",
        "repo-exec-repo-b",
    )
    source_facts = team.model_dump(mode="json", exclude_computed_fields=True)
    assert "duration_ms" not in source_facts
    assert "overlap_observed" not in source_facts
    assert [(item.status, item.count) for item in team.status_counts] == [
        (GameTapeStatus.PASSED, 2),
        (GameTapeStatus.FAILED, 1),
    ]
    assert team.first_failure is not None
    assert team.first_failure.repository_name == "repo-b"
    assert team.first_failure.step_id == "build"
    assert team.first_failure.occurred_at == now + timedelta(seconds=4)


def test_team_composition_treats_touching_repository_windows_as_non_overlapping(
    tmp_path: Path,
) -> None:
    now = utc_now()
    repo_a = _repository(
        tmp_path,
        "repo-a",
        GameTapeStatus.PASSED,
        now,
        now + timedelta(seconds=2),
    )
    repo_b = _repository(
        tmp_path,
        "repo-b",
        GameTapeStatus.INTERRUPTED,
        now + timedelta(seconds=2),
        now + timedelta(seconds=4),
    )

    repositories = (repo_a, repo_b)
    team = project_team_composition(repositories, project_repository_failure_focus(repositories))

    assert team.observed_peak_concurrency == 1
    assert team.overlap_observed is False
    assert team.overlapping_repository_execution_ids == ()
    assert [(item.status, item.count) for item in team.status_counts] == [
        (GameTapeStatus.PASSED, 1),
        (GameTapeStatus.INTERRUPTED, 1),
    ]


def test_observability_receipt_uses_the_runtime_live_output_policy() -> None:
    receipt = project_observability_receipt()

    assert receipt.raw_output_max_chunks == GAME_TAPE_MAX_CHUNKS_PER_RUN
    assert receipt.raw_output_max_chunk_chars == GAME_TAPE_MAX_CHUNK_CHARS
    assert receipt.terminal_handoff_seconds == GAME_TAPE_TERMINAL_RETENTION_SECONDS
    assert receipt.persistent_raw_output_store is False
    assert receipt.persistent_search_index is False
    assert receipt.ui_execution_authority is False
    assert receipt.execution_order_authority == "resolved_plan"
    assert "output_fingerprint" in receipt.durable_evidence
    assert receipt.transient_evidence == ("raw_stdout", "raw_stderr")


def test_team_composition_does_not_invent_first_failure_when_competing_timing_is_missing(
    tmp_path: Path,
) -> None:
    now = utc_now()
    timed = _repository(
        tmp_path,
        "timed-failure",
        GameTapeStatus.FAILED,
        now,
        now + timedelta(seconds=3),
        (
            _step(
                "timed",
                "test",
                1,
                GameTapeStatus.FAILED,
                now + timedelta(seconds=1),
                now + timedelta(seconds=2),
            ),
        ),
    )
    unknown_identity = RepositoryIdentity(path=tmp_path / "unknown-failure")
    unknown = GameTapeRepositoryExecution(
        repository_execution_id="repo-exec-unknown",
        repository=unknown_identity,
        status=GameTapeStatus.FAILED,
        fingerprint=GameTapeEnvironmentFingerprint(),
    )

    repositories = (timed, unknown)
    team = project_team_composition(repositories, project_repository_failure_focus(repositories))

    assert team.first_failure is None
