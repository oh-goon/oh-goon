from pathlib import Path

from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.admitted_execution import admitted_fleet_plan_execution


def _view(root: Path):
    return Journal(LocalDatabase(root / "runtime.db")).read_view()


def test_current_failure_inventory_projects_canonical_failed_execution(tmp_path: Path) -> None:
    execution = admitted_fleet_plan_execution(
        tmp_path,
        returncodes=(7,),
        fleet_run_id="failure-run",
    )

    view = _view(tmp_path)
    inventory = view.failure_inventory()
    groups = view.failure_groups()

    assert inventory.failing_repositories == 1
    assert inventory.failure_groups == 1
    assert groups.total == 1
    group = groups.items[0]
    assert group.plan_id == execution.target.plan.plan_id
    assert group.step_id == "check"
    assert group.affected_repositories == 1
    assert group.signature_count == 1

    repositories = view.failure_repositories(group.group_id)
    assert repositories.total == 1
    assert repositories.items[0].fleet_run_id == "failure-run"
    assert repositories.items[0].repository.repository_id == execution.operation.repository.repository_id
    assert repositories.items[0].signature_id is not None


def test_later_success_for_same_plan_slot_clears_current_failure(tmp_path: Path) -> None:
    admitted_fleet_plan_execution(tmp_path, returncodes=(5,), fleet_run_id="failure-run")
    admitted_fleet_plan_execution(tmp_path, returncodes=(0,), fleet_run_id="success-run")

    inventory = _view(tmp_path).failure_inventory()

    assert inventory.failing_repositories == 0
    assert inventory.failure_groups == 0



def test_success_for_different_plan_slot_does_not_hide_current_failure(tmp_path: Path) -> None:
    failed = admitted_fleet_plan_execution(
        tmp_path, returncodes=(5,), fleet_run_id="failure-run"
    )
    different_plan = """version: 1
steps:
  - id: other-check
    run: "true"
"""
    successful = admitted_fleet_plan_execution(
        tmp_path,
        plan_text=different_plan,
        returncodes=(0,),
        fleet_run_id="different-success-run",
    )

    assert successful.target.plan.plan_id != failed.target.plan.plan_id
    inventory = _view(tmp_path).failure_inventory()

    assert inventory.failing_repositories == 1
    assert inventory.failure_groups == 1


def test_failure_group_and_repository_reads_are_searchable(tmp_path: Path) -> None:
    execution = admitted_fleet_plan_execution(
        tmp_path,
        returncodes=(9,),
        fleet_run_id="searchable-failure",
    )
    view = _view(tmp_path)
    group = view.failure_groups(query="check").items[0]

    assert view.failure_groups(query="not-present").total == 0
    assert view.failure_repositories(group.group_id, query=execution.operation.repository.name).total == 1
    assert view.failure_repositories(group.group_id, query="not-present").total == 0
    signature = view.failure_repositories(group.group_id).items[0].signature_id
    assert signature is not None
    assert view.failure_repositories(group.group_id, signature_id=signature).total == 1
    assert view.failure_repositories(group.group_id, signature_id="missing-signature").total == 0
