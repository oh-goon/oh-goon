from __future__ import annotations

from oh_goon.core.models.fleet import (
    ExcludedFleetRepository,
    FleetRepository,
    IncludedFleetRepository,
)
from oh_goon.core.models.repository import RepositoryCatalogEntry
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary import FleetScope, OperationAction
from oh_goon.runtime.journal_view import JournalReadView

_FLEET_STATE_ACTIONS = (
    OperationAction.INIT,
    OperationAction.PLAN,
    OperationAction.UPDATE,
    OperationAction.VERIFY,
)


def repository_view(entry: RepositoryCatalogEntry, journal: JournalReadView) -> FleetRepository:
    """Project one catalog entry from one pinned journal read cut."""
    if entry.scope is FleetScope.EXCLUDED:
        return ExcludedFleetRepository(
            scope=FleetScope.EXCLUDED,
            repository=entry.repository,
            repository_instance_id=repository_subject_instance_id(entry.repository.path),
            exclusion_reason=entry.exclusion_reason,
            excluded_at=entry.excluded_at,
        )
    current = {
        action: value
        for action in _FLEET_STATE_ACTIONS
        if (value := journal.current_family(entry.repository.path, action)) is not None
    }
    return IncludedFleetRepository(
        scope=FleetScope.INCLUDED,
        repository=entry.repository,
        repository_instance_id=repository_subject_instance_id(entry.repository.path),
        init=current.get(OperationAction.INIT),
        plan=current.get(OperationAction.PLAN),
        update=current.get(OperationAction.UPDATE),
        verify=current.get(OperationAction.VERIFY),
    )
