"""Exact history filters retain original admission ordering and continuation."""

from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import FleetRunTerminalIntent, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.journal import admit_run, terminalize_run
from tests.factories.run_admission import repository_admissions_for


def test_exact_plan_and_repository_scope_page_survives_fresh_journal(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    journal = Journal(LocalDatabase(database))
    first = RepositoryIdentity.model_validate({"name": "first", "path": tmp_path / "first"})
    second = RepositoryIdentity.model_validate({"name": "second", "path": tmp_path / "second"})
    selected = ResolvedFleetPlan.create(
        name="quality",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="check", argv=("true",)),),
    )
    different = ResolvedFleetPlan.create(
        name="quality extended",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="check", argv=("false",)),),
    )
    for run_id, plan, repository in (
        ("selected-old", selected, first),
        ("other-plan", different, first),
        ("selected-new", selected, first),
        ("other-repository", selected, second),
    ):
        target = structural_fleet_plan_target(plan)
        intent = FleetRunAdmissionIntent(
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=repository_admissions_for(target, [repository]),
        )
        admit_run(
            journal,
            run_id,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
        terminalize_run(journal, run_id, FleetRunTerminalIntent.CANCEL)

    # Read in a new owner, not an invocation-local latest pointer or cache.
    restarted = Journal(LocalDatabase(database))
    page = restarted.read_view().run_history(
        limit=1, repository_id=first.repository_id, plan_id=selected.plan_id
    )
    assert [item.run.fleet_run_id for item in page.items] == ["selected-new"]
    assert page.next_before_revision == page.items[0].admitted_revision
    older = restarted.read_view().run_history(
        limit=1,
        before_revision=page.next_before_revision,
        repository_id=first.repository_id,
        plan_id=selected.plan_id,
    )
    assert [item.run.fleet_run_id for item in older.items] == ["selected-old"]
    assert older.next_before_revision is None
    assert restarted.read_view().run_history(plan_id="quality").items == ()


@pytest.mark.parametrize("before_revision,plan_id", [(0, None), (None, " ")])
def test_history_rejects_invalid_scope_or_cursor(
    tmp_path: Path, before_revision: int | None, plan_id: str | None
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "history.db"))
    with pytest.raises(InvalidRequest):
        journal.read_view().run_history(before_revision=before_revision, plan_id=plan_id)
