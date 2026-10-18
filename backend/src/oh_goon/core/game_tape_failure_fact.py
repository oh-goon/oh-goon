from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from oh_goon.core.game_tape_reproduction_context import compare_reproduction_context
from oh_goon.core.models.game_tape import (
    GameTapeFailureFingerprint,
    GameTapeRepositoryExecution,
    GameTapeStepExecution,
)
from oh_goon.core.models.game_tape_failure_relation import GameTapeFailureSignatureRelation
from oh_goon.core.models.game_tape_reproduction_context import (
    GameTapeReproductionContextComparison,
)
from oh_goon.core.vocabulary import GameTapeStatus


@dataclass(frozen=True, slots=True)
class GameTapeComparableStepFact:
    """One normalized comparable step fact interpreted relative to the selected failure."""

    run_id: str
    accepted_at: datetime
    repository_id: str
    repository_name: str
    step_id: str
    status: GameTapeStatus
    duration_ms: int | None
    attempt_count: int
    failure: GameTapeFailureFingerprint | None
    signature_relation: GameTapeFailureSignatureRelation
    selected_context: GameTapeReproductionContextComparison
    source_repository: GameTapeRepositoryExecution
    source_step: GameTapeStepExecution


@dataclass(frozen=True, slots=True)
class GameTapeComparableTransitionFact:
    """One adjacent comparable-run transition interpreted exactly once."""

    from_run_id: str
    to_run_id: str
    from_status: GameTapeStatus
    to_status: GameTapeStatus
    status_changed: bool
    from_signature_relation: GameTapeFailureSignatureRelation
    to_signature_relation: GameTapeFailureSignatureRelation
    signature_relation_changed: bool
    duration_delta_ms: int | None
    context: GameTapeReproductionContextComparison


def compare_adjacent_step_facts(
    previous: GameTapeComparableStepFact,
    current: GameTapeComparableStepFact,
) -> GameTapeComparableTransitionFact:
    """Compare one chronological neighbor pair; never infer why the outcome changed."""
    return GameTapeComparableTransitionFact(
        from_run_id=previous.run_id,
        to_run_id=current.run_id,
        from_status=previous.status,
        to_status=current.status,
        status_changed=previous.status != current.status,
        from_signature_relation=previous.signature_relation,
        to_signature_relation=current.signature_relation,
        signature_relation_changed=previous.signature_relation != current.signature_relation,
        duration_delta_ms=_delta(previous.duration_ms, current.duration_ms),
        context=compare_reproduction_context(
            previous.source_repository,
            previous.source_step,
            current.source_repository,
            current.source_step,
        ),
    )


def _delta(previous: int | None, current: int | None) -> int | None:
    if previous is None or current is None:
        return None
    return current - previous
