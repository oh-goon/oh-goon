from __future__ import annotations

from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from oh_goon.services.capability_executor import CapabilityExecutor
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.reconciliation import RepositoryReconciler
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution
from oh_goon.services.repository_effect_fence import RepositoryEffectFence
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement


def effect_execution(
    database: LocalDatabase,
    executor: CapabilityExecutor,
    journal: Journal,
    attempts: RepositoryEffectAttemptStore,
    git: GitService,
    *,
    faults=None,
    effect_fence: RepositoryEffectFence | None = None,
) -> RepositoryEffectExecution:
    """Test-only convenience around explicit production effect ownership."""
    return RepositoryEffectExecution(
        executor,
        journal,
        attempts,
        git,
        faults=faults,
        effect_fence=effect_fence or RepositoryEffectFence(git),
        settlement=RepositoryEffectSettlement(database, journal, attempts),
    )


def effect_recovery(
    database: LocalDatabase,
    attempts: RepositoryEffectAttemptStore,
    journal: Journal,
    execution: ExecutionCoordinator,
    git: GitService | None = None,
    *,
    reconciler: RepositoryReconciler | None = None,
    settlement: RepositoryEffectSettlement | None = None,
) -> RepositoryEffectRecovery:
    """Test-only convenience around explicit production recovery ownership."""
    git = git or GitService()
    return RepositoryEffectRecovery(
        database,
        attempts,
        journal,
        execution,
        reconciler or RepositoryReconciler(journal, git),
        settlement or RepositoryEffectSettlement(database, journal, attempts),
    )
