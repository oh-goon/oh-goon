from __future__ import annotations
from oh_goon.core.capability_registry import resolve_bound_capability_intent
from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import FleetRepositoryScope, FleetRunAdmissionIntent, fleet_readmission_parent, fleet_rerun_parent
from oh_goon.core.run_identity import require_fleet_run_binding
from oh_goon.core.vocabulary import FleetRunSource, FleetRunStatus
from oh_goon.runtime.journal_view import JournalReadView

def _require_valid_readmission(
    view: JournalReadView,
    fleet_run_id: str,
    intent: FleetRunAdmissionIntent,
    repository_scope: FleetRepositoryScope,
    targets: list[RepositoryIdentity],
) -> None:
    parent_id = fleet_readmission_parent(intent.execution)
    if parent_id is None:
        return
    if parent_id == fleet_run_id:
        raise JournalConflict("Fleet re-admission cannot reuse its parent run identity")
    parent = view.run_detail(parent_id)
    if parent is None:
        raise JournalConflict(f"Fleet re-admission parent does not exist: {parent_id}")
    failure = getattr(parent.run, "failure", None)
    if (
        parent.run.status is not FleetRunStatus.FAILED
        or failure is None
        or failure.code != "EXECUTOR_PLACEMENT_LOST"
    ):
        raise JournalConflict(
            "Fleet re-admission parent must be a failed EXECUTOR_PLACEMENT_LOST run"
        )
    if parent.run.progress.completed > 0:
        raise JournalConflict(
            "Fleet re-admission cannot replay a parent with durably completed repository effects"
        )
    existing_child = view.readmission_child(parent_id)
    if existing_child is not None:
        raise JournalConflict(
            f"Fleet re-admission parent {parent_id} already has child "
            f"{existing_child.run.fleet_run_id}"
        )
    require_fleet_run_binding(
        parent.run,
        source=intent.source,
        capability=intent.capability,
        target=intent.target,
        repository_scope=repository_scope,
    )
    if parent.run.targets != tuple(targets):
        raise JournalConflict(
            "Fleet re-admission must preserve the parent captured repository identities exactly"
        )


def _require_valid_rerun(
    view: JournalReadView,
    fleet_run_id: str,
    intent: FleetRunAdmissionIntent,
    repository_scope: FleetRepositoryScope,
    targets: list[RepositoryIdentity],
) -> None:
    parent_id = fleet_rerun_parent(intent.execution)
    if parent_id is None:
        return
    if parent_id == fleet_run_id:
        raise JournalConflict("Fleet rerun cannot reuse its parent run identity")
    if intent.source is not FleetRunSource.MANUAL:
        raise JournalConflict("Fleet Run-history rerun must have MANUAL execution source")
    parent = view.run_detail(parent_id)
    if parent is None:
        raise JournalConflict(f"Fleet rerun parent does not exist: {parent_id}")
    if not parent.run.status.terminal:
        raise JournalConflict("Fleet rerun parent must be terminal")
    current_intent = resolve_bound_capability_intent(intent.capability, intent.target)
    if not current_intent.definition.rerun_supported:
        raise JournalConflict(
            f"{parent.run.capability.action.value} is not repeatable through Run history"
        )
    require_fleet_run_binding(
        parent.run,
        source=parent.run.source,
        capability=intent.capability,
        target=intent.target,
        repository_scope=parent.run.repository_scope,
    )
    selected_targets = tuple(targets)
    if not selected_targets:
        raise JournalConflict("Fleet rerun must retain at least one parent repository target")
    parent_targets = {target.repository_id: target for target in parent.run.targets}
    if any(parent_targets.get(target.repository_id) != target for target in selected_targets):
        raise JournalConflict(
            "Fleet rerun may only select exact repository identities captured by its parent"
        )


