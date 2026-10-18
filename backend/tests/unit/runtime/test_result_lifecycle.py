from pathlib import Path

from oh_goon.core.result_lifecycle import result_freshness
from oh_goon.core.vocabulary import OperationAction, ResultFreshness, Verdict
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.journal import commit_operation
from tests.factories.operation_reports import make_operation_report


def report(repository: Path, action: OperationAction, verdict: Verdict, operation_id: str):
    from oh_goon.core.models.explanation import ReasonCode

    return make_operation_report(
        repository,
        action,
        verdict=verdict,
        operation_id=operation_id,
        fleet_run_id=None,
        reason_code=(
            ReasonCode.DEPENDENCIES_UPDATED if verdict is Verdict.UPDATED else ReasonCode.READY
        ),
    )


def test_journal_retains_history_and_derives_current_freshness(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    repository.mkdir()
    journal = Journal(LocalDatabase(tmp_path / "state.db"))

    first_init = report(repository, OperationAction.INIT, Verdict.READY, "init-1")
    update = report(repository, OperationAction.UPDATE, Verdict.UPDATED, "update-1")
    second_init = report(repository, OperationAction.INIT, Verdict.READY, "init-2")

    commit_operation(journal, first_init)
    current_init = journal.current_capability(repository, "initialize")
    assert current_init is not None
    assert current_init.report.provenance.operation_id == "init-1"
    assert current_init.freshness is ResultFreshness.CURRENT

    commit_operation(journal, update)
    current_init = journal.current_capability(repository, "initialize")
    current_update = journal.current_capability(repository, "update")
    assert current_init is not None and current_init.freshness is ResultFreshness.STALE
    assert current_update is not None and current_update.freshness is ResultFreshness.CURRENT

    commit_operation(journal, second_init)
    current_init = journal.current_capability(repository, "initialize")
    current_update = journal.current_capability(repository, "update")
    assert current_init is not None and current_init.report.provenance.operation_id == "init-2"
    assert current_init.freshness is ResultFreshness.CURRENT
    assert current_update is not None and current_update.freshness is ResultFreshness.STALE
    assert [item.provenance.operation_id for item in journal.history(repository)] == [
        "init-1",
        "update-1",
        "init-2",
    ]
    assert journal.operation("init-1") == first_init


def test_freshness_uses_capability_identity_not_action_coincidence(tmp_path: Path) -> None:
    repository = tmp_path / "identity"
    repository.mkdir()
    generic_plan = report(repository, OperationAction.PLAN, Verdict.READY, "plan-generic")
    dependency_update = report(
        repository, OperationAction.UPDATE, Verdict.UPDATED, "update-dependency"
    )
    dependency_update = dependency_update.model_copy(
        update={
            "capability": dependency_update.capability.model_copy(
                update={"invalidates_capabilities": ("verify",)}
            )
        }
    )

    assert generic_plan.capability.capability_id == "plan"
    assert result_freshness(generic_plan, [dependency_update.capability]) is ResultFreshness.CURRENT


def test_invalidates_all_stales_any_prior_capability(tmp_path: Path) -> None:
    repository = tmp_path / "all"
    repository.mkdir()
    init = report(repository, OperationAction.INIT, Verdict.READY, "init-before-task")
    later = report(repository, OperationAction.UPDATE, Verdict.UPDATED, "arbitrary-worktree-task")
    later = later.model_copy(
        update={"capability": later.capability.model_copy(update={"invalidates_all": True})}
    )

    assert result_freshness(init, [later.capability]) is ResultFreshness.STALE
