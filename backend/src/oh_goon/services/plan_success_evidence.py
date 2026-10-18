from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence

if TYPE_CHECKING:
    from oh_goon.application import ApplicationRuntime


def has_exact_plan_success(
    runtime: ApplicationRuntime,
    *,
    plan: Path,
    repository_id: str,
    source_root: Path,
    lock_path: Path,
    fleet_run_id: str | None = None,
) -> bool:
    """Require source/lock/admission success, and an exact Run for deployment APP.

    Legacy package qualification may check any matching APP success. The
    deployment-owned APP path must provide the *enrolled* Run ID: an earlier
    successful Run for identical source is never proof that this Run finished.
    """

    if fleet_run_id is not None and (
        type(fleet_run_id) is not str
        or not fleet_run_id.strip()
        or fleet_run_id != fleet_run_id.strip()
    ):
        return False

    expected = runtime.plans.prepare_execution_target(plan, lock_path=lock_path)
    repository = RepositoryIdentity(path=source_root)
    if repository.repository_id != repository_id:
        return False
    current_admission = repository_plan_admission_evidence(repository, expected.plan)
    before_revision = None
    while True:
        page = runtime.runs.history(
            limit=100,
            before_revision=before_revision,
            outcome=FleetRunOutcome.SUCCESS,
            repository_id=repository_id,
        )
        for item in page.items:
            if fleet_run_id is not None and item.run.fleet_run_id != fleet_run_id:
                continue
            target = item.run.target
            if not (
                isinstance(target, FleetPlanTarget)
                and target.identity.digest == expected.identity.digest
                and target.source == expected.source
            ):
                continue
            detail = runtime.runs.get(item.run.fleet_run_id)
            if fleet_run_id is not None:
                # The history filter is only a selection of SUCCESS rows, not
                # the final persisted APP receipt. Recheck the detail returned
                # by the original Run owner: a substituted Run, changed outcome,
                # or changed target must never validate this enrolled Run.
                detail_target = detail.run.target
                if not (
                    detail.run.fleet_run_id == fleet_run_id
                    and detail.outcome is FleetRunOutcome.SUCCESS
                    and isinstance(detail_target, FleetPlanTarget)
                    and detail_target.identity.digest == expected.identity.digest
                    and detail_target.source == expected.source
                ):
                    continue
            if current_admission in detail.repository_admissions:
                return True
        if page.next_before_revision is None:
            return False
        before_revision = page.next_before_revision
