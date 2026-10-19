from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRunTransition, RunNotFound
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run, resolved_intent
from tests.factories.journal import (
    admit_run,
    commit_operation,
    start_repository,
    start_run,
    terminalize_run,
)
from tests.factories.operation_reports import make_operation_report


def operation(
    identity: RepositoryIdentity,
    action: OperationAction,
    fleet_run_id: str | None,
    operation_id: str,
    verdict: Verdict = Verdict.READY,
) -> OperationReport:
    if fleet_run_id is not None:
        operation_id = repository_attempt_operation_id(fleet_run_id, identity.repository_id)
    return make_operation_report(
        identity,
        action,
        verdict=verdict,
        fleet_run_id=fleet_run_id,
        operation_id=operation_id,
        reason_code=(ReasonCode.NO_UPDATES if verdict is Verdict.UNCHANGED else ReasonCode.READY),
    )


def claim_effect(journal: Journal, run_id: str, identity: RepositoryIdentity) -> str:
    del journal
    return repository_attempt_operation_id(run_id, identity.repository_id)


def running_journal(
    tmp_path: Path, *, action: OperationAction = OperationAction.INIT
) -> tuple[Journal, RepositoryIdentity]:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-1", action, [identity])
    start_run(journal, "run-1")
    claim_effect(journal, "run-1", identity)
    return journal, identity


def _complete_run(
    journal: Journal,
    identity: RepositoryIdentity,
    *,
    run_id: str,
    action: OperationAction,
    operation_id: str,
    verdict: Verdict = Verdict.READY,
) -> None:
    admit_test_run(journal, run_id, action, [identity])
    start_run(journal, run_id)
    claim_effect(journal, run_id, identity)
    start_repository(journal, run_id, identity)
    commit_operation(journal, operation(identity, action, run_id, operation_id, verdict))
    terminalize_run(journal, run_id, FleetRunTerminalIntent.COMPLETE)


def test_run_history_is_compact_newest_first_and_cursor_paged(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "history.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    _complete_run(
        journal, identity, run_id="run-1", action=OperationAction.INIT, operation_id="op-1"
    )
    _complete_run(
        journal,
        identity,
        run_id="run-2",
        action=OperationAction.PLAN,
        operation_id="op-2",
        verdict=Verdict.FAILED,
    )
    _complete_run(
        journal, identity, run_id="run-3", action=OperationAction.VERIFY, operation_id="op-3"
    )

    view = journal.read_view()
    first = view.run_history(limit=2)

    assert [item.run.fleet_run_id for item in first.items] == ["run-3", "run-2"]
    assert first.items[1].outcome.value == "ATTENTION_REQUIRED"
    assert first.next_before_revision == first.items[-1].admitted_revision
    assert first.journal_revision == view.revision
    assert not hasattr(first.items[0], "operations")

    second = view.run_history(limit=2, before_revision=first.next_before_revision)
    assert [item.run.fleet_run_id for item in second.items] == ["run-1"]
    assert second.next_before_revision is None


def test_run_history_materializes_only_page_plus_continuation_witness(
    tmp_path: Path, monkeypatch
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "history-bounded.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    for number in range(1, 8):
        _complete_run(
            journal,
            identity,
            run_id=f"run-{number}",
            action=OperationAction.VERIFY,
            operation_id=f"op-{number}",
        )

    view = journal.read_view()
    calls: list[str] = []
    original = type(view)._required_indexed_run_state

    def counted(self, run_id: str):
        calls.append(run_id)
        return original(self, run_id)

    monkeypatch.setattr(type(view), "_required_indexed_run_state", counted)
    page = view.run_history(limit=2)

    assert [item.run.fleet_run_id for item in page.items] == ["run-7", "run-6"]
    assert calls == ["run-7", "run-6", "run-5"]
    assert page.next_before_revision == page.items[-1].admitted_revision


def test_run_history_view_is_pinned_while_new_runs_commit(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "history-cut.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    _complete_run(
        journal, identity, run_id="run-old", action=OperationAction.INIT, operation_id="op-old"
    )
    pinned = journal.read_view()

    _complete_run(
        journal, identity, run_id="run-new", action=OperationAction.VERIFY, operation_id="op-new"
    )

    assert [item.run.fleet_run_id for item in pinned.run_history().items] == ["run-old"]
    assert [item.run.fleet_run_id for item in journal.read_view().run_history().items] == [
        "run-new",
        "run-old",
    ]


def test_pinned_read_view_folds_each_run_once_then_reuses_indexed_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "projection-context.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    _complete_run(
        journal, identity, run_id="run-1", action=OperationAction.INIT, operation_id="op-1"
    )
    _complete_run(
        journal, identity, run_id="run-2", action=OperationAction.PLAN, operation_id="op-2"
    )

    from oh_goon.runtime import journal_index

    original = journal_index.project_fleet_run_detail
    calls = 0

    def counting_projection(events, resolver):
        nonlocal calls
        calls += 1
        return original(events, resolver)

    monkeypatch.setattr(journal_index, "project_fleet_run_detail", counting_projection)
    view = journal.read_view()
    assert calls == 2

    assert view.run_detail("run-1") is not None
    assert view.latest_run_family(OperationAction.PLAN) is not None
    assert len(view.run_history().items) == 2
    assert calls == 2


def test_repeated_unchanged_reads_reuse_verified_read_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.journal_store as journal_store_module

    journal = Journal(LocalDatabase(tmp_path / "read-cache.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    commit_operation(journal, operation(identity, OperationAction.INIT, None, "op-cache"))

    original = journal_store_module.JournalFactStore.load_entries
    calls = 0

    def counting_load_entries(self, connection):
        nonlocal calls
        calls += 1
        return original(self, connection)

    monkeypatch.setattr(
        journal_store_module.JournalFactStore, "load_entries", counting_load_entries
    )
    first = journal.read_view()
    second = journal.read_view()

    assert first.revision == second.revision == 1
    assert calls == 1


def test_run_overview_partitions_canonical_execution_truth_and_history_filters(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.run import FleetRunFailure
    from oh_goon.core.vocabulary import FleetRunOutcome

    journal = Journal(LocalDatabase(tmp_path / "overview.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    _complete_run(
        journal,
        identity,
        run_id="run-success",
        action=OperationAction.INIT,
        operation_id="op-success",
    )
    _complete_run(
        journal,
        identity,
        run_id="run-attention",
        action=OperationAction.PLAN,
        operation_id="op-attention",
        verdict=Verdict.FAILED,
    )
    admit_test_run(journal, "run-queued", OperationAction.VERIFY, [identity])
    # The canonical runtime admits one active run at a time, so use a second journal cut for a top-level failure.
    overview = journal.read_view().run_overview()

    assert overview.total == 3
    assert overview.successful == 1
    assert overview.attention_required == 1
    assert overview.queued == 1
    assert overview.failed == 0
    assert overview.running == 0
    assert overview.cancelled == 0
    assert [
        item.run.fleet_run_id
        for item in journal.read_view().run_history(outcome=FleetRunOutcome.SUCCESS).items
    ] == ["run-success"]
    assert [
        item.run.fleet_run_id
        for item in journal.read_view().run_history(status=FleetRunStatus.QUEUED).items
    ] == ["run-queued"]

    failed_journal = Journal(LocalDatabase(tmp_path / "failed-overview.db"))
    admit_test_run(failed_journal, "run-failed", OperationAction.INIT, [identity])
    start_run(failed_journal, "run-failed")
    terminalize_run(
        failed_journal,
        "run-failed",
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure.recovery("test failure"),
    )
    failed = failed_journal.read_view().run_overview()
    assert failed.failed == 1
    assert failed.total == 1


def test_required_run_detail_uses_one_canonical_not_found_boundary(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "journal.sqlite3"))

    with pytest.raises(RunNotFound, match="Unknown fleet run: missing-run"):
        journal.read_view().required_run_detail("missing-run")


def test_run_history_preserves_source_after_lifecycle_transitions(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "source-history.db"))
    identity = RepositoryIdentity(path=tmp_path / "repo-source")
    intent = resolved_intent(OperationAction.INIT)
    admitted = FleetRunAdmissionIntent(
        source=FleetRunSource.CICD,
        target=intent.target,
        capability=intent.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    admit_run(journal, "cicd-history", admitted, fleet_repository_scope(None), [identity])
    start_run(journal, "cicd-history")
    terminalize_run(
        journal,
        "cicd-history",
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure.recovery("fixture failure"),
    )

    page = journal.read_view().run_history(source=FleetRunSource.CICD)
    assert [item.run.fleet_run_id for item in page.items] == ["cicd-history"]
    assert page.items[0].run.source is FleetRunSource.CICD
    assert (
        journal.read_view().required_run_detail("cicd-history").game_tape.run_source
        is FleetRunSource.CICD
    )


def test_failed_terminalization_requires_structured_failure(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "terminal-failure-evidence.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-fail", OperationAction.INIT, [identity])

    with pytest.raises(InvalidRunTransition, match="structured failure"):
        terminalize_run(journal, "run-fail", FleetRunTerminalIntent.FAIL)


def test_incremental_observers_reject_negative_revision(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "negative-revision.db"))

    with pytest.raises(ValueError, match="zero or greater"):
        journal.run_details_changed_after(-1)
    with pytest.raises(ValueError, match="zero or greater"):
        journal.operation_reports_after(-1)


def test_terminal_run_identity_cannot_be_admitted_again(tmp_path: Path) -> None:
    from oh_goon.core.errors import StateConflict
    from oh_goon.core.vocabulary import FleetRunFailureStage

    journal = Journal(LocalDatabase(tmp_path / "duplicate-terminal-run.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-duplicate", OperationAction.INIT, [identity])
    failure = FleetRunFailure.from_exception(
        StateConflict("terminal test failure"), stage=FleetRunFailureStage.EXECUTION
    )
    terminalize_run(journal, "run-duplicate", FleetRunTerminalIntent.FAIL, failure)

    with pytest.raises(Exception, match="already exists"):
        admit_test_run(journal, "run-duplicate", OperationAction.INIT, [identity])


def test_operation_reports_after_returns_only_canonical_operation_facts(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "operation-reports-after.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    report = operation(identity, OperationAction.PLAN, None, "op-observed")
    commit_operation(journal, report)

    revision, reports = journal.operation_reports_after(0)

    assert revision == journal.read_view().revision
    assert reports == ((1, report),)
    same_revision, unchanged = journal.operation_reports_after(revision)
    assert same_revision == revision
    assert unchanged == ()


def test_direct_operation_revision_does_not_emit_fleet_run_change(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "direct-operation-change.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    commit_operation(journal, operation(identity, OperationAction.PLAN, None, "op-direct-change"))

    revision, details = journal.run_details_changed_after(0)

    assert revision == 1
    assert details == ()


def test_storage_maintenance_is_committed_as_canonical_run_fact(tmp_path: Path) -> None:
    from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt

    journal, identity = running_journal(tmp_path)
    report = operation(identity, OperationAction.INIT, "run-1", "ignored")
    start_repository(journal, "run-1", identity)
    commit_operation(journal, report)
    operation_id = report.provenance.operation_id
    receipt = FleetContentMaintenanceReceipt(
        outcome="MAINTAINED",
        reason=None,
        before_bytes=900,
        after_bytes=700,
        before_objects=9,
        after_objects=7,
        removed_bytes=200,
        removed_objects=2,
        protected_objects=1,
        idle_protected_objects=0,
        budget_satisfied=True,
        removal_limit_reached=False,
        recovered_deletions=0,
        removed_orphan_lifecycle=0,
        max_retained_bytes=1024,
        max_retained_objects=10,
        min_idle_ns=100,
        max_removed_bytes=500,
        max_removed_objects=3,
    )

    journal.record_storage_maintenance(
        fleet_run_id="run-1",
        repository_id=identity.repository_id,
        operation_id=operation_id,
        receipt=receipt,
    )

    batch = journal.run_events("run-1")
    assert batch.events[-1].event_type is FleetRunEventType.STORAGE_MAINTENANCE_RECORDED
    detail = journal.read_view().run_detail("run-1")
    assert detail is not None
    assert detail.storage_maintenance[-1].receipt == receipt
