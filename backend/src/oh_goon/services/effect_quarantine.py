from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict

from oh_goon.core.errors import RepositoryEffectQuarantined
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_attempt import (
    IndeterminateRepositoryEffectAttempt,
    OutcomeUnknownRepositoryEffectAttempt,
    PartiallyAppliedRepositoryEffectAttempt,
    RepositoryEffectAttempt,
)
from oh_goon.core.models.effect_origin import RepositoryEffectOrigin
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.recovery import (
    IndeterminateRepositoryEffectQuarantine,
    OutcomeUnknownRepositoryEffectQuarantine,
    PartiallyAppliedRepositoryEffectQuarantine,
    RepositoryEffectQuarantine,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import CommandSafety


class _QuarantineFields(TypedDict):
    operation_id: str
    repository: RepositoryIdentity
    capability: CapabilitySnapshot
    baseline: RepositoryStateFingerprint
    origin: RepositoryEffectOrigin
    record_version: int


def effect_quarantines(
    attempts: Iterable[RepositoryEffectAttempt],
) -> tuple[RepositoryEffectQuarantine, ...]:
    """Project only structurally quarantined external-effect states."""
    items = [
        quarantine
        for attempt in attempts
        if attempt.quarantines_repository and (quarantine := _quarantine_for(attempt)) is not None
    ]
    return tuple(sorted(items, key=lambda item: (str(item.repository.path), item.operation_id)))


def require_effects_allowed_for_repositories(
    safety: CommandSafety,
    repository_ids: Iterable[str],
    quarantines: Iterable[RepositoryEffectQuarantine],
) -> None:
    if safety is CommandSafety.READ_ONLY:
        return
    requested = set(repository_ids)
    blocked = [item for item in quarantines if item.repository.repository_id in requested]
    if not blocked:
        return
    details = ", ".join(
        f"{item.repository.path} ({item.origin.kind.value}:{item.operation_id})" for item in blocked
    )
    raise RepositoryEffectQuarantined(
        "Repository effect outcome requires recovery inspection before another mutation: " + details
    )


def _quarantine_for(
    attempt: RepositoryEffectAttempt,
) -> RepositoryEffectQuarantine | None:
    common: _QuarantineFields = {
        "operation_id": attempt.operation_id,
        "repository": attempt.repository,
        "capability": attempt.capability,
        "baseline": attempt.baseline,
        "origin": attempt.origin,
        "record_version": attempt.record_version,
    }
    if isinstance(attempt, OutcomeUnknownRepositoryEffectAttempt):
        return OutcomeUnknownRepositoryEffectQuarantine(
            status=attempt.status,
            note=attempt.note,
            **common,
        )
    if isinstance(attempt, PartiallyAppliedRepositoryEffectAttempt):
        return PartiallyAppliedRepositoryEffectQuarantine(
            status=attempt.status,
            reconciliation=attempt.reconciliation,
            **common,
        )
    if isinstance(attempt, IndeterminateRepositoryEffectAttempt):
        return IndeterminateRepositoryEffectQuarantine(
            status=attempt.status,
            reconciliation=attempt.reconciliation,
            **common,
        )
    return None
