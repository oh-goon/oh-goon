from __future__ import annotations

from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.scope_mutation_guard import DurableScopeMutationGuard
from oh_goon.runtime.state import LocalState


def local_state(database: LocalDatabase) -> LocalState:
    """Build an isolated LocalState graph for tests with explicit shared owners."""
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    return LocalState(
        database,
        journal=journal,
        mutation_guard=DurableScopeMutationGuard(execution, journal),
        league_folders=LeagueFolderStore(database.path.with_name("league.yml")),
    )
