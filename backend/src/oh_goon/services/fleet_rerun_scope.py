from __future__ import annotations

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import FleetRepositoryScope, fleet_repository_scope
from oh_goon.core.vocabulary import FleetRunOutcome


def rerun_repository_scope(detail: FleetRunDetail) -> FleetRepositoryScope:
    """Select unresolved parent targets without replaying durably successful siblings."""
    if detail.outcome is FleetRunOutcome.SUCCESS:
        return detail.run.repository_scope
    successful_repository_ids = {
        operation.repository.repository_id
        for operation in detail.operations
        if operation.outcome.state == "successful"
    }
    unresolved_repository_ids = {
        repository.repository_id for repository in detail.run.targets
    } - successful_repository_ids
    if not unresolved_repository_ids:
        raise StateConflict(
            "Fleet rerun has no unresolved repository targets; use recovery/repair rather than "
            "replaying durably successful repository effects"
        )
    return fleet_repository_scope(unresolved_repository_ids)
