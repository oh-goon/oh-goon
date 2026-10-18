from __future__ import annotations

from oh_goon.core.models.game_tape import GameTape
from oh_goon.core.models.game_tape_live_output import (
    GameTapeLiveFailureAnchor,
    GameTapeLiveOutputChunk,
)


def project_live_failure_anchors(
    tape: GameTape,
    retained: tuple[GameTapeLiveOutputChunk, ...],
) -> tuple[GameTapeLiveFailureAnchor, ...]:
    """Bind durable failure evidence to one exact retained live-output cut."""

    anchors: list[GameTapeLiveFailureAnchor] = []
    for repository in tape.repositories:
        repository_id = repository.repository.repository_id
        for step in repository.steps:
            failure = step.failure
            if (
                failure is None
                or failure.attempt is None
                or failure.evidence_stream is None
                or not failure.evidence_line_count
            ):
                continue
            line = failure.evidence_line_count
            sequence = _retained_sequence(
                retained,
                repository_id,
                step.step_id,
                failure.attempt,
                failure.evidence_stream,
                line,
            )
            anchors.append(
                GameTapeLiveFailureAnchor(
                    step_execution_id=step.step_execution_id,
                    repository_id=repository_id,
                    step_id=step.step_id,
                    attempt=failure.attempt,
                    stream=failure.evidence_stream,
                    line=line,
                    sequence=sequence,
                    retained=sequence is not None,
                )
            )
    return tuple(anchors)


def _retained_sequence(
    retained: tuple[GameTapeLiveOutputChunk, ...],
    repository_id: str,
    step_id: str,
    attempt: int,
    stream: str,
    line: int,
) -> int | None:
    return max(
        (
            item.sequence
            for item in retained
            if item.repository_id == repository_id
            and item.step_id == step_id
            and item.attempt == attempt
            and item.stream == stream
            and item.line_start <= line <= item.line_end
        ),
        default=None,
    )
