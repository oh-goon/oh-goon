from __future__ import annotations

from oh_goon.core.models.game_tape import GameTapeRepositoryExecution, GameTapeStepExecution
from oh_goon.core.models.game_tape_reproduction_context import (
    GameTapeEvidenceMatch,
    GameTapeReproductionContextComparison,
    GameTapeReproductionContextSummary,
)

_CONTEXT_DIMENSIONS = 5


def compare_reproduction_context(
    current_repository: GameTapeRepositoryExecution,
    current_step: GameTapeStepExecution,
    historical_repository: GameTapeRepositoryExecution,
    historical_step: GameTapeStepExecution,
) -> GameTapeReproductionContextComparison:
    """Compare only recorded immutable context identities; missing evidence stays unknown."""
    current = current_repository.fingerprint
    historical = historical_repository.fingerprint
    values = (
        _match(current.base_workspace_sha256, historical.base_workspace_sha256),
        _match(current.runtime_root_sha256, historical.runtime_root_sha256),
        _match(current.host_toolchain_id, historical.host_toolchain_id),
        _match(_isolation(current), _isolation(historical)),
        _match(_command(current_step), _command(historical_step)),
    )
    return GameTapeReproductionContextComparison(
        workspace=values[0],
        runtime=values[1],
        toolchain=values[2],
        isolation=values[3],
        command=values[4],
        summary=_summary(values),
    )


def _summary(values: tuple[GameTapeEvidenceMatch, ...]) -> GameTapeReproductionContextSummary:
    matching = values.count("MATCH")
    different = values.count("DIFFERENT")
    known = matching + different
    if different:
        classification = "CHANGED_CONTEXT"
    elif known == _CONTEXT_DIMENSIONS:
        classification = "SAME_CONTEXT"
    else:
        classification = "INCOMPLETE_CONTEXT"
    return GameTapeReproductionContextSummary(
        classification=classification,
        known_dimensions=known,
        matching_dimensions=matching,
        different_dimensions=different,
        total_dimensions=_CONTEXT_DIMENSIONS,
    )


def _match(left: object | None, right: object | None) -> GameTapeEvidenceMatch:
    if left is None or right is None:
        return "UNKNOWN"
    return "MATCH" if left == right else "DIFFERENT"


def _isolation(fingerprint) -> tuple[str, str, str] | None:
    if (
        fingerprint.isolation_backend_id is None
        or fingerprint.isolation_configuration_id is None
        or fingerprint.isolation_kernel_boundary is None
    ):
        return None
    return (
        fingerprint.isolation_backend_id,
        fingerprint.isolation_configuration_id,
        fingerprint.isolation_kernel_boundary,
    )


def _command(step: GameTapeStepExecution) -> tuple[str, ...] | None:
    return step.command if step.command else None
